"""Capability ingestion, freshness, and immutable snapshot use cases."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

from vital_relay.application.health_ingestion import Clock, HealthMetricRepository
from vital_relay.domain.health import AcquisitionClass, HealthMetric
from vital_relay.domain.health_context import (
    CapabilityStatus,
    FreshnessLabel,
    HealthCapability,
    HealthCapabilityBatch,
    HealthCapabilityBatchResult,
    HealthSnapshot,
    HealthSnapshotCreateRequest,
    HealthSnapshotItem,
    HealthSnapshotItemView,
    HealthSnapshotView,
)


class HealthCapabilityRepository(Protocol):
    def ingest_batch(
        self,
        batch: HealthCapabilityBatch,
        *,
        server_received_at: datetime,
    ) -> HealthCapabilityBatchResult:
        """Atomically insert or deduplicate a capability batch."""

    def latest_by_type(
        self,
        *,
        user_id: str,
        as_of: datetime,
    ) -> dict[str, HealthCapability]:
        """Return the latest capability of each type as of a timestamp."""


class HealthSnapshotRepository(Protocol):
    def find_by_request(
        self,
        request: HealthSnapshotCreateRequest,
    ) -> HealthSnapshot | None:
        """Return an exact prior request or raise on conflicting ID reuse."""

    def save(
        self,
        request: HealthSnapshotCreateRequest,
        snapshot: HealthSnapshot,
    ) -> tuple[HealthSnapshot, bool]:
        """Atomically save, returning the stored snapshot and whether it was new."""

    def get(self, snapshot_id: UUID) -> HealthSnapshot | None:
        """Load an immutable snapshot by ID."""


@dataclass(frozen=True)
class HealthSnapshotRepositories:
    """Three repositories participating in one snapshot capture transaction."""

    metric: HealthMetricRepository
    capability: HealthCapabilityRepository
    snapshot: HealthSnapshotRepository


class HealthSnapshotUnitOfWork(Protocol):
    """Open one consistent storage transaction for snapshot capture."""

    def begin(
        self,
        *,
        snapshot_id: UUID,
    ) -> AbstractContextManager[HealthSnapshotRepositories]:
        """Serialize a snapshot ID and expose transaction-bound repositories."""


@dataclass(frozen=True)
class FreshnessWindows:
    live_seconds: int
    recent_seconds: int

    def __post_init__(self) -> None:
        if self.live_seconds < 0 or self.recent_seconds < 0:
            raise ValueError("freshness windows cannot be negative")
        if self.live_seconds > self.recent_seconds:
            raise ValueError("live freshness cannot exceed recent freshness")


class FreshnessPolicy:
    """Deterministic display freshness; these are not medical thresholds."""

    def __init__(
        self,
        *,
        default: FreshnessWindows | None = None,
        overrides: Mapping[str, FreshnessWindows] | None = None,
    ) -> None:
        self._default = default or FreshnessWindows(
            live_seconds=15,
            recent_seconds=86_400,
        )
        self._overrides = dict(overrides or {})

    def windows_for(self, metric_type: str) -> FreshnessWindows:
        return self._overrides.get(metric_type, self._default)

    def classify(
        self,
        *,
        acquisition_class: AcquisitionClass,
        age_seconds: int,
        windows: FreshnessWindows,
    ) -> FreshnessLabel:
        if (
            acquisition_class is AcquisitionClass.LIVE
            and age_seconds <= windows.live_seconds
        ):
            return FreshnessLabel.LIVE
        if age_seconds <= windows.recent_seconds:
            return FreshnessLabel.RECENT
        return FreshnessLabel.HISTORICAL


def _capability_hides_prior_metric(
    capability: HealthCapability,
    metric: HealthMetric,
) -> bool:
    """Honor the newest visibility state for the same collection source.

    Previously accepted observations remain immutable in storage and in older
    snapshots. A later non-available capability must, however, prevent that
    source's older value from leaking into a newly requested snapshot after an
    explicit opt-out, HealthKit visibility change, or current read failure.
    """

    return (
        capability.status is not CapabilityStatus.AVAILABLE
        and capability.checked_at >= metric.observed_at
        and capability.acquisition_class is metric.acquisition_class
        and capability.source_kind is metric.source_kind
        and capability.source == metric.source
    )


class HealthCapabilityIngestionService:
    def __init__(self, repository: HealthCapabilityRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def ingest(self, batch: HealthCapabilityBatch) -> HealthCapabilityBatchResult:
        return self._repository.ingest_batch(
            batch,
            server_received_at=self._clock.now(),
        )


@dataclass(frozen=True)
class SnapshotCreationOutcome:
    snapshot: HealthSnapshot
    created: bool


class HealthSnapshotService:
    def __init__(
        self,
        *,
        metric_repository: HealthMetricRepository,
        capability_repository: HealthCapabilityRepository,
        snapshot_repository: HealthSnapshotRepository,
        clock: Clock,
        freshness_policy: FreshnessPolicy | None = None,
        unit_of_work: HealthSnapshotUnitOfWork | None = None,
    ) -> None:
        self._metric_repository = metric_repository
        self._capability_repository = capability_repository
        self._snapshot_repository = snapshot_repository
        self._clock = clock
        self._freshness_policy = freshness_policy or FreshnessPolicy()
        self._unit_of_work = unit_of_work

    def create(self, request: HealthSnapshotCreateRequest) -> SnapshotCreationOutcome:
        if self._unit_of_work is not None:
            with self._unit_of_work.begin(
                snapshot_id=request.snapshot_id,
            ) as repositories:
                return self._create_with_repositories(request, repositories)
        repositories = HealthSnapshotRepositories(
            metric=self._metric_repository,
            capability=self._capability_repository,
            snapshot=self._snapshot_repository,
        )
        return self._create_with_repositories(request, repositories)

    def _create_with_repositories(
        self,
        request: HealthSnapshotCreateRequest,
        repositories: HealthSnapshotRepositories,
    ) -> SnapshotCreationOutcome:
        existing = repositories.snapshot.find_by_request(request)
        if existing is not None:
            return SnapshotCreationOutcome(snapshot=existing, created=False)

        captured_at = self._clock.now()
        metrics = repositories.metric.latest_by_type(
            user_id=request.user_id,
            as_of=captured_at,
        )
        capabilities = repositories.capability.latest_by_type(
            user_id=request.user_id,
            as_of=captured_at,
        )

        items: list[HealthSnapshotItem] = []
        for metric_type in sorted(set(metrics) | set(capabilities)):
            metric = metrics.get(metric_type)
            capability = capabilities.get(metric_type)
            windows = self._freshness_policy.windows_for(metric_type)

            if (
                metric is not None
                and capability is not None
                and _capability_hides_prior_metric(capability, metric)
            ):
                metric = None

            if metric is None:
                assert capability is not None
                item = HealthSnapshotItem(
                    metric_type=metric_type,
                    metric=None,
                    capability=capability,
                    availability=capability.status,
                    freshness=FreshnessLabel.UNAVAILABLE,
                    age_seconds=None,
                    live_window_seconds=windows.live_seconds,
                    recent_window_seconds=windows.recent_seconds,
                    used_for_escalation=False,
                )
            else:
                age_seconds = int((captured_at - metric.observed_at).total_seconds())
                item = HealthSnapshotItem(
                    metric_type=metric_type,
                    metric=metric,
                    capability=capability,
                    availability=CapabilityStatus.AVAILABLE,
                    freshness=self._freshness_policy.classify(
                        acquisition_class=metric.acquisition_class,
                        age_seconds=age_seconds,
                        windows=windows,
                    ),
                    age_seconds=age_seconds,
                    live_window_seconds=windows.live_seconds,
                    recent_window_seconds=windows.recent_seconds,
                    used_for_escalation=False,
                )
            items.append(item)

        snapshot = HealthSnapshot(
            schema_version=request.schema_version,
            snapshot_id=request.snapshot_id,
            user_id=request.user_id,
            capture_reason=request.capture_reason,
            captured_at=captured_at,
            items=tuple(items),
            used_for_escalation=False,
        )
        stored, created = repositories.snapshot.save(request, snapshot)
        return SnapshotCreationOutcome(snapshot=stored, created=created)

    def get(self, snapshot_id: UUID) -> HealthSnapshot | None:
        return self._snapshot_repository.get(snapshot_id)

    def get_view(self, snapshot_id: UUID) -> HealthSnapshotView | None:
        snapshot = self.get(snapshot_id)
        return self.to_view(snapshot) if snapshot is not None else None

    @staticmethod
    def to_view(snapshot: HealthSnapshot) -> HealthSnapshotView:
        items: list[HealthSnapshotItemView] = []
        for item in snapshot.items:
            if item.metric is not None:
                source = item.metric.source
                source_kind = item.metric.source_kind
                source_name = item.metric.source_name
                simulated = item.metric.simulated
                value = item.metric.value
                unit = item.metric.unit
                observed_at = item.metric.observed_at
            else:
                assert item.capability is not None
                source = item.capability.source
                source_kind = item.capability.source_kind
                source_name = item.capability.source_name
                simulated = item.capability.simulated
                value = None
                unit = None
                observed_at = None

            items.append(
                HealthSnapshotItemView(
                    metric_type=item.metric_type,
                    availability=item.availability,
                    freshness=item.freshness,
                    age_seconds=item.age_seconds,
                    value=value,
                    unit=unit,
                    observed_at=observed_at,
                    source=source,
                    source_kind=source_kind,
                    source_name=source_name,
                    capability_checked_at=(
                        item.capability.checked_at
                        if item.capability is not None
                        else None
                    ),
                    simulated=simulated,
                    used_for_escalation=False,
                )
            )

        return HealthSnapshotView(
            schema_version=snapshot.schema_version,
            snapshot_id=snapshot.snapshot_id,
            user_id=snapshot.user_id,
            capture_reason=snapshot.capture_reason,
            captured_at=snapshot.captured_at,
            items=tuple(items),
            used_for_escalation=False,
        )
