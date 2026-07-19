"""Add durable live-agent runs, policy activation, proxy audit, and replay.

Revision ID: 0008_agent_control_plane
Revises: 0007_persona_sessions
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_agent_control_plane"
down_revision: str | None = "0007_persona_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_STATEMENTS = (
    """
    ALTER TABLE persona_sessions
    ADD CONSTRAINT uq_persona_sessions_exact_account
    UNIQUE (scope_id, session_id, account_id)
    """,
    """
    CREATE TABLE agent_active_policies (
        scope_id UUID PRIMARY KEY,
        policy_id VARCHAR(64) NOT NULL,
        policy_version VARCHAR(32) NOT NULL,
        policy_sha256 VARCHAR(64) NOT NULL,
        revision BIGINT NOT NULL,
        activated_at TIMESTAMP WITH TIME ZONE NOT NULL,
        activated_by_account_id UUID NOT NULL,
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        FOREIGN KEY (scope_id, activated_by_account_id)
            REFERENCES persona_accounts (scope_id, account_id)
            ON DELETE RESTRICT,
        CONSTRAINT ck_agent_active_policies_id
            CHECK (policy_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
        CONSTRAINT ck_agent_active_policies_version
            CHECK (policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'),
        CONSTRAINT ck_agent_active_policies_sha256
            CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_agent_active_policies_revision
            CHECK (revision >= 1)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_active_agent_policy()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM persona_accounts AS account
            WHERE account.scope_id = NEW.scope_id
              AND account.account_id = NEW.activated_by_account_id
              AND account.persona = 'command'
              AND account.status = 'active'
        ) THEN
            RAISE EXCEPTION 'agent policy activation requires an active command account'
                USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'INSERT' AND NEW.revision <> 1 THEN
            RAISE EXCEPTION 'initial agent policy revision must be one'
                USING ERRCODE = '23514';
        ELSIF TG_OP = 'UPDATE' AND (
            NEW.scope_id IS DISTINCT FROM OLD.scope_id OR
            NEW.revision <> OLD.revision + 1 OR
            NEW.activated_at < OLD.activated_at OR
            NEW.policy_sha256 = OLD.policy_sha256
        ) THEN
            RAISE EXCEPTION 'agent policy pointer update is not a new revision'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_agent_active_policies_validate
    BEFORE INSERT OR UPDATE ON agent_active_policies
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_active_agent_policy()
    """,
    """
    CREATE TABLE agent_runs (
        scope_id UUID NOT NULL,
        run_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        incident_state_version INTEGER NOT NULL,
        schema_version SMALLINT NOT NULL,
        objective VARCHAR(64) NOT NULL,
        requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        lease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        requested_by_account_id UUID NOT NULL,
        requested_by_session_id UUID NOT NULL,
        policy_id VARCHAR(64) NOT NULL,
        policy_version VARCHAR(32) NOT NULL,
        policy_sha256 VARCHAR(64) NOT NULL,
        max_total_tool_calls SMALLINT NOT NULL,
        max_mutating_tool_calls SMALLINT NOT NULL,
        total_tool_calls SMALLINT NOT NULL DEFAULT 0,
        mutating_tool_calls SMALLINT NOT NULL DEFAULT 0,
        model_id VARCHAR(200) NOT NULL,
        sandbox VARCHAR(16) NOT NULL,
        status VARCHAR(24) NOT NULL,
        started_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        tool_trace JSONB NOT NULL,
        action_summary VARCHAR(500),
        failure_code VARCHAR(64),
        PRIMARY KEY (scope_id, run_id),
        FOREIGN KEY (scope_id, incident_id)
            REFERENCES incidents (scope_id, incident_id)
            ON DELETE CASCADE,
        FOREIGN KEY (
            scope_id, requested_by_session_id, requested_by_account_id
        ) REFERENCES persona_sessions (scope_id, session_id, account_id)
            ON DELETE RESTRICT,
        CONSTRAINT uq_agent_runs_exact_incident
            UNIQUE (scope_id, run_id, incident_id),
        CONSTRAINT ck_agent_runs_schema_version CHECK (schema_version > 0),
        CONSTRAINT ck_agent_runs_state_version
            CHECK (incident_state_version >= 1),
        CONSTRAINT ck_agent_runs_objective
            CHECK (objective = 'coordinate_emergency_response'),
        CONSTRAINT ck_agent_runs_policy_id
            CHECK (policy_id ~ '^[a-z][a-z0-9_-]{0,63}$'),
        CONSTRAINT ck_agent_runs_policy_version
            CHECK (policy_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'),
        CONSTRAINT ck_agent_runs_policy_sha256
            CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_agent_runs_model_id CHECK (
            char_length(model_id) BETWEEN 1 AND 200 AND
            model_id = btrim(model_id)
        ),
        CONSTRAINT ck_agent_runs_sandbox
            CHECK (sandbox IN ('in_process', 'nemoclaw', 'docker')),
        CONSTRAINT ck_agent_runs_status
            CHECK (status IN ('running', 'completed', 'manual_required')),
        CONSTRAINT ck_agent_runs_creation_time
            CHECK (created_at >= requested_at),
        CONSTRAINT ck_agent_runs_lease
            CHECK (lease_expires_at > created_at),
        CONSTRAINT ck_agent_runs_tool_budget CHECK (
            max_total_tool_calls BETWEEN 1 AND 50 AND
            max_mutating_tool_calls BETWEEN 0 AND 10 AND
            max_mutating_tool_calls <= max_total_tool_calls
        ),
        CONSTRAINT ck_agent_runs_tool_usage CHECK (
            total_tool_calls BETWEEN 0 AND max_total_tool_calls AND
            mutating_tool_calls BETWEEN 0 AND max_mutating_tool_calls AND
            mutating_tool_calls <= total_tool_calls
        ),
        CONSTRAINT ck_agent_runs_tool_trace
            CHECK (jsonb_typeof(tool_trace) = 'array'),
        CONSTRAINT ck_agent_runs_result_state CHECK (
            (
                status = 'running' AND started_at IS NULL AND
                finished_at IS NULL AND tool_trace = '[]'::jsonb AND
                action_summary IS NULL AND failure_code IS NULL
            ) OR (
                status = 'completed' AND started_at IS NOT NULL AND
                finished_at >= started_at AND started_at >= requested_at AND
                finished_at <= lease_expires_at AND
                action_summary IS NOT NULL AND failure_code IS NULL
            ) OR (
                status = 'manual_required' AND started_at IS NOT NULL AND
                finished_at >= started_at AND started_at >= requested_at AND
                finished_at <= lease_expires_at AND
                action_summary IS NULL AND failure_code IS NOT NULL
            )
        ),
        CONSTRAINT ck_agent_runs_action_summary CHECK (
            action_summary IS NULL OR
            char_length(action_summary) BETWEEN 1 AND 500
        ),
        CONSTRAINT ck_agent_runs_failure_code CHECK (
            failure_code IS NULL OR failure_code IN (
                'model_timeout', 'model_unavailable', 'invalid_model_output',
                'tool_denied', 'tool_failed', 'agent_requested_human',
                'policy_invalid', 'runner_error'
            )
        )
    )
    """,
    """
    CREATE UNIQUE INDEX uq_agent_runs_scope_incident_running
    ON agent_runs (scope_id, incident_id)
    WHERE status = 'running'
    """,
    """
    CREATE INDEX ix_agent_runs_scope_incident_requested
    ON agent_runs (scope_id, incident_id, requested_at DESC, run_id DESC)
    """,
    """
    CREATE FUNCTION vital_relay_validate_agent_run_start()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM persona_sessions AS session
            JOIN persona_accounts AS account
              ON account.scope_id = session.scope_id
             AND account.account_id = session.account_id
            WHERE session.scope_id = NEW.scope_id
              AND session.session_id = NEW.requested_by_session_id
              AND session.account_id = NEW.requested_by_account_id
              AND session.status = 'active'
              AND session.access_expires_at > NEW.created_at
              AND account.persona = 'command'
              AND account.status = 'active'
        ) THEN
            RAISE EXCEPTION 'agent run requires an active command session'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM incidents AS incident
            WHERE incident.scope_id = NEW.scope_id
              AND incident.incident_id = NEW.incident_id
              AND incident.current_state IN ('escalating', 'response_active')
              AND incident.state_version = NEW.incident_state_version
        ) THEN
            RAISE EXCEPTION 'agent run requires the current active incident version'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM agent_active_policies AS policy
            WHERE policy.scope_id = NEW.scope_id
              AND policy.policy_id = NEW.policy_id
              AND policy.policy_version = NEW.policy_version
              AND policy.policy_sha256 = NEW.policy_sha256
        ) THEN
            RAISE EXCEPTION 'agent run requires the active policy identity'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_agent_runs_validate_start
    BEFORE INSERT ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_agent_run_start()
    """,
    """
    CREATE FUNCTION vital_relay_validate_agent_run_change()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'agent run cannot be deleted'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'running' AND NEW.status = 'running' THEN
            IF NEW.total_tool_calls <> OLD.total_tool_calls + 1 OR
               NEW.mutating_tool_calls NOT IN (
                   OLD.mutating_tool_calls,
                   OLD.mutating_tool_calls + 1
               ) OR
               ROW(
                   NEW.scope_id, NEW.run_id, NEW.incident_id,
                   NEW.incident_state_version, NEW.schema_version, NEW.objective,
                   NEW.requested_at, NEW.created_at, NEW.lease_expires_at,
                   NEW.requested_by_account_id, NEW.requested_by_session_id,
                   NEW.policy_id, NEW.policy_version, NEW.policy_sha256,
                   NEW.max_total_tool_calls, NEW.max_mutating_tool_calls,
                   NEW.model_id, NEW.sandbox,
                   NEW.started_at, NEW.finished_at, NEW.tool_trace,
                   NEW.action_summary, NEW.failure_code
               ) IS DISTINCT FROM ROW(
                   OLD.scope_id, OLD.run_id, OLD.incident_id,
                   OLD.incident_state_version, OLD.schema_version, OLD.objective,
                   OLD.requested_at, OLD.created_at, OLD.lease_expires_at,
                   OLD.requested_by_account_id, OLD.requested_by_session_id,
                   OLD.policy_id, OLD.policy_version, OLD.policy_sha256,
                   OLD.max_total_tool_calls, OLD.max_mutating_tool_calls,
                   OLD.model_id, OLD.sandbox,
                   OLD.started_at, OLD.finished_at, OLD.tool_trace,
                   OLD.action_summary, OLD.failure_code
               ) THEN
                RAISE EXCEPTION 'agent run tool usage permits one reservation'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status <> 'running' OR
           NEW.status NOT IN ('completed', 'manual_required') OR
           ROW(
               NEW.scope_id, NEW.run_id, NEW.incident_id,
               NEW.incident_state_version, NEW.schema_version, NEW.objective,
               NEW.requested_at, NEW.created_at, NEW.lease_expires_at,
               NEW.requested_by_account_id, NEW.requested_by_session_id,
               NEW.policy_id, NEW.policy_version, NEW.policy_sha256,
               NEW.max_total_tool_calls, NEW.max_mutating_tool_calls,
               NEW.total_tool_calls, NEW.mutating_tool_calls,
               NEW.model_id, NEW.sandbox
           ) IS DISTINCT FROM ROW(
               OLD.scope_id, OLD.run_id, OLD.incident_id,
               OLD.incident_state_version, OLD.schema_version, OLD.objective,
               OLD.requested_at, OLD.created_at, OLD.lease_expires_at,
               OLD.requested_by_account_id, OLD.requested_by_session_id,
               OLD.policy_id, OLD.policy_version, OLD.policy_sha256,
               OLD.max_total_tool_calls, OLD.max_mutating_tool_calls,
               OLD.total_tool_calls, OLD.mutating_tool_calls,
               OLD.model_id, OLD.sandbox
           ) THEN
            RAISE EXCEPTION 'agent run permits only one terminal transition'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_agent_runs_validate_change
    BEFORE UPDATE OR DELETE ON agent_runs
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_agent_run_change()
    """,
    """
    CREATE TABLE agent_run_tool_budgets (
        scope_id UUID NOT NULL,
        run_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        tool_name VARCHAR(64) NOT NULL,
        effect VARCHAR(16) NOT NULL,
        max_calls SMALLINT NOT NULL,
        calls_used SMALLINT NOT NULL DEFAULT 0,
        PRIMARY KEY (scope_id, run_id, tool_name),
        FOREIGN KEY (scope_id, run_id, incident_id)
            REFERENCES agent_runs (scope_id, run_id, incident_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_agent_run_tool_budgets_name
            CHECK (tool_name ~ '^[a-z][a-z0-9_]{0,63}$'),
        CONSTRAINT ck_agent_run_tool_budgets_effect
            CHECK (effect IN ('read', 'mutate')),
        CONSTRAINT ck_agent_run_tool_budgets_max_calls
            CHECK (max_calls BETWEEN 1 AND 20),
        CONSTRAINT ck_agent_run_tool_budgets_usage
            CHECK (calls_used BETWEEN 0 AND max_calls)
    )
    """,
    """
    CREATE FUNCTION vital_relay_validate_agent_tool_budget_change()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'agent run tool budget cannot be deleted'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.calls_used <> OLD.calls_used + 1 OR
           ROW(
               NEW.scope_id, NEW.run_id, NEW.incident_id,
               NEW.tool_name, NEW.effect, NEW.max_calls
           ) IS DISTINCT FROM ROW(
               OLD.scope_id, OLD.run_id, OLD.incident_id,
               OLD.tool_name, OLD.effect, OLD.max_calls
           ) OR NOT EXISTS (
               SELECT 1 FROM agent_runs AS run
               JOIN agent_active_policies AS policy
                 ON policy.scope_id = run.scope_id
                AND policy.policy_id = run.policy_id
                AND policy.policy_version = run.policy_version
                AND policy.policy_sha256 = run.policy_sha256
               WHERE run.scope_id = OLD.scope_id
                 AND run.run_id = OLD.run_id
                 AND run.incident_id = OLD.incident_id
                 AND run.status = 'running'
                 AND run.lease_expires_at > statement_timestamp()
           ) THEN
            RAISE EXCEPTION 'agent tool budget permits one active-run reservation'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_agent_run_tool_budgets_validate_change
    BEFORE UPDATE OR DELETE ON agent_run_tool_budgets
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_agent_tool_budget_change()
    """,
    """
    CREATE TABLE agent_tool_proxy_audits (
        scope_id UUID NOT NULL,
        audit_id UUID NOT NULL,
        invocation_id UUID NOT NULL,
        occurred_at TIMESTAMP WITH TIME ZONE NOT NULL,
        requested_scope_id VARCHAR(128) NOT NULL,
        requested_run_id UUID NOT NULL,
        requested_incident_id UUID NOT NULL,
        requested_policy_sha256 VARCHAR(64) NOT NULL,
        granted_scope_id VARCHAR(128),
        granted_run_id UUID,
        granted_incident_id UUID,
        granted_state_version INTEGER,
        granted_policy_sha256 VARCHAR(64),
        tool_name VARCHAR(64) NOT NULL,
        effect VARCHAR(16),
        status VARCHAR(16) NOT NULL,
        idempotency_key UUID,
        request_sha256 VARCHAR(64) NOT NULL,
        result_sha256 VARCHAR(64),
        error_code VARCHAR(64),
        PRIMARY KEY (scope_id, audit_id),
        FOREIGN KEY (scope_id)
            REFERENCES demo_scopes (scope_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_agent_tool_audits_requested_scope
            CHECK (
                requested_scope_id ~
                '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            ),
        CONSTRAINT ck_agent_tool_audits_granted_scope CHECK (
            granted_scope_id IS NULL OR
            granted_scope_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
        ),
        CONSTRAINT ck_agent_tool_audits_requested_policy
            CHECK (requested_policy_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_agent_tool_audits_granted_policy CHECK (
            granted_policy_sha256 IS NULL OR
            granted_policy_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_agent_tool_audits_request_hash
            CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_agent_tool_audits_result_hash CHECK (
            result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'
        ),
        CONSTRAINT ck_agent_tool_audits_tool_name
            CHECK (tool_name ~ '^[a-z][a-z0-9_]{0,63}$'),
        CONSTRAINT ck_agent_tool_audits_effect
            CHECK (effect IS NULL OR effect IN ('read', 'mutate')),
        CONSTRAINT ck_agent_tool_audits_status CHECK (
            status IN ('started', 'completed', 'replayed', 'denied', 'failed')
        ),
        CONSTRAINT ck_agent_tool_audits_error_code CHECK (
            error_code IS NULL OR error_code IN (
                'invalid_capability', 'expired_capability', 'wrong_run',
                'wrong_scope', 'wrong_incident', 'policy_mismatch',
                'run_not_active', 'tool_not_registered', 'tool_not_allowed',
                'tool_budget_exceeded', 'invalid_arguments',
                'stale_state', 'incident_not_active', 'idempotency_required',
                'idempotency_conflict', 'idempotency_capacity_exceeded',
                'idempotency_in_doubt', 'application_failed', 'invalid_result',
                'audit_unavailable'
            )
        ),
        CONSTRAINT ck_agent_tool_audits_grant CHECK (
            (
                granted_scope_id IS NULL AND granted_run_id IS NULL AND
                granted_incident_id IS NULL AND
                granted_state_version IS NULL AND
                granted_policy_sha256 IS NULL
            ) OR (
                granted_scope_id IS NOT NULL AND granted_run_id IS NOT NULL AND
                granted_incident_id IS NOT NULL AND
                granted_state_version >= 1 AND
                granted_policy_sha256 IS NOT NULL
            )
        ),
        CONSTRAINT ck_agent_tool_audits_outcome CHECK (
            (
                status IN ('completed', 'replayed') AND
                result_sha256 IS NOT NULL AND error_code IS NULL
            ) OR (
                status = 'started' AND result_sha256 IS NULL AND
                error_code IS NULL AND effect = 'mutate'
            ) OR (
                status IN ('denied', 'failed') AND
                result_sha256 IS NULL AND error_code IS NOT NULL
            )
        )
    )
    """,
    """
    CREATE INDEX ix_agent_tool_audits_scope_run_time
    ON agent_tool_proxy_audits (
        scope_id, requested_run_id, occurred_at, audit_id
    )
    """,
    """
    CREATE INDEX ix_agent_tool_audits_scope_granted_run_time
    ON agent_tool_proxy_audits (
        scope_id, granted_run_id, occurred_at, audit_id
    )
    WHERE granted_run_id IS NOT NULL
    """,
    """
    CREATE INDEX ix_agent_tool_audits_scope_incident_time
    ON agent_tool_proxy_audits (
        scope_id, requested_incident_id, occurred_at, audit_id
    )
    """,
    """
    CREATE TRIGGER tr_agent_tool_proxy_audits_append_only
    BEFORE UPDATE OR DELETE ON agent_tool_proxy_audits
    FOR EACH ROW EXECUTE FUNCTION vital_relay_reject_incident_audit_change()
    """,
    """
    CREATE TABLE agent_tool_idempotency (
        scope_id UUID NOT NULL,
        run_id UUID NOT NULL,
        incident_id UUID NOT NULL,
        tool_name VARCHAR(64) NOT NULL,
        idempotency_key UUID NOT NULL,
        request_sha256 VARCHAR(64) NOT NULL,
        status VARCHAR(16) NOT NULL,
        result JSONB,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
        completed_at TIMESTAMP WITH TIME ZONE,
        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (
            scope_id, run_id, incident_id, tool_name, idempotency_key
        ),
        FOREIGN KEY (scope_id, run_id, incident_id)
            REFERENCES agent_runs (scope_id, run_id, incident_id)
            ON DELETE CASCADE,
        CONSTRAINT ck_agent_tool_idempotency_tool_name
            CHECK (tool_name ~ '^[a-z][a-z0-9_]{0,63}$'),
        CONSTRAINT ck_agent_tool_idempotency_request_hash
            CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
        CONSTRAINT ck_agent_tool_idempotency_status
            CHECK (status IN ('in_doubt', 'completed')),
        CONSTRAINT ck_agent_tool_idempotency_expiration
            CHECK (expires_at > created_at),
        CONSTRAINT ck_agent_tool_idempotency_outcome CHECK (
            (
                status = 'in_doubt' AND result IS NULL AND
                completed_at IS NULL
            ) OR (
                status = 'completed' AND result IS NOT NULL AND
                completed_at IS NOT NULL AND completed_at >= created_at
            )
        )
    )
    """,
    """
    CREATE INDEX ix_agent_tool_idempotency_scope_expiry
    ON agent_tool_idempotency (scope_id, expires_at)
    """,
    """
    CREATE FUNCTION vital_relay_validate_agent_idempotency_change()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            IF OLD.expires_at > statement_timestamp() THEN
                RAISE EXCEPTION 'unexpired agent idempotency outcome cannot be deleted'
                    USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
        END IF;
        IF OLD.status <> 'in_doubt' OR NEW.status <> 'completed' OR
           ROW(
               NEW.scope_id, NEW.run_id, NEW.incident_id, NEW.tool_name,
               NEW.idempotency_key, NEW.request_sha256,
               NEW.created_at, NEW.expires_at
           ) IS DISTINCT FROM ROW(
               OLD.scope_id, OLD.run_id, OLD.incident_id, OLD.tool_name,
               OLD.idempotency_key, OLD.request_sha256,
               OLD.created_at, OLD.expires_at
           ) THEN
            RAISE EXCEPTION 'agent idempotency permits only outcome completion'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$
    """,
    """
    CREATE TRIGGER tr_agent_tool_idempotency_validate_change
    BEFORE UPDATE OR DELETE ON agent_tool_idempotency
    FOR EACH ROW EXECUTE FUNCTION vital_relay_validate_agent_idempotency_change()
    """,
)


DOWNGRADE_STATEMENTS = (
    "DROP TABLE agent_tool_idempotency",
    "DROP FUNCTION vital_relay_validate_agent_idempotency_change()",
    "DROP TABLE agent_tool_proxy_audits",
    "DROP TABLE agent_run_tool_budgets",
    "DROP FUNCTION vital_relay_validate_agent_tool_budget_change()",
    "DROP TABLE agent_runs",
    "DROP FUNCTION vital_relay_validate_agent_run_change()",
    "DROP FUNCTION vital_relay_validate_agent_run_start()",
    "DROP TABLE agent_active_policies",
    "DROP FUNCTION vital_relay_validate_active_agent_policy()",
    "ALTER TABLE persona_sessions DROP CONSTRAINT uq_persona_sessions_exact_account",
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE_STATEMENTS:
        op.execute(statement)
