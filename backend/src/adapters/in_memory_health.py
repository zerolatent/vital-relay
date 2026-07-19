"""Deterministic in-memory scalar-health repository.

This fast adapter supplies immutable metrics to ``HealthSnapshotService`` while
the PostgreSQL adapter provides equivalent durable behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from uuid import UUID

from vital_relay.application.health_ingestion import IdempotencyConflictError
from vital_relay.adapters.fingerprints import model_fingerprint
from vital_relay.domain.health import (
    SCHEMA_VERSION,
    HealthMetric,
    HealthMetricBatch,
    HealthMetricBatchResult,
    IngestionStatus,
)


@dataclass(frozen=True)
class _StoredMetric:
    metric: HealthMetric
    server_received_at: datetime


@dataclass(frozen=True)
class _StoredBatch:
    fingerprint: str
    result: HealthMetricBatchResult


class InMemoryHealthMetricRepository:
    """Thread-safe, atomic idempotency behavior without durable storage."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._metric_fingerprints: dict[UUID, str] = {}
        self._metrics: dict[UUID, _StoredMetric] = {}
        self._batches: dict[UUID, _StoredBatch] = {}

    def ingest_batch(
        self,
        batch: HealthMetricBatch,
        *,
        server_received_at: datetime,
    ) -> HealthMetricBatchResult:
        batch_fingerprint = model_fingerprint(batch)

        with self._lock:
            prior_batch = self._batches.get(batch.batch_id)
            if prior_batch is not None:
                if prior_batch.fingerprint != batch_fingerprint:
                    raise IdempotencyConflictError(
                        code="batch_id_conflict",
                        identifier=str(batch.batch_id),
                    )
                return prior_batch.result.model_copy(
                    update={"status": IngestionStatus.ALREADY_PROCESSED}
                )

            incoming = {
                metric.metric_id: (metric, model_fingerprint(metric))
                for metric in batch.metrics
            }

            # Validate the complete batch before writing so conflicts never cause a
            # partially accepted request.
            for metric_id, (_, fingerprint) in incoming.items():
                stored_fingerprint = self._metric_fingerprints.get(metric_id)
                if stored_fingerprint is not None and stored_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        code="metric_id_conflict",
                        identifier=str(metric_id),
                    )

            accepted_ids: list[UUID] = []
            duplicate_ids: list[UUID] = []

            for metric_id, (metric, fingerprint) in incoming.items():
                if metric_id in self._metrics:
                    duplicate_ids.append(metric_id)
                    continue
                self._metrics[metric_id] = _StoredMetric(
                    metric=metric,
                    server_received_at=server_received_at,
                )
                self._metric_fingerprints[metric_id] = fingerprint
                accepted_ids.append(metric_id)

            result = HealthMetricBatchResult(
                schema_version=SCHEMA_VERSION,
                batch_id=batch.batch_id,
                status=IngestionStatus.ACCEPTED,
                accepted_count=len(accepted_ids),
                duplicate_count=len(duplicate_ids),
                accepted_metric_ids=tuple(accepted_ids),
                duplicate_metric_ids=tuple(duplicate_ids),
                server_received_at=server_received_at,
            )
            self._batches[batch.batch_id] = _StoredBatch(
                fingerprint=batch_fingerprint,
                result=result,
            )
            return result

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthMetric]:
        with self._lock:
            latest: dict[str, HealthMetric] = {}
            for stored in self._metrics.values():
                metric = stored.metric
                if metric.user_id != user_id or metric.observed_at > as_of:
                    continue
                current = latest.get(metric.metric_type)
                if current is None or (metric.observed_at, str(metric.metric_id)) > (
                    current.observed_at,
                    str(current.metric_id),
                ):
                    latest[metric.metric_type] = metric
            return latest

    def get(self, metric_id: UUID) -> HealthMetric | None:
        with self._lock:
            stored = self._metrics.get(metric_id)
            return stored.metric if stored is not None else None

    def count(self) -> int:
        with self._lock:
            return len(self._metrics)
