"""Fail-closed retention orchestration for one bounded demo scope.

The application service deliberately knows nothing about SQL or deletion order.
Its repository is bound to exactly one scope and is responsible for atomically
deleting only unprotected health records.  Callers must repeat the bound UUID as
an explicit confirmation before even a preview is read.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class HealthRetentionCounts:
    """Record counts returned by retention previews and completed resets."""

    metric_batches: int = 0
    metrics: int = 0
    capability_batches: int = 0
    capabilities: int = 0
    snapshot_requests: int = 0
    snapshots: int = 0
    snapshot_items: int = 0
    snapshot_holds: int = 0

    def __post_init__(self) -> None:
        for field in fields(self):
            if getattr(self, field.name) < 0:
                raise ValueError(f"{field.name} cannot be negative")

    @property
    def total_records(self) -> int:
        """Return the total number of represented database records."""

        return sum(getattr(self, field.name) for field in fields(self))


@dataclass(frozen=True, slots=True)
class HealthRetentionPreview:
    """A read-only view of what reset would delete and preserve."""

    scope_id: UUID
    as_of: datetime
    expires_at: datetime
    expired: bool
    deletable: HealthRetentionCounts
    protected: HealthRetentionCounts

    def __post_init__(self) -> None:
        _require_aware(self.as_of, field_name="as_of")
        _require_aware(self.expires_at, field_name="expires_at")
        if self.expired != (self.as_of >= self.expires_at):
            raise ValueError("expired must match as_of >= expires_at")


@dataclass(frozen=True, slots=True)
class HealthRetentionResetResult:
    """The actual outcome of one manual reset or expiration purge."""

    scope_id: UUID
    completed_at: datetime
    expired_purge: bool
    deleted: HealthRetentionCounts
    preserved: HealthRetentionCounts

    def __post_init__(self) -> None:
        _require_aware(self.completed_at, field_name="completed_at")


class HealthRetentionRepository(Protocol):
    """Persistence operations bound to exactly one configured demo scope."""

    @property
    def scope_id(self) -> UUID:
        """Return the only scope this repository is allowed to inspect or reset."""

    @property
    def expires_at(self) -> datetime:
        """Return the authoritative expiration time for the bound scope."""

    def preview_counts(
        self,
    ) -> tuple[HealthRetentionCounts, HealthRetentionCounts]:
        """Return ``(deletable, protected)`` counts for the bound scope."""

    def delete_unprotected(
        self,
        *,
        deleted_at: datetime,
    ) -> tuple[HealthRetentionCounts, HealthRetentionCounts]:
        """Atomically delete unprotected rows and return ``(deleted, preserved)``."""


class HealthRetentionRefusedError(Exception):
    """A retention operation failed a mandatory safety precondition."""

    def __init__(self, *, code: str, scope_id: UUID) -> None:
        self.code = code
        self.scope_id = scope_id
        super().__init__(f"{code}: {scope_id}")


class HealthRetentionService:
    """Preview and reset health data without accepting an implicit broad scope."""

    def __init__(self, repository: HealthRetentionRepository) -> None:
        self._repository = repository

    def preview(
        self,
        now: datetime,
        confirmation: UUID,
    ) -> HealthRetentionPreview:
        normalized_now = _normalize_utc(now, field_name="now")
        self._require_confirmation(confirmation)
        expires_at = _normalize_utc(
            self._repository.expires_at,
            field_name="repository expires_at",
        )
        deletable, protected = self._repository.preview_counts()
        return HealthRetentionPreview(
            scope_id=self._repository.scope_id,
            as_of=normalized_now,
            expires_at=expires_at,
            expired=normalized_now >= expires_at,
            deletable=deletable,
            protected=protected,
        )

    def reset(
        self,
        now: datetime,
        confirmation: UUID,
    ) -> HealthRetentionResetResult:
        """Perform an explicitly confirmed reset, even before scope expiration."""

        normalized_now = _normalize_utc(now, field_name="now")
        self._require_confirmation(confirmation)
        return self._delete(normalized_now, expired_purge=False)

    def purge_if_expired(
        self,
        now: datetime,
        confirmation: UUID,
    ) -> HealthRetentionResetResult:
        """Purge only when the bound scope has reached its expiration time."""

        normalized_now = _normalize_utc(now, field_name="now")
        self._require_confirmation(confirmation)
        expires_at = _normalize_utc(
            self._repository.expires_at,
            field_name="repository expires_at",
        )
        if normalized_now < expires_at:
            raise HealthRetentionRefusedError(
                code="retention_scope_not_expired",
                scope_id=self._repository.scope_id,
            )
        return self._delete(normalized_now, expired_purge=True)

    def _delete(
        self,
        completed_at: datetime,
        *,
        expired_purge: bool,
    ) -> HealthRetentionResetResult:
        deleted, preserved = self._repository.delete_unprotected(
            deleted_at=completed_at,
        )
        return HealthRetentionResetResult(
            scope_id=self._repository.scope_id,
            completed_at=completed_at,
            expired_purge=expired_purge,
            deleted=deleted,
            preserved=preserved,
        )

    def _require_confirmation(self, confirmation: UUID) -> None:
        scope_id = self._repository.scope_id
        if not isinstance(confirmation, UUID) or confirmation != scope_id:
            raise HealthRetentionRefusedError(
                code="retention_scope_confirmation_mismatch",
                scope_id=scope_id,
            )


def _require_aware(value: datetime, *, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _normalize_utc(value: datetime, *, field_name: str) -> datetime:
    _require_aware(value, field_name=field_name)
    return value.astimezone(UTC)
