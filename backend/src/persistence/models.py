"""SQLAlchemy models for scoped, immutable health persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from geoalchemy2 import Geography
from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Metadata root used by Alembic and integration tests."""


class DemoScopeRow(Base):
    __tablename__ = "demo_scopes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_demo_scopes_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_demo_scopes_expiration",
        ),
        CheckConstraint(
            "(status = 'active' AND closed_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_demo_scopes_closed_at",
        ),
        Index("ix_demo_scopes_status_expires_at", "status", "expires_at"),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HealthMetricBatchRow(Base):
    __tablename__ = "health_metric_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_metric_batches_fingerprint",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_metric_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PostgreSQLUUID(as_uuid=True)),
        nullable=False,
    )
    duplicate_metric_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PostgreSQLUUID(as_uuid=True)),
        nullable=False,
    )


class HealthMetricRow(Base):
    __tablename__ = "health_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "first_batch_id"],
            [
                "health_metric_batches.scope_id",
                "health_metric_batches.batch_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_metrics_fingerprint",
        ),
        CheckConstraint(
            "acquisition_class IN ('live', 'recent_context', 'user_initiated')",
            name="ck_health_metrics_acquisition_class",
        ),
        CheckConstraint(
            "source_kind IN ('apple_healthkit', 'apple_live_workout', "
            "'core_motion', 'pedometer', 'connected_device', 'manual', 'replay')",
            name="ck_health_metrics_source_kind",
        ),
        CheckConstraint(
            "quality IS NULL OR quality IN ('good', 'degraded', 'unknown')",
            name="ck_health_metrics_quality",
        ),
        CheckConstraint(
            "metric_type NOT IN ('ecg_voltage', 'raw_accelerometer', "
            "'raw_gyroscope', 'raw_magnetometer')",
            name="ck_health_metrics_no_raw_types",
        ),
        CheckConstraint(
            "used_for_escalation = false",
            name="ck_health_metrics_no_escalation",
        ),
        CheckConstraint(
            "source_kind <> 'replay' OR simulated = true",
            name="ck_health_metrics_replay_simulated",
        ),
        CheckConstraint(
            "source !~ '^replay_' OR simulated = true",
            name="ck_health_metrics_replay_name_simulated",
        ),
        CheckConstraint(
            "value NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision)",
            name="ck_health_metrics_finite_value",
        ),
        Index(
            "ix_health_metrics_scope_user_type_observed",
            "scope_id",
            "user_id",
            "metric_type",
            desc("observed_at"),
            desc("metric_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    metric_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    first_batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(128), nullable=False)
    acquisition_class: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_bundle_id: Mapped[str | None] = mapped_column(String(255))
    device_model: Mapped[str | None] = mapped_column(String(128))
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    quality: Mapped[str | None] = mapped_column(String(32))
    used_for_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class HealthCapabilityBatchRow(Base):
    __tablename__ = "health_capability_batches"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_capability_batches_fingerprint",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_capability_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PostgreSQLUUID(as_uuid=True)),
        nullable=False,
    )
    duplicate_capability_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PostgreSQLUUID(as_uuid=True)),
        nullable=False,
    )


class HealthCapabilityRow(Base):
    __tablename__ = "health_capabilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "first_batch_id"],
            [
                "health_capability_batches.scope_id",
                "health_capability_batches.batch_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "content_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_capabilities_fingerprint",
        ),
        CheckConstraint(
            "acquisition_class IN ('live', 'recent_context', 'user_initiated')",
            name="ck_health_capabilities_acquisition_class",
        ),
        CheckConstraint(
            "status IN ('unsupported', 'not_requested', 'requested_no_sample', "
            "'available', 'error')",
            name="ck_health_capabilities_status",
        ),
        CheckConstraint(
            "source_kind IN ('apple_healthkit', 'apple_live_workout', "
            "'core_motion', 'pedometer', 'connected_device', 'manual', 'replay')",
            name="ck_health_capabilities_source_kind",
        ),
        CheckConstraint(
            "metric_type NOT IN ('ecg_voltage', 'raw_accelerometer', "
            "'raw_gyroscope', 'raw_magnetometer')",
            name="ck_health_capabilities_no_raw_types",
        ),
        CheckConstraint(
            "(status = 'error') = (error_code IS NOT NULL)",
            name="ck_health_capabilities_error_code",
        ),
        CheckConstraint(
            "used_for_escalation = false",
            name="ck_health_capabilities_no_escalation",
        ),
        CheckConstraint(
            "source_kind <> 'replay' OR simulated = true",
            name="ck_health_capabilities_replay_simulated",
        ),
        CheckConstraint(
            "source !~ '^replay_' OR simulated = true",
            name="ck_health_capabilities_replay_name_simulated",
        ),
        Index(
            "ix_health_capabilities_scope_user_type_checked",
            "scope_id",
            "user_id",
            "metric_type",
            desc("checked_at"),
            desc("capability_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    capability_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    first_batch_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_type: Mapped[str] = mapped_column(String(128), nullable=False)
    acquisition_class: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_bundle_id: Mapped[str | None] = mapped_column(String(255))
    device_model: Mapped[str | None] = mapped_column(String(128))
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    used_for_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class HealthSnapshotRequestRow(Base):
    __tablename__ = "health_snapshot_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_health_snapshot_requests_fingerprint",
        ),
        CheckConstraint(
            "capture_reason IN "
            "('monitoring_started', 'manual_refresh', 'incident_created')",
            name="ck_health_snapshot_requests_reason",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capture_reason: Mapped[str] = mapped_column(String(32), nullable=False)


class HealthSnapshotRow(Base):
    __tablename__ = "health_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "snapshot_id"],
            [
                "health_snapshot_requests.scope_id",
                "health_snapshot_requests.snapshot_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "capture_reason IN "
            "('monitoring_started', 'manual_refresh', 'incident_created')",
            name="ck_health_snapshots_reason",
        ),
        CheckConstraint(
            "used_for_escalation = false",
            name="ck_health_snapshots_no_escalation",
        ),
        Index(
            "ix_health_snapshots_scope_user_captured",
            "scope_id",
            "user_id",
            desc("captured_at"),
            desc("snapshot_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    capture_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_for_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False)


class HealthSnapshotItemRow(Base):
    __tablename__ = "health_snapshot_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "snapshot_id"],
            ["health_snapshots.scope_id", "health_snapshots.snapshot_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "metric_payload IS NOT NULL OR capability_payload IS NOT NULL",
            name="ck_health_snapshot_items_has_context",
        ),
        CheckConstraint(
            "metric_type NOT IN ('ecg_voltage', 'raw_accelerometer', "
            "'raw_gyroscope', 'raw_magnetometer')",
            name="ck_health_snapshot_items_no_raw_types",
        ),
        CheckConstraint(
            "metric_payload IS NULL OR ("
            "jsonb_typeof(metric_payload) = 'object' AND "
            "metric_payload ? 'metric_type' AND "
            "metric_payload ->> 'metric_type' = metric_type AND "
            "metric_payload ->> 'metric_type' NOT IN "
            "('ecg_voltage', 'raw_accelerometer', 'raw_gyroscope', "
            "'raw_magnetometer') AND "
            "metric_payload ? 'used_for_escalation' AND "
            "metric_payload -> 'used_for_escalation' = 'false'::jsonb)",
            name="ck_health_snapshot_items_metric_payload_safety",
        ),
        CheckConstraint(
            "capability_payload IS NULL OR ("
            "jsonb_typeof(capability_payload) = 'object' AND "
            "capability_payload ? 'metric_type' AND "
            "capability_payload ->> 'metric_type' = metric_type AND "
            "capability_payload ->> 'metric_type' NOT IN "
            "('ecg_voltage', 'raw_accelerometer', 'raw_gyroscope', "
            "'raw_magnetometer') AND "
            "capability_payload ? 'used_for_escalation' AND "
            "capability_payload -> 'used_for_escalation' = 'false'::jsonb)",
            name="ck_health_snapshot_items_capability_payload_safety",
        ),
        CheckConstraint(
            "availability IN ('unsupported', 'not_requested', "
            "'requested_no_sample', 'available', 'error')",
            name="ck_health_snapshot_items_availability",
        ),
        CheckConstraint(
            "freshness IN ('live', 'recent', 'historical', 'unavailable')",
            name="ck_health_snapshot_items_freshness",
        ),
        CheckConstraint(
            "age_seconds IS NULL OR age_seconds >= 0",
            name="ck_health_snapshot_items_age",
        ),
        CheckConstraint(
            "live_window_seconds >= 0 AND recent_window_seconds >= 0 AND "
            "live_window_seconds <= recent_window_seconds",
            name="ck_health_snapshot_items_windows",
        ),
        CheckConstraint(
            "(metric_payload IS NULL AND age_seconds IS NULL AND "
            "freshness = 'unavailable') OR "
            "(metric_payload IS NOT NULL AND age_seconds IS NOT NULL AND "
            "freshness <> 'unavailable' AND availability = 'available')",
            name="ck_health_snapshot_items_metric_state",
        ),
        CheckConstraint(
            "used_for_escalation = false",
            name="ck_health_snapshot_items_no_escalation",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    metric_type: Mapped[str] = mapped_column(String(128), primary_key=True)
    metric_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    capability_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    availability: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness: Mapped[str] = mapped_column(String(32), nullable=False)
    age_seconds: Mapped[int | None] = mapped_column(Integer)
    live_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    recent_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    used_for_escalation: Mapped[bool] = mapped_column(Boolean, nullable=False)


class HealthSnapshotHoldRow(Base):
    __tablename__ = "health_snapshot_holds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "snapshot_id"],
            ["health_snapshots.scope_id", "health_snapshots.snapshot_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "snapshot_id",
            "reason",
            "reference_id",
            name="uq_health_snapshot_holds_reference",
        ),
        UniqueConstraint(
            "scope_id",
            "hold_id",
            "snapshot_id",
            name="uq_health_snapshot_holds_exact_snapshot",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    hold_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class WearableEventRow(Base):
    """Immutable authenticated wearable/manual safety event."""

    __tablename__ = "wearable_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            name="fk_wearable_events_incident",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        UniqueConstraint(
            "scope_id",
            "event_id",
            "incident_id",
            name="uq_wearable_events_incident_link",
        ),
        UniqueConstraint(
            "scope_id",
            "device_id",
            "sequence",
            name="uq_wearable_events_device_sequence",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_wearable_events_fingerprint",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_wearable_events_schema_version",
        ),
        CheckConstraint(
            "(source = 'apple_fall' AND event_type = 'fall_detected') OR "
            "(source = 'manual_sos' AND event_type = 'manual_sos')",
            name="ck_wearable_events_source_type",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_wearable_events_not_simulated",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_wearable_events_sequence",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_wearable_events_payload_object",
        ),
        CheckConstraint(
            "CASE source "
            "WHEN 'apple_fall' THEN ("
            "payload ? 'fall_date' AND "
            "payload -> 'fall_detection_available' = 'true'::jsonb AND "
            "payload -> 'entitlement_present' = 'true'::jsonb AND "
            "payload ->> 'authorization_status' = 'authorized' AND "
            "(payload ->> 'fall_date')::timestamptz = observed_at) "
            "WHEN 'manual_sos' THEN ("
            "payload ->> 'activation_method' IN "
            "('watch_button', 'iphone_button')) "
            "ELSE false END",
            name="ck_wearable_events_payload_contract",
        ),
        CheckConstraint(
            "latitude NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND latitude BETWEEN -90 AND 90",
            name="ck_wearable_events_latitude",
        ),
        CheckConstraint(
            "longitude NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND longitude BETWEEN -180 AND 180",
            name="ck_wearable_events_longitude",
        ),
        CheckConstraint(
            "horizontal_accuracy_m NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND horizontal_accuracy_m BETWEEN 0 AND 10000",
            name="ck_wearable_events_location_accuracy",
        ),
        Index(
            "uq_wearable_events_apple_natural",
            "scope_id",
            "user_id",
            "device_id",
            "observed_at",
            unique=True,
            postgresql_where=text("source = 'apple_fall'"),
        ),
        Index(
            "ix_wearable_events_scope_user_observed",
            "scope_id",
            "user_id",
            desc("observed_at"),
            desc("event_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    horizontal_accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    location_captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )


class IncidentRow(Base):
    """Mutable current-state projection for one durable incident."""

    __tablename__ = "incidents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "trigger_event_id", "incident_id"],
            [
                "wearable_events.scope_id",
                "wearable_events.event_id",
                "wearable_events.incident_id",
            ],
            name="fk_incidents_trigger_event",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            [
                "scope_id",
                "health_snapshot_hold_id",
                "health_snapshot_id",
            ],
            [
                "health_snapshot_holds.scope_id",
                "health_snapshot_holds.hold_id",
                "health_snapshot_holds.snapshot_id",
            ],
            name="fk_incidents_exact_snapshot_hold",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "trigger_event_id",
            name="uq_incidents_trigger_event",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_incidents_schema_version",
        ),
        CheckConstraint(
            "kind IN ('fall', 'manual_sos')",
            name="ck_incidents_kind",
        ),
        CheckConstraint(
            "current_state IN "
            "('verifying', 'escalating', 'response_active', 'resolved')",
            name="ck_incidents_state",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_incidents_not_simulated",
        ),
        CheckConstraint(
            "state_version >= 1 AND next_timeline_sequence >= 1",
            name="ck_incidents_sequences",
        ),
        CheckConstraint(
            "updated_at >= opened_at",
            name="ck_incidents_updated_at",
        ),
        CheckConstraint(
            "(current_state = 'resolved' AND resolved_at IS NOT NULL "
            "AND resolved_at >= opened_at) OR "
            "(current_state <> 'resolved' AND resolved_at IS NULL)",
            name="ck_incidents_resolved_at",
        ),
        CheckConstraint(
            "latitude NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND latitude BETWEEN -90 AND 90",
            name="ck_incidents_latitude",
        ),
        CheckConstraint(
            "longitude NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND longitude BETWEEN -180 AND 180",
            name="ck_incidents_longitude",
        ),
        CheckConstraint(
            "horizontal_accuracy_m NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND horizontal_accuracy_m BETWEEN 0 AND 10000",
            name="ck_incidents_location_accuracy",
        ),
        Index(
            "uq_incidents_scope_user_active",
            "scope_id",
            "user_id",
            unique=True,
            postgresql_where=text(
                "current_state IN "
                "('verifying', 'escalating', 'response_active')"
            ),
        ),
        Index(
            "ix_incidents_scope_state_updated",
            "scope_id",
            "current_state",
            desc("updated_at"),
            desc("incident_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    current_state: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger_event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    health_snapshot_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    health_snapshot_hold_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    next_timeline_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    horizontal_accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    location_captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IncidentCommandRow(Base):
    """Immutable idempotency receipt for a first-party check-in command."""

    __tablename__ = "incident_commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            name="uq_incident_commands_one_check_in",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_incident_commands_fingerprint",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_incident_commands_schema_version",
        ),
        CheckConstraint(
            "command_type IN ('i_am_okay', 'i_need_help')",
            name="ck_incident_commands_type",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_incident_commands_payload_object",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    command_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    command_type: Mapped[str] = mapped_column(String(32), nullable=False)
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class IncidentResolutionReceiptRow(Base):
    """Append-only idempotency receipt for a command resolution."""

    __tablename__ = "incident_resolution_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            name="uq_incident_resolution_receipts_incident",
        ),
        UniqueConstraint(
            "scope_id",
            "resolution_id",
            "incident_id",
            name="uq_incident_resolution_receipts_exact_link",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_incident_resolution_receipts_fingerprint",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_incident_resolution_receipts_schema_version",
        ),
        CheckConstraint(
            "action IN ('close', 'handoff')",
            name="ck_incident_resolution_receipts_action",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_incident_resolution_receipts_payload_object",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_incident_resolution_receipts_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    resolution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class IncidentStateTransitionRow(Base):
    """Immutable state transition authorized by the frozen transition table."""

    __tablename__ = "incident_state_transitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "source_event_id"],
            ["wearable_events.scope_id", "wearable_events.event_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "command_id"],
            ["incident_commands.scope_id", "incident_commands.command_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "resolution_id", "incident_id"],
            [
                "incident_resolution_receipts.scope_id",
                "incident_resolution_receipts.resolution_id",
                "incident_resolution_receipts.incident_id",
            ],
            name="fk_incident_transitions_resolution",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            "sequence",
            name="uq_incident_transitions_sequence",
        ),
        UniqueConstraint(
            "scope_id",
            "transition_id",
            "incident_id",
            name="uq_incident_transitions_incident_link",
        ),
        UniqueConstraint(
            "scope_id",
            "source_event_id",
            name="uq_incident_transitions_source_event",
        ),
        UniqueConstraint(
            "scope_id",
            "command_id",
            name="uq_incident_transitions_command",
        ),
        UniqueConstraint(
            "scope_id",
            "resolution_id",
            name="uq_incident_transitions_resolution",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_incident_transitions_schema_version",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_incident_transitions_sequence",
        ),
        CheckConstraint(
            "from_state IN "
            "('monitoring', 'verifying', 'escalating', "
            "'response_active', 'resolved') AND "
            "to_state IN "
            "('verifying', 'escalating', 'response_active', 'resolved')",
            name="ck_incident_transitions_states",
        ),
        CheckConstraint(
            "(from_state = 'monitoring' AND trigger = 'fall_detected' "
            "AND to_state = 'verifying') OR "
            "(from_state = 'monitoring' AND trigger = 'manual_sos' "
            "AND to_state = 'escalating') OR "
            "(from_state = 'verifying' AND trigger = 'check_in_okay' "
            "AND to_state = 'resolved') OR "
            "(from_state = 'verifying' AND trigger IN "
            "('check_in_help', 'verification_timeout', 'manual_sos') "
            "AND to_state = 'escalating') OR "
            "(from_state = 'escalating' AND trigger = 'responder_accepted' "
            "AND to_state = 'response_active') OR "
            "(from_state = 'escalating' AND trigger = 'cancellation' "
            "AND to_state = 'resolved') OR "
            "(from_state = 'response_active' AND trigger IN "
            "('close', 'handoff') AND to_state = 'resolved')",
            name="ck_incident_transitions_policy",
        ),
        CheckConstraint(
            "((trigger IN ('fall_detected', 'manual_sos')) = "
            "(source_event_id IS NOT NULL)) AND "
            "((trigger IN ('check_in_okay', 'check_in_help')) = "
            "(command_id IS NOT NULL)) AND "
            "((trigger IN ('close', 'handoff')) = "
            "(resolution_id IS NOT NULL)) AND "
            "NOT ((source_event_id IS NOT NULL AND command_id IS NOT NULL) OR "
            "(source_event_id IS NOT NULL AND resolution_id IS NOT NULL) OR "
            "(command_id IS NOT NULL AND resolution_id IS NOT NULL))",
            name="ck_incident_transitions_authority",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_incident_transitions_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    transition_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    from_state: Mapped[str] = mapped_column(String(32), nullable=False)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    source_event_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    command_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    resolution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class IncidentDeadlineRow(Base):
    """Mutable authoritative deadline that survives process restarts."""

    __tablename__ = "incident_deadlines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "settled_transition_id", "incident_id"],
            [
                "incident_state_transitions.scope_id",
                "incident_state_transitions.transition_id",
                "incident_state_transitions.incident_id",
            ],
            name="fk_incident_deadlines_settlement_transition",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            "kind",
            name="uq_incident_deadlines_kind",
        ),
        CheckConstraint(
            "kind = 'verification_timeout'",
            name="ck_incident_deadlines_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'fired', 'cancelled')",
            name="ck_incident_deadlines_status",
        ),
        CheckConstraint(
            "due_at >= created_at",
            name="ck_incident_deadlines_due_at",
        ),
        CheckConstraint(
            "(status = 'pending' AND settled_at IS NULL "
            "AND settled_transition_id IS NULL) OR "
            "(status IN ('fired', 'cancelled') AND settled_at IS NOT NULL "
            "AND settled_transition_id IS NOT NULL)",
            name="ck_incident_deadlines_settlement",
        ),
        Index(
            "ix_incident_deadlines_pending_due",
            "due_at",
            "deadline_id",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    deadline_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_transition_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )


class IncidentTimelineEntryRow(Base):
    """Immutable ordered audit entry presented by the incident API."""

    __tablename__ = "incident_timeline_entries"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "transition_id"],
            [
                "incident_state_transitions.scope_id",
                "incident_state_transitions.transition_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "source_event_id"],
            ["wearable_events.scope_id", "wearable_events.event_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "command_id"],
            ["incident_commands.scope_id", "incident_commands.command_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            "sequence",
            name="uq_incident_timeline_sequence",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_incident_timeline_schema_version",
        ),
        CheckConstraint(
            "sequence >= 1",
            name="ck_incident_timeline_sequence",
        ),
        CheckConstraint(
            "event_type IN "
            "('wearable_event_received', 'incident_opened', "
            "'verification_started', 'check_in_recorded', "
            "'verification_timed_out', 'state_transitioned', "
            "'responder_search_started', 'responder_invited', "
            "'responder_declined', 'responder_accepted', "
            "'dispatch_activated')",
            name="ck_incident_timeline_event_type",
        ),
        CheckConstraint(
            "state IN "
            "('verifying', 'escalating', 'response_active', 'resolved')",
            name="ck_incident_timeline_state",
        ),
        CheckConstraint(
            "char_length(summary) BETWEEN 1 AND 256",
            name="ck_incident_timeline_summary",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_incident_timeline_not_simulated",
        ),
        Index(
            "ix_incident_timeline_scope_incident_order",
            "scope_id",
            "incident_id",
            "sequence",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    timeline_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    source_event_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    command_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    summary: Mapped[str] = mapped_column(String(256), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ResponderRow(Base):
    """Scope-local responder identity used only by the live dispatch demo."""

    __tablename__ = "responders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "access_token_hash",
            name="uq_responders_access_token_hash",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 128",
            name="ck_responders_display_name",
        ),
        CheckConstraint(
            "role IN ('venue_staff', 'trained_volunteer', "
            "'medical_professional')",
            name="ck_responders_role",
        ),
        CheckConstraint(
            "access_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_responders_access_token_hash",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_responders_status",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_responders_updated_at",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responders_not_simulated",
        ),
        Index("ix_responders_scope_status", "scope_id", "status"),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class PersonaAccountRow(Base):
    """Scope-local account whose subject and authority are fixed by persona."""

    __tablename__ = "persona_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "enrollment_token_hash",
            name="uq_persona_accounts_enrollment_token_hash",
        ),
        CheckConstraint(
            "char_length(display_name) BETWEEN 1 AND 128 AND "
            "display_name = btrim(display_name)",
            name="ck_persona_accounts_display_name",
        ),
        CheckConstraint(
            "persona IN ('community', 'responder', 'command')",
            name="ck_persona_accounts_persona",
        ),
        CheckConstraint(
            "(persona = 'community' AND user_id IS NOT NULL "
            "AND responder_id IS NULL) OR "
            "(persona = 'responder' AND user_id IS NULL "
            "AND responder_id IS NOT NULL) OR "
            "(persona = 'command' AND user_id IS NULL "
            "AND responder_id IS NULL)",
            name="ck_persona_accounts_subject",
        ),
        CheckConstraint(
            "user_id IS NULL OR (char_length(user_id) BETWEEN 1 AND 128 "
            "AND user_id ~ '^[A-Za-z0-9._:-]+$')",
            name="ck_persona_accounts_user_id",
        ),
        CheckConstraint(
            "enrollment_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_persona_accounts_enrollment_token_hash",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_persona_accounts_status",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_persona_accounts_updated_at",
        ),
        Index(
            "uq_persona_accounts_community_subject",
            "scope_id",
            "user_id",
            unique=True,
            postgresql_where=text("persona = 'community'"),
        ),
        Index(
            "uq_persona_accounts_responder_subject",
            "scope_id",
            "responder_id",
            unique=True,
            postgresql_where=text("persona = 'responder'"),
        ),
        Index(
            "ix_persona_accounts_scope_persona_status",
            "scope_id",
            "persona",
            "status",
            "account_id",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    persona: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(128))
    responder_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    enrollment_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class PersonaSessionRow(Base):
    """Revocable installation session containing token hashes only."""

    __tablename__ = "persona_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "account_id"],
            ["persona_accounts.scope_id", "persona_accounts.account_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "access_token_hash",
            name="uq_persona_sessions_access_token_hash",
        ),
        UniqueConstraint(
            "scope_id",
            "refresh_token_hash",
            name="uq_persona_sessions_refresh_token_hash",
        ),
        UniqueConstraint(
            "scope_id",
            "session_id",
            "account_id",
            name="uq_persona_sessions_exact_account",
        ),
        CheckConstraint(
            "access_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_persona_sessions_access_token_hash",
        ),
        CheckConstraint(
            "refresh_token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_persona_sessions_refresh_token_hash",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_persona_sessions_status",
        ),
        CheckConstraint(
            "issued_at <= rotated_at AND rotated_at < access_expires_at "
            "AND access_expires_at <= refresh_expires_at",
            name="ck_persona_sessions_expiration_order",
        ),
        CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_persona_sessions_revocation_state",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_persona_sessions_revoked_at",
        ),
        Index(
            "uq_persona_sessions_account_installation_active",
            "scope_id",
            "account_id",
            "installation_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_persona_sessions_scope_account_status",
            "scope_id",
            "account_id",
            "status",
            "refresh_expires_at",
            "session_id",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    installation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    access_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    access_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    refresh_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResponderSkillRow(Base):
    """One explicit, allowlisted responder qualification."""

    __tablename__ = "responder_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "skill IN ('first_aid', 'cpr', 'aed')",
            name="ck_responder_skills_skill",
        ),
        Index(
            "ix_responder_skills_scope_skill",
            "scope_id",
            "skill",
            "certified_until",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    skill: Mapped[str] = mapped_column(String(32), primary_key=True)
    certified_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class ResponderAvailabilityRow(Base):
    """Mutable current availability projection for one responder."""

    __tablename__ = "responder_availability"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="CASCADE",
        ),
        Index(
            "ix_responder_availability_scope_available_updated",
            "scope_id",
            "available",
            desc("updated_at"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class ResponderLocationRow(Base):
    """Immutable point-in-time responder location used for proximity search."""

    __tablename__ = "responder_locations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "horizontal_accuracy_m NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND horizontal_accuracy_m BETWEEN 0 AND 10000",
            name="ck_responder_locations_accuracy",
        ),
        CheckConstraint(
            "ST_SRID(location::geometry) = 4326 AND "
            "GeometryType(location::geometry) = 'POINT'",
            name="ck_responder_locations_point",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responder_locations_not_simulated",
        ),
        Index(
            "ix_responder_locations_scope_responder_captured",
            "scope_id",
            "responder_id",
            desc("captured_at"),
            desc("location_id"),
        ),
        Index(
            "ix_responder_locations_location_gist",
            "location",
            postgresql_using="gist",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    location_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    horizontal_accuracy_m: Mapped[float] = mapped_column(Float, nullable=False)
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class AEDSiteRow(Base):
    """A verified static venue AED location."""

    __tablename__ = "aed_sites"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "char_length(name) BETWEEN 1 AND 160",
            name="ck_aed_sites_name",
        ),
        CheckConstraint(
            "char_length(location_description) BETWEEN 1 AND 256",
            name="ck_aed_sites_location_description",
        ),
        CheckConstraint(
            "char_length(access_instructions) BETWEEN 1 AND 512",
            name="ck_aed_sites_access_instructions",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_aed_sites_updated_at",
        ),
        CheckConstraint(
            "ST_SRID(location::geometry) = 4326 AND "
            "GeometryType(location::geometry) = 'POINT'",
            name="ck_aed_sites_point",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_aed_sites_not_simulated",
        ),
        Index("ix_aed_sites_scope_active", "scope_id", "active"),
        Index(
            "ix_aed_sites_location_gist",
            "location",
            postgresql_using="gist",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    aed_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    location_description: Mapped[str] = mapped_column(String(256), nullable=False)
    access_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    publicly_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    location: Mapped[Any] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class ResponderInvitationRow(Base):
    """Mutable invitation projection with a redacted candidate snapshot."""

    __tablename__ = "responder_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            "responder_id",
            name="uq_responder_invitations_incident_responder",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            "rank",
            name="uq_responder_invitations_incident_rank",
        ),
        UniqueConstraint(
            "scope_id",
            "invitation_id",
            "incident_id",
            "responder_id",
            name="uq_responder_invitations_exact_link",
        ),
        CheckConstraint(
            "rank >= 1",
            name="ck_responder_invitations_rank",
        ),
        CheckConstraint(
            "status IN ('pending', 'declined', 'accepted')",
            name="ck_responder_invitations_status",
        ),
        CheckConstraint(
            "distance_m NOT IN ('NaN'::double precision, "
            "'Infinity'::double precision, '-Infinity'::double precision) "
            "AND distance_m >= 0",
            name="ck_responder_invitations_distance",
        ),
        CheckConstraint(
            "jsonb_typeof(candidate_snapshot) = 'object'",
            name="ck_responder_invitations_candidate_snapshot",
        ),
        CheckConstraint(
            "(status = 'pending' AND responded_at IS NULL) OR "
            "(status IN ('declined', 'accepted') AND responded_at IS NOT NULL)",
            name="ck_responder_invitations_response_state",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responder_invitations_not_simulated",
        ),
        Index(
            "uq_responder_invitations_incident_pending",
            "scope_id",
            "incident_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index(
            "uq_responder_invitations_incident_accepted",
            "scope_id",
            "incident_id",
            unique=True,
            postgresql_where=text("status = 'accepted'"),
        ),
        Index(
            "ix_responder_invitations_scope_incident_rank",
            "scope_id",
            "incident_id",
            "rank",
        ),
        Index(
            "ix_responder_invitations_scope_responder_status_created",
            "scope_id",
            "responder_id",
            "status",
            desc("created_at"),
            "incident_id",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    candidate_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class ResponderInvitationResponseRow(Base):
    """Append-only, idempotent responder decision receipt."""

    __tablename__ = "responder_invitation_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "invitation_id", "incident_id", "responder_id"],
            [
                "responder_invitations.scope_id",
                "responder_invitations.invitation_id",
                "responder_invitations.incident_id",
                "responder_invitations.responder_id",
            ],
            name="fk_responder_invitation_responses_exact_invitation",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "invitation_id",
            name="uq_responder_invitation_responses_invitation",
        ),
        UniqueConstraint(
            "scope_id",
            "response_id",
            "invitation_id",
            "incident_id",
            "responder_id",
            name="uq_responder_invitation_responses_exact_link",
        ),
        CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_responder_invitation_responses_fingerprint",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_responder_invitation_responses_schema_version",
        ),
        CheckConstraint(
            "decision IN ('accept', 'decline')",
            name="ck_responder_invitation_responses_decision",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object'",
            name="ck_responder_invitation_responses_payload",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responder_invitation_responses_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    response_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    server_received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class ResponderAssignmentRow(Base):
    """Append-only accepted-responder, AED, and static-route assignment."""

    __tablename__ = "responder_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "aed_id"],
            ["aed_sites.scope_id", "aed_sites.aed_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "invitation_id", "incident_id", "responder_id"],
            [
                "responder_invitations.scope_id",
                "responder_invitations.invitation_id",
                "responder_invitations.incident_id",
                "responder_invitations.responder_id",
            ],
            name="fk_responder_assignments_exact_invitation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "scope_id",
                "response_id",
                "invitation_id",
                "incident_id",
                "responder_id",
            ],
            [
                "responder_invitation_responses.scope_id",
                "responder_invitation_responses.response_id",
                "responder_invitation_responses.invitation_id",
                "responder_invitation_responses.incident_id",
                "responder_invitation_responses.responder_id",
            ],
            name="fk_responder_assignments_exact_response",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            name="uq_responder_assignments_incident",
        ),
        UniqueConstraint(
            "scope_id",
            "invitation_id",
            name="uq_responder_assignments_invitation",
        ),
        UniqueConstraint(
            "scope_id",
            "response_id",
            name="uq_responder_assignments_response",
        ),
        UniqueConstraint(
            "scope_id",
            "assignment_id",
            "incident_id",
            "responder_id",
            name="uq_responder_assignments_exact_link",
        ),
        CheckConstraint(
            "jsonb_typeof(static_route) = 'object'",
            name="ck_responder_assignments_static_route",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responder_assignments_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    assignment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    aed_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    response_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    static_route: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ResponderAssignmentRevocationRow(Base):
    """Append-only revocation of exact responder access after resolution."""

    __tablename__ = "responder_assignment_revocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "assignment_id", "incident_id", "responder_id"],
            [
                "responder_assignments.scope_id",
                "responder_assignments.assignment_id",
                "responder_assignments.incident_id",
                "responder_assignments.responder_id",
            ],
            name="fk_assignment_revocations_exact_assignment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "resolution_id", "incident_id"],
            [
                "incident_resolution_receipts.scope_id",
                "incident_resolution_receipts.resolution_id",
                "incident_resolution_receipts.incident_id",
            ],
            name="fk_assignment_revocations_exact_resolution",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "transition_id", "incident_id"],
            [
                "incident_state_transitions.scope_id",
                "incident_state_transitions.transition_id",
                "incident_state_transitions.incident_id",
            ],
            name="fk_assignment_revocations_exact_transition",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "assignment_id",
            name="uq_assignment_revocations_assignment",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            name="uq_assignment_revocations_incident",
        ),
        UniqueConstraint(
            "scope_id",
            "resolution_id",
            name="uq_assignment_revocations_resolution",
        ),
        UniqueConstraint(
            "scope_id",
            "transition_id",
            name="uq_assignment_revocations_transition",
        ),
        CheckConstraint(
            "reason IN ('close', 'handoff')",
            name="ck_assignment_revocations_reason",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_assignment_revocations_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    revocation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    assignment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    resolution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    transition_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(16), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class ProtocolPresentationRow(Base):
    """Append-only fixed-protocol snapshot attached to an accepted assignment."""

    __tablename__ = "protocol_presentations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "assignment_id", "incident_id", "responder_id"],
            [
                "responder_assignments.scope_id",
                "responder_assignments.assignment_id",
                "responder_assignments.incident_id",
                "responder_assignments.responder_id",
            ],
            name="fk_protocol_presentations_exact_assignment",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "incident_id",
            name="uq_protocol_presentations_incident",
        ),
        UniqueConstraint(
            "scope_id",
            "assignment_id",
            name="uq_protocol_presentations_assignment",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_protocol_presentations_schema_version",
        ),
        CheckConstraint(
            "char_length(btrim(protocol_id)) BETWEEN 1 AND 128",
            name="ck_protocol_presentations_protocol_id",
        ),
        CheckConstraint(
            "char_length(btrim(protocol_version)) BETWEEN 1 AND 64",
            name="ck_protocol_presentations_protocol_version",
        ),
        CheckConstraint(
            "emergency_kind IN ('fall', 'manual_sos')",
            name="ck_protocol_presentations_emergency_kind",
        ),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_protocol_presentations_content_sha256",
        ),
        CheckConstraint(
            "jsonb_typeof(protocol_snapshot) = 'object'",
            name="ck_protocol_presentations_snapshot",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_protocol_presentations_not_simulated",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    presentation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    assignment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    protocol_id: Mapped[str] = mapped_column(String(128), nullable=False)
    protocol_version: Mapped[str] = mapped_column(String(64), nullable=False)
    emergency_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    presented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    protocol_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class ResponderPushRegistrationRow(Base):
    """One consented, write-only APNs destination for a responder."""

    __tablename__ = "responder_push_registrations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "responder_id"],
            ["responders.scope_id", "responders.responder_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "installation_id",
            name="uq_responder_push_registrations_installation",
        ),
        CheckConstraint(
            "platform = 'apns'",
            name="ck_responder_push_registrations_platform",
        ),
        CheckConstraint(
            "environment IN ('sandbox', 'production')",
            name="ck_responder_push_registrations_environment",
        ),
        CheckConstraint(
            "device_token_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_responder_push_registrations_token_hash",
        ),
        CheckConstraint(
            "octet_length(device_token_ciphertext) > 0",
            name="ck_responder_push_registrations_token_ciphertext",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_responder_push_registrations_status",
        ),
        CheckConstraint(
            "updated_at >= authorized_at",
            name="ck_responder_push_registrations_updated_at",
        ),
        CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) OR "
            "(status = 'revoked' AND revoked_at IS NOT NULL)",
            name="ck_responder_push_registrations_revocation_state",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= authorized_at",
            name="ck_responder_push_registrations_revoked_at",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_responder_push_registrations_not_simulated",
        ),
        Index(
            "uq_responder_push_registrations_responder_active",
            "scope_id",
            "responder_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_responder_push_registrations_destination_active",
            "scope_id",
            "environment",
            "device_token_sha256",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    registration_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    installation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    environment: Mapped[str] = mapped_column(String(16), nullable=False)
    device_token_ciphertext: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    device_token_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class NotificationOutboxRow(Base):
    """Durable logical responder notification, unique per invitation."""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "invitation_id", "incident_id", "responder_id"],
            [
                "responder_invitations.scope_id",
                "responder_invitations.invitation_id",
                "responder_invitations.incident_id",
                "responder_invitations.responder_id",
            ],
            name="fk_notification_outbox_exact_invitation",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "invitation_id",
            "channel",
            "template",
            name="uq_notification_outbox_logical_delivery",
        ),
        UniqueConstraint(
            "scope_id",
            "notification_id",
            "invitation_id",
            name="uq_notification_outbox_exact_link",
        ),
        CheckConstraint(
            "channel = 'apns'",
            name="ck_notification_outbox_channel",
        ),
        CheckConstraint(
            "template = 'responder_invitation_v1'",
            name="ck_notification_outbox_template",
        ),
        CheckConstraint(
            "status IN ("
            "'pending', 'provider_accepted', 'permanent_failed', "
            "'unavailable', 'unknown'"
            ")",
            name="ck_notification_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_notification_outbox_attempt_count",
        ),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND "
            "payload ?& ARRAY["
            "'schema_version', 'kind', 'incident_id', 'invitation_id'"
            "] AND "
            "payload - 'schema_version' - 'kind' - 'incident_id' - "
            "'invitation_id' = '{}'::jsonb AND "
            "payload -> 'schema_version' = '1'::jsonb AND "
            "payload ->> 'kind' = 'responder_invitation' AND "
            "payload ->> 'incident_id' = incident_id::text AND "
            "payload ->> 'invitation_id' = invitation_id::text",
            name="ck_notification_outbox_payload",
        ),
        CheckConstraint(
            "updated_at >= created_at",
            name="ck_notification_outbox_updated_at",
        ),
        CheckConstraint(
            "next_attempt_at >= created_at",
            name="ck_notification_outbox_next_attempt_at",
        ),
        CheckConstraint(
            "(lease_token IS NULL) = (lease_until IS NULL)",
            name="ck_notification_outbox_lease_state",
        ),
        CheckConstraint(
            "(status = 'pending' AND finalized_at IS NULL "
            "AND provider_message_id IS NULL AND last_error_code IS NULL) OR "
            "(status = 'provider_accepted' AND finalized_at IS NOT NULL "
            "AND provider_message_id = notification_id "
            "AND last_error_code IS NULL "
            "AND attempt_count >= 1) OR "
            "(status IN ('permanent_failed', 'unavailable', 'unknown') "
            "AND finalized_at IS NOT NULL AND provider_message_id IS NULL "
            "AND last_error_code IS NOT NULL)",
            name="ck_notification_outbox_terminal_state",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR last_error_code IN ("
            "'active_push_registration_unavailable', 'bad_apns_topic', "
            "'bad_device_token', 'device_token_not_for_topic', "
            "'device_token_unreadable', 'device_token_unregistered', "
            "'incident_not_escalating', 'invalid_apns_topic', "
            "'invalid_device_token', 'invitation_not_pending', "
            "'missing_apns_topic', 'payload_empty', 'payload_too_large', "
            "'provider_authentication_failed', 'provider_delayed_retry', "
            "'provider_outcome_unknown', 'provider_rate_limited', "
            "'provider_rejected', 'provider_response_invalid', "
            "'provider_unavailable', "
            "'responder_not_notification_allowlisted'"
            ")",
            name="ck_notification_outbox_error_code",
        ),
        CheckConstraint(
            "finalized_at IS NULL OR finalized_at >= created_at",
            name="ck_notification_outbox_finalized_at",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_notification_outbox_not_simulated",
        ),
        Index(
            "ix_notification_outbox_due",
            "scope_id",
            "status",
            "next_attempt_at",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    notification_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    responder_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    template: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class NotificationDeliveryAttemptRow(Base):
    """Append-only provider-attempt receipt without destination secrets."""

    __tablename__ = "notification_delivery_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "notification_id", "invitation_id"],
            [
                "notification_outbox.scope_id",
                "notification_outbox.notification_id",
                "notification_outbox.invitation_id",
            ],
            name="fk_notification_attempts_exact_outbox",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "scope_id",
            "invitation_id",
            "attempt_number",
            name="uq_notification_attempts_invitation_sequence",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="ck_notification_attempts_number",
        ),
        CheckConstraint(
            "outcome IN ("
            "'provider_accepted', 'transient_failure', "
            "'permanent_failure', 'unknown'"
            ")",
            name="ck_notification_attempts_outcome",
        ),
        CheckConstraint(
            "responded_at >= requested_at",
            name="ck_notification_attempts_responded_at",
        ),
        CheckConstraint(
            "(outcome = 'provider_accepted' "
            "AND provider_message_id = notification_id "
            "AND error_code IS NULL) OR "
            "(outcome <> 'provider_accepted' AND provider_message_id IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_notification_attempts_outcome_metadata",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'active_push_registration_unavailable', 'bad_apns_topic', "
            "'bad_device_token', 'device_token_not_for_topic', "
            "'device_token_unreadable', 'device_token_unregistered', "
            "'incident_not_escalating', 'invalid_apns_topic', "
            "'invalid_device_token', 'invitation_not_pending', "
            "'missing_apns_topic', 'payload_empty', 'payload_too_large', "
            "'provider_authentication_failed', 'provider_delayed_retry', "
            "'provider_outcome_unknown', 'provider_rate_limited', "
            "'provider_rejected', 'provider_response_invalid', "
            "'provider_unavailable', "
            "'responder_not_notification_allowlisted'"
            ")",
            name="ck_notification_attempts_error_code",
        ),
        CheckConstraint(
            "simulated = false",
            name="ck_notification_attempts_not_simulated",
        ),
        Index(
            "ix_notification_attempts_scope_invitation",
            "scope_id",
            "invitation_id",
            "attempt_number",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    attempt_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    notification_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    invitation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    responded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    simulated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )


class AgentActivePolicyRow(Base):
    """Scope-local policy pointer checked on every proxied tool invocation."""

    __tablename__ = "agent_active_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "activated_by_account_id"],
            ["persona_accounts.scope_id", "persona_accounts.account_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "policy_id ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_agent_active_policies_id",
        ),
        CheckConstraint(
            "policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_agent_active_policies_version",
        ),
        CheckConstraint(
            "policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_active_policies_sha256",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_agent_active_policies_revision",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    activated_by_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )


class AgentRunRow(Base):
    """Durable live-run lifecycle with one controlled terminal transition."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "incident_id"],
            ["incidents.scope_id", "incidents.incident_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "scope_id",
                "requested_by_session_id",
                "requested_by_account_id",
            ],
            [
                "persona_sessions.scope_id",
                "persona_sessions.session_id",
                "persona_sessions.account_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "run_id",
            "incident_id",
            name="uq_agent_runs_exact_incident",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="ck_agent_runs_schema_version",
        ),
        CheckConstraint(
            "incident_state_version >= 1",
            name="ck_agent_runs_state_version",
        ),
        CheckConstraint(
            "objective = 'coordinate_emergency_response'",
            name="ck_agent_runs_objective",
        ),
        CheckConstraint(
            "policy_id ~ '^[a-z][a-z0-9_-]{0,63}$'",
            name="ck_agent_runs_policy_id",
        ),
        CheckConstraint(
            "policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'",
            name="ck_agent_runs_policy_version",
        ),
        CheckConstraint(
            "policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_runs_policy_sha256",
        ),
        CheckConstraint(
            "char_length(model_id) BETWEEN 1 AND 200 "
            "AND model_id = btrim(model_id)",
            name="ck_agent_runs_model_id",
        ),
        CheckConstraint(
            "sandbox IN ('in_process', 'nemoclaw', 'docker')",
            name="ck_agent_runs_sandbox",
        ),
        CheckConstraint(
            "status IN ('running', 'completed', 'manual_required')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint(
            "created_at >= requested_at",
            name="ck_agent_runs_creation_time",
        ),
        CheckConstraint(
            "lease_expires_at > created_at",
            name="ck_agent_runs_lease",
        ),
        CheckConstraint(
            "max_total_tool_calls BETWEEN 1 AND 50 AND "
            "max_mutating_tool_calls BETWEEN 0 AND 10 AND "
            "max_mutating_tool_calls <= max_total_tool_calls",
            name="ck_agent_runs_tool_budget",
        ),
        CheckConstraint(
            "total_tool_calls BETWEEN 0 AND max_total_tool_calls AND "
            "mutating_tool_calls BETWEEN 0 AND max_mutating_tool_calls AND "
            "mutating_tool_calls <= total_tool_calls",
            name="ck_agent_runs_tool_usage",
        ),
        CheckConstraint(
            "jsonb_typeof(tool_trace) = 'array'",
            name="ck_agent_runs_tool_trace",
        ),
        CheckConstraint(
            "(status = 'running' AND started_at IS NULL "
            "AND finished_at IS NULL AND tool_trace = '[]'::jsonb "
            "AND action_summary IS NULL AND failure_code IS NULL) OR "
            "(status = 'completed' AND started_at IS NOT NULL "
            "AND finished_at >= started_at AND started_at >= requested_at "
            "AND finished_at <= lease_expires_at "
            "AND action_summary IS NOT NULL AND failure_code IS NULL) OR "
            "(status = 'manual_required' AND started_at IS NOT NULL "
            "AND finished_at >= started_at AND started_at >= requested_at "
            "AND finished_at <= lease_expires_at "
            "AND action_summary IS NULL AND failure_code IS NOT NULL)",
            name="ck_agent_runs_result_state",
        ),
        CheckConstraint(
            "action_summary IS NULL OR "
            "char_length(action_summary) BETWEEN 1 AND 500",
            name="ck_agent_runs_action_summary",
        ),
        CheckConstraint(
            "failure_code IS NULL OR failure_code IN ("
            "'model_timeout', 'model_unavailable', 'invalid_model_output', "
            "'tool_denied', 'tool_failed', 'agent_requested_human', "
            "'policy_invalid', 'runner_error')",
            name="ck_agent_runs_failure_code",
        ),
        Index(
            "uq_agent_runs_scope_incident_running",
            "scope_id",
            "incident_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_agent_runs_scope_incident_requested",
            "scope_id",
            "incident_id",
            desc("requested_at"),
            desc("run_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    incident_state_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    objective: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    requested_by_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    requested_by_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    max_total_tool_calls: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_mutating_tool_calls: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
    )
    total_tool_calls: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    mutating_tool_calls: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    sandbox: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tool_trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    action_summary: Mapped[str | None] = mapped_column(String(500))
    failure_code: Mapped[str | None] = mapped_column(String(64))


class AgentRunToolBudgetRow(Base):
    """Pinned per-tool ceiling and atomically reserved call count for one run."""

    __tablename__ = "agent_run_tool_budgets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "run_id", "incident_id"],
            ["agent_runs.scope_id", "agent_runs.run_id", "agent_runs.incident_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "tool_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_agent_run_tool_budgets_name",
        ),
        CheckConstraint(
            "effect IN ('read', 'mutate')",
            name="ck_agent_run_tool_budgets_effect",
        ),
        CheckConstraint(
            "max_calls BETWEEN 1 AND 20",
            name="ck_agent_run_tool_budgets_max_calls",
        ),
        CheckConstraint(
            "calls_used BETWEEN 0 AND max_calls",
            name="ck_agent_run_tool_budgets_usage",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    effect: Mapped[str] = mapped_column(String(16), nullable=False)
    max_calls: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    calls_used: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
    )


class AgentToolProxyAuditRow(Base):
    """Append-only proxy metadata; request and result bodies are never stored."""

    __tablename__ = "agent_tool_proxy_audits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "requested_scope_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_agent_tool_audits_requested_scope",
        ),
        CheckConstraint(
            "granted_scope_id IS NULL OR "
            "granted_scope_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",
            name="ck_agent_tool_audits_granted_scope",
        ),
        CheckConstraint(
            "requested_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_audits_requested_policy",
        ),
        CheckConstraint(
            "granted_policy_sha256 IS NULL OR "
            "granted_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_audits_granted_policy",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_audits_request_hash",
        ),
        CheckConstraint(
            "result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_audits_result_hash",
        ),
        CheckConstraint(
            "tool_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_agent_tool_audits_tool_name",
        ),
        CheckConstraint(
            "effect IS NULL OR effect IN ('read', 'mutate')",
            name="ck_agent_tool_audits_effect",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'replayed', 'denied', 'failed')",
            name="ck_agent_tool_audits_status",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'invalid_capability', 'expired_capability', 'wrong_run', "
            "'wrong_scope', 'wrong_incident', 'policy_mismatch', "
            "'run_not_active', 'tool_not_registered', 'tool_not_allowed', "
            "'tool_budget_exceeded', 'invalid_arguments', "
            "'stale_state', 'incident_not_active', 'idempotency_required', "
            "'idempotency_conflict', 'idempotency_capacity_exceeded', "
            "'idempotency_in_doubt', 'application_failed', 'invalid_result', "
            "'audit_unavailable')",
            name="ck_agent_tool_audits_error_code",
        ),
        CheckConstraint(
            "(granted_scope_id IS NULL AND granted_run_id IS NULL "
            "AND granted_incident_id IS NULL "
            "AND granted_state_version IS NULL "
            "AND granted_policy_sha256 IS NULL) OR "
            "(granted_scope_id IS NOT NULL AND granted_run_id IS NOT NULL "
            "AND granted_incident_id IS NOT NULL "
            "AND granted_state_version >= 1 "
            "AND granted_policy_sha256 IS NOT NULL)",
            name="ck_agent_tool_audits_grant",
        ),
        CheckConstraint(
            "(status IN ('completed', 'replayed') "
            "AND result_sha256 IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'started' AND result_sha256 IS NULL "
            "AND error_code IS NULL AND effect = 'mutate') OR "
            "(status IN ('denied', 'failed') AND result_sha256 IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_agent_tool_audits_outcome",
        ),
        Index(
            "ix_agent_tool_audits_scope_run_time",
            "scope_id",
            "requested_run_id",
            "occurred_at",
            "audit_id",
        ),
        Index(
            "ix_agent_tool_audits_scope_granted_run_time",
            "scope_id",
            "granted_run_id",
            "occurred_at",
            "audit_id",
            postgresql_where=text("granted_run_id IS NOT NULL"),
        ),
        Index(
            "ix_agent_tool_audits_scope_incident_time",
            "scope_id",
            "requested_incident_id",
            "occurred_at",
            "audit_id",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    audit_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    invocation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    requested_scope_id: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    requested_incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    requested_policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_scope_id: Mapped[str | None] = mapped_column(String(128))
    granted_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    granted_incident_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    granted_state_version: Mapped[int | None] = mapped_column(Integer)
    granted_policy_sha256: Mapped[str | None] = mapped_column(String(64))
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    effect: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True)
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))


class AgentToolIdempotencyRow(Base):
    """Durable replay outcome or conservative in-doubt mutation marker."""

    __tablename__ = "agent_tool_idempotency"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id", "run_id", "incident_id"],
            ["agent_runs.scope_id", "agent_runs.run_id", "agent_runs.incident_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "tool_name ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_agent_tool_idempotency_tool_name",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_agent_tool_idempotency_request_hash",
        ),
        CheckConstraint(
            "status IN ('in_doubt', 'completed')",
            name="ck_agent_tool_idempotency_status",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="ck_agent_tool_idempotency_expiration",
        ),
        CheckConstraint(
            "(status = 'in_doubt' AND result IS NULL "
            "AND completed_at IS NULL) OR "
            "(status = 'completed' AND result IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= created_at)",
            name="ck_agent_tool_idempotency_outcome",
        ),
        Index(
            "ix_agent_tool_idempotency_scope_expiry",
            "scope_id",
            "expires_at",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    incident_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[Any | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class EvolutionCandidateVersionRow(Base):
    """Immutable full release identity and exact trusted-host canonical bytes."""

    __tablename__ = "evolution_candidate_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "target_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_target_sha256",
        ),
        CheckConstraint(
            "release_kind IN ('playbook_only', 'candidate_or_policy_change')",
            name="ck_evolution_candidate_versions_release_kind",
        ),
        CheckConstraint(
            "candidate_bundle_sha256 ~ '^[0-9a-f]{64}$' AND "
            "candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_candidate_hashes",
        ),
        CheckConstraint(
            "policy_id ~ '^[a-z][a-z0-9_-]{0,63}$' AND "
            "policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$' AND "
            "policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_policy",
        ),
        CheckConstraint(
            "playbook_sha256 ~ '^[0-9a-f]{64}$' AND "
            "delta_log_sha256 ~ '^[0-9a-f]{64}$' AND "
            "improver_sha256 ~ '^[0-9a-f]{64}$' AND "
            "generator_role_sha256 ~ '^[0-9a-f]{64}$' AND "
            "generator_model_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_release_hashes",
        ),
        CheckConstraint(
            "development_report_sha256 ~ '^[0-9a-f]{64}$' AND "
            "development_report_signature_sha256 ~ '^[0-9a-f]{64}$' AND "
            "protected_validation_report_sha256 ~ '^[0-9a-f]{64}$' AND "
            "protected_validation_report_signature_sha256 "
            "~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_evidence_hashes",
        ),
        CheckConstraint(
            "final_report_sha256 ~ '^[0-9a-f]{64}$' AND "
            "final_cadence_receipt_sha256 ~ '^[0-9a-f]{64}$' AND "
            "final_cadence_artifact_sha256 ~ '^[0-9a-f]{64}$' AND "
            "paired_report_sha256 ~ '^[0-9a-f]{64}$' AND "
            "paired_report_artifact_sha256 ~ '^[0-9a-f]{64}$' AND "
            "ace_release_sha256 ~ '^[0-9a-f]{64}$' AND "
            "ace_release_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_final_hashes",
        ),
        CheckConstraint(
            "active_baseline_version_sha256 ~ '^[0-9a-f]{64}$' AND "
            "active_baseline_candidate_sha256 ~ '^[0-9a-f]{64}$' AND "
            "active_baseline_policy_id ~ '^[a-z][a-z0-9_-]{0,63}$' AND "
            "active_baseline_policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$' AND "
            "active_baseline_policy_sha256 ~ '^[0-9a-f]{64}$' AND "
            "active_baseline_playbook_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_candidate_versions_baseline_hashes",
        ),
        CheckConstraint(
            "(release_kind = 'playbook_only' AND "
            "promotion_evidence_sha256 IS NULL AND "
            "evidence_canonical_bytes IS NULL AND "
            "candidate_sha256 = active_baseline_candidate_sha256 AND "
            "policy_id = active_baseline_policy_id AND "
            "policy_version = active_baseline_policy_version AND "
            "policy_sha256 = active_baseline_policy_sha256 AND "
            "playbook_sha256 <> active_baseline_playbook_sha256) OR "
            "(release_kind = 'candidate_or_policy_change' AND "
            "promotion_evidence_sha256 ~ '^[0-9a-f]{64}$' AND "
            "evidence_canonical_bytes IS NOT NULL AND "
            "candidate_sha256 <> active_baseline_candidate_sha256)",
            name="ck_evolution_candidate_versions_evidence_mode",
        ),
        CheckConstraint(
            "octet_length(target_canonical_bytes) BETWEEN 1 AND 16000000 AND "
            "(evidence_canonical_bytes IS NULL OR "
            "octet_length(evidence_canonical_bytes) BETWEEN 1 AND 16000000) AND "
            "octet_length(ace_release_canonical_bytes) "
            "BETWEEN 1 AND 16000000 AND "
            "octet_length(paired_report_canonical_bytes) "
            "BETWEEN 1 AND 16000000 AND "
            "octet_length(final_cadence_canonical_bytes) "
            "BETWEEN 1 AND 16000000",
            name="ck_evolution_candidate_versions_byte_bounds",
        ),
        Index(
            "ix_evolution_candidate_versions_scope_archived",
            "scope_id",
            desc("archived_at"),
            desc("target_sha256"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    target_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    release_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    playbook_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    delta_log_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    improver_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_role_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generator_model_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    promotion_evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    development_report_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    development_report_signature_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    protected_validation_report_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    protected_validation_report_signature_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    final_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    final_cadence_receipt_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    final_cadence_artifact_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    paired_report_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    paired_report_artifact_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    ace_release_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ace_release_artifact_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    active_baseline_version_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    active_baseline_candidate_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    active_baseline_policy_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    active_baseline_policy_version: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    active_baseline_policy_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    active_baseline_playbook_sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    target_canonical_bytes: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    evidence_canonical_bytes: Mapped[bytes | None] = mapped_column(LargeBinary)
    ace_release_canonical_bytes: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    paired_report_canonical_bytes: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    final_cadence_canonical_bytes: Mapped[bytes] = mapped_column(
        LargeBinary,
        nullable=False,
    )
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class EvolutionActiveVersionRow(Base):
    """Scope-local monotonic pointer to one complete retained release."""

    __tablename__ = "evolution_active_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "active_version_sha256"],
            [
                "evolution_candidate_versions.scope_id",
                "evolution_candidate_versions.target_sha256",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "previous_version_sha256"],
            [
                "evolution_candidate_versions.scope_id",
                "evolution_candidate_versions.target_sha256",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "activated_by_session_id", "activated_by_account_id"],
            [
                "persona_sessions.scope_id",
                "persona_sessions.session_id",
                "persona_sessions.account_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "active_version_sha256 ~ '^[0-9a-f]{64}$' AND "
            "active_candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_active_versions_active_hashes",
        ),
        CheckConstraint(
            "previous_version_sha256 IS NULL OR "
            "previous_version_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_active_versions_previous_version",
        ),
        CheckConstraint(
            "previous_candidate_sha256 IS NULL OR "
            "previous_candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_active_versions_previous_candidate",
        ),
        CheckConstraint(
            "(previous_version_sha256 IS NULL) = "
            "(previous_candidate_sha256 IS NULL)",
            name="ck_evolution_active_versions_previous_pair",
        ),
        CheckConstraint(
            "revision >= 0",
            name="ck_evolution_active_versions_revision",
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    active_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    active_candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_version_sha256: Mapped[str | None] = mapped_column(String(64))
    previous_candidate_sha256: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    activated_by_account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    activated_by_session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )


class EvolutionCommandApprovalRow(Base):
    """Authenticated single-use command approval, inserted already consumed."""

    __tablename__ = "evolution_command_approvals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "target_version_sha256"],
            [
                "evolution_candidate_versions.scope_id",
                "evolution_candidate_versions.target_sha256",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "session_id", "account_id"],
            [
                "persona_sessions.scope_id",
                "persona_sessions.session_id",
                "persona_sessions.account_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action IN ('promote', 'rollback')",
            name="ck_evolution_command_approvals_action",
        ),
        CheckConstraint(
            "target_version_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_command_approvals_target",
        ),
        CheckConstraint(
            "(action = 'promote' AND (evidence_sha256 IS NULL OR "
            "evidence_sha256 ~ '^[0-9a-f]{64}$')) OR "
            "(action = 'rollback' AND evidence_sha256 IS NULL)",
            name="ck_evolution_command_approvals_evidence",
        ),
        CheckConstraint(
            "expected_pointer_revision >= 0 AND "
            "resulting_pointer_revision = expected_pointer_revision + 1",
            name="ck_evolution_command_approvals_revisions",
        ),
        CheckConstraint(
            "consumed_at >= approved_at",
            name="ck_evolution_command_approvals_consumed_at",
        ),
        Index(
            "ix_evolution_command_approvals_scope_consumed",
            "scope_id",
            desc("consumed_at"),
            desc("approval_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    approval_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    target_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    expected_pointer_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    consumed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    resulting_pointer_revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )


class EvolutionPromotionEventRow(Base):
    """Append-only proof of one committed full-release pointer transition."""

    __tablename__ = "evolution_promotion_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["scope_id"],
            ["demo_scopes.scope_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["scope_id", "approval_id"],
            [
                "evolution_command_approvals.scope_id",
                "evolution_command_approvals.approval_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "from_version_sha256"],
            [
                "evolution_candidate_versions.scope_id",
                "evolution_candidate_versions.target_sha256",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "to_version_sha256"],
            [
                "evolution_candidate_versions.scope_id",
                "evolution_candidate_versions.target_sha256",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["scope_id", "session_id", "account_id"],
            [
                "persona_sessions.scope_id",
                "persona_sessions.session_id",
                "persona_sessions.account_id",
            ],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "scope_id",
            "approval_id",
            name="uq_evolution_promotion_events_approval",
        ),
        UniqueConstraint(
            "scope_id",
            "pointer_revision",
            name="uq_evolution_promotion_events_pointer_revision",
        ),
        CheckConstraint(
            "action IN ('promote', 'rollback')",
            name="ck_evolution_promotion_events_action",
        ),
        CheckConstraint(
            "from_version_sha256 ~ '^[0-9a-f]{64}$' AND "
            "to_version_sha256 ~ '^[0-9a-f]{64}$' AND "
            "from_candidate_sha256 ~ '^[0-9a-f]{64}$' AND "
            "to_candidate_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evolution_promotion_events_hashes",
        ),
        CheckConstraint(
            "from_version_sha256 <> to_version_sha256",
            name="ck_evolution_promotion_events_changed",
        ),
        CheckConstraint(
            "(action = 'promote' AND (evidence_sha256 IS NULL OR "
            "evidence_sha256 ~ '^[0-9a-f]{64}$')) OR "
            "(action = 'rollback' AND evidence_sha256 IS NULL)",
            name="ck_evolution_promotion_events_evidence",
        ),
        CheckConstraint(
            "pointer_revision >= 1",
            name="ck_evolution_promotion_events_revision",
        ),
        Index(
            "ix_evolution_promotion_events_scope_occurred",
            "scope_id",
            desc("occurred_at"),
            desc("event_id"),
        ),
    )

    scope_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    event_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    approval_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    from_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    to_version_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    from_candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    to_candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_sha256: Mapped[str | None] = mapped_column(String(64))
    pointer_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    session_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
