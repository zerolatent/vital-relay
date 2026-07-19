"""Add immutable evolution releases and transactional activation.

Revision ID: 0009_evolution_activation
Revises: 0008_agent_control_plane
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_evolution_activation"
down_revision: str | None = "0008_agent_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    CREATE TABLE evolution_candidate_versions (
        scope_id UUID NOT NULL,
        target_sha256 VARCHAR(64) NOT NULL,
        release_kind VARCHAR(32) NOT NULL,
        candidate_bundle_sha256 VARCHAR(64) NOT NULL,
        candidate_sha256 VARCHAR(64) NOT NULL,
        policy_id VARCHAR(64) NOT NULL,
        policy_version VARCHAR(32) NOT NULL,
        policy_sha256 VARCHAR(64) NOT NULL,
        playbook_sha256 VARCHAR(64) NOT NULL,
        delta_log_sha256 VARCHAR(64) NOT NULL,
        improver_sha256 VARCHAR(64) NOT NULL,
        generator_role_sha256 VARCHAR(64) NOT NULL,
        generator_model_sha256 VARCHAR(64) NOT NULL,
        promotion_evidence_sha256 VARCHAR(64),
        development_report_sha256 VARCHAR(64) NOT NULL,
        development_report_signature_sha256 VARCHAR(64) NOT NULL,
        protected_validation_report_sha256 VARCHAR(64) NOT NULL,
        protected_validation_report_signature_sha256 VARCHAR(64) NOT NULL,
        final_report_sha256 VARCHAR(64) NOT NULL,
        final_cadence_receipt_sha256 VARCHAR(64) NOT NULL,
        final_cadence_artifact_sha256 VARCHAR(64) NOT NULL,
        paired_report_sha256 VARCHAR(64) NOT NULL,
        paired_report_artifact_sha256 VARCHAR(64) NOT NULL,
        ace_release_sha256 VARCHAR(64) NOT NULL,
        ace_release_artifact_sha256 VARCHAR(64) NOT NULL,
        active_baseline_version_sha256 VARCHAR(64) NOT NULL,
        active_baseline_candidate_sha256 VARCHAR(64) NOT NULL,
        active_baseline_policy_id VARCHAR(64) NOT NULL,
        active_baseline_policy_version VARCHAR(32) NOT NULL,
        active_baseline_policy_sha256 VARCHAR(64) NOT NULL,
        active_baseline_playbook_sha256 VARCHAR(64) NOT NULL,
        target_canonical_bytes BYTEA NOT NULL,
        evidence_canonical_bytes BYTEA,
        ace_release_canonical_bytes BYTEA NOT NULL,
        paired_report_canonical_bytes BYTEA NOT NULL,
        final_cadence_canonical_bytes BYTEA NOT NULL,
        archived_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, target_sha256),
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_evolution_candidate_versions_target_sha256
            CHECK (target_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_evolution_candidate_versions_release_kind CHECK (
            release_kind IN ('playbook_only', 'candidate_or_policy_change')
        ),
        CONSTRAINT ck_evolution_candidate_versions_candidate_hashes CHECK (
            candidate_bundle_sha256 ~ '^[0-9a-f]{64}$' AND
            candidate_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_policy CHECK (
            policy_id ~ '^[a-z][a-z0-9_-]{0,63}$' AND
            policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$' AND
            policy_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_release_hashes CHECK (
            playbook_sha256 ~ '^[0-9a-f]{64}$' AND
            delta_log_sha256 ~ '^[0-9a-f]{64}$' AND
            improver_sha256 ~ '^[0-9a-f]{64}$' AND
            generator_role_sha256 ~ '^[0-9a-f]{64}$' AND
            generator_model_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_evidence_hashes CHECK (
            development_report_sha256 ~ '^[0-9a-f]{64}$' AND
            development_report_signature_sha256 ~ '^[0-9a-f]{64}$' AND
            protected_validation_report_sha256 ~ '^[0-9a-f]{64}$' AND
            protected_validation_report_signature_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_final_hashes CHECK (
            final_report_sha256 ~ '^[0-9a-f]{64}$' AND
            final_cadence_receipt_sha256 ~ '^[0-9a-f]{64}$' AND
            final_cadence_artifact_sha256 ~ '^[0-9a-f]{64}$' AND
            paired_report_sha256 ~ '^[0-9a-f]{64}$' AND
            paired_report_artifact_sha256 ~ '^[0-9a-f]{64}$' AND
            ace_release_sha256 ~ '^[0-9a-f]{64}$' AND
            ace_release_artifact_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_baseline_hashes CHECK (
            active_baseline_version_sha256 ~ '^[0-9a-f]{64}$' AND
            active_baseline_candidate_sha256 ~ '^[0-9a-f]{64}$' AND
            active_baseline_policy_id ~ '^[a-z][a-z0-9_-]{0,63}$' AND
            active_baseline_policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$' AND
            active_baseline_policy_sha256 ~ '^[0-9a-f]{64}$' AND
            active_baseline_playbook_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_candidate_versions_evidence_mode CHECK (
            (
                release_kind = 'playbook_only' AND
                promotion_evidence_sha256 IS NULL AND
                evidence_canonical_bytes IS NULL AND
                candidate_sha256 = active_baseline_candidate_sha256 AND
                policy_id = active_baseline_policy_id AND
                policy_version = active_baseline_policy_version AND
                policy_sha256 = active_baseline_policy_sha256 AND
                playbook_sha256 <> active_baseline_playbook_sha256
            ) OR (
                release_kind = 'candidate_or_policy_change' AND
                promotion_evidence_sha256 ~ '^[0-9a-f]{64}$' AND
                evidence_canonical_bytes IS NOT NULL AND
                candidate_sha256 <> active_baseline_candidate_sha256
            )
        ),
        CONSTRAINT ck_evolution_candidate_versions_byte_bounds CHECK (
            octet_length(target_canonical_bytes) BETWEEN 1 AND 16000000 AND
            (
                evidence_canonical_bytes IS NULL OR
                octet_length(evidence_canonical_bytes) BETWEEN 1 AND 16000000
            ) AND
            octet_length(ace_release_canonical_bytes) BETWEEN 1 AND 16000000 AND
            octet_length(paired_report_canonical_bytes) BETWEEN 1 AND 16000000 AND
            octet_length(final_cadence_canonical_bytes) BETWEEN 1 AND 16000000
        )
    )
    """,
    """
    CREATE INDEX ix_evolution_candidate_versions_scope_archived
    ON evolution_candidate_versions (scope_id, archived_at DESC, target_sha256 DESC)
    """,
    """
    CREATE FUNCTION vital_relay_reject_evolution_immutable_change()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' AND NOT EXISTS (
            SELECT 1 FROM demo_scopes WHERE scope_id = OLD.scope_id
        ) THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'evolution release history is append-only'
            USING ERRCODE = '23514';
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_evolution_candidate_versions_append_only
    BEFORE UPDATE OR DELETE ON evolution_candidate_versions
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_evolution_immutable_change()
    """,
    """
    CREATE TABLE evolution_active_versions (
        scope_id UUID PRIMARY KEY,
        active_version_sha256 VARCHAR(64) NOT NULL,
        active_candidate_sha256 VARCHAR(64) NOT NULL,
        previous_version_sha256 VARCHAR(64),
        previous_candidate_sha256 VARCHAR(64),
        revision BIGINT NOT NULL,
        activated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        activated_by_account_id UUID NOT NULL,
        activated_by_session_id UUID NOT NULL,
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, active_version_sha256)
            REFERENCES evolution_candidate_versions (scope_id, target_sha256)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, previous_version_sha256)
            REFERENCES evolution_candidate_versions (scope_id, target_sha256)
            ON DELETE RESTRICT,
        FOREIGN KEY (
            scope_id, activated_by_session_id, activated_by_account_id
        ) REFERENCES persona_sessions (scope_id, session_id, account_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_evolution_active_versions_active_hashes CHECK (
            active_version_sha256 ~ '^[0-9a-f]{64}$' AND
            active_candidate_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_active_versions_previous_version CHECK (
            previous_version_sha256 IS NULL OR
            previous_version_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_active_versions_previous_candidate CHECK (
            previous_candidate_sha256 IS NULL OR
            previous_candidate_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_active_versions_previous_pair CHECK (
            (previous_version_sha256 IS NULL) =
            (previous_candidate_sha256 IS NULL)
        ),
        CONSTRAINT ck_evolution_active_versions_revision CHECK (revision >= 0)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_evolution_command_principal(
        requested_scope_id UUID,
        requested_session_id UUID,
        requested_account_id UUID,
        as_of TIMESTAMP WITH TIME ZONE
    ) RETURNS boolean
    LANGUAGE sql
    STABLE
    AS $$
        SELECT EXISTS (
            SELECT 1
            FROM persona_sessions AS session
            JOIN persona_accounts AS account
              ON account.scope_id = session.scope_id
             AND account.account_id = session.account_id
            WHERE session.scope_id = requested_scope_id
              AND session.session_id = requested_session_id
              AND session.account_id = requested_account_id
              AND session.status = 'active'
              AND session.rotated_at <= as_of
              AND session.access_expires_at > as_of
              AND account.status = 'active'
              AND account.persona = 'command'
        )
    $$
    """,
    """
    CREATE FUNCTION vital_relay_validate_evolution_active_version()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        retained_candidate_sha256 VARCHAR(64);
    BEGIN
        SELECT candidate_sha256 INTO retained_candidate_sha256
        FROM evolution_candidate_versions
        WHERE scope_id = NEW.scope_id
          AND target_sha256 = NEW.active_version_sha256;
        IF retained_candidate_sha256 IS NULL OR
           retained_candidate_sha256 <> NEW.active_candidate_sha256 THEN
            RAISE EXCEPTION 'active evolution pointer does not match retained release'
                USING ERRCODE = '23514';
        END IF;
        IF NOT vital_relay_validate_evolution_command_principal(
            NEW.scope_id,
            NEW.activated_by_session_id,
            NEW.activated_by_account_id,
            NEW.activated_at
        ) THEN
            RAISE EXCEPTION 'evolution activation requires an active command session'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.revision <> 0 OR NEW.previous_version_sha256 IS NOT NULL OR
               NEW.previous_candidate_sha256 IS NOT NULL THEN
                RAISE EXCEPTION 'initial evolution pointer must be revision zero'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.scope_id IS DISTINCT FROM OLD.scope_id OR
              NEW.revision <> OLD.revision + 1 OR
              NEW.previous_version_sha256 IS DISTINCT FROM
                  OLD.active_version_sha256 OR
              NEW.previous_candidate_sha256 IS DISTINCT FROM
                  OLD.active_candidate_sha256 OR
              NEW.active_version_sha256 = OLD.active_version_sha256 OR
              NEW.activated_at < OLD.activated_at THEN
            RAISE EXCEPTION 'evolution pointer update is not a monotonic release CAS'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_evolution_active_versions_validate
    BEFORE INSERT OR UPDATE ON evolution_active_versions
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_evolution_active_version()
    """,
    """
    CREATE TABLE evolution_command_approvals (
        scope_id UUID NOT NULL,
        approval_id UUID NOT NULL,
        action VARCHAR(16) NOT NULL,
        target_version_sha256 VARCHAR(64) NOT NULL,
        evidence_sha256 VARCHAR(64),
        expected_pointer_revision BIGINT NOT NULL,
        account_id UUID NOT NULL,
        session_id UUID NOT NULL,
        approved_at TIMESTAMP WITH TIME ZONE NOT NULL,
        consumed_at TIMESTAMP WITH TIME ZONE NOT NULL,
        resulting_pointer_revision BIGINT NOT NULL,
        PRIMARY KEY (scope_id, approval_id),
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, target_version_sha256)
            REFERENCES evolution_candidate_versions (scope_id, target_sha256)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, session_id, account_id)
            REFERENCES persona_sessions (scope_id, session_id, account_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_evolution_command_approvals_action
            CHECK (action IN ('promote', 'rollback')),
        CONSTRAINT ck_evolution_command_approvals_target
            CHECK (target_version_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_evolution_command_approvals_evidence CHECK (
            (
                action = 'promote' AND
                (
                    evidence_sha256 IS NULL OR
                    evidence_sha256 ~ '^[0-9a-f]{64}$'
                )
            ) OR (
                action = 'rollback' AND evidence_sha256 IS NULL
            )
        ),
        CONSTRAINT ck_evolution_command_approvals_revisions CHECK (
            expected_pointer_revision >= 0 AND
            resulting_pointer_revision = expected_pointer_revision + 1
        ),
        CONSTRAINT ck_evolution_command_approvals_consumed_at
            CHECK (consumed_at >= approved_at)
    )
    """,
    """
    CREATE INDEX ix_evolution_command_approvals_scope_consumed
    ON evolution_command_approvals (scope_id, consumed_at DESC, approval_id DESC)
    """,
    """
    CREATE FUNCTION vital_relay_validate_evolution_approval()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    DECLARE
        retained_evidence_sha256 VARCHAR(64);
    BEGIN
        IF NOT vital_relay_validate_evolution_command_principal(
            NEW.scope_id, NEW.session_id, NEW.account_id, NEW.approved_at
        ) THEN
            RAISE EXCEPTION 'evolution approval requires an active command session'
                USING ERRCODE = '23514';
        END IF;
        SELECT promotion_evidence_sha256 INTO retained_evidence_sha256
        FROM evolution_candidate_versions
        WHERE scope_id = NEW.scope_id
          AND target_sha256 = NEW.target_version_sha256;
        IF NOT FOUND OR (
            NEW.action = 'promote' AND
            retained_evidence_sha256 IS DISTINCT FROM NEW.evidence_sha256
        ) THEN
            RAISE EXCEPTION 'evolution approval does not bind the retained release'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_evolution_command_approvals_validate
    BEFORE INSERT ON evolution_command_approvals
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_evolution_approval()
    """,
    """
    CREATE TRIGGER tr_evolution_command_approvals_append_only
    BEFORE UPDATE OR DELETE ON evolution_command_approvals
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_evolution_immutable_change()
    """,
    """
    CREATE TABLE evolution_promotion_events (
        scope_id UUID NOT NULL,
        event_id UUID NOT NULL,
        approval_id UUID NOT NULL,
        action VARCHAR(16) NOT NULL,
        from_version_sha256 VARCHAR(64) NOT NULL,
        to_version_sha256 VARCHAR(64) NOT NULL,
        from_candidate_sha256 VARCHAR(64) NOT NULL,
        to_candidate_sha256 VARCHAR(64) NOT NULL,
        evidence_sha256 VARCHAR(64),
        pointer_revision BIGINT NOT NULL,
        account_id UUID NOT NULL,
        session_id UUID NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (scope_id, event_id),
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, approval_id)
            REFERENCES evolution_command_approvals (scope_id, approval_id)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, from_version_sha256)
            REFERENCES evolution_candidate_versions (scope_id, target_sha256)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, to_version_sha256)
            REFERENCES evolution_candidate_versions (scope_id, target_sha256)
            ON DELETE RESTRICT,
        FOREIGN KEY (scope_id, session_id, account_id)
            REFERENCES persona_sessions (scope_id, session_id, account_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_evolution_promotion_events_approval
            UNIQUE (scope_id, approval_id),
        CONSTRAINT uq_evolution_promotion_events_pointer_revision
            UNIQUE (scope_id, pointer_revision),
        CONSTRAINT ck_evolution_promotion_events_action
            CHECK (action IN ('promote', 'rollback')),
        CONSTRAINT ck_evolution_promotion_events_hashes CHECK (
            from_version_sha256 ~ '^[0-9a-f]{64}$' AND
            to_version_sha256 ~ '^[0-9a-f]{64}$' AND
            from_candidate_sha256 ~ '^[0-9a-f]{64}$' AND
            to_candidate_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_evolution_promotion_events_changed
            CHECK (from_version_sha256 <> to_version_sha256),
        CONSTRAINT ck_evolution_promotion_events_evidence CHECK (
            (
                action = 'promote' AND
                (
                    evidence_sha256 IS NULL OR
                    evidence_sha256 ~ '^[0-9a-f]{64}$'
                )
            ) OR (
                action = 'rollback' AND evidence_sha256 IS NULL
            )
        ),
        CONSTRAINT ck_evolution_promotion_events_revision
            CHECK (pointer_revision >= 1)
    )
    """,
    """
    CREATE INDEX ix_evolution_promotion_events_scope_occurred
    ON evolution_promotion_events (scope_id, occurred_at DESC, event_id DESC)
    """,
    """
    CREATE FUNCTION vital_relay_validate_evolution_promotion_event()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM evolution_command_approvals AS approval
            WHERE approval.scope_id = NEW.scope_id
              AND approval.approval_id = NEW.approval_id
              AND approval.action = NEW.action
              AND approval.target_version_sha256 = NEW.to_version_sha256
              AND approval.evidence_sha256 IS NOT DISTINCT FROM NEW.evidence_sha256
              AND approval.account_id = NEW.account_id
              AND approval.session_id = NEW.session_id
              AND approval.resulting_pointer_revision = NEW.pointer_revision
              AND approval.consumed_at = NEW.occurred_at
        ) THEN
            RAISE EXCEPTION 'promotion event does not match consumed approval'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM evolution_active_versions AS active
            WHERE active.scope_id = NEW.scope_id
              AND active.active_version_sha256 = NEW.to_version_sha256
              AND active.active_candidate_sha256 = NEW.to_candidate_sha256
              AND active.previous_version_sha256 = NEW.from_version_sha256
              AND active.previous_candidate_sha256 = NEW.from_candidate_sha256
              AND active.revision = NEW.pointer_revision
        ) THEN
            RAISE EXCEPTION 'promotion event does not match committed pointer state'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_evolution_promotion_events_validate
    BEFORE INSERT ON evolution_promotion_events
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_evolution_promotion_event()
    """,
    """
    CREATE TRIGGER tr_evolution_promotion_events_append_only
    BEFORE UPDATE OR DELETE ON evolution_promotion_events
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_evolution_immutable_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE evolution_promotion_events",
    "DROP FUNCTION vital_relay_validate_evolution_promotion_event()",
    "DROP TABLE evolution_command_approvals",
    "DROP FUNCTION vital_relay_validate_evolution_approval()",
    "DROP TABLE evolution_active_versions",
    "DROP FUNCTION vital_relay_validate_evolution_active_version()",
    "DROP FUNCTION vital_relay_validate_evolution_command_principal("
    "UUID, UUID, UUID, TIMESTAMP WITH TIME ZONE)",
    "DROP TABLE evolution_candidate_versions",
    "DROP FUNCTION vital_relay_reject_evolution_immutable_change()",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
