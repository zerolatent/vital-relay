"""Application boundary for durable wearable-event incident coordination."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from vital_relay.application.health_ingestion import Clock
from vital_relay.domain.incidents import (
    CheckInRequest,
    CheckInResult,
    IncidentResolutionReceipt,
    IncidentResolutionRequest,
    IncidentState,
    IncidentTimelineEntry,
    IncidentView,
    WearableEventRequest,
    WearableEventResult,
    WearableEventType,
)


DEFAULT_VERIFICATION_TIMEOUT_SECONDS = 15
MAX_DEADLINE_BATCH_SIZE = 1_000


class IncidentRepository(Protocol):
    """High-level atomic persistence operations for the incident state machine.

    Implementations own transaction boundaries. In particular, ``ingest`` must
    commit the event, incident, initial transition, timeline, deadline, health
    snapshot link, and snapshot hold as one logical idempotent effect.
    """

    def ingest(
        self,
        event: WearableEventRequest,
        *,
        server_received_at: datetime,
        verification_deadline_at: datetime | None,
    ) -> WearableEventResult:
        """Persist or deduplicate one wearable safety event atomically."""

    def check_in(
        self,
        incident_id: UUID,
        request: CheckInRequest,
        *,
        server_received_at: datetime,
    ) -> CheckInResult:
        """Record one response and perform its state transition atomically."""

    def resolve(
        self,
        incident_id: UUID,
        request: IncidentResolutionRequest,
        *,
        server_received_at: datetime,
    ) -> IncidentResolutionReceipt:
        """Resolve an active response and revoke its assignment atomically."""

    def get(self, incident_id: UUID) -> IncidentView | None:
        """Load a scope-bound incident, or return ``None`` when absent."""

    def timeline(
        self,
        incident_id: UUID,
    ) -> tuple[IncidentTimelineEntry, ...] | None:
        """Load the ordered timeline, or ``None`` when the incident is absent."""

    def process_due(
        self,
        *,
        as_of: datetime,
        limit: int = 100,
    ) -> int:
        """Atomically advance up to ``limit`` due verification deadlines."""


class IncidentConflictError(Exception):
    """A stable ID conflicted or a requested transition is not allowed."""

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


class IncidentNotFoundError(Exception):
    """The requested incident is absent from the repository's bound scope."""

    code = "incident_not_found"

    def __init__(self, incident_id: UUID) -> None:
        self.incident_id = incident_id
        super().__init__(f"{self.code}: {incident_id}")

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "identifier": str(self.incident_id)}


class IncidentService:
    """Apply authoritative server time before delegating durable operations."""

    def __init__(
        self,
        repository: IncidentRepository,
        clock: Clock,
        *,
        verification_timeout_seconds: int = DEFAULT_VERIFICATION_TIMEOUT_SECONDS,
    ) -> None:
        if verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be positive")
        self._repository = repository
        self._clock = clock
        self._verification_window = timedelta(
            seconds=verification_timeout_seconds
        )

    def ingest(self, event: WearableEventRequest) -> WearableEventResult:
        received_at = _utc(self._clock.now())
        verification_deadline_at = (
            received_at + self._verification_window
            if event.event_type is WearableEventType.FALL_DETECTED
            else None
        )
        return self._repository.ingest(
            event,
            server_received_at=received_at,
            verification_deadline_at=verification_deadline_at,
        )

    def check_in(
        self,
        incident_id: UUID,
        request: CheckInRequest,
    ) -> CheckInResult:
        return self._repository.check_in(
            incident_id,
            request,
            server_received_at=_utc(self._clock.now()),
        )

    def resolve(
        self,
        incident_id: UUID,
        request: IncidentResolutionRequest,
    ) -> IncidentResolutionReceipt:
        return self._repository.resolve(
            incident_id,
            request,
            server_received_at=_utc(self._clock.now()),
        )

    def get(self, incident_id: UUID) -> IncidentView:
        incident = self._repository.get(incident_id)
        if incident is None:
            raise IncidentNotFoundError(incident_id)
        return incident

    def timeline(self, incident_id: UUID) -> tuple[IncidentTimelineEntry, ...]:
        entries = self._repository.timeline(incident_id)
        if entries is None:
            raise IncidentNotFoundError(incident_id)
        return entries

    def process_due(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= MAX_DEADLINE_BATCH_SIZE:
            raise ValueError(
                f"limit must be between 1 and {MAX_DEADLINE_BATCH_SIZE}"
            )
        return self._repository.process_due(
            as_of=_utc(self._clock.now()),
            limit=limit,
        )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("incident clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)
