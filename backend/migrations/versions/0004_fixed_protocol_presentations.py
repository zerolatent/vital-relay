"""Persist append-only fixed first-aid protocol presentations.

Revision ID: 0004_protocol_presentations
Revises: 0003_postgis_dispatch
Create Date: 2026-07-18
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import UUID, uuid5

from alembic import op
from sqlalchemy import text

from vital_relay.domain.incidents import IncidentKind
from vital_relay.protocols.registry import FixedProtocolRegistry

revision: str = "0004_protocol_presentations"
down_revision: str | None = "0003_postgis_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID_NAMESPACE = UUID("fabd65cd-f0b6-48c0-8c70-3a854f1c9540")
_BACKFILL_PROTOCOLS = {
    IncidentKind.FALL: (
        "fall-response",
        "1.0.0",
        "ab3958b01c17d83e7ef8f1a33898ccd9a45833589c3652eca20adb3a757afcad",
    ),
    IncidentKind.MANUAL_SOS: (
        "manual-sos-response",
        "1.0.0",
        "547eeb0045bb60f8a2da8d6f775a9a9eda188934800fea062fb50726b1db31ee",
    ),
}


UPGRADE_STATEMENTS = (
    """
    ALTER TABLE responder_assignments
    ADD CONSTRAINT uq_responder_assignments_exact_link
    UNIQUE (scope_id, assignment_id, incident_id, responder_id)
    """,
    """
    CREATE TABLE protocol_presentations (
        scope_id UUID NOT NULL,
        presentation_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        assignment_id UUID NOT NULL,
        responder_id UUID NOT NULL,
        schema_version SMALLINT NOT NULL,
        protocol_id VARCHAR(128) NOT NULL,
        protocol_version VARCHAR(64) NOT NULL,
        emergency_kind VARCHAR(32) NOT NULL,
        content_sha256 VARCHAR(64) NOT NULL,
        presented_at TIMESTAMP WITH TIME ZONE NOT NULL,
        protocol_snapshot JSONB NOT NULL,
        simulated BOOLEAN NOT NULL DEFAULT false,
        PRIMARY KEY (scope_id, presentation_id),
        CONSTRAINT fk_protocol_presentations_exact_assignment
            FOREIGN KEY (
                scope_id, assignment_id, incident_id, responder_id
            )
            REFERENCES responder_assignments (
                scope_id, assignment_id, incident_id, responder_id
            )
            ON DELETE RESTRICT,
        CONSTRAINT uq_protocol_presentations_incident
            UNIQUE (scope_id, incident_id),
        CONSTRAINT uq_protocol_presentations_assignment
            UNIQUE (scope_id, assignment_id),
        CONSTRAINT ck_protocol_presentations_schema_version
            CHECK (schema_version > 0),
        CONSTRAINT ck_protocol_presentations_protocol_id
            CHECK (char_length(btrim(protocol_id)) BETWEEN 1 AND 128),
        CONSTRAINT ck_protocol_presentations_protocol_version
            CHECK (char_length(btrim(protocol_version)) BETWEEN 1 AND 64),
        CONSTRAINT ck_protocol_presentations_emergency_kind
            CHECK (emergency_kind IN ('fall', 'manual_sos')),
        CONSTRAINT ck_protocol_presentations_content_sha256
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_protocol_presentations_snapshot
            CHECK (jsonb_typeof(protocol_snapshot) = 'object'),
        CONSTRAINT ck_protocol_presentations_not_simulated
            CHECK (simulated = false)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_protocol_presentation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM responder_assignments AS assignment
            JOIN incidents AS incident
              ON incident.scope_id = assignment.scope_id
             AND incident.incident_id = assignment.incident_id
            WHERE assignment.scope_id = NEW.scope_id
              AND assignment.assignment_id = NEW.assignment_id
              AND assignment.incident_id = NEW.incident_id
              AND assignment.responder_id = NEW.responder_id
              AND assignment.accepted_at = NEW.presented_at
              AND incident.kind = NEW.emergency_kind
        ) THEN
            RAISE EXCEPTION
                'protocol presentation must match its accepted assignment and incident kind'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_protocol_presentations_validate_link
    BEFORE INSERT ON protocol_presentations
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_protocol_presentation()
    """,
    """
    CREATE TRIGGER tr_protocol_presentations_append_only
    BEFORE UPDATE OR DELETE ON protocol_presentations
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE protocol_presentations",
    "DROP FUNCTION vital_relay_validate_protocol_presentation()",
    """
    ALTER TABLE responder_assignments
    DROP CONSTRAINT uq_responder_assignments_exact_link
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)
    _backfill_existing_assignments()


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)


def _backfill_existing_assignments() -> None:
    """Attach the protected v1 protocol to every pre-Slice-06 assignment."""

    connection = op.get_bind()
    assignments = connection.execute(
        text(
            """
            SELECT assignment.scope_id,
                   assignment.assignment_id,
                   assignment.incident_id,
                   assignment.responder_id,
                   assignment.accepted_at,
                   incident.kind
            FROM responder_assignments AS assignment
            JOIN incidents AS incident
              ON incident.scope_id = assignment.scope_id
             AND incident.incident_id = assignment.incident_id
            ORDER BY assignment.scope_id, assignment.assignment_id
            """
        )
    ).mappings().all()
    registry = FixedProtocolRegistry()
    registry.validate_all()

    for assignment in assignments:
        protocol_identity = _BACKFILL_PROTOCOLS[IncidentKind(assignment["kind"])]
        protocol = registry.load_exact(*protocol_identity)
        presentation_id = uuid5(
            _ID_NAMESPACE,
            (
                f"{assignment['scope_id']}:protocol-presentation:"
                f"{assignment['incident_id']}"
            ),
        )
        connection.execute(
            text(
                """
                INSERT INTO protocol_presentations (
                    scope_id, presentation_id, incident_id, assignment_id,
                    responder_id, schema_version, protocol_id,
                    protocol_version, emergency_kind, content_sha256,
                    presented_at, protocol_snapshot, simulated
                ) VALUES (
                    :scope_id, :presentation_id, :incident_id, :assignment_id,
                    :responder_id, :schema_version, :protocol_id,
                    :protocol_version, :emergency_kind, :content_sha256,
                    :presented_at, CAST(:protocol_snapshot AS jsonb), false
                )
                """
            ),
            {
                "scope_id": assignment["scope_id"],
                "presentation_id": presentation_id,
                "incident_id": assignment["incident_id"],
                "assignment_id": assignment["assignment_id"],
                "responder_id": assignment["responder_id"],
                "schema_version": protocol.schema_version,
                "protocol_id": protocol.protocol_id,
                "protocol_version": protocol.version,
                "emergency_kind": protocol.emergency_kind.value,
                "content_sha256": protocol.content_sha256,
                "presented_at": assignment["accepted_at"],
                "protocol_snapshot": json.dumps(
                    protocol.model_dump(mode="json"),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        )
