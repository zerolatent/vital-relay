"""Shared persona-session authentication and authorization helpers."""

from __future__ import annotations

from collections.abc import Collection
import re
from secrets import compare_digest

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from vital_relay.application.persona_session_service import (
    PersonaAuthenticationError,
    PersonaSessionService,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import DemoScopeUnavailableError


DEVICE_TOKEN_HEADER = "X-Vital-Relay-Device-Token"
RESPONDER_TOKEN_HEADER = "X-Vital-Relay-Responder-Token"
REFRESH_TOKEN_HEADER = "X-Vital-Relay-Refresh-Token"
ENROLLMENT_TOKEN_HEADER = "X-Vital-Relay-Enrollment-Token"
_OPAQUE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,256}$")


def configured_persona_session_service(request: Request) -> PersonaSessionService:
    service = getattr(request.app.state, "persona_session_service", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "persona_session_persistence_unavailable"},
        )
    return service


def authenticate_bearer(
    request: Request,
    authorization: str | None,
) -> PersonaPrincipal:
    if authorization is None:
        _raise_invalid_session()
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or " " in token:
        _raise_invalid_session()
    return authenticate_access_token(request, token)


def authenticate_access_token(
    request: Request,
    token: str | None,
) -> PersonaPrincipal:
    if not valid_opaque_token(token):
        _raise_invalid_session()
    service = configured_persona_session_service(request)
    try:
        return service.authenticate_access(access_token=token)
    except PersonaAuthenticationError:
        _raise_invalid_session()
    except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
        _raise_persistence_unavailable(exc)


def authenticate_device_access(
    request: Request,
    token: str | None,
    *,
    allowed_personas: Collection[Persona],
) -> PersonaPrincipal | None:
    """Authenticate a session token, or an explicit test-only legacy token."""

    if _matches_legacy_device_token(request, token):
        return None
    principal = authenticate_access_token(request, token)
    require_persona(principal, allowed_personas)
    return principal


def authenticate_responder_access(
    request: Request,
    token: str | None,
    *,
    responder_id: object,
) -> PersonaPrincipal | None:
    """Authenticate a responder session while preserving explicit legacy tests."""

    if getattr(request.app.state, "legacy_persona_auth", False):
        # The responder repository still validates this credential against the
        # responder row. Returning None selects that explicit compatibility path.
        if token is None or not token:
            _raise_invalid_responder()
        return None
    principal = authenticate_access_token(request, token)
    require_persona(principal, {Persona.RESPONDER})
    if principal.responder_id != responder_id:
        _raise_persona_not_authorized()
    return principal


def require_persona(
    principal: PersonaPrincipal,
    allowed_personas: Collection[Persona],
) -> None:
    if principal.persona not in allowed_personas:
        _raise_persona_not_authorized()


def valid_opaque_token(value: str | None) -> bool:
    return value is not None and _OPAQUE_TOKEN_PATTERN.fullmatch(value) is not None


def _matches_legacy_device_token(
    request: Request,
    token: str | None,
) -> bool:
    if not getattr(request.app.state, "legacy_persona_auth", False):
        return False
    expected = getattr(request.app.state, "device_token", None)
    return (
        token is not None
        and isinstance(expected, str)
        and bool(expected)
        and compare_digest(token, expected)
    )


def _raise_invalid_session() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_session_token"},
    )


def _raise_invalid_responder() -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_responder_token"},
    )


def _raise_persona_not_authorized() -> None:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "persona_not_authorized"},
    )


def _raise_persistence_unavailable(exc: Exception) -> None:
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "persona_session_persistence_unavailable"},
    ) from exc
