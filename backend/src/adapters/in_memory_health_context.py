"""In-memory capability and immutable snapshot repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from uuid import UUID

from vital_relay.adapters.fingerprints import model_fingerprint
from vital_relay.application.health_ingestion import IdempotencyConflictError
from vital_relay.domain.health import SCHEMA_VERSION, IngestionStatus
from vital_relay.domain.health_context import (
    HealthCapability,
    HealthCapabilityBatch,
    HealthCapabilityBatchResult,
    HealthSnapshot,
    HealthSnapshotCreateRequest,
)


@dataclass(frozen=True)
class _StoredCapability:
    capability: HealthCapability
    server_received_at: datetime


@dataclass(frozen=True)
class _StoredCapabilityBatch:
    fingerprint: str
    result: HealthCapabilityBatchResult


class InMemoryHealthCapabilityRepository:
    """Thread-safe capability storage with immutable IDs and batch retries."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._capability_fingerprints: dict[UUID, str] = {}
        self._capabilities: dict[UUID, _StoredCapability] = {}
        self._batches: dict[UUID, _StoredCapabilityBatch] = {}

    def ingest_batch(
        self,
        batch: HealthCapabilityBatch,
        *,
        server_received_at: datetime,
    ) -> HealthCapabilityBatchResult:
        batch_fingerprint = model_fingerprint(batch)

        with self._lock:
            prior_batch = self._batches.get(batch.batch_id)
            if prior_batch is not None:
                if prior_batch.fingerprint != batch_fingerprint:
                    raise IdempotencyConflictError(
                        code="capability_batch_id_conflict",
                        identifier=str(batch.batch_id),
                    )
                return prior_batch.result.model_copy(
                    update={"status": IngestionStatus.ALREADY_PROCESSED}
                )

            incoming = {
                item.capability_id: (item, model_fingerprint(item))
                for item in batch.capabilities
            }
            for capability_id, (_, fingerprint) in incoming.items():
                stored_fingerprint = self._capability_fingerprints.get(capability_id)
                if stored_fingerprint is not None and stored_fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        code="capability_id_conflict",
                        identifier=str(capability_id),
                    )

            accepted_ids: list[UUID] = []
            duplicate_ids: list[UUID] = []
            for capability_id, (capability, fingerprint) in incoming.items():
                if capability_id in self._capabilities:
                    duplicate_ids.append(capability_id)
                    continue
                self._capabilities[capability_id] = _StoredCapability(
                    capability=capability,
                    server_received_at=server_received_at,
                )
                self._capability_fingerprints[capability_id] = fingerprint
                accepted_ids.append(capability_id)

            result = HealthCapabilityBatchResult(
                schema_version=SCHEMA_VERSION,
                batch_id=batch.batch_id,
                status=IngestionStatus.ACCEPTED,
                accepted_count=len(accepted_ids),
                duplicate_count=len(duplicate_ids),
                accepted_capability_ids=tuple(accepted_ids),
                duplicate_capability_ids=tuple(duplicate_ids),
                server_received_at=server_received_at,
            )
            self._batches[batch.batch_id] = _StoredCapabilityBatch(
                fingerprint=batch_fingerprint,
                result=result,
            )
            return result

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthCapability]:
        with self._lock:
            latest: dict[str, HealthCapability] = {}
            for stored in self._capabilities.values():
                capability = stored.capability
                if capability.user_id != user_id or capability.checked_at > as_of:
                    continue
                current = latest.get(capability.metric_type)
                if current is None or (
                    capability.checked_at,
                    str(capability.capability_id),
                ) > (current.checked_at, str(current.capability_id)):
                    latest[capability.metric_type] = capability
            return latest

    def count(self) -> int:
        with self._lock:
            return len(self._capabilities)


@dataclass(frozen=True)
class _StoredSnapshot:
    request_fingerprint: str
    snapshot: HealthSnapshot


class InMemoryHealthSnapshotRepository:
    """Thread-safe immutable snapshots keyed by client-provided snapshot ID."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[UUID, _StoredSnapshot] = {}

    def find_by_request(
        self,
        request: HealthSnapshotCreateRequest,
    ) -> HealthSnapshot | None:
        request_fingerprint = model_fingerprint(request)
        with self._lock:
            stored = self._snapshots.get(request.snapshot_id)
            if stored is None:
                return None
            if stored.request_fingerprint != request_fingerprint:
                raise IdempotencyConflictError(
                    code="snapshot_id_conflict",
                    identifier=str(request.snapshot_id),
                )
            return stored.snapshot

    def save(
        self,
        request: HealthSnapshotCreateRequest,
        snapshot: HealthSnapshot,
    ) -> tuple[HealthSnapshot, bool]:
        request_fingerprint = model_fingerprint(request)
        with self._lock:
            stored = self._snapshots.get(request.snapshot_id)
            if stored is not None:
                if stored.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(
                        code="snapshot_id_conflict",
                        identifier=str(request.snapshot_id),
                    )
                return stored.snapshot, False
            self._snapshots[request.snapshot_id] = _StoredSnapshot(
                request_fingerprint=request_fingerprint,
                snapshot=snapshot,
            )
            return snapshot, True

    def get(self, snapshot_id: UUID) -> HealthSnapshot | None:
        with self._lock:
            stored = self._snapshots.get(snapshot_id)
            return stored.snapshot if stored is not None else None

    def count(self) -> int:
        with self._lock:
            return len(self._snapshots)
