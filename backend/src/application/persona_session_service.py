"""Application boundary for durable persona sessions and incident discovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from vital_relay.application.health_ingestion import Clock
from vital_relay.domain.persona_sessions import (
    ActiveIncidentList,
    Persona,
    PersonaPrincipal,
    PersonaSessionCreateRequest,
    PersonaSessionReceipt,
    PersonaSessionRevocationReceipt,
    PersonaSessionRotateRequest,
    PersonaSessionRotationReceipt,
    PersonaSessionView,
)


ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(hours=24)


class PersonaSessionRepository(Protocol):
    """Durable, scope-bound persona session operations."""

    def create(
        self,
        request: PersonaSessionCreateRequest,
        *,
        enrollment_token: str,
        issued_at: datetime,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
    ) -> PersonaSessionReceipt:
        """Issue a new session after authenticating an enrollment token."""

    def current(
        self,
        *,
        access_token: str,
        as_of: datetime,
    ) -> PersonaSessionView:
        """Load an active session without re-disclosing its credentials."""

    def authenticate_access(
        self,
        *,
        access_token: str,
        as_of: datetime,
    ) -> PersonaPrincipal:
        """Authenticate one access token and return its exact principal."""

    def rotate(
        self,
        session_id: UUID,
        request: PersonaSessionRotateRequest,
        *,
        refresh_token: str,
        rotated_at: datetime,
        access_ttl: timedelta,
    ) -> PersonaSessionRotationReceipt:
        """Replace only the access credential for one active session."""

    def revoke(
        self,
        session_id: UUID,
        *,
        refresh_token: str,
        revoked_at: datetime,
    ) -> PersonaSessionRevocationReceipt:
        """Idempotently revoke one refresh-authenticated session."""

    def active_incidents(
        self,
        principal: PersonaPrincipal,
        *,
        as_of: datetime,
    ) -> ActiveIncidentList:
        """Return only active incident locators visible to the principal."""


class PersonaAuthenticationError(Exception):
    """An enrollment, access, or refresh credential is invalid."""

    def __init__(self, *, code: str) -> None:
        self.code = code
        super().__init__(code)


class PersonaAuthorizationError(Exception):
    """A valid session is not authorized for the requested persona boundary."""

    code = "persona_not_authorized"


class PersonaSessionService:
    """Apply authoritative time and fixed lifetimes to session operations."""

    def __init__(
        self,
        repository: PersonaSessionRepository,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def create(
        self,
        request: PersonaSessionCreateRequest,
        *,
        enrollment_token: str,
    ) -> PersonaSessionReceipt:
        return self._repository.create(
            request,
            enrollment_token=enrollment_token,
            issued_at=_utc(self._clock.now()),
            access_ttl=ACCESS_TOKEN_TTL,
            refresh_ttl=REFRESH_TOKEN_TTL,
        )

    def current(self, *, access_token: str) -> PersonaSessionView:
        return self._repository.current(
            access_token=access_token,
            as_of=_utc(self._clock.now()),
        )

    def authenticate_access(self, *, access_token: str) -> PersonaPrincipal:
        return self._repository.authenticate_access(
            access_token=access_token,
            as_of=_utc(self._clock.now()),
        )

    def rotate(
        self,
        session_id: UUID,
        request: PersonaSessionRotateRequest,
        *,
        refresh_token: str,
    ) -> PersonaSessionRotationReceipt:
        return self._repository.rotate(
            session_id,
            request,
            refresh_token=refresh_token,
            rotated_at=_utc(self._clock.now()),
            access_ttl=ACCESS_TOKEN_TTL,
        )

    def revoke(
        self,
        session_id: UUID,
        *,
        refresh_token: str,
    ) -> PersonaSessionRevocationReceipt:
        return self._repository.revoke(
            session_id,
            refresh_token=refresh_token,
            revoked_at=_utc(self._clock.now()),
        )

    def active_incidents(
        self,
        principal: PersonaPrincipal,
        *,
        required_persona: Persona,
    ) -> ActiveIncidentList:
        if principal.persona is not required_persona:
            raise PersonaAuthorizationError
        return self._repository.active_incidents(
            principal,
            as_of=_utc(self._clock.now()),
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("persona-session clock must return an aware timestamp")
    return value.astimezone(UTC)
