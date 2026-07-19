"""Canonical scalar health metric transport contracts.

Scalar observations remain separate from capability states in ``health_context``.
Structured records such as sleep or ECG metadata require future typed contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1
FORBIDDEN_RAW_METRIC_TYPES = frozenset(
    {
        "ecg_voltage",
        "raw_accelerometer",
        "raw_gyroscope",
        "raw_magnetometer",
    }
)


class AcquisitionClass(StrEnum):
    LIVE = "live"
    RECENT_CONTEXT = "recent_context"
    USER_INITIATED = "user_initiated"


class HealthSourceKind(StrEnum):
    APPLE_HEALTHKIT = "apple_healthkit"
    APPLE_LIVE_WORKOUT = "apple_live_workout"
    CORE_MOTION = "core_motion"
    PEDOMETER = "pedometer"
    CONNECTED_DEVICE = "connected_device"
    MANUAL = "manual"
    REPLAY = "replay"


class MetricQuality(StrEnum):
    GOOD = "good"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class IngestionStatus(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_PROCESSED = "already_processed"


class HealthMetric(BaseModel):
    """One immutable, visible scalar health observation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    metric_id: UUID
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    metric_type: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    acquisition_class: AcquisitionClass
    value: FiniteFloat
    unit: str = Field(min_length=1, max_length=64)
    observed_at: AwareDatetime
    source: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    source_kind: HealthSourceKind
    source_name: str | None = Field(min_length=1, max_length=128)
    source_bundle_id: str | None = Field(min_length=1, max_length=255)
    device_model: str | None = Field(min_length=1, max_length=128)
    simulated: bool
    quality: MetricQuality | None
    used_for_escalation: Literal[False]

    @field_validator("observed_at")
    @classmethod
    def normalize_observed_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_safety_and_source(self) -> HealthMetric:
        if self.metric_type in FORBIDDEN_RAW_METRIC_TYPES:
            raise ValueError(f"raw metric type is not accepted: {self.metric_type}")
        if self.source.startswith("replay_") and not self.simulated:
            raise ValueError("replay sources require simulated=true")
        if self.source_kind is HealthSourceKind.REPLAY and not self.simulated:
            raise ValueError("replay source_kind requires simulated=true")
        return self


class HealthMetricBatch(BaseModel):
    """An idempotent, single-user, single-device transport batch."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    batch_id: UUID
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    device_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    sent_at: AwareDatetime
    metrics: tuple[HealthMetric, ...] = Field(min_length=1, max_length=100)

    @field_validator("sent_at")
    @classmethod
    def normalize_sent_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_batch_scope(self) -> HealthMetricBatch:
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metric_id values must be unique within a batch")
        if any(metric.user_id != self.user_id for metric in self.metrics):
            raise ValueError("every metric user_id must match the batch user_id")
        return self


class HealthMetricBatchResult(BaseModel):
    """Synchronous result of accepting an idempotent metric batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    batch_id: UUID
    status: IngestionStatus
    accepted_count: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    accepted_metric_ids: tuple[UUID, ...]
    duplicate_metric_ids: tuple[UUID, ...]
    server_received_at: AwareDatetime

    @field_validator("server_received_at")
    @classmethod
    def normalize_received_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_result_counts(self) -> HealthMetricBatchResult:
        if self.accepted_count != len(self.accepted_metric_ids):
            raise ValueError("accepted_count must match accepted_metric_ids")
        if self.duplicate_count != len(self.duplicate_metric_ids):
            raise ValueError("duplicate_count must match duplicate_metric_ids")
        if set(self.accepted_metric_ids) & set(self.duplicate_metric_ids):
            raise ValueError("accepted and duplicate metric IDs must be disjoint")
        return self
