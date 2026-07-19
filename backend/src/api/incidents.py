"""Authenticated HTTP boundary for wearable events and durable incidents."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import (
    DEVICE_TOKEN_HEADER,
    authenticate_device_access,
)
from vital_relay.application.incident_service import (
    IncidentConflictError,
    IncidentNotFoundError,
    IncidentService,
)
from vital_relay.domain.incidents import (
    CheckInRequest,
    CheckInResult,
    EventIngestionStatus,
    IncidentResolutionReceipt,
    IncidentResolutionRequest,
    IncidentTimelineEntry,
    IncidentView,
    WearableEventRequest,
    WearableEventResult,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError

router = APIRouter(tags=["incidents"])


def _configured_incident_service(request: Request) -> IncidentService:
    service = getattr(request.app.state, "incident_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "incident_persistence_unavailable"},
        )

    return service


DeviceToken = Annotated[
    str | None,
    Header(alias=DEVICE_TOKEN_HEADER),
]


@router.post(
    "/v1/wearable/events",
    response_model=WearableEventResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": WearableEventResult,
            "description": "Exact retry or naturally deduplicated event.",
        },
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid device token."
        },
        status.HTTP_409_CONFLICT: {
            "description": "A stable event identity has conflicting content."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Durable incident persistence is unavailable."
        },
    },
)
def ingest_wearable_event(
    event: WearableEventRequest,
    response: Response,
    request: Request,
    device_token: DeviceToken = None,
) -> WearableEventResult:
    principal = authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY},
    )
    _authorize_community_input(
        principal,
        user_id=event.user_id,
        device_id=event.device_id,
    )
    service = _configured_incident_service(request)
    try:
        result = service.ingest(event)
    except IncidentConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)
    if result.status is EventIngestionStatus.ALREADY_PROCESSED:
        response.status_code = status.HTTP_200_OK
    return result


@router.get(
    "/v1/incidents/{incident_id}",
    response_model=IncidentView,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid device token."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Incident not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Durable incident persistence is unavailable."
        },
    },
)
def get_incident(
    incident_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> IncidentView:
    principal = authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY, Persona.COMMAND},
    )
    service = _configured_incident_service(request)
    try:
        incident = service.get(incident_id)
        _authorize_incident_read(principal, incident)
        return incident
    except IncidentNotFoundError as exc:
        _raise_not_found(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.post(
    "/v1/incidents/{incident_id}/check-in",
    response_model=CheckInResult,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid device token."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Incident not found."},
        status.HTTP_409_CONFLICT: {
            "description": "Conflicting response ID or forbidden state transition."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Durable incident persistence is unavailable."
        },
    },
)
def record_incident_check_in(
    incident_id: UUID,
    check_in: CheckInRequest,
    request: Request,
    device_token: DeviceToken = None,
) -> CheckInResult:
    principal = authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY},
    )
    service = _configured_incident_service(request)
    try:
        incident = service.get(incident_id)
        _authorize_incident_read(principal, incident)
        if principal is not None:
            _authorize_community_input(
                principal,
                user_id=incident.user_id,
                device_id=check_in.device_id,
            )
        return service.check_in(incident_id, check_in)
    except IncidentNotFoundError as exc:
        _raise_not_found(exc)
    except IncidentConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.post(
    "/v1/incidents/{incident_id}/resolution",
    response_model=IncidentResolutionReceipt,
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid device token."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Incident not found."},
        status.HTTP_409_CONFLICT: {
            "description": "Conflicting resolution ID or forbidden transition."
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Durable incident persistence is unavailable."
        },
    },
)
def resolve_incident(
    incident_id: UUID,
    resolution: IncidentResolutionRequest,
    request: Request,
    device_token: DeviceToken = None,
) -> IncidentResolutionReceipt:
    authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMAND},
    )
    service = _configured_incident_service(request)
    try:
        return service.resolve(incident_id, resolution)
    except IncidentNotFoundError as exc:
        _raise_not_found(exc)
    except IncidentConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/incidents/{incident_id}/timeline",
    response_model=tuple[IncidentTimelineEntry, ...],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "description": "Missing or invalid device token."
        },
        status.HTTP_404_NOT_FOUND: {"description": "Incident not found."},
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "description": "Durable incident persistence is unavailable."
        },
    },
)
def get_incident_timeline(
    incident_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> tuple[IncidentTimelineEntry, ...]:
    authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMAND},
    )
    service = _configured_incident_service(request)
    try:
        return service.timeline(incident_id)
    except IncidentNotFoundError as exc:
        _raise_not_found(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


def _raise_conflict(exc: IncidentConflictError) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=exc.as_detail(),
    ) from exc


def _raise_not_found(exc: IncidentNotFoundError) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=exc.as_detail(),
    ) from exc


def _raise_persistence_unavailable(
    exc: DemoScopeUnavailableError | SQLAlchemyError,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "incident_persistence_unavailable"},
    ) from exc


def _authorize_community_input(
    principal: PersonaPrincipal | None,
    *,
    user_id: str,
    device_id: str,
) -> None:
    if principal is None:
        return
    if (
        principal.persona is not Persona.COMMUNITY
        or principal.user_id != user_id
        or str(principal.installation_id) != device_id.lower()
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "persona_not_authorized"},
        )


def _authorize_incident_read(
    principal: PersonaPrincipal | None,
    incident: IncidentView,
) -> None:
    if principal is None or principal.persona is Persona.COMMAND:
        return
    if (
        principal.persona is not Persona.COMMUNITY
        or principal.user_id != incident.user_id
    ):
        # Do not disclose whether another community member's incident exists.
        _raise_not_found(IncidentNotFoundError(incident.incident_id))
