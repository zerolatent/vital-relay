"""Add authenticated persona accounts and revocable installation sessions.

Revision ID: 0007_persona_sessions
Revises: 0006_incident_resolution
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007_persona_sessions"
down_revision: str | None = "0006_incident_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE persona_accounts (
        scope_id UUID NOT NULL,
        account_id UUID NOT NULL,
        display_name VARCHAR(128) NOT NULL,
        persona VARCHAR(16) NOT NULL,
        user_id VARCHAR(128),
        responder_id UUID,
        enrollment_token_hash VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, account_id),
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, responder_id)
            REFERENCES responders (scope_id, responder_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_persona_accounts_enrollment_token_hash
            UNIQUE (scope_id, enrollment_token_hash),
        CONSTRAINT ck_persona_accounts_display_name CHECK (
            char_length(display_name) BETWEEN 1 AND 128 AND
            display_name = btrim(display_name)
        ),
        CONSTRAINT ck_persona_accounts_persona
            CHECK (persona IN ('community', 'responder', 'command')),
        CONSTRAINT ck_persona_accounts_subject CHECK (
            (
                persona = 'community' AND
                user_id IS NOT NULL AND responder_id IS NULL
            ) OR (
                persona = 'responder' AND
                user_id IS NULL AND responder_id IS NOT NULL
            ) OR (
                persona = 'command' AND
                user_id IS NULL AND responder_id IS NULL
            )
        ),
        CONSTRAINT ck_persona_accounts_user_id CHECK (
            user_id IS NULL OR (
                char_length(user_id) BETWEEN 1 AND 128 AND
                user_id ~ '^[A-Za-z0-9._:-]+$'
            )
        ),
        CONSTRAINT ck_persona_accounts_enrollment_token_hash CHECK (
            enrollment_token_hash ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_persona_accounts_status
            CHECK (status IN ('active', 'revoked')),
        CONSTRAINT ck_persona_accounts_updated_at
            CHECK (updated_at >= created_at)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_persona_accounts_community_subject
    ON persona_accounts (scope_id, user_id)
    WHERE persona = 'community'
    """,
    """
    CREATE UNIQUE INDEX uq_persona_accounts_responder_subject
    ON persona_accounts (scope_id, responder_id)
    WHERE persona = 'responder'
    """,
    """
    CREATE INDEX ix_persona_accounts_scope_persona_status
    ON persona_accounts (scope_id, persona, status, account_id)
    """,
    """
    INSERT INTO persona_accounts (
        scope_id, account_id, display_name, persona, user_id, responder_id,
        enrollment_token_hash, status, created_at, updated_at
    )
    SELECT
        scope_id,
        responder_id,
        display_name,
        'responder',
        NULL,
        responder_id,
        access_token_hash,
        CASE WHEN status = 'active' THEN 'active' ELSE 'revoked' END,
        created_at,
        updated_at
    FROM responders
    """,
    """
    CREATE TABLE persona_sessions (
        scope_id UUID NOT NULL,
        session_id UUID NOT NULL,
        account_id UUID NOT NULL,
        installation_id UUID NOT NULL,
        access_token_hash VARCHAR(64) NOT NULL,
        refresh_token_hash VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        issued_at TIMESTAMP WITH TIME ZONE NOT NULL,
        rotated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        access_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        refresh_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        revoked_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (scope_id, session_id),
        FOREIGN KEY (scope_id, account_id)
            REFERENCES persona_accounts (scope_id, account_id)
            ON DELETE CASCADE,
        CONSTRAINT uq_persona_sessions_access_token_hash
            UNIQUE (scope_id, access_token_hash),
        CONSTRAINT uq_persona_sessions_refresh_token_hash
            UNIQUE (scope_id, refresh_token_hash),
        CONSTRAINT ck_persona_sessions_access_token_hash
            CHECK (access_token_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_persona_sessions_refresh_token_hash
            CHECK (refresh_token_hash ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_persona_sessions_status
            CHECK (status IN ('active', 'revoked')),
        CONSTRAINT ck_persona_sessions_expiration_order CHECK (
            issued_at <= rotated_at AND
            rotated_at < access_expires_at AND
            access_expires_at <= refresh_expires_at
        ),
        CONSTRAINT ck_persona_sessions_revocation_state CHECK (
            (status = 'active' AND revoked_at IS NULL) OR
            (status = 'revoked' AND revoked_at IS NOT NULL)
        ),
        CONSTRAINT ck_persona_sessions_revoked_at
            CHECK (revoked_at IS NULL OR revoked_at >= issued_at)
    )
    """,
    """
    CREATE UNIQUE INDEX uq_persona_sessions_account_installation_active
    ON persona_sessions (scope_id, account_id, installation_id)
    WHERE status = 'active'
    """,
    """
    CREATE INDEX ix_persona_sessions_scope_account_status
    ON persona_sessions (
        scope_id, account_id, status, refresh_expires_at, session_id
    )
    """,
    """
    CREATE INDEX ix_responder_invitations_scope_responder_status_created
    ON responder_invitations (
        scope_id, responder_id, status, created_at DESC, incident_id
    )
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP INDEX ix_responder_invitations_scope_responder_status_created",
    "DROP INDEX ix_persona_sessions_scope_account_status",
    "DROP INDEX uq_persona_sessions_account_installation_active",
    "DROP TABLE persona_sessions",
    "DROP INDEX ix_persona_accounts_scope_persona_status",
    "DROP INDEX uq_persona_accounts_responder_subject",
    "DROP INDEX uq_persona_accounts_community_subject",
    "DROP TABLE persona_accounts",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
