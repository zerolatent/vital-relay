"""Create authenticated wearable-event and durable incident persistence.

Revision ID: 0002_incident_core
Revises: 0001_health_persistence
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002_incident_core"
down_revision: str | None = "0001_health_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    ALTER TABLE health_snapshot_requests
    DROP CONSTRAINT ck_health_snapshot_requests_reason
    """,
    """
    ALTER TABLE health_snapshot_requests
    ADD CONSTRAINT ck_health_snapshot_requests_reason CHECK (
        capture_reason IN (
            'monitoring_started', 'manual_refresh', 'incident_created'
        )
    )
    """,
    """
    ALTER TABLE health_snapshots
    DROP CONSTRAINT ck_health_snapshots_reason
    """,
    """
    ALTER TABLE health_snapshots
    ADD CONSTRAINT ck_health_snapshots_reason CHECK (
        capture_reason IN (
            'monitoring_started', 'manual_refresh', 'incident_created'
        )
    )
    """,
    """
    ALTER TABLE health_snapshot_holds
    ADD CONSTRAINT uq_health_snapshot_holds_exact_snapshot
    UNIQUE (scope_id, hold_id, snapshot_id)
    """,
    """
    CREATE TABLE wearable_events (
        scope_id UUID NOT NULL,
        event_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        event_type VARCHAR(32) NOT NULL,
        source VARCHAR(32) NOT NULL,
        simulated BOOLEAN NOT NULL,
        observed_at TIMESTAMP WITH TIME ZONE NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        sequence BIGINT NOT NULL,
        payload JSONB NOT NULL,
        latitude DOUBLE PRECISION NOT NULL,
        longitude DOUBLE PRECISION NOT NULL,
        horizontal_accuracy_m DOUBLE PRECISION NOT NULL,
        location_captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
        incident_id UUID NOT NULL,
        PRIMARY KEY (scope_id, event_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_wearable_events_incident_link
            UNIQUE (scope_id, event_id, incident_id),
        CONSTRAINT uq_wearable_events_device_sequence
            UNIQUE (scope_id, device_id, sequence),
        CONSTRAINT ck_wearable_events_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_wearable_events_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_wearable_events_source_type CHECK (
            (source = 'apple_fall' AND event_type = 'fall_detected') OR
            (source = 'manual_sos' AND event_type = 'manual_sos')
        ),
        CONSTRAINT ck_wearable_events_not_simulated
            CHECK (simulated = false),
        CONSTRAINT ck_wearable_events_sequence
            CHECK (sequence >= 0),
        CONSTRAINT ck_wearable_events_payload_object
            CHECK (jsonb_typeof(payload) = 'object'),
        CONSTRAINT ck_wearable_events_payload_contract CHECK (
            CASE source
                WHEN 'apple_fall' THEN (
                    payload ? 'fall_date' AND
                    payload -> 'fall_detection_available' = 'true'::jsonb AND
                    payload -> 'entitlement_present' = 'true'::jsonb AND
                    payload ->> 'authorization_status' = 'authorized' AND
                    (payload ->> 'fall_date')::timestamptz = observed_at
                )
                WHEN 'manual_sos' THEN (
                    payload ->> 'activation_method' IN (
                        'watch_button', 'iphone_button'
                    )
                )
                ELSE false
            END
        ),
        CONSTRAINT ck_wearable_events_latitude CHECK (
            latitude NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND latitude BETWEEN -90 AND 90
        ),
        CONSTRAINT ck_wearable_events_longitude CHECK (
            longitude NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND longitude BETWEEN -180 AND 180
        ),
        CONSTRAINT ck_wearable_events_location_accuracy CHECK (
            horizontal_accuracy_m NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND horizontal_accuracy_m BETWEEN 0 AND 10000
        )
    )
    """,
    """
    CREATE UNIQUE INDEX uq_wearable_events_apple_natural
    ON wearable_events (scope_id, user_id, device_id, observed_at)
    WHERE source = 'apple_fall'
    """,
    """
    CREATE INDEX ix_wearable_events_scope_user_observed
    ON wearable_events (
        scope_id, user_id, observed_at DESC, event_id DESC
    )
    """,
    """
    CREATE TABLE incidents (
        scope_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        schema_version SMALLINT NOT NULL,
        user_id VARCHAR(128) NOT NULL,
        kind VARCHAR(32) NOT NULL,
        current_state VARCHAR(32) NOT NULL,
        trigger_event_id UUID NOT NULL,
        health_snapshot_id UUID NOT NULL,
        health_snapshot_hold_id UUID NOT NULL,
        simulated BOOLEAN NOT NULL,
        state_version INTEGER NOT NULL,
        next_timeline_sequence BIGINT NOT NULL,
        latitude DOUBLE PRECISION NOT NULL,
        longitude DOUBLE PRECISION NOT NULL,
        horizontal_accuracy_m DOUBLE PRECISION NOT NULL,
        location_captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
        opened_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        resolved_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (scope_id, incident_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT fk_incidents_trigger_event
            FOREIGN KEY (scope_id, trigger_event_id, incident_id)
            REFERENCES wearable_events (scope_id, event_id, incident_id)
            ON DELETE RESTRICT
            DEFERRABLE INITIALLY DEFERRED,
        CONSTRAINT fk_incidents_exact_snapshot_hold
            FOREIGN KEY (
                scope_id, health_snapshot_hold_id, health_snapshot_id
            )
            REFERENCES health_snapshot_holds (
                scope_id, hold_id, snapshot_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT uq_incidents_trigger_event
            UNIQUE (scope_id, trigger_event_id),
        CONSTRAINT ck_incidents_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_incidents_kind
            CHECK (kind IN ('fall', 'manual_sos')),
        CONSTRAINT ck_incidents_state CHECK (
            current_state IN (
                'verifying', 'escalating', 'response_active', 'resolved'
            )
        ),
        CONSTRAINT ck_incidents_not_simulated
            CHECK (simulated = false),
        CONSTRAINT ck_incidents_sequences
            CHECK (state_version >= 1 AND next_timeline_sequence >= 1),
        CONSTRAINT ck_incidents_updated_at
            CHECK (updated_at >= opened_at),
        CONSTRAINT ck_incidents_resolved_at CHECK (
            (
                current_state = 'resolved' AND
                resolved_at IS NOT NULL AND
                resolved_at >= opened_at
            ) OR (
                current_state <> 'resolved' AND resolved_at IS NULL
            )
        ),
        CONSTRAINT ck_incidents_latitude CHECK (
            latitude NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND latitude BETWEEN -90 AND 90
        ),
        CONSTRAINT ck_incidents_longitude CHECK (
            longitude NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND longitude BETWEEN -180 AND 180
        ),
        CONSTRAINT ck_incidents_location_accuracy CHECK (
            horizontal_accuracy_m NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND horizontal_accuracy_m BETWEEN 0 AND 10000
        )
    )
    """,
    """
    ALTER TABLE wearable_events
    ADD CONSTRAINT fk_wearable_events_incident
    FOREIGN KEY (scope_id, incident_id)
    REFERENCES incidents (scope_id, incident_id)
    ON DELETE CASCADE
    DEFERRABLE INITIALLY DEFERRED
    """,
    """
    CREATE UNIQUE INDEX uq_incidents_scope_user_active
    ON incidents (scope_id, user_id)
    WHERE current_state IN ('verifying', 'escalating', 'response_active')
    """,
    """
    CREATE INDEX ix_incidents_scope_state_updated
    ON incidents (
        scope_id, current_state, updated_at DESC, incident_id DESC
    )
    """,
    """
    CREATE TABLE incident_commands (
        scope_id UUID NOT NULL,
        command_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        command_type VARCHAR(32) NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        responded_at TIMESTAMP WITH TIME ZONE NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        payload JSONB NOT NULL,
        PRIMARY KEY (scope_id, command_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_incident_commands_one_check_in
            UNIQUE (scope_id, incident_id),
        CONSTRAINT ck_incident_commands_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_incident_commands_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_incident_commands_type
            CHECK (command_type IN ('i_am_okay', 'i_need_help')),
        CONSTRAINT ck_incident_commands_payload_object
            CHECK (jsonb_typeof(payload) = 'object')
    )
    """,
    """
    CREATE TABLE incident_state_transitions (
        scope_id UUID NOT NULL,
        transition_id UUID NOT NULL,
        schema_version SMALLINT NOT NULL,
        incident_id UUID NOT NULL,
        sequence INTEGER NOT NULL,
        from_state VARCHAR(32) NOT NULL,
        to_state VARCHAR(32) NOT NULL,
        trigger VARCHAR(32) NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        source_event_id UUID,
        command_id UUID,
        simulated BOOLEAN NOT NULL,
        PRIMARY KEY (scope_id, transition_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, source_event_id)
            REFERENCES wearable_events (scope_id, event_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, command_id)
            REFERENCES incident_commands (scope_id, command_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_incident_transitions_sequence
            UNIQUE (scope_id, incident_id, sequence),
        CONSTRAINT uq_incident_transitions_incident_link
            UNIQUE (scope_id, transition_id, incident_id),
        CONSTRAINT uq_incident_transitions_source_event
            UNIQUE (scope_id, source_event_id),
        CONSTRAINT uq_incident_transitions_command
            UNIQUE (scope_id, command_id),
        CONSTRAINT ck_incident_transitions_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_incident_transitions_sequence
            CHECK (sequence >= 1),
        CONSTRAINT ck_incident_transitions_states CHECK (
            from_state IN (
                'monitoring', 'verifying', 'escalating',
                'response_active', 'resolved'
            ) AND
            to_state IN (
                'verifying', 'escalating', 'response_active', 'resolved'
            )
        ),
        CONSTRAINT ck_incident_transitions_policy CHECK (
            (
                from_state = 'monitoring' AND
                trigger = 'fall_detected' AND
                to_state = 'verifying'
            ) OR (
                from_state = 'monitoring' AND
                trigger = 'manual_sos' AND
                to_state = 'escalating'
            ) OR (
                from_state = 'verifying' AND
                trigger = 'check_in_okay' AND
                to_state = 'resolved'
            ) OR (
                from_state = 'verifying' AND
                trigger IN (
                    'check_in_help', 'verification_timeout', 'manual_sos'
                ) AND
                to_state = 'escalating'
            ) OR (
                from_state = 'escalating' AND
                trigger = 'responder_accepted' AND
                to_state = 'response_active'
            ) OR (
                from_state = 'escalating' AND
                trigger = 'cancellation' AND
                to_state = 'resolved'
            ) OR (
                from_state = 'response_active' AND
                trigger IN ('close', 'handoff') AND
                to_state = 'resolved'
            )
        ),
        CONSTRAINT ck_incident_transitions_authority CHECK (
            (
                (trigger IN ('fall_detected', 'manual_sos')) =
                (source_event_id IS NOT NULL)
            ) AND (
                (trigger IN ('check_in_okay', 'check_in_help')) =
                (command_id IS NOT NULL)
            ) AND NOT (
                source_event_id IS NOT NULL AND command_id IS NOT NULL
            )
        ),
        CONSTRAINT ck_incident_transitions_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE TABLE incident_deadlines (
        scope_id UUID NOT NULL,
        deadline_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        kind VARCHAR(32) NOT NULL,
        due_at TIMESTAMP WITH TIME ZONE NOT NULL,
        status VARCHAR(16) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        settled_at TIMESTAMP WITH TIME ZONE,
        settled_transition_id UUID,
        PRIMARY KEY (scope_id, deadline_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        CONSTRAINT fk_incident_deadlines_settlement_transition
            FOREIGN KEY (scope_id, settled_transition_id, incident_id)
            REFERENCES incident_state_transitions (
                scope_id, transition_id, incident_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT uq_incident_deadlines_kind
            UNIQUE (scope_id, incident_id, kind),
        CONSTRAINT ck_incident_deadlines_kind
            CHECK (kind = 'verification_timeout'),
        CONSTRAINT ck_incident_deadlines_status
            CHECK (status IN ('pending', 'fired', 'cancelled')),
        CONSTRAINT ck_incident_deadlines_due_at
            CHECK (due_at >= created_at),
        CONSTRAINT ck_incident_deadlines_settlement CHECK (
            (
                status = 'pending' AND
                settled_at IS NULL AND
                settled_transition_id IS NULL
            ) OR (
                status IN ('fired', 'cancelled') AND
                settled_at IS NOT NULL AND
                settled_transition_id IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX ix_incident_deadlines_pending_due
    ON incident_deadlines (due_at, deadline_id)
    WHERE status = 'pending'
    """,
    """
    CREATE TABLE incident_timeline_entries (
        scope_id UUID NOT NULL,
        timeline_id UUID NOT NULL,
        schema_version SMALLINT NOT NULL,
        incident_id UUID NOT NULL,
        sequence BIGINT NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        state VARCHAR(32) NOT NULL,
        transition_id UUID,
        source_event_id UUID,
        command_id UUID,
        summary VARCHAR(256) NOT NULL,
        simulated BOOLEAN NOT NULL,
        PRIMARY KEY (scope_id, timeline_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, transition_id)
            REFERENCES incident_state_transitions (scope_id, transition_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, source_event_id)
            REFERENCES wearable_events (scope_id, event_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, command_id)
            REFERENCES incident_commands (scope_id, command_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_incident_timeline_sequence
            UNIQUE (scope_id, incident_id, sequence),
        CONSTRAINT ck_incident_timeline_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_incident_timeline_sequence
            CHECK (sequence >= 1),
        CONSTRAINT ck_incident_timeline_event_type CHECK (
            event_type IN (
                'wearable_event_received', 'incident_opened',
                'verification_started', 'check_in_recorded',
                'verification_timed_out', 'state_transitioned'
            )
        ),
        CONSTRAINT ck_incident_timeline_state CHECK (
            state IN (
                'verifying', 'escalating', 'response_active', 'resolved'
            )
        ),
        CONSTRAINT ck_incident_timeline_summary
            CHECK (char_length(summary) BETWEEN 1 AND 256),
        CONSTRAINT ck_incident_timeline_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_incident_timeline_scope_incident_order
    ON incident_timeline_entries (scope_id, incident_id, sequence)
    """,
    """
    CREATE FUNCTION vital_relay_reject_incident_audit_change()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'append-only incident audit table % cannot be %d',
            TG_TABLE_NAME, lower(TG_OP)
            USING ERRCODE = '23514';
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_wearable_events_append_only
    BEFORE UPDATE OR DELETE ON wearable_events
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TRIGGER tr_incident_commands_append_only
    BEFORE UPDATE OR DELETE ON incident_commands
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TRIGGER tr_incident_state_transitions_append_only
    BEFORE UPDATE OR DELETE ON incident_state_transitions
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TRIGGER tr_incident_timeline_entries_append_only
    BEFORE UPDATE OR DELETE ON incident_timeline_entries
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE incident_timeline_entries",
    "DROP TABLE incident_deadlines",
    "DROP TABLE incident_state_transitions",
    "DROP TABLE incident_commands",
    "ALTER TABLE wearable_events DROP CONSTRAINT fk_wearable_events_incident",
    "DROP TABLE incidents",
    "DROP TABLE wearable_events",
    "DROP FUNCTION vital_relay_reject_incident_audit_change()",
    """
    DELETE FROM health_snapshot_holds
    WHERE (scope_id, snapshot_id) IN (
        SELECT scope_id, snapshot_id
        FROM health_snapshots
        WHERE capture_reason = 'incident_created'
    )
    """,
    """
    DELETE FROM health_snapshot_requests
    WHERE capture_reason = 'incident_created'
    """,
    """
    ALTER TABLE health_snapshot_holds
    DROP CONSTRAINT uq_health_snapshot_holds_exact_snapshot
    """,
    """
    ALTER TABLE health_snapshots
    DROP CONSTRAINT ck_health_snapshots_reason
    """,
    """
    ALTER TABLE health_snapshots
    ADD CONSTRAINT ck_health_snapshots_reason CHECK (
        capture_reason IN ('monitoring_started', 'manual_refresh')
    )
    """,
    """
    ALTER TABLE health_snapshot_requests
    DROP CONSTRAINT ck_health_snapshot_requests_reason
    """,
    """
    ALTER TABLE health_snapshot_requests
    ADD CONSTRAINT ck_health_snapshot_requests_reason CHECK (
        capture_reason IN ('monitoring_started', 'manual_refresh')
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
