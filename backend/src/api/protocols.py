"""Authenticated HTTP reads for fixed first-aid protocol presentations."""

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
from vital_relay.application.protocol_service import (
    ProtocolAuthenticationError,
    ProtocolNotFoundError,
    ProtocolService,
)
from vital_relay.domain.protocols import ProtocolPresentationView
from vital_relay.domain.persona_sessions import Persona
from vital_relay.persistence.database import DemoScopeUnavailableError
from vital_relay.protocols.registry import ProtocolContentError


router = APIRouter(tags=["protocols"])


def _configured_service(request: Request) -> ProtocolService:
    service = getattr(request.app.state, "protocol_service", None)
    if service is None:
        _raise_unavailable()
    return service


DeviceToken = Annotated[
    str | None,
    Header(alias=DEVICE_TOKEN_HEADER),
]
ResponderToken = Annotated[
    str | None,
    Header(alias=RESPONDER_TOKEN_HEADER),
]


@router.get(
    "/v1/incidents/{incident_id}/protocol",
    response_model=ProtocolPresentationView,
)
def get_command_protocol(
    incident_id: UUID,
    request: Request,
    device_token: DeviceToken = None,
) -> ProtocolPresentationView:
    authenticate_device_access(
        request,
        device_token,
        allowed_personas={Persona.COMMAND},
    )
    service = _configured_service(request)
    try:
        return service.get_for_command(incident_id)
    except ProtocolNotFoundError as exc:
        _raise_not_found(exc)
    except ProtocolContentError as exc:
        _raise_unavailable(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_unavailable(exc)


@router.get(
    "/v1/incidents/{incident_id}/responders/{responder_id}/protocol",
    response_model=ProtocolPresentationView,
)
def get_responder_protocol(
    incident_id: UUID,
    responder_id: UUID,
    request: Request,
    responder_token: ResponderToken = None,
) -> ProtocolPresentationView:
    service = _configured_service(request)
    principal = authenticate_responder_access(
        request,
        responder_token,
        responder_id=responder_id,
    )
    try:
        return service.get_for_responder(
            incident_id,
            responder_id,
            responder_token=(responder_token if principal is None else None),
            authenticated_responder_id=(
                principal.responder_id if principal is not None else None
            ),
        )
    except ProtocolAuthenticationError:
        _raise_responder_authentication()
    except ProtocolNotFoundError as exc:
        _raise_not_found(exc)
    except ProtocolContentError as exc:
        _raise_unavailable(exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_unavailable(exc)


def _raise_responder_authentication() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_responder_token"},
    )


def _raise_not_found(exc: ProtocolNotFoundError) -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=exc.as_detail(),
    ) from exc


def _raise_unavailable(exc: Exception | None = None) -> None:
    error = HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "protocol_content_unavailable"},
    )
    if exc is None:
        raise error
    raise error from exc
