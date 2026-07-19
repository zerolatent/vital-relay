"""Authenticated command and responder HTTP boundary for live dispatch."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import (
    DEVICE_TOKEN_HEADER,
    RESPONDER_TOKEN_HEADER,
    authenticate_device_access,
    authenticate_responder_access,
)
from vital_relay.application.dispatch_service import (
    DispatchConflictError,
    DispatchNotFoundError,
    DispatchService,
    ResponderAuthenticationError,
)
from vital_relay.application.incident_service import IncidentNotFoundError
from vital_relay.domain.dispatch import (
    AcceptedDispatchView,
    DispatchCoordinationView,
    ResponderDecisionReceiptView,
    ResponderDecisionRequest,
    ResponderIncidentView,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError
from vital_relay.protocols.registry import ProtocolContentError


router = APIRouter(tags=["dispatch"])


DeviceToken = Annotated[
    str | None,
    Header(alias=DEVICE_TOKEN_HEADER),
]
ResponderToken = Annotated[
    str | None,
    Header(alias=RESPONDER_TOKEN_HEADER),
]


@router.post(
    "/v1/incidents/{incident_id}/dispatch",
    response_model=DispatchCoordinationView,
)
def coordinate_dispatch(
    incident_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> DispatchCoordinationView:
    principal = authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY, Persona.COMMAND},
    )
    _authorize_dispatch_owner(request, incident_id, principal)
    service = _configured_dispatch_service(request)
    try:
        return service.coordinate(incident_id)
    except DispatchNotFoundError as exc:
        _raise_not_found(exc)
    except DispatchConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/incidents/{incident_id}/dispatch",
    response_model=DispatchCoordinationView,
)
def get_dispatch_coordination(
    incident_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> DispatchCoordinationView:
    principal = authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMUNITY, Persona.COMMAND},
    )
    _authorize_dispatch_owner(request, incident_id, principal)
    service = _configured_dispatch_service(request)
    try:
        return service.get_coordination(incident_id)
    except DispatchNotFoundError as exc:
        _raise_not_found(exc)
    except DispatchConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/incidents/{incident_id}/responders/{responder_id}/invitation",
    response_model=ResponderIncidentView,
)
def get_responder_invitation(
    incident_id: UUID,
    responder_id: UUID,
    request: Request,
    responder_token: ResponderToken = None,
) -> ResponderIncidentView:
    service = _configured_dispatch_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    try:
        return service.get_responder_incident(
            incident_id,
            responder_id,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except DispatchNotFoundError as exc:
        _raise_not_found(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.post(
    "/v1/incidents/{incident_id}/responders/{responder_id}/response",
    response_model=ResponderDecisionReceiptView,
)
def respond_to_invitation(
    incident_id: UUID,
    responder_id: UUID,
    decision: ResponderDecisionRequest,
    request: Request,
    responder_token: ResponderToken = None,
) -> ResponderDecisionReceiptView:
    service = _configured_dispatch_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    try:
        return ResponderDecisionReceiptView.from_result(
            service.respond(
                incident_id,
                responder_id,
                decision,
                responder_token=(responder_token if principal is None else None),
                authenticated_responder_id=(
                    principal.responder_id if principal is not None else None
                ),
            )
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except DispatchNotFoundError as exc:
        _raise_not_found(exc)
    except DispatchConflictError as exc:
        _raise_conflict(exc)
    except ProtocolContentError as exc:
        _raise_protocol_unavailable(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/incidents/{incident_id}/responders/{responder_id}/dispatch",
    response_model=AcceptedDispatchView,
)
def get_accepted_responder_dispatch(
    incident_id: UUID,
    responder_id: UUID,
    request: Request,
    responder_token: ResponderToken = None,
) -> AcceptedDispatchView:
    service = _configured_dispatch_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    try:
        return service.get_accepted_dispatch(
            incident_id,
            responder_id,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ResponderAuthenticationError:
        _raise_responder_authentication()
    except DispatchNotFoundError as exc:
        _raise_not_found(exc)
    except DispatchConflictError as exc:
        _raise_conflict(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


def _configured_dispatch_service(request: Request) -> DispatchService:
    service = getattr(request.app.state, "dispatch_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "dispatch_persistence_unavailable"},
        )
    return service


def _raise_responder_authentication() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_responder_token"},
    )


def _raise_conflict(exc: DispatchConflictError) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=exc.as_detail(),
    ) from exc


def _raise_not_found(exc: DispatchNotFoundError) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=exc.as_detail(),
    ) from exc


def _raise_persistence_unavailable(
    exc: DemoScopeUnavailableError | SQLAlchemyError,
) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "dispatch_persistence_unavailable"},
    ) from exc


def _raise_protocol_unavailable(exc: ProtocolContentError) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "protocol_content_unavailable"},
    ) from exc


def _authorize_dispatch_owner(
    request: Request,
    incident_id: UUID,
    principal: PersonaPrincipal | None,
) -> None:
    if principal is None or principal.persona is Persona.COMMAND:
        return
    incident_service = getattr(request.app.state, "incident_service", None)
    if incident_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "incident_persistence_unavailable"},
        )
    try:
        incident = incident_service.get(incident_id)
    except IncidentNotFoundError as exc:
        # Dispatch returns its own privacy-safe absence shape below; avoid
        # leaking whether an incident belongs to another community account.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "identifier": str(incident_id)},
        ) from exc
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "incident_persistence_unavailable"},
        ) from exc
    if principal.user_id != incident.user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "identifier": str(incident_id)},
        )
