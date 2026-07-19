"""Create scoped health persistence tables.

Revision ID: 0001_health_persistence
Revises: None
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_health_persistence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE demo_scopes (
        scope_id UUID NOT NULL,
        status VARCHAR(16) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        closed_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (scope_id),
        CONSTRAINT ck_demo_scopes_status
            CHECK (status IN ('active', 'closed')),
        CONSTRAINT ck_demo_scopes_expiration
            CHECK (expires_at > created_at),
        CONSTRAINT ck_demo_scopes_closed_at CHECK (
            (status = 'active' AND closed_at IS NULL) OR
            (status = 'closed' AND closed_at IS NOT NULL)
        )
    )
    """,
    """
    CREATE INDEX ix_demo_scopes_status_expires_at
    ON demo_scopes (status, expires_at)
    """,
    """
    CREATE TABLE health_metric_batches (
        scope_id UUID NOT NULL,
        batch_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        sent_at TIMESTAMP WITH TIME ZONE NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        accepted_metric_ids UUID[] NOT NULL,
        duplicate_metric_ids UUID[] NOT NULL,
        PRIMARY KEY (scope_id, batch_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_health_metric_batches_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE health_capability_batches (
        scope_id UUID NOT NULL,
        batch_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        sent_at TIMESTAMP WITH TIME ZONE NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        accepted_capability_ids UUID[] NOT NULL,
        duplicate_capability_ids UUID[] NOT NULL,
        PRIMARY KEY (scope_id, batch_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_health_capability_batches_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$')
    )
    """,
    """
    CREATE TABLE health_snapshot_requests (
        scope_id UUID NOT NULL,
        snapshot_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        capture_reason VARCHAR(32) NOT NULL,
        PRIMARY KEY (scope_id, snapshot_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_health_snapshot_requests_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_health_snapshot_requests_reason
            CHECK (capture_reason IN ('monitoring_started', 'manual_refresh'))
    )
    """,
    """
    CREATE TABLE health_metrics (
        scope_id UUID NOT NULL,
        metric_id UUID NOT NULL,
        first_batch_id UUID NOT NULL,
        content_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        metric_type VARCHAR(128) NOT NULL,
        acquisition_class VARCHAR(32) NOT NULL,
        value DOUBLE PRECISION NOT NULL,
        unit VARCHAR(64) NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
        source VARCHAR(128) NOT NULL,
        source_kind VARCHAR(32) NOT NULL,
        source_name VARCHAR(128),
        source_bundle_id VARCHAR(255),
        device_model VARCHAR(128),
        simulated BOOLEAN NOT NULL,
        quality VARCHAR(32),
        used_for_escalation BOOLEAN NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, metric_id),
        FOREIGN KEY (scope_id, first_batch_id)
            REFERENCES health_metric_batches (scope_id, batch_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_health_metrics_fingerprint
            CHECK (content_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_health_metrics_acquisition_class CHECK (
            acquisition_class IN ('live', 'recent_context', 'user_initiated')
        ),
        CONSTRAINT ck_health_metrics_source_kind CHECK (
            source_kind IN (
                'apple_healthkit', 'apple_live_workout', 'core_motion',
                'pedometer', 'connected_device', 'manual', 'replay'
            )
        ),
        CONSTRAINT ck_health_metrics_quality
            CHECK (quality IS NULL OR quality IN ('good', 'degraded', 'unknown')),
        CONSTRAINT ck_health_metrics_no_raw_types CHECK (
            metric_type NOT IN (
                'ecg_voltage', 'raw_accelerometer',
                'raw_gyroscope', 'raw_magnetometer'
            )
        ),
        CONSTRAINT ck_health_metrics_no_escalation
            CHECK (used_for_escalation = false),
        CONSTRAINT ck_health_metrics_replay_simulated
            CHECK (source_kind <> 'replay' OR simulated = true),
        CONSTRAINT ck_health_metrics_replay_name_simulated
            CHECK (source !~ '^replay_' OR simulated = true),
        CONSTRAINT ck_health_metrics_finite_value CHECK (
            value NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            )
        )
    )
    """,
    """
    CREATE INDEX ix_health_metrics_scope_user_type_observed
    ON health_metrics (
        scope_id, user_id, metric_type, observed_at DESC, metric_id DESC
    )
    """,
    """
    CREATE TABLE health_capabilities (
        scope_id UUID NOT NULL,
        capability_id UUID NOT NULL,
        first_batch_id UUID NOT NULL,
        content_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        metric_type VARCHAR(128) NOT NULL,
        acquisition_class VARCHAR(32) NOT NULL,
        status VARCHAR(32) NOT NULL,
        checked_at TIMESTAMP WITH TIME ZONE NOT NULL,
        source VARCHAR(128) NOT NULL,
        source_kind VARCHAR(32) NOT NULL,
        source_name VARCHAR(128),
        source_bundle_id VARCHAR(255),
        device_model VARCHAR(128),
        simulated BOOLEAN NOT NULL,
        error_code VARCHAR(64),
        used_for_escalation BOOLEAN NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, capability_id),
        FOREIGN KEY (scope_id, first_batch_id)
            REFERENCES health_capability_batches (scope_id, batch_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_health_capabilities_fingerprint
            CHECK (content_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_health_capabilities_acquisition_class CHECK (
            acquisition_class IN ('live', 'recent_context', 'user_initiated')
        ),
        CONSTRAINT ck_health_capabilities_status CHECK (
            status IN (
                'unsupported', 'not_requested', 'requested_no_sample',
                'available', 'error'
            )
        ),
        CONSTRAINT ck_health_capabilities_source_kind CHECK (
            source_kind IN (
                'apple_healthkit', 'apple_live_workout', 'core_motion',
                'pedometer', 'connected_device', 'manual', 'replay'
            )
        ),
        CONSTRAINT ck_health_capabilities_no_raw_types CHECK (
            metric_type NOT IN (
                'ecg_voltage', 'raw_accelerometer',
                'raw_gyroscope', 'raw_magnetometer'
            )
        ),
        CONSTRAINT ck_health_capabilities_error_code
            CHECK ((status = 'error') = (error_code IS NOT NULL)),
        CONSTRAINT ck_health_capabilities_no_escalation
            CHECK (used_for_escalation = false),
        CONSTRAINT ck_health_capabilities_replay_simulated
            CHECK (source_kind <> 'replay' OR simulated = true),
        CONSTRAINT ck_health_capabilities_replay_name_simulated
            CHECK (source !~ '^replay_' OR simulated = true)
    )
    """,
    """
    CREATE INDEX ix_health_capabilities_scope_user_type_checked
    ON health_capabilities (
        scope_id, user_id, metric_type, checked_at DESC, capability_id DESC
    )
    """,
    """
    CREATE TABLE health_snapshots (
        scope_id UUID NOT NULL,
        snapshot_id UUID NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        capture_reason VARCHAR(32) NOT NULL,
        captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
        used_for_escalation BOOLEAN NOT NULL,
        PRIMARY KEY (scope_id, snapshot_id),
        FOREIGN KEY (scope_id, snapshot_id)
            REFERENCES health_snapshot_requests (scope_id, snapshot_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_health_snapshots_reason
            CHECK (capture_reason IN ('monitoring_started', 'manual_refresh')),
        CONSTRAINT ck_health_snapshots_no_escalation
            CHECK (used_for_escalation = false)
    )
    """,
    """
    CREATE INDEX ix_health_snapshots_scope_user_captured
    ON health_snapshots (
        scope_id, user_id, captured_at DESC, snapshot_id DESC
    )
    """,
    """
    CREATE TABLE health_snapshot_items (
        scope_id UUID NOT NULL,
        snapshot_id UUID NOT NULL,
        metric_type VARCHAR(128) NOT NULL,
        metric_payload JSONB,
        capability_payload JSONB,
        availability VARCHAR(32) NOT NULL,
        freshness VARCHAR(32) NOT NULL,
        age_seconds INTEGER,
        live_window_seconds INTEGER NOT NULL,
        recent_window_seconds INTEGER NOT NULL,
        used_for_escalation BOOLEAN NOT NULL,
        PRIMARY KEY (scope_id, snapshot_id, metric_type),
        FOREIGN KEY (scope_id, snapshot_id)
            REFERENCES health_snapshots (scope_id, snapshot_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_health_snapshot_items_has_context
            CHECK (metric_payload IS NOT NULL OR capability_payload IS NOT NULL),
        CONSTRAINT ck_health_snapshot_items_no_raw_types CHECK (
            metric_type NOT IN (
                'ecg_voltage', 'raw_accelerometer',
                'raw_gyroscope', 'raw_magnetometer'
            )
        ),
        CONSTRAINT ck_health_snapshot_items_metric_payload_safety CHECK (
            metric_payload IS NULL OR (
                jsonb_typeof(metric_payload) = 'object' AND
                metric_payload ? 'metric_type' AND
                metric_payload ->> 'metric_type' = metric_type AND
                metric_payload ->> 'metric_type' NOT IN (
                    'ecg_voltage', 'raw_accelerometer',
                    'raw_gyroscope', 'raw_magnetometer'
                ) AND
                metric_payload ? 'used_for_escalation' AND
                metric_payload -> 'used_for_escalation' = 'false'::jsonb
            )
        ),
        CONSTRAINT ck_health_snapshot_items_capability_payload_safety CHECK (
            capability_payload IS NULL OR (
                jsonb_typeof(capability_payload) = 'object' AND
                capability_payload ? 'metric_type' AND
                capability_payload ->> 'metric_type' = metric_type AND
                capability_payload ->> 'metric_type' NOT IN (
                    'ecg_voltage', 'raw_accelerometer',
                    'raw_gyroscope', 'raw_magnetometer'
                ) AND
                capability_payload ? 'used_for_escalation' AND
                capability_payload -> 'used_for_escalation' = 'false'::jsonb
            )
        ),
        CONSTRAINT ck_health_snapshot_items_availability CHECK (
            availability IN (
                'unsupported', 'not_requested', 'requested_no_sample',
                'available', 'error'
            )
        ),
        CONSTRAINT ck_health_snapshot_items_freshness
            CHECK (freshness IN ('live', 'recent', 'historical', 'unavailable')),
        CONSTRAINT ck_health_snapshot_items_age
            CHECK (age_seconds IS NULL OR age_seconds >= 0),
        CONSTRAINT ck_health_snapshot_items_windows CHECK (
            live_window_seconds >= 0 AND recent_window_seconds >= 0 AND
            live_window_seconds <= recent_window_seconds
        ),
        CONSTRAINT ck_health_snapshot_items_metric_state CHECK (
            (
                metric_payload IS NULL AND age_seconds IS NULL AND
                freshness = 'unavailable'
            ) OR (
                metric_payload IS NOT NULL AND age_seconds IS NOT NULL AND
                freshness <> 'unavailable' AND availability = 'available'
            )
        ),
        CONSTRAINT ck_health_snapshot_items_no_escalation
            CHECK (used_for_escalation = false)
    )
    """,
    """
    CREATE TABLE health_snapshot_holds (
        scope_id UUID NOT NULL,
        hold_id UUID NOT NULL,
        snapshot_id UUID NOT NULL,
        reason VARCHAR(64) NOT NULL,
        reference_id VARCHAR(128) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        notes TEXT,
        PRIMARY KEY (scope_id, hold_id),
        FOREIGN KEY (scope_id, snapshot_id)
            REFERENCES health_snapshots (scope_id, snapshot_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_health_snapshot_holds_reference
            UNIQUE (scope_id, snapshot_id, reason, reference_id)
    )
    """,
    """
    CREATE FUNCTION vital_relay_reject_health_update()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'immutable health table % cannot be updated', TG_TABLE_NAME
            USING ERRCODE = '23514';
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_health_metric_batches_immutable
    BEFORE UPDATE ON health_metric_batches
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_metrics_immutable
    BEFORE UPDATE ON health_metrics
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_capability_batches_immutable
    BEFORE UPDATE ON health_capability_batches
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_capabilities_immutable
    BEFORE UPDATE ON health_capabilities
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_snapshot_requests_immutable
    BEFORE UPDATE ON health_snapshot_requests
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_snapshots_immutable
    BEFORE UPDATE ON health_snapshots
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_snapshot_items_immutable
    BEFORE UPDATE ON health_snapshot_items
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
    """
    CREATE TRIGGER tr_health_snapshot_holds_immutable
    BEFORE UPDATE ON health_snapshot_holds
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_health_update()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE health_snapshot_holds",
    "DROP TABLE health_snapshot_items",
    "DROP TABLE health_snapshots",
    "DROP TABLE health_snapshot_requests",
    "DROP TABLE health_capabilities",
    "DROP TABLE health_metrics",
    "DROP TABLE health_capability_batches",
    "DROP TABLE health_metric_batches",
    "DROP TABLE demo_scopes",
    "DROP FUNCTION vital_relay_reject_health_update()",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
