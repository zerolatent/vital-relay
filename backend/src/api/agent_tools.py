"""Private authenticated HTTP boundary for sandboxed coordination tools."""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import ValidationError

from vital_relay.agent.capabilities import MAX_CAPABILITY_TOKEN_LENGTH
from vital_relay.agent.http_tools import (
    AGENT_CAPABILITY_HEADER,
    AGENT_TOOL_PROXY_PATH,
    DEFAULT_MAX_TOOL_REQUEST_BYTES,
    DEFAULT_MAX_TOOL_RESPONSE_BYTES,
    ToolProxyTransportSuccess,
)
from vital_relay.application.agent_tool_execution import (
    AgentToolExecutionPool,
    AgentToolExecutionSaturated,
)
from vital_relay.application.tool_proxy import (
    InternalAgentToolProxy,
    ToolProxyError,
    ToolProxyErrorCode,
    ToolProxyInvocation,
)


INVALID_TOOL_INVOCATION: Final = "invalid_tool_invocation"
TOOL_INVOCATION_TOO_LARGE: Final = "tool_invocation_too_large"
TOOL_PROXY_UNAVAILABLE: Final = "tool_proxy_unavailable"

router = APIRouter(tags=["internal agent tools"])
AgentCapability = Annotated[
    str | None,
    Header(alias=AGENT_CAPABILITY_HEADER),
]


@router.post(
    AGENT_TOOL_PROXY_PATH,
    response_model=None,
    include_in_schema=False,
)
async def invoke_agent_tool(
    request: Request,
    agent_capability: AgentCapability = None,
) -> ToolProxyTransportSuccess:
    """Invoke one bounded tool without exposing capability material in JSON."""

    if (
        agent_capability is None
        or not agent_capability
        or len(agent_capability) > MAX_CAPABILITY_TOKEN_LENGTH
    ):
        _raise_proxy_error(
            ToolProxyError(ToolProxyErrorCode.INVALID_CAPABILITY)
        )
    proxy = _configured_tool_proxy(request)
    execution_pool = _configured_execution_pool(request)
    body = await _read_bounded_request(request)
    try:
        invocation = ToolProxyInvocation.model_validate_json(body)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": INVALID_TOOL_INVOCATION},
        ) from exc

    try:
        result = await execution_pool.invoke(
            proxy,
            agent_capability,
            invocation,
        )
    except AgentToolExecutionSaturated as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": TOOL_PROXY_UNAVAILABLE},
        ) from exc
    except ToolProxyError as exc:
        _raise_proxy_error(exc)
    except Exception as exc:
        # Application and adapter exceptions can contain provider data. Only a
        # closed transport code crosses the internal HTTP boundary.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": TOOL_PROXY_UNAVAILABLE},
        ) from exc
    try:
        response = ToolProxyTransportSuccess(result=result)
        if (
            len(response.model_dump_json().encode("utf-8"))
            > DEFAULT_MAX_TOOL_RESPONSE_BYTES
        ):
            raise ValueError("tool proxy response exceeds the transport limit")
    except (ValidationError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": TOOL_PROXY_UNAVAILABLE},
        ) from exc
    return response


def _configured_tool_proxy(request: Request) -> InternalAgentToolProxy:
    proxy = getattr(request.app.state, "internal_agent_tool_proxy", None)
    if proxy is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": TOOL_PROXY_UNAVAILABLE},
        )
    return proxy


def _configured_execution_pool(request: Request) -> AgentToolExecutionPool:
    execution_pool = getattr(
        request.app.state,
        "agent_tool_execution_pool",
        None,
    )
    if execution_pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": TOOL_PROXY_UNAVAILABLE},
        )
    return execution_pool


async def _read_bounded_request(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/json"
    ):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"code": INVALID_TOOL_INVOCATION},
        )

    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": INVALID_TOOL_INVOCATION},
            ) from exc
        if content_length < 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": INVALID_TOOL_INVOCATION},
            )
        if content_length > DEFAULT_MAX_TOOL_REQUEST_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": TOOL_INVOCATION_TOO_LARGE},
            )

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > DEFAULT_MAX_TOOL_REQUEST_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail={"code": TOOL_INVOCATION_TOO_LARGE},
            )
    if not body:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": INVALID_TOOL_INVOCATION},
        )
    return bytes(body)


def _raise_proxy_error(exc: ToolProxyError) -> None:
    error_status = {
        ToolProxyErrorCode.INVALID_CAPABILITY: status.HTTP_401_UNAUTHORIZED,
        ToolProxyErrorCode.EXPIRED_CAPABILITY: status.HTTP_401_UNAUTHORIZED,
        ToolProxyErrorCode.RUN_NOT_ACTIVE: status.HTTP_409_CONFLICT,
        ToolProxyErrorCode.WRONG_RUN: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.WRONG_SCOPE: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.WRONG_INCIDENT: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.POLICY_MISMATCH: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.TOOL_NOT_REGISTERED: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.TOOL_NOT_ALLOWED: status.HTTP_403_FORBIDDEN,
        ToolProxyErrorCode.TOOL_BUDGET_EXCEEDED: (
            status.HTTP_429_TOO_MANY_REQUESTS
        ),
        ToolProxyErrorCode.INVALID_ARGUMENTS: (
            status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        ToolProxyErrorCode.IDEMPOTENCY_REQUIRED: (
            status.HTTP_422_UNPROCESSABLE_CONTENT
        ),
        ToolProxyErrorCode.STALE_STATE: status.HTTP_409_CONFLICT,
        ToolProxyErrorCode.INCIDENT_NOT_ACTIVE: status.HTTP_409_CONFLICT,
        ToolProxyErrorCode.IDEMPOTENCY_CONFLICT: status.HTTP_409_CONFLICT,
        ToolProxyErrorCode.IDEMPOTENCY_CAPACITY_EXCEEDED: (
            status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT: (
            status.HTTP_503_SERVICE_UNAVAILABLE
        ),
        ToolProxyErrorCode.APPLICATION_FAILED: status.HTTP_503_SERVICE_UNAVAILABLE,
        ToolProxyErrorCode.INVALID_RESULT: status.HTTP_503_SERVICE_UNAVAILABLE,
        ToolProxyErrorCode.AUDIT_UNAVAILABLE: status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(exc.code, status.HTTP_503_SERVICE_UNAVAILABLE)
    raise HTTPException(
        status_code=error_status,
        detail={"code": exc.code.value},
    ) from exc
