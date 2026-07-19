"""Application boundary for PostGIS-backed responder dispatch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from vital_relay.application.health_ingestion import Clock
from vital_relay.domain.dispatch import (
    AcceptedDispatchView,
    DispatchCoordinationView,
    ResponderDecisionRequest,
    ResponderDecisionResult,
    ResponderIncidentView,
)
from vital_relay.domain.incidents import IncidentState


DEFAULT_RESPONDER_RADIUS_M = 1_000
DEFAULT_RESPONDER_STALE_SECONDS = 120
MAX_RESPONDER_RADIUS_M = 2_000


class DispatchRepository(Protocol):
    """Atomic persistence operations for discovery and responder acceptance."""

    def coordinate(
        self,
        incident_id: UUID,
        *,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
        expected_state_version: int | None = None,
    ) -> DispatchCoordinationView:
        """Rank eligible resources and create at most one pending invitation."""

    def get_coordination(
        self,
        incident_id: UUID,
        *,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> DispatchCoordinationView:
        """Load the redacted command view without creating an invitation."""

    def respond(
        self,
        incident_id: UUID,
        responder_id: UUID,
        decision: ResponderDecisionRequest,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
        server_received_at: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> ResponderDecisionResult:
        """Apply one authenticated decline or acceptance atomically."""

    def get_responder_incident(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> ResponderIncidentView | None:
        """Return current state and only the authenticated responder's invitation."""

    def get_accepted_dispatch(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> AcceptedDispatchView | None:
        """Return exact dispatch data only to the assigned responder."""


class DispatchConflictError(Exception):
    """The incident or invitation cannot accept the requested operation."""

    def __init__(
        self,
        *,
        code: str,
        identifier: str | None = None,
        current_state: IncidentState | None = None,
    ) -> None:
        self.code = code
        self.identifier = identifier
        self.current_state = current_state
        super().__init__(code)

    def as_detail(self) -> dict[str, str]:
        detail = {"code": self.code}
        if self.identifier is not None:
            detail["identifier"] = self.identifier
        if self.current_state is not None:
            detail["current_state"] = self.current_state.value
        return detail


class DispatchNotFoundError(Exception):
    """A scope-bound incident, invitation, or accepted dispatch is absent."""

    def __init__(self, *, code: str, identifier: str) -> None:
        self.code = code
        self.identifier = identifier
        super().__init__(code)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "identifier": self.identifier}


class ResponderAuthenticationError(Exception):
    """A responder token is missing, invalid, or bound to another responder."""

    code = "invalid_responder_token"


class DispatchService:
    """Apply authoritative time and fixed discovery bounds to dispatch work."""

    def __init__(
        self,
        repository: DispatchRepository,
        clock: Clock,
        *,
        responder_radius_m: int = DEFAULT_RESPONDER_RADIUS_M,
        responder_stale_seconds: int = DEFAULT_RESPONDER_STALE_SECONDS,
    ) -> None:
        if not 1 <= responder_radius_m <= MAX_RESPONDER_RADIUS_M:
            raise ValueError(
                f"responder_radius_m must be between 1 and {MAX_RESPONDER_RADIUS_M}"
            )
        if responder_stale_seconds <= 0:
            raise ValueError("responder_stale_seconds must be positive")
        self._repository = repository
        self._clock = clock
        self._radius_m = responder_radius_m
        self._stale_window = timedelta(seconds=responder_stale_seconds)

    def coordinate(
        self,
        incident_id: UUID,
        *,
        expected_state_version: int | None = None,
    ) -> DispatchCoordinationView:
        now = _utc(self._clock.now())
        return self._repository.coordinate(
            incident_id,
            as_of=now,
            radius_m=self._radius_m,
            fresh_after=now - self._stale_window,
            expected_state_version=expected_state_version,
        )

    def get_coordination(self, incident_id: UUID) -> DispatchCoordinationView:
        now = _utc(self._clock.now())
        return self._repository.get_coordination(
            incident_id,
            as_of=now,
            radius_m=self._radius_m,
            fresh_after=now - self._stale_window,
        )

    def respond(
        self,
        incident_id: UUID,
        responder_id: UUID,
        decision: ResponderDecisionRequest,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> ResponderDecisionResult:
        now = _utc(self._clock.now())
        return self._repository.respond(
            incident_id,
            responder_id,
            decision,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
            server_received_at=now,
            radius_m=self._radius_m,
            fresh_after=now - self._stale_window,
        )

    def get_responder_incident(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> ResponderIncidentView:
        view = self._repository.get_responder_incident(
            incident_id,
            responder_id,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
        )
        if view is None:
            raise DispatchNotFoundError(
                code="responder_invitation_not_found",
                identifier=str(incident_id),
            )
        return view

    def get_accepted_dispatch(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None = None,
        authenticated_responder_id: UUID | None = None,
    ) -> AcceptedDispatchView:
        dispatch = self._repository.get_accepted_dispatch(
            incident_id,
            responder_id,
            responder_token=responder_token,
            authenticated_responder_id=authenticated_responder_id,
        )
        if dispatch is None:
            raise DispatchNotFoundError(
                code="accepted_dispatch_not_found",
                identifier=str(incident_id),
            )
        return dispatch


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("dispatch clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)
