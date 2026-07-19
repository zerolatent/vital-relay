"""Bounded sandbox-to-host HTTP adapter for the internal agent tool proxy.

The model sees only the typed ``BoundedToolGateway`` schemas. The short-lived
capability and transport idempotency identity are copied from trusted runtime
context into the HTTP envelope by this adapter and are never model arguments.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from vital_relay.agent.capability_runtime import (
    MAX_CAPABILITY_TOKEN_LENGTH,
    ToolInvocationContext,
)
from vital_relay.agent.contracts import ToolEffect
from vital_relay.agent.tool_contracts import (
    COORDINATE_DISPATCH,
    GET_DISPATCH_COORDINATION,
    GET_FIXED_PROTOCOL,
    GET_INCIDENT,
    GET_INCIDENT_TIMELINE,
    AgentDispatchToolView,
    AgentIncidentToolView,
    AgentProtocolReferenceToolView,
    AgentTimelineToolResult,
    IncidentBoundToolInput,
    TimelineToolInput,
)
from vital_relay.agent.tools import (
    BoundedToolGateway,
    ToolBinding,
    ToolGatewayError,
    ToolGatewayErrorCode,
)
from vital_relay.agent.tool_transport import (
    ToolProxyErrorCode,
    ToolProxyInvocation,
    ToolProxyTransportSuccess,
)


AGENT_CAPABILITY_HEADER: Final = "X-Vital-Relay-Agent-Capability"
AGENT_TOOL_PROXY_PATH: Final = "/internal/v1/agent/tools/invoke"
DOCKER_TOOL_PROXY_ENDPOINT: Final = (
    f"http://tool-proxy-gateway:8080{AGENT_TOOL_PROXY_PATH}"
)
DEFAULT_MAX_TOOL_REQUEST_BYTES: Final = 32_768
DEFAULT_MAX_TOOL_RESPONSE_BYTES: Final = 131_072
DEFAULT_TOOL_TIMEOUT_SECONDS: Final = 3.0
NEMOCLAW_PROXY_HOST_FILE: Final = Path(
    "/usr/local/share/nemoclaw/dcode-proxy-host"
)
NEMOCLAW_PROXY_PORT_FILE: Final = Path(
    "/usr/local/share/nemoclaw/dcode-proxy-port"
)
MAX_NEMOCLAW_CA_BUNDLE_BYTES: Final = 4 * 1024 * 1024
_NEMOCLAW_PROXY_HOST_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

_JSON_ADAPTER = TypeAdapter(JsonValue)


@dataclass(frozen=True, slots=True)
class HttpToolContract:
    """Model-visible schema plus the expected host response schema."""

    name: str
    description: str
    effect: ToolEffect
    input_model: type[BaseModel]
    output_model: type[BaseModel]


INITIAL_HTTP_TOOL_CONTRACTS: Final = (
    HttpToolContract(
        name=GET_INCIDENT,
        description="Read the current privacy-bounded incident state.",
        effect=ToolEffect.READ,
        input_model=IncidentBoundToolInput,
        output_model=AgentIncidentToolView,
    ),
    HttpToolContract(
        name=GET_INCIDENT_TIMELINE,
        description="Read a bounded page of observable incident timeline entries.",
        effect=ToolEffect.READ,
        input_model=TimelineToolInput,
        output_model=AgentTimelineToolResult,
    ),
    HttpToolContract(
        name=GET_DISPATCH_COORDINATION,
        description="Read coarse responder-search and invitation state.",
        effect=ToolEffect.READ,
        input_model=IncidentBoundToolInput,
        output_model=AgentDispatchToolView,
    ),
    HttpToolContract(
        name=COORDINATE_DISPATCH,
        description=(
            "Atomically advance bounded responder coordination using the "
            "application service's authorization and recipient rules."
        ),
        effect=ToolEffect.MUTATE,
        input_model=IncidentBoundToolInput,
        output_model=AgentDispatchToolView,
    ),
    HttpToolContract(
        name=GET_FIXED_PROTOCOL,
        description=(
            "Read only the immutable fixed-protocol identity for an active "
            "accepted response; no medical content is returned."
        ),
        effect=ToolEffect.READ,
        input_model=IncidentBoundToolInput,
        output_model=AgentProtocolReferenceToolView,
    ),
)


@dataclass(frozen=True, slots=True)
class NemoClawHTTPTransport:
    """Explicit OpenShell CONNECT proxy and trust context for one worker."""

    proxy_url: str
    ssl_context: ssl.SSLContext = field(repr=False)

    @classmethod
    def from_managed_environment(
        cls,
        *,
        environment: Mapping[str, str] | None = None,
        proxy_host_file: Path = NEMOCLAW_PROXY_HOST_FILE,
        proxy_port_file: Path = NEMOCLAW_PROXY_PORT_FILE,
        expected_owner_uid: int = 0,
    ) -> NemoClawHTTPTransport:
        """Resolve only image-owned proxy inputs; reject ambient overrides."""

        active_environment = os.environ if environment is None else environment
        host = _read_managed_text_file(
            proxy_host_file,
            label="proxy host",
            expected_owner_uid=expected_owner_uid,
            exact_mode=0o444,
            maximum_bytes=256,
        )
        port_text = _read_managed_text_file(
            proxy_port_file,
            label="proxy port",
            expected_owner_uid=expected_owner_uid,
            exact_mode=0o444,
            maximum_bytes=16,
        )
        if _NEMOCLAW_PROXY_HOST_PATTERN.fullmatch(host) is None:
            raise ValueError("managed NemoClaw proxy host is invalid")
        if not port_text.isascii() or not port_text.isdecimal():
            raise ValueError("managed NemoClaw proxy port is invalid")
        port = int(port_text)
        if not 1 <= port <= 65_535:
            raise ValueError("managed NemoClaw proxy port is invalid")
        proxy_url = f"http://{host}:{port}"
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            if active_environment.get(name) != proxy_url:
                raise ValueError("NemoClaw runtime proxy environment is inconsistent")
        expected_no_proxy = f"localhost,127.0.0.1,::1,{host}"
        if any(
            active_environment.get(name) != expected_no_proxy
            for name in ("NO_PROXY", "no_proxy")
        ):
            raise ValueError("NemoClaw no-proxy environment is inconsistent")
        if any(
            active_environment.get(name)
            for name in ("ALL_PROXY", "all_proxy", "OPENAI_PROXY")
        ):
            raise ValueError("unmanaged proxy fallback is forbidden")

        raw_ca_path = active_environment.get("SSL_CERT_FILE", "")
        ca_path = Path(raw_ca_path)
        if not raw_ca_path or not ca_path.is_absolute():
            raise ValueError("NemoClaw TLS CA path is missing or invalid")
        for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
            configured_path = active_environment.get(name)
            if configured_path is not None and configured_path != raw_ca_path:
                raise ValueError("NemoClaw TLS CA environment is inconsistent")
        ca_bytes = _read_managed_binary_file(
            ca_path,
            label="TLS CA bundle",
            expected_owner_uid=expected_owner_uid,
            maximum_bytes=MAX_NEMOCLAW_CA_BUNDLE_BYTES,
        )
        try:
            ca_text = ca_bytes.decode("ascii")
            context = ssl.create_default_context(cadata=ca_text)
        except (UnicodeError, ValueError, ssl.SSLError) as exc:
            raise ValueError("NemoClaw TLS CA bundle is invalid") from exc
        return cls(proxy_url=proxy_url, ssl_context=context)


class HttpToolProxyClient:
    """Synchronous, no-retry HTTP adapter suitable for a sandboxed runner.

    The adapter intentionally does not log requests or responses. Production
    construction uses an explicitly validated NemoClaw CONNECT proxy and CA;
    tests may inject an isolated client. Redirects are disabled per request.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        max_request_bytes: int = DEFAULT_MAX_TOOL_REQUEST_BYTES,
        max_response_bytes: int = DEFAULT_MAX_TOOL_RESPONSE_BYTES,
        identifier_factory: Callable[[], UUID] = uuid4,
        contracts: tuple[HttpToolContract, ...] = INITIAL_HTTP_TOOL_CONTRACTS,
    ) -> None:
        parsed_endpoint = httpx.URL(endpoint)
        if (
            parsed_endpoint.scheme not in {"http", "https"}
            or not parsed_endpoint.host
            or parsed_endpoint.username
            or parsed_endpoint.password
            or parsed_endpoint.query
            or parsed_endpoint.fragment
            or parsed_endpoint.path != AGENT_TOOL_PROXY_PATH
            or (
                parsed_endpoint.scheme == "http"
                and parsed_endpoint.host
                not in {"127.0.0.1", "localhost", "::1", "tool-proxy-gateway"}
            )
        ):
            raise ValueError(
                "tool proxy endpoint must be an absolute credential-free URL"
            )
        if not 0.1 <= timeout_seconds <= 30.0:
            raise ValueError("tool proxy timeout must be between 0.1 and 30 seconds")
        if not 1_024 <= max_request_bytes <= 1_048_576:
            raise ValueError("invalid maximum tool request size")
        if not 1_024 <= max_response_bytes <= 1_048_576:
            raise ValueError("invalid maximum tool response size")
        if not contracts:
            raise ValueError("at least one HTTP tool contract is required")

        if client is None:
            raise ValueError("an explicit reviewed HTTP transport is required")
        self._endpoint = str(parsed_endpoint)
        self._client = client
        self._owns_client = False
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max_response_bytes
        self._identifier_factory = identifier_factory
        self._contracts = contracts

    @classmethod
    def nemoclaw(
        cls,
        endpoint: str,
        *,
        environment: Mapping[str, str] | None = None,
        proxy_host_file: Path = NEMOCLAW_PROXY_HOST_FILE,
        proxy_port_file: Path = NEMOCLAW_PROXY_PORT_FILE,
        expected_owner_uid: int = 0,
        **kwargs,
    ) -> HttpToolProxyClient:
        """Build the production client without trusting ambient proxy values."""

        transport = NemoClawHTTPTransport.from_managed_environment(
            environment=environment,
            proxy_host_file=proxy_host_file,
            proxy_port_file=proxy_port_file,
            expected_owner_uid=expected_owner_uid,
        )
        client = httpx.Client(
            proxy=transport.proxy_url,
            verify=transport.ssl_context,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            instance = cls(endpoint, client=client, **kwargs)
        except BaseException:
            client.close()
            raise
        instance._owns_client = True
        return instance

    @classmethod
    def docker(
        cls,
        endpoint: str,
        **kwargs,
    ) -> HttpToolProxyClient:
        """Build the Docker client for its one internal authenticated route."""

        if endpoint != DOCKER_TOOL_PROXY_ENDPOINT:
            raise ValueError("Docker tool proxy endpoint is not the fixed route")
        client = httpx.Client(
            follow_redirects=False,
            trust_env=False,
        )
        try:
            instance = cls(endpoint, client=client, **kwargs)
        except BaseException:
            client.close()
            raise
        instance._owns_client = True
        return instance

    def gateway(self) -> BoundedToolGateway:
        """Return the exact initial tool surface backed by the HTTP proxy."""

        return BoundedToolGateway(
            tuple(
                ToolBinding(
                    name=contract.name,
                    description=contract.description,
                    effect=contract.effect,
                    input_model=contract.input_model,
                    handler=(
                        lambda arguments, context, active=contract: self._invoke(
                            active,
                            arguments,
                            context,
                        )
                    ),
                )
                for contract in self._contracts
            )
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpToolProxyClient:
        return self

    def __exit__(self, *args: object) -> None:
        del args
        self.close()

    def _invoke(
        self,
        contract: HttpToolContract,
        arguments: BaseModel,
        context: ToolInvocationContext,
    ) -> JsonValue:
        invocation_id = context.idempotency_key or self._identifier_factory()
        invocation = ToolProxyInvocation(
            invocation_id=invocation_id,
            scope_id=context.scope_id,
            run_id=context.run_id,
            incident_id=context.incident_id,
            policy_sha256=context.policy_sha256,
            tool_name=contract.name,
            arguments=arguments.model_dump(mode="json"),
            idempotency_key=(
                context.idempotency_key
                if contract.effect is ToolEffect.MUTATE
                else None
            ),
        )
        body = invocation.model_dump_json().encode("utf-8")
        if len(body) > self._max_request_bytes:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_ARGUMENTS)
        raw_capability = context.raw_capability.get_secret_value()
        if not raw_capability or len(raw_capability) > MAX_CAPABILITY_TOKEN_LENGTH:
            raise ToolGatewayError(ToolGatewayErrorCode.HANDLER_FAILED)

        try:
            with self._client.stream(
                "POST",
                self._endpoint,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    AGENT_CAPABILITY_HEADER: raw_capability,
                },
                content=body,
                timeout=self._timeout,
                follow_redirects=False,
            ) as response:
                response_body = _read_bounded_response(
                    response,
                    maximum_bytes=self._max_response_bytes,
                )
        except ToolGatewayError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise ToolGatewayError(ToolGatewayErrorCode.HANDLER_FAILED) from exc

        if response.status_code != 200:
            raise ToolGatewayError(_map_proxy_error(response_body))
        content_type = response.headers.get("content-type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT)
        try:
            envelope = ToolProxyTransportSuccess.model_validate_json(response_body)
            parsed_result = contract.output_model.model_validate(envelope.result)
            return _JSON_ADAPTER.validate_python(
                parsed_result.model_dump(mode="json")
            )
        except ValidationError as exc:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT) from exc


def _read_bounded_response(
    response: httpx.Response,
    *,
    maximum_bytes: int,
) -> bytes:
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT) from exc
        if content_length < 0 or content_length > maximum_bytes:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT)

    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT)
    return bytes(body)


def _map_proxy_error(body: bytes) -> ToolGatewayErrorCode:
    """Collapse host errors into the existing bounded model-visible taxonomy."""

    try:
        payload = json.loads(body)
        code = payload["detail"]["code"]
    except (KeyError, TypeError, UnicodeError, ValueError):
        return ToolGatewayErrorCode.HANDLER_FAILED
    return {
        ToolProxyErrorCode.INVALID_CAPABILITY.value: (
            ToolGatewayErrorCode.EXPIRED_CAPABILITY
        ),
        ToolProxyErrorCode.EXPIRED_CAPABILITY.value: (
            ToolGatewayErrorCode.EXPIRED_CAPABILITY
        ),
        ToolProxyErrorCode.RUN_NOT_ACTIVE.value: (
            ToolGatewayErrorCode.EXPIRED_CAPABILITY
        ),
        ToolProxyErrorCode.WRONG_RUN.value: ToolGatewayErrorCode.POLICY_MISMATCH,
        ToolProxyErrorCode.WRONG_SCOPE.value: ToolGatewayErrorCode.POLICY_MISMATCH,
        ToolProxyErrorCode.WRONG_INCIDENT.value: (
            ToolGatewayErrorCode.POLICY_MISMATCH
        ),
        ToolProxyErrorCode.POLICY_MISMATCH.value: (
            ToolGatewayErrorCode.POLICY_MISMATCH
        ),
        ToolProxyErrorCode.TOOL_NOT_REGISTERED.value: (
            ToolGatewayErrorCode.UNKNOWN_TOOL
        ),
        ToolProxyErrorCode.TOOL_NOT_ALLOWED.value: (
            ToolGatewayErrorCode.TOOL_NOT_ALLOWED
        ),
        ToolProxyErrorCode.TOOL_BUDGET_EXCEEDED.value: (
            ToolGatewayErrorCode.TOOL_BUDGET_EXCEEDED
        ),
        ToolProxyErrorCode.INCIDENT_NOT_ACTIVE.value: (
            ToolGatewayErrorCode.TOOL_NOT_ALLOWED
        ),
        ToolProxyErrorCode.INVALID_ARGUMENTS.value: (
            ToolGatewayErrorCode.INVALID_ARGUMENTS
        ),
        ToolProxyErrorCode.STALE_STATE.value: (
            ToolGatewayErrorCode.TOOL_NOT_ALLOWED
        ),
        ToolProxyErrorCode.IDEMPOTENCY_REQUIRED.value: (
            ToolGatewayErrorCode.INVALID_ARGUMENTS
        ),
        ToolProxyErrorCode.IDEMPOTENCY_CONFLICT.value: (
            ToolGatewayErrorCode.INVALID_ARGUMENTS
        ),
        ToolProxyErrorCode.INVALID_RESULT.value: ToolGatewayErrorCode.INVALID_RESULT,
    }.get(str(code), ToolGatewayErrorCode.HANDLER_FAILED)


def _read_managed_text_file(
    path: Path,
    *,
    label: str,
    expected_owner_uid: int,
    exact_mode: int,
    maximum_bytes: int,
) -> str:
    raw = _read_managed_file(
        path,
        label=label,
        expected_owner_uid=expected_owner_uid,
        maximum_bytes=maximum_bytes,
        exact_mode=exact_mode,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"managed NemoClaw {label} is invalid") from exc
    value = text.removesuffix("\n")
    if (
        not value
        or text not in {value, value + "\n"}
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"managed NemoClaw {label} is invalid")
    return value


def _read_managed_binary_file(
    path: Path,
    *,
    label: str,
    expected_owner_uid: int,
    maximum_bytes: int,
) -> bytes:
    return _read_managed_file(
        path,
        label=label,
        expected_owner_uid=expected_owner_uid,
        maximum_bytes=maximum_bytes,
        exact_mode=None,
    )


def _read_managed_file(
    path: Path,
    *,
    label: str,
    expected_owner_uid: int,
    maximum_bytes: int,
    exact_mode: int | None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"managed NemoClaw {label} is missing or unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_owner_uid
            or metadata.st_size <= 0
            or metadata.st_size > maximum_bytes
            or (exact_mode is not None and mode != exact_mode)
            or (exact_mode is None and mode & 0o022 != 0)
        ):
            raise ValueError(f"managed NemoClaw {label} is missing or unsafe")
        chunks = bytearray()
        while len(chunks) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(65_536, maximum_bytes + 1 - len(chunks)),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        if not chunks or len(chunks) > maximum_bytes:
            raise ValueError(f"managed NemoClaw {label} is missing or unsafe")
        return bytes(chunks)
    finally:
        os.close(descriptor)
