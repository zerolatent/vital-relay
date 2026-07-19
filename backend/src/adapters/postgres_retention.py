"""Scope-bound PostgreSQL retention and snapshot-hold persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.application.health_retention import HealthRetentionCounts
from vital_relay.persistence.database import DemoScopeUnavailableError
from vital_relay.persistence.models import (
    DemoScopeRow,
    HealthCapabilityBatchRow,
    HealthCapabilityRow,
    HealthMetricBatchRow,
    HealthMetricRow,
    HealthSnapshotHoldRow,
    HealthSnapshotItemRow,
    HealthSnapshotRequestRow,
    HealthSnapshotRow,
)


class PostgresHealthRetentionRepository:
    """Retention operations permanently bound to one explicit demo scope."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._sessions = sessions
        self._scope_id = scope_id

    @property
    def scope_id(self) -> UUID:
        return self._scope_id

    @property
    def expires_at(self) -> datetime:
        with self._sessions() as session:
            scope = session.get(DemoScopeRow, self._scope_id)
            if scope is None:
                raise DemoScopeUnavailableError(
                    scope_id=self._scope_id,
                    reason="missing",
                )
            return scope.expires_at

    def preview_counts(
        self,
    ) -> tuple[HealthRetentionCounts, HealthRetentionCounts]:
        with self._sessions() as session:
            self._require_scope(session)
            return self._counts(session)

    def delete_unprotected(
        self,
        *,
        deleted_at: datetime,
    ) -> tuple[HealthRetentionCounts, HealthRetentionCounts]:
        normalized_deleted_at = _utc(deleted_at)
        with self._sessions.begin() as session:
            scope = session.scalar(
                select(DemoScopeRow)
                .where(DemoScopeRow.scope_id == self._scope_id)
                .with_for_update()
            )
            if scope is None:
                raise DemoScopeUnavailableError(
                    scope_id=self._scope_id,
                    reason="missing",
                )
            deletable, _ = self._counts(session)
            held_snapshot_ids = self._held_snapshot_ids()

            session.execute(
                delete(HealthSnapshotRequestRow).where(
                    HealthSnapshotRequestRow.scope_id == self._scope_id,
                    HealthSnapshotRequestRow.snapshot_id.not_in(
                        held_snapshot_ids
                    ),
                )
            )
            session.execute(
                delete(HealthMetricRow).where(
                    HealthMetricRow.scope_id == self._scope_id
                )
            )
            session.execute(
                delete(HealthMetricBatchRow).where(
                    HealthMetricBatchRow.scope_id == self._scope_id
                )
            )
            session.execute(
                delete(HealthCapabilityRow).where(
                    HealthCapabilityRow.scope_id == self._scope_id
                )
            )
            session.execute(
                delete(HealthCapabilityBatchRow).where(
                    HealthCapabilityBatchRow.scope_id == self._scope_id
                )
            )
            scope.status = "closed"
            scope.closed_at = scope.closed_at or normalized_deleted_at
            session.flush()
            _, preserved = self._counts(session)
            return deletable, preserved

    def add_snapshot_hold(
        self,
        *,
        hold_id: UUID,
        snapshot_id: UUID,
        reason: str,
        reference_id: str,
        created_at: datetime,
        notes: str | None = None,
    ) -> bool:
        """Protect a stored snapshot through a stable incident/reference hold."""

        if not reason or len(reason) > 64:
            raise ValueError("hold reason must contain 1-64 characters")
        if not reference_id or len(reference_id) > 128:
            raise ValueError("hold reference_id must contain 1-128 characters")
        normalized_created_at = _utc(created_at)
        with self._sessions.begin() as session:
            scope = self._require_scope(session, lock=True)
            if scope.status != "active":
                raise DemoScopeUnavailableError(
                    scope_id=self._scope_id,
                    reason="closed",
                )
            if scope.expires_at <= datetime.now(UTC):
                raise DemoScopeUnavailableError(
                    scope_id=self._scope_id,
                    reason="expired",
                )
            snapshot = session.get(
                HealthSnapshotRow,
                (self._scope_id, snapshot_id),
            )
            if snapshot is None:
                raise ValueError(f"cannot hold unknown snapshot: {snapshot_id}")
            existing = session.get(
                HealthSnapshotHoldRow,
                (self._scope_id, hold_id),
            )
            if existing is not None:
                if (
                    existing.snapshot_id == snapshot_id
                    and existing.reason == reason
                    and existing.reference_id == reference_id
                    and existing.created_at == normalized_created_at
                    and existing.notes == notes
                ):
                    return False
                raise ValueError(f"snapshot hold ID conflict: {hold_id}")
            duplicate_reference = session.scalar(
                select(HealthSnapshotHoldRow).where(
                    HealthSnapshotHoldRow.scope_id == self._scope_id,
                    HealthSnapshotHoldRow.snapshot_id == snapshot_id,
                    HealthSnapshotHoldRow.reason == reason,
                    HealthSnapshotHoldRow.reference_id == reference_id,
                )
            )
            if duplicate_reference is not None:
                return False
            session.add(
                HealthSnapshotHoldRow(
                    scope_id=self._scope_id,
                    hold_id=hold_id,
                    snapshot_id=snapshot_id,
                    reason=reason,
                    reference_id=reference_id,
                    created_at=normalized_created_at,
                    notes=notes,
                )
            )
            return True

    def _require_scope(
        self,
        session: Session,
        *,
        lock: bool = False,
    ) -> DemoScopeRow:
        statement = select(DemoScopeRow).where(
            DemoScopeRow.scope_id == self._scope_id
        )
        if lock:
            statement = statement.with_for_update()
        scope = session.scalar(statement)
        if scope is None:
            raise DemoScopeUnavailableError(
                scope_id=self._scope_id,
                reason="missing",
            )
        return scope

    def _held_snapshot_ids(self):
        return select(HealthSnapshotHoldRow.snapshot_id).where(
            HealthSnapshotHoldRow.scope_id == self._scope_id
        )

    def _counts(
        self,
        session: Session,
    ) -> tuple[HealthRetentionCounts, HealthRetentionCounts]:
        held_ids = self._held_snapshot_ids()
        deletable = HealthRetentionCounts(
            metric_batches=_count(
                session,
                HealthMetricBatchRow,
                HealthMetricBatchRow.scope_id == self._scope_id,
            ),
            metrics=_count(
                session,
                HealthMetricRow,
                HealthMetricRow.scope_id == self._scope_id,
            ),
            capability_batches=_count(
                session,
                HealthCapabilityBatchRow,
                HealthCapabilityBatchRow.scope_id == self._scope_id,
            ),
            capabilities=_count(
                session,
                HealthCapabilityRow,
                HealthCapabilityRow.scope_id == self._scope_id,
            ),
            snapshot_requests=_count(
                session,
                HealthSnapshotRequestRow,
                HealthSnapshotRequestRow.scope_id == self._scope_id,
                HealthSnapshotRequestRow.snapshot_id.not_in(held_ids),
            ),
            snapshots=_count(
                session,
                HealthSnapshotRow,
                HealthSnapshotRow.scope_id == self._scope_id,
                HealthSnapshotRow.snapshot_id.not_in(held_ids),
            ),
            snapshot_items=_count(
                session,
                HealthSnapshotItemRow,
                HealthSnapshotItemRow.scope_id == self._scope_id,
                HealthSnapshotItemRow.snapshot_id.not_in(held_ids),
            ),
        )
        protected = HealthRetentionCounts(
            snapshot_holds=_count(
                session,
                HealthSnapshotHoldRow,
                HealthSnapshotHoldRow.scope_id == self._scope_id,
            ),
            snapshot_requests=_count(
                session,
                HealthSnapshotRequestRow,
                HealthSnapshotRequestRow.scope_id == self._scope_id,
                HealthSnapshotRequestRow.snapshot_id.in_(held_ids),
            ),
            snapshots=_count(
                session,
                HealthSnapshotRow,
                HealthSnapshotRow.scope_id == self._scope_id,
                HealthSnapshotRow.snapshot_id.in_(held_ids),
            ),
            snapshot_items=_count(
                session,
                HealthSnapshotItemRow,
                HealthSnapshotItemRow.scope_id == self._scope_id,
                HealthSnapshotItemRow.snapshot_id.in_(held_ids),
            ),
        )
        return deletable, protected


def _count(session: Session, model: type, *criteria: object) -> int:
    return int(
        session.scalar(select(func.count()).select_from(model).where(*criteria))
        or 0
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("retention timestamps must be timezone-aware")
    return value.astimezone(UTC)
