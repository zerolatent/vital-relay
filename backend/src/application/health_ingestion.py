"""Health metric ingestion use case and persistence port."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from vital_relay.domain.health import HealthMetric, HealthMetricBatch, HealthMetricBatchResult


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""


class HealthMetricRepository(Protocol):
    def ingest_batch(
        self,
        batch: HealthMetricBatch,
        *,
        server_received_at: datetime,
    ) -> HealthMetricBatchResult:
        """Atomically insert or deduplicate a normalized batch."""

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthMetric]:
        """Return the latest visible scalar metric of each type as of a timestamp.

        ``HealthSnapshotService`` uses this seam to construct immutable context.
        """


class IdempotencyConflictError(Exception):
    """A stable idempotency identifier was reused for different content."""

    def __init__(self, *, code: str, identifier: str) -> None:
        self.code = code
        self.identifier = identifier
        super().__init__(f"{code}: {identifier}")


class HealthIngestionService:
    def __init__(self, repository: HealthMetricRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def ingest(self, batch: HealthMetricBatch) -> HealthMetricBatchResult:
        return self._repository.ingest_batch(
            batch,
            server_received_at=self._clock.now(),
        )
