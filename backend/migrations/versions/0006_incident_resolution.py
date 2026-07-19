"""Add durable incident resolution and assignment revocation audit records.

Revision ID: 0006_incident_resolution
Revises: 0005_responder_notifications
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006_incident_resolution"
down_revision: str | None = "0005_responder_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE incident_resolution_receipts (
        scope_id UUID NOT NULL,
        resolution_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        action VARCHAR(16) NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        payload JSONB NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, resolution_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_incident_resolution_receipts_incident
            UNIQUE (scope_id, incident_id),
        CONSTRAINT uq_incident_resolution_receipts_exact_link
            UNIQUE (scope_id, resolution_id, incident_id),
        CONSTRAINT ck_incident_resolution_receipts_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_incident_resolution_receipts_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_incident_resolution_receipts_action
            CHECK (action IN ('close', 'handoff')),
        CONSTRAINT ck_incident_resolution_receipts_payload_object
            CHECK (jsonb_typeof(payload) = 'object'),
        CONSTRAINT ck_incident_resolution_receipts_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE TRIGGER tr_incident_resolution_receipts_append_only
    BEFORE UPDATE OR DELETE ON incident_resolution_receipts
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    ALTER TABLE incident_state_transitions
    ADD COLUMN resolution_id UUID
    """,
    """
    ALTER TABLE incident_state_transitions
    ADD CONSTRAINT fk_incident_transitions_resolution
    FOREIGN KEY (scope_id, resolution_id, incident_id)
    REFERENCES incident_resolution_receipts (
        scope_id, resolution_id, incident_id
    )
    ON DELETE RESTRICT
    """,
    """
    ALTER TABLE incident_state_transitions
    ADD CONSTRAINT uq_incident_transitions_resolution
    UNIQUE (scope_id, resolution_id)
    """,
    """
    ALTER TABLE incident_state_transitions
    DROP CONSTRAINT ck_incident_transitions_authority
    """,
    """
    ALTER TABLE incident_state_transitions
    ADD CONSTRAINT ck_incident_transitions_authority CHECK (
        (
            (trigger IN ('fall_detected', 'manual_sos')) =
            (source_event_id IS NOT NULL)
        ) AND (
            (trigger IN ('check_in_okay', 'check_in_help')) =
            (command_id IS NOT NULL)
        ) AND (
            (trigger IN ('close', 'handoff')) =
            (resolution_id IS NOT NULL)
        ) AND NOT (
            (source_event_id IS NOT NULL AND command_id IS NOT NULL) OR
            (source_event_id IS NOT NULL AND resolution_id IS NOT NULL) OR
            (command_id IS NOT NULL AND resolution_id IS NOT NULL)
        )
    )
    """,
    """
    CREATE TABLE responder_assignment_revocations (
        scope_id UUID NOT NULL,
        revocation_id UUID NOT NULL,
        assignment_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        resolution_id UUID NOT NULL,
        transition_id UUID NOT NULL,
        reason VARCHAR(16) NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, revocation_id),
        CONSTRAINT fk_assignment_revocations_exact_assignment
            FOREIGN KEY (
                scope_id, assignment_id, incident_id, responder_id
            )
            REFERENCES responder_assignments (
                scope_id, assignment_id, incident_id, responder_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT fk_assignment_revocations_exact_resolution
            FOREIGN KEY (scope_id, resolution_id, incident_id)
            REFERENCES incident_resolution_receipts (
                scope_id, resolution_id, incident_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT fk_assignment_revocations_exact_transition
            FOREIGN KEY (scope_id, transition_id, incident_id)
            REFERENCES incident_state_transitions (
                scope_id, transition_id, incident_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT uq_assignment_revocations_assignment
            UNIQUE (scope_id, assignment_id),
        CONSTRAINT uq_assignment_revocations_incident
            UNIQUE (scope_id, incident_id),
        CONSTRAINT uq_assignment_revocations_resolution
            UNIQUE (scope_id, resolution_id),
        CONSTRAINT uq_assignment_revocations_transition
            UNIQUE (scope_id, transition_id),
        CONSTRAINT ck_assignment_revocations_reason
            CHECK (reason IN ('close', 'handoff')),
        CONSTRAINT ck_assignment_revocations_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_assignment_revocation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM responder_assignments AS assignment
            JOIN incident_resolution_receipts AS receipt
              ON receipt.scope_id = assignment.scope_id
             AND receipt.incident_id = assignment.incident_id
            JOIN incident_state_transitions AS transition
              ON transition.scope_id = receipt.scope_id
             AND transition.incident_id = receipt.incident_id
             AND transition.resolution_id = receipt.resolution_id
            JOIN incidents AS incident
              ON incident.scope_id = assignment.scope_id
             AND incident.incident_id = assignment.incident_id
            WHERE assignment.scope_id = NEW.scope_id
              AND assignment.assignment_id = NEW.assignment_id
              AND assignment.incident_id = NEW.incident_id
              AND assignment.responder_id = NEW.responder_id
              AND assignment.accepted_at <= NEW.revoked_at
              AND receipt.resolution_id = NEW.resolution_id
              AND receipt.action = NEW.reason
              AND receipt.server_received_at = NEW.revoked_at
              AND transition.transition_id = NEW.transition_id
              AND transition.trigger = NEW.reason
              AND transition.from_state = 'response_active'
              AND transition.to_state = 'resolved'
              AND transition.occurred_at = NEW.revoked_at
              AND incident.current_state = 'resolved'
              AND incident.resolved_at = NEW.revoked_at
        ) THEN
            RAISE EXCEPTION
                'assignment revocation must match its resolution transition'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_assignment_revocations_validate_link
    BEFORE INSERT ON responder_assignment_revocations
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_assignment_revocation()
    """,
    """
    CREATE TRIGGER tr_assignment_revocations_append_only
    BEFORE UPDATE OR DELETE ON responder_assignment_revocations
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE responder_assignment_revocations",
    "DROP FUNCTION vital_relay_validate_assignment_revocation()",
    """
    CREATE TEMP TABLE vital_relay_resolution_downgrade_incidents
    ON COMMIT DROP AS
    SELECT incident.scope_id,
           incident.incident_id,
           transition.resolution_id,
           transition.transition_id,
           transition.sequence - 1 AS previous_state_version,
           COALESCE(
               (
                   SELECT max(timeline.occurred_at)
                   FROM incident_timeline_entries AS timeline
                   WHERE timeline.scope_id = incident.scope_id
                     AND timeline.incident_id = incident.incident_id
                     AND timeline.transition_id IS DISTINCT FROM
                         transition.transition_id
               ),
               incident.opened_at
           ) AS previous_updated_at
    FROM incidents AS incident
    JOIN incident_state_transitions AS transition
      ON transition.scope_id = incident.scope_id
     AND transition.incident_id = incident.incident_id
     AND transition.resolution_id IS NOT NULL
    """,
    """
    ALTER TABLE incident_timeline_entries
    DISABLE TRIGGER tr_incident_timeline_entries_append_only
    """,
    """
    ALTER TABLE incident_state_transitions
    DISABLE TRIGGER tr_incident_state_transitions_append_only
    """,
    """
    ALTER TABLE incident_resolution_receipts
    DISABLE TRIGGER tr_incident_resolution_receipts_append_only
    """,
    """
    DELETE FROM incident_timeline_entries AS timeline
    USING vital_relay_resolution_downgrade_incidents AS affected
    WHERE timeline.scope_id = affected.scope_id
      AND timeline.incident_id = affected.incident_id
      AND timeline.transition_id = affected.transition_id
    """,
    """
    DELETE FROM incident_state_transitions AS transition
    USING vital_relay_resolution_downgrade_incidents AS affected
    WHERE transition.scope_id = affected.scope_id
      AND transition.transition_id = affected.transition_id
    """,
    """
    UPDATE incidents AS incident
    SET current_state = 'response_active',
        state_version = affected.previous_state_version,
        next_timeline_sequence = COALESCE(
            (
                SELECT max(timeline.sequence) + 1
                FROM incident_timeline_entries AS timeline
                WHERE timeline.scope_id = incident.scope_id
                  AND timeline.incident_id = incident.incident_id
            ),
            1
        ),
        updated_at = affected.previous_updated_at,
        resolved_at = NULL
    FROM vital_relay_resolution_downgrade_incidents AS affected
    WHERE incident.scope_id = affected.scope_id
      AND incident.incident_id = affected.incident_id
    """,
    """
    DELETE FROM incident_resolution_receipts AS receipt
    USING vital_relay_resolution_downgrade_incidents AS affected
    WHERE receipt.scope_id = affected.scope_id
      AND receipt.resolution_id = affected.resolution_id
    """,
    """
    ALTER TABLE incident_timeline_entries
    ENABLE TRIGGER tr_incident_timeline_entries_append_only
    """,
    """
    ALTER TABLE incident_state_transitions
    ENABLE TRIGGER tr_incident_state_transitions_append_only
    """,
    """
    ALTER TABLE incident_state_transitions
    DROP CONSTRAINT ck_incident_transitions_authority
    """,
    """
    ALTER TABLE incident_state_transitions
    DROP CONSTRAINT fk_incident_transitions_resolution
    """,
    """
    ALTER TABLE incident_state_transitions
    DROP CONSTRAINT uq_incident_transitions_resolution
    """,
    """
    ALTER TABLE incident_state_transitions
    DROP COLUMN resolution_id
    """,
    """
    ALTER TABLE incident_state_transitions
    ADD CONSTRAINT ck_incident_transitions_authority CHECK (
        (
            (trigger IN ('fall_detected', 'manual_sos')) =
            (source_event_id IS NOT NULL)
        ) AND (
            (trigger IN ('check_in_okay', 'check_in_help')) =
            (command_id IS NOT NULL)
        ) AND NOT (
            source_event_id IS NOT NULL AND command_id IS NOT NULL
        )
    )
    """,
    "DROP TABLE incident_resolution_receipts",
    "DROP TABLE vital_relay_resolution_downgrade_incidents",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
