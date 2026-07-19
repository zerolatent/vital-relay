"""Create PostGIS responder discovery and durable live dispatch persistence.

Revision ID: 0003_postgis_dispatch
Revises: 0002_incident_core
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003_postgis_dispatch"
down_revision: str | None = "0002_incident_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    "CREATE EXTENSION IF NOT EXISTS postgis",
    """
    ALTER TABLE incident_timeline_entries
    DROP CONSTRAINT ck_incident_timeline_event_type
    """,
    """
    ALTER TABLE incident_timeline_entries
    ADD CONSTRAINT ck_incident_timeline_event_type CHECK (
        event_type IN (
            'wearable_event_received', 'incident_opened',
            'verification_started', 'check_in_recorded',
            'verification_timed_out', 'state_transitioned',
            'responder_search_started', 'responder_invited',
            'responder_declined', 'responder_accepted',
            'dispatch_activated'
        )
    )
    """,
    """
    CREATE TABLE responders (
        scope_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        display_name VARCHAR(128) NOT NULL,
        role VARCHAR(32) NOT NULL,
        access_token_hash VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, responder_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_responders_access_token_hash
            UNIQUE (scope_id, access_token_hash),
        CONSTRAINT ck_responders_display_name
            CHECK (char_length(display_name) BETWEEN 1 AND 128),
        CONSTRAINT ck_responders_role CHECK (
            role IN (
                'venue_staff', 'trained_volunteer', 'medical_professional'
            )
        ),
        CONSTRAINT ck_responders_access_token_hash
            CHECK (access_token_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_responders_status
            CHECK (status IN ('active', 'inactive')),
        CONSTRAINT ck_responders_updated_at
            CHECK (updated_at >= created_at),
        CONSTRAINT ck_responders_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_responders_scope_status
    ON responders (scope_id, status)
    """,
    """
    CREATE TABLE responder_skills (
        scope_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        skill VARCHAR(32) NOT NULL,
        certified_until TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (scope_id, responder_id, skill),
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_responder_skills_skill
            CHECK (skill IN ('first_aid', 'cpr', 'aed'))
    )
    """,
    """
    CREATE INDEX ix_responder_skills_scope_skill
    ON responder_skills (scope_id, skill, certified_until)
    """,
    """
    CREATE TABLE responder_availability (
        scope_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        available BOOLEAN NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, responder_id),
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE CASCADE
    )
    """,
    """
    CREATE INDEX ix_responder_availability_scope_available_updated
    ON responder_availability (scope_id, available, updated_at DESC)
    """,
    """
    CREATE TABLE responder_locations (
        scope_id UUID NOT NULL,
        location_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        captured_at TIMESTAMP WITH TIME ZONE NOT NULL,
        horizontal_accuracy_m DOUBLE PRECISION NOT NULL,
        location geography(POINT, 4326) NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, location_id),
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_responder_locations_accuracy CHECK (
            horizontal_accuracy_m NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND horizontal_accuracy_m BETWEEN 0 AND 10000
        ),
        CONSTRAINT ck_responder_locations_point CHECK (
            ST_SRID(location::geometry) = 4326 AND
            GeometryType(location::geometry) = 'POINT'
        ),
        CONSTRAINT ck_responder_locations_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_responder_locations_scope_responder_captured
    ON responder_locations (
        scope_id, responder_id, captured_at DESC, location_id DESC
    )
    """,
    """
    CREATE INDEX ix_responder_locations_location_gist
    ON responder_locations USING GIST (location)
    """,
    """
    CREATE TABLE aed_sites (
        scope_id UUID NOT NULL,
        aed_id UUID NOT NULL,
        name VARCHAR(160) NOT NULL,
        location_description VARCHAR(256) NOT NULL,
        access_instructions TEXT NOT NULL,
        publicly_accessible BOOLEAN NOT NULL,
        location geography(POINT, 4326) NOT NULL,
        active BOOLEAN NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, aed_id),
        FOREIGN KEY (scope_id) REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_aed_sites_name
            CHECK (char_length(name) BETWEEN 1 AND 160),
        CONSTRAINT ck_aed_sites_location_description
            CHECK (char_length(location_description) BETWEEN 1 AND 256),
        CONSTRAINT ck_aed_sites_access_instructions
            CHECK (char_length(access_instructions) BETWEEN 1 AND 512),
        CONSTRAINT ck_aed_sites_updated_at
            CHECK (updated_at >= created_at),
        CONSTRAINT ck_aed_sites_point CHECK (
            ST_SRID(location::geometry) = 4326 AND
            GeometryType(location::geometry) = 'POINT'
        ),
        CONSTRAINT ck_aed_sites_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE INDEX ix_aed_sites_scope_active
    ON aed_sites (scope_id, active)
    """,
    """
    CREATE INDEX ix_aed_sites_location_gist
    ON aed_sites USING GIST (location)
    """,
    """
    CREATE TABLE responder_invitations (
        scope_id UUID NOT NULL,
        invitation_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        rank INTEGER NOT NULL,
        status VARCHAR(16) NOT NULL,
        distance_m DOUBLE PRECISION NOT NULL,
        candidate_snapshot JSONB NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        responded_at TIMESTAMP WITH TIME ZONE,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, invitation_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_responder_invitations_incident_responder
            UNIQUE (scope_id, incident_id, responder_id),
        CONSTRAINT uq_responder_invitations_incident_rank
            UNIQUE (scope_id, incident_id, rank),
        CONSTRAINT uq_responder_invitations_exact_link
            UNIQUE (scope_id, invitation_id, incident_id, responder_id),
        CONSTRAINT ck_responder_invitations_rank
            CHECK (rank >= 1),
        CONSTRAINT ck_responder_invitations_status CHECK (
            status IN ('pending', 'declined', 'accepted')
        ),
        CONSTRAINT ck_responder_invitations_distance CHECK (
            distance_m NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            ) AND distance_m >= 0
        ),
        CONSTRAINT ck_responder_invitations_candidate_snapshot
            CHECK (jsonb_typeof(candidate_snapshot) = 'object'),
        CONSTRAINT ck_responder_invitations_response_state CHECK (
            (status = 'pending' AND responded_at IS NULL) OR
            (
                status IN ('declined', 'accepted') AND
                responded_at IS NOT NULL
            )
        ),
        CONSTRAINT ck_responder_invitations_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_responder_invitations_incident_pending
    ON responder_invitations (scope_id, incident_id)
    WHERE status = 'pending'
    """,
    """
    CREATE UNIQUE INDEX uq_responder_invitations_incident_accepted
    ON responder_invitations (scope_id, incident_id)
    WHERE status = 'accepted'
    """,
    """
    CREATE INDEX ix_responder_invitations_scope_incident_rank
    ON responder_invitations (scope_id, incident_id, rank)
    """,
    """
    CREATE TABLE responder_invitation_responses (
        scope_id UUID NOT NULL,
        response_id UUID NOT NULL,
        invitation_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        request_fingerprint VARCHAR(64) NOT NULL,
        schema_version SMALLINT NOT NULL,
        decision VARCHAR(16) NOT NULL,
        server_received_at TIMESTAMP WITH TIME ZONE NOT NULL,
        payload JSONB NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, response_id),
        CONSTRAINT fk_responder_invitation_responses_exact_invitation
            FOREIGN KEY (
                scope_id, invitation_id, incident_id, responder_id
            )
            REFERENCES responder_invitations (
                scope_id, invitation_id, incident_id, responder_id
            )
            ON DELETE CASCADE,
        CONSTRAINT uq_responder_invitation_responses_invitation
            UNIQUE (scope_id, invitation_id),
        CONSTRAINT uq_responder_invitation_responses_exact_link
            UNIQUE (
                scope_id, response_id, invitation_id,
                incident_id, responder_id
            ),
        CONSTRAINT ck_responder_invitation_responses_fingerprint
            CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_responder_invitation_responses_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_responder_invitation_responses_decision
            CHECK (decision IN ('accept', 'decline')),
        CONSTRAINT ck_responder_invitation_responses_payload
            CHECK (jsonb_typeof(payload) = 'object'),
        CONSTRAINT ck_responder_invitation_responses_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE TABLE responder_assignments (
        scope_id UUID NOT NULL,
        assignment_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        invitation_id UUID NOT NULL,
        aed_id UUID NOT NULL,
        response_id UUID NOT NULL,
        accepted_at TIMESTAMP WITH TIME ZONE NOT NULL,
        static_route JSONB NOT NULL,
        simulated BOOLEAN NOT NULL,
        PRIMARY KEY (scope_id, assignment_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, aed_id)
            REFERENCES aed_sites (scope_id, aed_id)
            ON DELETE RESTRICT,
        CONSTRAINT fk_responder_assignments_exact_invitation
            FOREIGN KEY (
                scope_id, invitation_id, incident_id, responder_id
            )
            REFERENCES responder_invitations (
                scope_id, invitation_id, incident_id, responder_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT fk_responder_assignments_exact_response
            FOREIGN KEY (
                scope_id, response_id, invitation_id,
                incident_id, responder_id
            )
            REFERENCES responder_invitation_responses (
                scope_id, response_id, invitation_id,
                incident_id, responder_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT uq_responder_assignments_incident
            UNIQUE (scope_id, incident_id),
        CONSTRAINT uq_responder_assignments_invitation
            UNIQUE (scope_id, invitation_id),
        CONSTRAINT uq_responder_assignments_response
            UNIQUE (scope_id, response_id),
        CONSTRAINT ck_responder_assignments_static_route
            CHECK (jsonb_typeof(static_route) = 'object'),
        CONSTRAINT ck_responder_assignments_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_responder_assignment()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM responder_invitations AS invitation
            JOIN responder_invitation_responses AS response
              ON response.scope_id = invitation.scope_id
             AND response.invitation_id = invitation.invitation_id
             AND response.incident_id = invitation.incident_id
             AND response.responder_id = invitation.responder_id
            WHERE invitation.scope_id = NEW.scope_id
              AND invitation.invitation_id = NEW.invitation_id
              AND invitation.incident_id = NEW.incident_id
              AND invitation.responder_id = NEW.responder_id
              AND invitation.status = 'accepted'
              AND response.response_id = NEW.response_id
              AND response.decision = 'accept'
        ) THEN
            RAISE EXCEPTION
                'assignment requires the exact accepted invitation response'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_responder_assignments_validate_acceptance
    BEFORE INSERT ON responder_assignments
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_responder_assignment()
    """,
    """
    CREATE TRIGGER tr_responder_locations_append_only
    BEFORE UPDATE OR DELETE ON responder_locations
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TRIGGER tr_responder_invitation_responses_append_only
    BEFORE UPDATE OR DELETE ON responder_invitation_responses
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TRIGGER tr_responder_assignments_append_only
    BEFORE UPDATE OR DELETE ON responder_assignments
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM responder_assignments AS assignment
            JOIN incidents AS incident
              ON incident.scope_id = assignment.scope_id
             AND incident.incident_id = assignment.incident_id
            WHERE incident.current_state <> 'response_active'
               OR (
                    SELECT count(*)
                    FROM incident_state_transitions AS accepted_transition
                    WHERE accepted_transition.scope_id = assignment.scope_id
                      AND accepted_transition.incident_id = assignment.incident_id
                      AND accepted_transition.trigger = 'responder_accepted'
                ) <> 1
               OR EXISTS (
                    SELECT 1
                    FROM incident_state_transitions AS later_transition
                    WHERE later_transition.scope_id = assignment.scope_id
                      AND later_transition.incident_id = assignment.incident_id
                      AND later_transition.sequence > (
                          SELECT max(accepted_transition.sequence)
                          FROM incident_state_transitions AS accepted_transition
                          WHERE accepted_transition.scope_id = assignment.scope_id
                            AND accepted_transition.incident_id = assignment.incident_id
                            AND accepted_transition.trigger = 'responder_accepted'
                      )
                )
        ) THEN
            RAISE EXCEPTION
                'cannot downgrade dispatch after a later incident transition'
                USING ERRCODE = '23514';
        END IF;
    END;
    $$
    """,
    """
    CREATE TEMPORARY TABLE vital_relay_dispatch_downgrade_incidents
    ON COMMIT DROP AS
    SELECT DISTINCT scope_id, incident_id
    FROM incident_timeline_entries
    WHERE event_type IN (
        'responder_search_started', 'responder_invited',
        'responder_declined', 'responder_accepted',
        'dispatch_activated'
    )
    UNION
    SELECT scope_id, incident_id
    FROM responder_assignments
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
    DELETE FROM incident_timeline_entries
    WHERE event_type IN (
        'responder_search_started', 'responder_invited',
        'responder_declined', 'responder_accepted',
        'dispatch_activated'
    )
    """,
    """
    UPDATE incident_timeline_entries AS timeline
    SET state = 'escalating'
    FROM responder_assignments AS assignment
    WHERE timeline.scope_id = assignment.scope_id
      AND timeline.incident_id = assignment.incident_id
      AND timeline.state = 'response_active'
    """,
    """
    DELETE FROM incident_state_transitions AS transition
    USING responder_assignments AS assignment
    WHERE transition.scope_id = assignment.scope_id
      AND transition.incident_id = assignment.incident_id
      AND transition.trigger = 'responder_accepted'
    """,
    """
    UPDATE incidents AS incident
    SET current_state = 'escalating',
        state_version = (
            SELECT max(transition.sequence)
            FROM incident_state_transitions AS transition
            WHERE transition.scope_id = incident.scope_id
              AND transition.incident_id = incident.incident_id
        ),
        resolved_at = NULL
    FROM responder_assignments AS assignment
    WHERE incident.scope_id = assignment.scope_id
      AND incident.incident_id = assignment.incident_id
    """,
    """
    UPDATE incidents AS incident
    SET next_timeline_sequence = COALESCE(
            (
                SELECT max(timeline.sequence) + 1
                FROM incident_timeline_entries AS timeline
                WHERE timeline.scope_id = incident.scope_id
                  AND timeline.incident_id = incident.incident_id
            ),
            1
        ),
        updated_at = GREATEST(
            incident.opened_at,
            COALESCE(
                (
                    SELECT max(timeline.occurred_at)
                    FROM incident_timeline_entries AS timeline
                    WHERE timeline.scope_id = incident.scope_id
                      AND timeline.incident_id = incident.incident_id
                ),
                incident.opened_at
            )
        )
    FROM vital_relay_dispatch_downgrade_incidents AS affected
    WHERE incident.scope_id = affected.scope_id
      AND incident.incident_id = affected.incident_id
    """,
    """
    ALTER TABLE incident_timeline_entries
    ENABLE TRIGGER tr_incident_timeline_entries_append_only
    """,
    """
    ALTER TABLE incident_state_transitions
    ENABLE TRIGGER tr_incident_state_transitions_append_only
    """,
    "DROP TABLE vital_relay_dispatch_downgrade_incidents",
    "DROP TABLE responder_assignments",
    "DROP FUNCTION vital_relay_validate_responder_assignment()",
    "DROP TABLE responder_invitation_responses",
    "DROP TABLE responder_invitations",
    "DROP TABLE aed_sites",
    "DROP TABLE responder_locations",
    "DROP TABLE responder_availability",
    "DROP TABLE responder_skills",
    "DROP TABLE responders",
    """
    ALTER TABLE incident_timeline_entries
    DROP CONSTRAINT ck_incident_timeline_event_type
    """,
    """
    ALTER TABLE incident_timeline_entries
    ADD CONSTRAINT ck_incident_timeline_event_type CHECK (
        event_type IN (
            'wearable_event_received', 'incident_opened',
            'verification_started', 'check_in_recorded',
            'verification_timed_out', 'state_transitioned'
        )
    )
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
