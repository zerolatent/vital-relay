"""PostgreSQL implementations of the frozen health repository ports."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.adapters.fingerprints import model_fingerprint
from vital_relay.application.health_context import (
    HealthSnapshotRepositories,
)
from vital_relay.application.health_ingestion import IdempotencyConflictError
from vital_relay.domain.health import (
    HealthMetric,
    HealthMetricBatch,
    HealthMetricBatchResult,
    IngestionStatus,
)
from vital_relay.domain.health_context import (
    HealthCapability,
    HealthCapabilityBatch,
    HealthCapabilityBatchResult,
    HealthSnapshot,
    HealthSnapshotCreateRequest,
    HealthSnapshotItem,
)
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    HealthCapabilityBatchRow,
    HealthCapabilityRow,
    HealthMetricBatchRow,
    HealthMetricRow,
    HealthSnapshotItemRow,
    HealthSnapshotRequestRow,
    HealthSnapshotRow,
)

T = TypeVar("T")


class PersistenceIntegrityError(RuntimeError):
    """Stored rows violate a domain invariant that migrations should enforce."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _advisory_key(scope_id: UUID, namespace: str, identifier: UUID) -> int:
    digest = sha256(f"{scope_id}:{namespace}:{identifier}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _transaction_lock(
    session: Session,
    *,
    scope_id: UUID,
    namespace: str,
    identifier: UUID,
) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _advisory_key(scope_id, namespace, identifier)},
    )


class _ScopedSessionRepository:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        *,
        session: Session | None = None,
    ) -> None:
        self._sessions = sessions
        self.scope_id = scope_id
        self._session = session

    def _read(self, operation: Callable[[Session], T]) -> T:
        if self._session is not None:
            return operation(self._session)
        with self._sessions() as session:
            return operation(session)

    def _write(self, operation: Callable[[Session], T]) -> T:
        if self._session is not None:
            return operation(self._session)
        with self._sessions.begin() as session:
            return operation(session)


class PostgresHealthMetricRepository(_ScopedSessionRepository):
    """Atomic metric ingestion and deterministic latest-as-of selection."""

    def ingest_batch(
        self,
        batch: HealthMetricBatch,
        *,
        server_received_at: datetime,
    ) -> HealthMetricBatchResult:
        received_at = _utc(server_received_at)
        request_fingerprint = model_fingerprint(batch)

        def ingest(session: Session) -> HealthMetricBatchResult:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="metric_batch",
                identifier=batch.batch_id,
            )
            prior = session.get(
                HealthMetricBatchRow,
                (self.scope_id, batch.batch_id),
            )
            if prior is not None:
                if prior.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(
                        code="batch_id_conflict",
                        identifier=str(batch.batch_id),
                    )
                return _metric_result(prior, IngestionStatus.ALREADY_PROCESSED)

            for metric_id in sorted(
                (metric.metric_id for metric in batch.metrics),
                key=str,
            ):
                _transaction_lock(
                    session,
                    scope_id=self.scope_id,
                    namespace="metric",
                    identifier=metric_id,
                )

            incoming_ids = [metric.metric_id for metric in batch.metrics]
            stored_rows = session.scalars(
                select(HealthMetricRow).where(
                    HealthMetricRow.scope_id == self.scope_id,
                    HealthMetricRow.metric_id.in_(incoming_ids),
                )
            ).all()
            stored = {row.metric_id: row for row in stored_rows}
            incoming = {
                metric.metric_id: (metric, model_fingerprint(metric))
                for metric in batch.metrics
            }
            for metric_id, (_, fingerprint) in incoming.items():
                existing = stored.get(metric_id)
                if (
                    existing is not None
                    and existing.content_fingerprint != fingerprint
                ):
                    raise IdempotencyConflictError(
                        code="metric_id_conflict",
                        identifier=str(metric_id),
                    )

            accepted_ids = [
                metric.metric_id
                for metric in batch.metrics
                if metric.metric_id not in stored
            ]
            duplicate_ids = [
                metric.metric_id
                for metric in batch.metrics
                if metric.metric_id in stored
            ]
            receipt = HealthMetricBatchRow(
                scope_id=self.scope_id,
                batch_id=batch.batch_id,
                request_fingerprint=request_fingerprint,
                schema_version=batch.schema_version,
                user_id=batch.user_id,
                device_id=batch.device_id,
                sent_at=batch.sent_at,
                server_received_at=received_at,
                accepted_metric_ids=accepted_ids,
                duplicate_metric_ids=duplicate_ids,
            )
            session.add(receipt)
            session.flush()
            for metric_id in accepted_ids:
                metric, fingerprint = incoming[metric_id]
                session.add(
                    _metric_row(
                        scope_id=self.scope_id,
                        batch_id=batch.batch_id,
                        metric=metric,
                        fingerprint=fingerprint,
                        server_received_at=received_at,
                    )
                )
            session.flush()
            return _metric_result(receipt, IngestionStatus.ACCEPTED)

        return self._write(ingest)

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthMetric]:
        normalized_as_of = _utc(as_of)

        def load(session: Session) -> dict[str, HealthMetric]:
            require_active_scope(session, self.scope_id)
            statement = (
                select(HealthMetricRow)
                .where(
                    HealthMetricRow.scope_id == self.scope_id,
                    HealthMetricRow.user_id == user_id,
                    HealthMetricRow.observed_at <= normalized_as_of,
                )
                .distinct(HealthMetricRow.metric_type)
                .order_by(
                    HealthMetricRow.metric_type,
                    HealthMetricRow.observed_at.desc(),
                    HealthMetricRow.metric_id.desc(),
                )
            )
            rows = session.scalars(statement).all()
            return {row.metric_type: _metric_from_row(row) for row in rows}

        return self._read(load)


class PostgresHealthCapabilityRepository(_ScopedSessionRepository):
    """Atomic capability ingestion and deterministic latest-as-of selection."""

    def ingest_batch(
        self,
        batch: HealthCapabilityBatch,
        *,
        server_received_at: datetime,
    ) -> HealthCapabilityBatchResult:
        received_at = _utc(server_received_at)
        request_fingerprint = model_fingerprint(batch)

        def ingest(session: Session) -> HealthCapabilityBatchResult:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="capability_batch",
                identifier=batch.batch_id,
            )
            prior = session.get(
                HealthCapabilityBatchRow,
                (self.scope_id, batch.batch_id),
            )
            if prior is not None:
                if prior.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflictError(
                        code="capability_batch_id_conflict",
                        identifier=str(batch.batch_id),
                    )
                return _capability_result(
                    prior,
                    IngestionStatus.ALREADY_PROCESSED,
                )

            for capability_id in sorted(
                (item.capability_id for item in batch.capabilities),
                key=str,
            ):
                _transaction_lock(
                    session,
                    scope_id=self.scope_id,
                    namespace="capability",
                    identifier=capability_id,
                )

            incoming_ids = [item.capability_id for item in batch.capabilities]
            stored_rows = session.scalars(
                select(HealthCapabilityRow).where(
                    HealthCapabilityRow.scope_id == self.scope_id,
                    HealthCapabilityRow.capability_id.in_(incoming_ids),
                )
            ).all()
            stored = {row.capability_id: row for row in stored_rows}
            incoming = {
                item.capability_id: (item, model_fingerprint(item))
                for item in batch.capabilities
            }
            for capability_id, (_, fingerprint) in incoming.items():
                existing = stored.get(capability_id)
                if (
                    existing is not None
                    and existing.content_fingerprint != fingerprint
                ):
                    raise IdempotencyConflictError(
                        code="capability_id_conflict",
                        identifier=str(capability_id),
                    )

            accepted_ids = [
                item.capability_id
                for item in batch.capabilities
                if item.capability_id not in stored
            ]
            duplicate_ids = [
                item.capability_id
                for item in batch.capabilities
                if item.capability_id in stored
            ]
            receipt = HealthCapabilityBatchRow(
                scope_id=self.scope_id,
                batch_id=batch.batch_id,
                request_fingerprint=request_fingerprint,
                schema_version=batch.schema_version,
                user_id=batch.user_id,
                device_id=batch.device_id,
                sent_at=batch.sent_at,
                server_received_at=received_at,
                accepted_capability_ids=accepted_ids,
                duplicate_capability_ids=duplicate_ids,
            )
            session.add(receipt)
            session.flush()
            for capability_id in accepted_ids:
                capability, fingerprint = incoming[capability_id]
                session.add(
                    _capability_row(
                        scope_id=self.scope_id,
                        batch_id=batch.batch_id,
                        capability=capability,
                        fingerprint=fingerprint,
                        server_received_at=received_at,
                    )
                )
            session.flush()
            return _capability_result(receipt, IngestionStatus.ACCEPTED)

        return self._write(ingest)

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthCapability]:
        normalized_as_of = _utc(as_of)

        def load(session: Session) -> dict[str, HealthCapability]:
            require_active_scope(session, self.scope_id)
            statement = (
                select(HealthCapabilityRow)
                .where(
                    HealthCapabilityRow.scope_id == self.scope_id,
                    HealthCapabilityRow.user_id == user_id,
                    HealthCapabilityRow.checked_at <= normalized_as_of,
                )
                .distinct(HealthCapabilityRow.metric_type)
                .order_by(
                    HealthCapabilityRow.metric_type,
                    HealthCapabilityRow.checked_at.desc(),
                    HealthCapabilityRow.capability_id.desc(),
                )
            )
            rows = session.scalars(statement).all()
            return {
                row.metric_type: _capability_from_row(row)
                for row in rows
            }

        return self._read(load)


class PostgresHealthSnapshotRepository(_ScopedSessionRepository):
    """Immutable snapshot requests, headers, and copied audit items."""

    def find_by_request(
        self,
        request: HealthSnapshotCreateRequest,
    ) -> HealthSnapshot | None:
        return self._read(lambda session: self._find(session, request))

    def save(
        self,
        request: HealthSnapshotCreateRequest,
        snapshot: HealthSnapshot,
    ) -> tuple[HealthSnapshot, bool]:
        def persist(session: Session) -> tuple[HealthSnapshot, bool]:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="snapshot",
                identifier=request.snapshot_id,
            )
            existing = self._find(session, request)
            if existing is not None:
                return existing, False
            _validate_snapshot_request(request, snapshot)

            session.add(
                HealthSnapshotRequestRow(
                    scope_id=self.scope_id,
                    snapshot_id=request.snapshot_id,
                    request_fingerprint=model_fingerprint(request),
                    schema_version=request.schema_version,
                    user_id=request.user_id,
                    capture_reason=request.capture_reason.value,
                )
            )
            session.flush()
            session.add(
                HealthSnapshotRow(
                    scope_id=self.scope_id,
                    snapshot_id=snapshot.snapshot_id,
                    schema_version=snapshot.schema_version,
                    user_id=snapshot.user_id,
                    capture_reason=snapshot.capture_reason.value,
                    captured_at=snapshot.captured_at,
                    used_for_escalation=False,
                )
            )
            session.flush()
            for item in snapshot.items:
                session.add(
                    HealthSnapshotItemRow(
                        scope_id=self.scope_id,
                        snapshot_id=snapshot.snapshot_id,
                        metric_type=item.metric_type,
                        metric_payload=(
                            item.metric.model_dump(mode="json")
                            if item.metric is not None
                            else None
                        ),
                        capability_payload=(
                            item.capability.model_dump(mode="json")
                            if item.capability is not None
                            else None
                        ),
                        availability=item.availability.value,
                        freshness=item.freshness.value,
                        age_seconds=item.age_seconds,
                        live_window_seconds=item.live_window_seconds,
                        recent_window_seconds=item.recent_window_seconds,
                        used_for_escalation=False,
                    )
                )
            session.flush()
            return snapshot, True

        return self._write(persist)

    def get(self, snapshot_id: UUID) -> HealthSnapshot | None:
        return self._read(lambda session: self._load(session, snapshot_id))

    def _find(
        self,
        session: Session,
        request: HealthSnapshotCreateRequest,
    ) -> HealthSnapshot | None:
        stored_request = session.get(
            HealthSnapshotRequestRow,
            (self.scope_id, request.snapshot_id),
        )
        if stored_request is None:
            return None
        if stored_request.request_fingerprint != model_fingerprint(request):
            raise IdempotencyConflictError(
                code="snapshot_id_conflict",
                identifier=str(request.snapshot_id),
            )
        snapshot = self._load(session, request.snapshot_id)
        if snapshot is None:
            raise PersistenceIntegrityError(
                f"snapshot request has no stored snapshot: {request.snapshot_id}"
            )
        return snapshot

    def _load(self, session: Session, snapshot_id: UUID) -> HealthSnapshot | None:
        header = session.get(
            HealthSnapshotRow,
            (self.scope_id, snapshot_id),
        )
        if header is None:
            return None
        item_rows = session.scalars(
            select(HealthSnapshotItemRow)
            .where(
                HealthSnapshotItemRow.scope_id == self.scope_id,
                HealthSnapshotItemRow.snapshot_id == snapshot_id,
            )
            .order_by(HealthSnapshotItemRow.metric_type)
        ).all()
        items = tuple(_snapshot_item_from_row(row) for row in item_rows)
        return HealthSnapshot(
            schema_version=header.schema_version,
            snapshot_id=header.snapshot_id,
            user_id=header.user_id,
            capture_reason=header.capture_reason,
            captured_at=header.captured_at,
            items=items,
            used_for_escalation=header.used_for_escalation,
        )


class PostgresHealthSnapshotUnitOfWork:
    """One serialized, repeatable-read transaction per snapshot capture."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._engine = engine
        self._sessions = sessions
        self._scope_id = scope_id

    @contextmanager
    def begin(
        self,
        *,
        snapshot_id: UUID,
    ) -> Iterator[HealthSnapshotRepositories]:
        key = _advisory_key(self._scope_id, "snapshot_capture", snapshot_id)
        connection = self._engine.connect()
        locked = False
        try:
            connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": key},
            )
            connection.commit()
            locked = True
            connection = connection.execution_options(
                isolation_level="REPEATABLE READ"
            )
            with Session(bind=connection, expire_on_commit=False) as session:
                with session.begin():
                    require_active_scope(session, self._scope_id, lock=True)
                    yield HealthSnapshotRepositories(
                        metric=PostgresHealthMetricRepository(
                            self._sessions,
                            self._scope_id,
                            session=session,
                        ),
                        capability=PostgresHealthCapabilityRepository(
                            self._sessions,
                            self._scope_id,
                            session=session,
                        ),
                        snapshot=PostgresHealthSnapshotRepository(
                            self._sessions,
                            self._scope_id,
                            session=session,
                        ),
                    )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "40001":
                # A scope reset can commit while REPEATABLE READ is waiting for
                # its share lock. Recheck at READ COMMITTED so callers receive
                # the stable scope lifecycle error instead of a driver failure.
                with self._sessions() as verification_session:
                    require_active_scope(verification_session, self._scope_id)
            raise
        finally:
            if connection.in_transaction():
                connection.rollback()
            if locked:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": key},
                )
                connection.commit()
            connection.close()


def _metric_result(
    row: HealthMetricBatchRow,
    status: IngestionStatus,
) -> HealthMetricBatchResult:
    return HealthMetricBatchResult(
        schema_version=row.schema_version,
        batch_id=row.batch_id,
        status=status,
        accepted_count=len(row.accepted_metric_ids),
        duplicate_count=len(row.duplicate_metric_ids),
        accepted_metric_ids=tuple(row.accepted_metric_ids),
        duplicate_metric_ids=tuple(row.duplicate_metric_ids),
        server_received_at=row.server_received_at,
    )


def _metric_row(
    *,
    scope_id: UUID,
    batch_id: UUID,
    metric: HealthMetric,
    fingerprint: str,
    server_received_at: datetime,
) -> HealthMetricRow:
    return HealthMetricRow(
        scope_id=scope_id,
        metric_id=metric.metric_id,
        first_batch_id=batch_id,
        content_fingerprint=fingerprint,
        schema_version=metric.schema_version,
        user_id=metric.user_id,
        metric_type=metric.metric_type,
        acquisition_class=metric.acquisition_class.value,
        value=metric.value,
        unit=metric.unit,
        observed_at=metric.observed_at,
        source=metric.source,
        source_kind=metric.source_kind.value,
        source_name=metric.source_name,
        source_bundle_id=metric.source_bundle_id,
        device_model=metric.device_model,
        simulated=metric.simulated,
        quality=metric.quality.value if metric.quality is not None else None,
        used_for_escalation=False,
        server_received_at=server_received_at,
    )


def _metric_from_row(row: HealthMetricRow) -> HealthMetric:
    return HealthMetric(
        schema_version=row.schema_version,
        metric_id=row.metric_id,
        user_id=row.user_id,
        metric_type=row.metric_type,
        acquisition_class=row.acquisition_class,
        value=row.value,
        unit=row.unit,
        observed_at=row.observed_at,
        source=row.source,
        source_kind=row.source_kind,
        source_name=row.source_name,
        source_bundle_id=row.source_bundle_id,
        device_model=row.device_model,
        simulated=row.simulated,
        quality=row.quality,
        used_for_escalation=row.used_for_escalation,
    )


def _capability_result(
    row: HealthCapabilityBatchRow,
    status: IngestionStatus,
) -> HealthCapabilityBatchResult:
    return HealthCapabilityBatchResult(
        schema_version=row.schema_version,
        batch_id=row.batch_id,
        status=status,
        accepted_count=len(row.accepted_capability_ids),
        duplicate_count=len(row.duplicate_capability_ids),
        accepted_capability_ids=tuple(row.accepted_capability_ids),
        duplicate_capability_ids=tuple(row.duplicate_capability_ids),
        server_received_at=row.server_received_at,
    )


def _capability_row(
    *,
    scope_id: UUID,
    batch_id: UUID,
    capability: HealthCapability,
    fingerprint: str,
    server_received_at: datetime,
) -> HealthCapabilityRow:
    return HealthCapabilityRow(
        scope_id=scope_id,
        capability_id=capability.capability_id,
        first_batch_id=batch_id,
        content_fingerprint=fingerprint,
        schema_version=capability.schema_version,
        user_id=capability.user_id,
        metric_type=capability.metric_type,
        acquisition_class=capability.acquisition_class.value,
        status=capability.status.value,
        checked_at=capability.checked_at,
        source=capability.source,
        source_kind=capability.source_kind.value,
        source_name=capability.source_name,
        source_bundle_id=capability.source_bundle_id,
        device_model=capability.device_model,
        simulated=capability.simulated,
        error_code=capability.error_code,
        used_for_escalation=False,
        server_received_at=server_received_at,
    )


def _capability_from_row(row: HealthCapabilityRow) -> HealthCapability:
    return HealthCapability(
        schema_version=row.schema_version,
        capability_id=row.capability_id,
        user_id=row.user_id,
        metric_type=row.metric_type,
        acquisition_class=row.acquisition_class,
        status=row.status,
        checked_at=row.checked_at,
        source=row.source,
        source_kind=row.source_kind,
        source_name=row.source_name,
        source_bundle_id=row.source_bundle_id,
        device_model=row.device_model,
        simulated=row.simulated,
        error_code=row.error_code,
        used_for_escalation=row.used_for_escalation,
    )


def _snapshot_item_from_row(row: HealthSnapshotItemRow) -> HealthSnapshotItem:
    return HealthSnapshotItem(
        metric_type=row.metric_type,
        metric=row.metric_payload,
        capability=row.capability_payload,
        availability=row.availability,
        freshness=row.freshness,
        age_seconds=row.age_seconds,
        live_window_seconds=row.live_window_seconds,
        recent_window_seconds=row.recent_window_seconds,
        used_for_escalation=row.used_for_escalation,
    )


def _validate_snapshot_request(
    request: HealthSnapshotCreateRequest,
    snapshot: HealthSnapshot,
) -> None:
    if (
        snapshot.schema_version != request.schema_version
        or snapshot.snapshot_id != request.snapshot_id
        or snapshot.user_id != request.user_id
        or snapshot.capture_reason is not request.capture_reason
    ):
        raise ValueError("snapshot header must match its creation request")
