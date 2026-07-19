"""HTTP boundary for persona session lifecycle and active discovery."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.api.persona_auth import (
    ENROLLMENT_TOKEN_HEADER,
    REFRESH_TOKEN_HEADER,
    authenticate_bearer,
    configured_persona_session_service,
    valid_opaque_token,
)
from vital_relay.application.persona_session_service import (
    PersonaAuthenticationError,
    PersonaAuthorizationError,
)
from vital_relay.domain.persona_sessions import (
    ActiveIncidentList,
    Persona,
    PersonaSessionCreateRequest,
    PersonaSessionReceipt,
    PersonaSessionRevocationReceipt,
    PersonaSessionRotateRequest,
    PersonaSessionRotationReceipt,
    PersonaSessionView,
)
from vital_relay.persistence.database import DemoScopeUnavailableError


router = APIRouter(tags=["persona sessions"])

EnrollmentToken = Annotated[
    str | None,
    Header(alias=ENROLLMENT_TOKEN_HEADER),
]
RefreshToken = Annotated[
    str | None,
    Header(alias=REFRESH_TOKEN_HEADER),
]
Authorization = Annotated[str | None, Header(alias="Authorization")]


@router.post(
    "/v1/persona-sessions",
    response_model=PersonaSessionReceipt,
    status_code=status.HTTP_201_CREATED,
)
def create_persona_session(
    create_request: PersonaSessionCreateRequest,
    request: Request,
    enrollment_token: EnrollmentToken = None,
) -> PersonaSessionReceipt:
    if not valid_opaque_token(enrollment_token):
        _raise_authentication("invalid_enrollment_token")
    service = configured_persona_session_service(request)
    try:
        return service.create(
            create_request,
            enrollment_token=enrollment_token,
        )
    except PersonaAuthenticationError as exc:
        _raise_authentication(exc.code, exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/persona-sessions/current",
    response_model=PersonaSessionView,
)
def get_current_persona_session(
    request: Request,
    authorization: Authorization = None,
) -> PersonaSessionView:
    principal = authenticate_bearer(request, authorization)
    service = configured_persona_session_service(request)
    # Authenticate once at the transport boundary, then use the exact same
    # access token to load the public session metadata.
    _, _, access_token = authorization.partition(" ")  # type: ignore[union-attr]
    try:
        view = service.current(access_token=access_token)
    except PersonaAuthenticationError as exc:
        _raise_authentication(exc.code, exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)
    if view.session_id != principal.session_id:
        _raise_authentication("invalid_session_token")
    return view


@router.post(
    "/v1/persona-sessions/{session_id}/rotation",
    response_model=PersonaSessionRotationReceipt,
)
def rotate_persona_session(
    session_id: UUID,
    rotation: PersonaSessionRotateRequest,
    request: Request,
    refresh_token: RefreshToken = None,
) -> PersonaSessionRotationReceipt:
    if not valid_opaque_token(refresh_token):
        _raise_authentication("invalid_refresh_token")
    service = configured_persona_session_service(request)
    try:
        return service.rotate(
            session_id,
            rotation,
            refresh_token=refresh_token,
        )
    except PersonaAuthenticationError as exc:
        _raise_authentication(exc.code, exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.delete(
    "/v1/persona-sessions/{session_id}",
    response_model=PersonaSessionRevocationReceipt,
)
def revoke_persona_session(
    session_id: UUID,
    request: Request,
    refresh_token: RefreshToken = None,
) -> PersonaSessionRevocationReceipt:
    if not valid_opaque_token(refresh_token):
        _raise_authentication("invalid_refresh_token")
    service = configured_persona_session_service(request)
    try:
        return service.revoke(
            session_id,
            refresh_token=refresh_token,
        )
    except PersonaAuthenticationError as exc:
        _raise_authentication(exc.code, exc)
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


@router.get(
    "/v1/community/incidents/active",
    response_model=ActiveIncidentList,
)
def get_community_active_incidents(
    request: Request,
    authorization: Authorization = None,
) -> ActiveIncidentList:
    return _active_incidents(
        request,
        authorization=authorization,
        persona=Persona.COMMUNITY,
    )


@router.get(
    "/v1/responders/me/incidents/active",
    response_model=ActiveIncidentList,
)
def get_responder_active_incidents(
    request: Request,
    authorization: Authorization = None,
) -> ActiveIncidentList:
    return _active_incidents(
        request,
        authorization=authorization,
        persona=Persona.RESPONDER,
    )


@router.get(
    "/v1/command/incidents/active",
    response_model=ActiveIncidentList,
)
def get_command_active_incidents(
    request: Request,
    authorization: Authorization = None,
) -> ActiveIncidentList:
    return _active_incidents(
        request,
        authorization=authorization,
        persona=Persona.COMMAND,
    )


def _active_incidents(
    request: Request,
    *,
    authorization: str | None,
    persona: Persona,
) -> ActiveIncidentList:
    principal = authenticate_bearer(request, authorization)
    service = configured_persona_session_service(request)
    try:
        return service.active_incidents(
            principal,
            required_persona=persona,
        )
    except PersonaAuthorizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": exc.code},
        ) from exc
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


def _raise_authentication(
    code: str,
    exc: Exception | None = None,
) -> None:
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": code},
    )
    if exc is None:
        raise error
    raise error from exc


def _raise_persistence_unavailable(exc: Exception) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "persona_session_persistence_unavailable"},
    ) from exc
