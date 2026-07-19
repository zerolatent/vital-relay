"""Command-only HTTP boundary for durable sandboxed coordination-agent runs."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import authenticate_bearer, require_persona
from vital_relay.application.agent_control import (
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentRunRecord,
)
from vital_relay.application.agent_service import (
    AgentRunCommand,
    AgentRunService,
    AgentRunServiceError,
)
from vital_relay.application.incident_service import IncidentNotFoundError
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError


router = APIRouter(tags=["agent runs"])

Authorization = Annotated[
    str | None,
    Header(alias="Authorization"),
]


@router.post(
    "/v1/incidents/{incident_id}/agent-runs",
    response_model=AgentRunRecord,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": AgentRunRecord,
            "description": "Exact retry of a terminal run.",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid command session."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "The session is not an incident-command session."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Incident not found."},
        status.HTTP_409_CONFLICT: {
            "description": "Incident state or run identity conflict."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The agent control plane is unavailable."
        },
    },
)
def start_agent_run(
    incident_id: UUID,
    command: AgentRunCommand,
    request: Request,
    response: Response,
    authorization: Authorization = None,
) -> AgentRunRecord:
    principal = _authenticate_command(request, authorization)
    service = _configured_agent_run_service(request)
    try:
        execution = service.start(incident_id, command, principal)
    except Exception as exc:
        _raise_agent_error(exc)
    if execution.replayed:
        response.status_code = status.HTTP_200_OK
    return execution.record


@router.get(
    "/v1/incidents/{incident_id}/agent-runs/{run_id}",
    response_model=AgentRunRecord,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid command session."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "The session is not an incident-command session."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Agent run not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The agent control plane is unavailable."
        },
    },
)
def get_agent_run(
    incident_id: UUID,
    run_id: UUID,
    request: Request,
    authorization: Authorization = None,
) -> AgentRunRecord:
    principal = _authenticate_command(request, authorization)
    service = _configured_agent_run_service(request)
    try:
        return service.get(incident_id, run_id, principal)
    except Exception as exc:
        _raise_agent_error(exc)


@router.get(
    "/v1/incidents/{incident_id}/agent-runs",
    response_model=tuple[AgentRunRecord, ...],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid command session."
        },
        status.HTTP_403_FORBIDDEN: {
            "description": "The session is not an incident-command session."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "The agent control plane is unavailable."
        },
    },
)
def list_agent_runs(
    incident_id: UUID,
    request: Request,
    authorization: Authorization = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> tuple[AgentRunRecord, ...]:
    principal = _authenticate_command(request, authorization)
    service = _configured_agent_run_service(request)
    try:
        return service.list_for_incident(incident_id, principal, limit=limit)
    except Exception as exc:
        _raise_agent_error(exc)


def _authenticate_command(
    request: Request,
    authorization: str | None,
) -> PersonaPrincipal:
    principal = authenticate_bearer(request, authorization)
    require_persona(principal, {Persona.COMMAND})
    return principal


def _configured_agent_run_service(request: Request) -> AgentRunService:
    service = getattr(request.app.state, "agent_run_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "agent_control_plane_unavailable"},
        )
    return service


def _raise_agent_error(exc: Exception) -> NoReturn:
    if isinstance(exc, IncidentNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, AgentRunNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": exc.code},
        ) from exc
    if isinstance(exc, AgentRunServiceError):
        if exc.code == "agent_run_not_found":
            error_status = status.HTTP_404_NOT_FOUND
        elif exc.code == "active_agent_policy_unavailable":
            error_status = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            error_status = status.HTTP_409_CONFLICT
        raise HTTPException(
            status_code=error_status,
            detail=exc.as_detail(),
        ) from exc
    if isinstance(exc, AgentRunConflictError):
        error_status = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "incident_not_found"
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(
            status_code=error_status,
            detail={"code": exc.code},
        ) from exc
    if isinstance(exc, (DemoScopeUnavailableError, SQLAlchemyError)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "agent_control_plane_unavailable"},
        ) from exc
    raise exc
