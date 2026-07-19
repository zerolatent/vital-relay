"""PostgreSQL control plane for live sandboxed coordination-agent runs."""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from threading import Lock, RLock
from typing import Any
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from vital_relay.agent.contracts import (
    AgentPolicyReference,
    AgentRunRequest,
    AgentRunResult,
    AgentToolTrace,
    SandboxKind,
    ToolEffect,
)
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.application.agent_control import (
    ActiveAgentPolicyConflictError,
    ActiveAgentPolicyRecord,
    AgentRunConflictError,
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunStart,
    PersistedAgentRunStatus,
)
from vital_relay.application.agent_evidence import (
    AgentRunEvidenceContext,
    host_audit_trace,
    reconcile_agent_result,
)
from vital_relay.application.tool_proxy import (
    IdempotencyOutcome,
    IdempotencyScope,
    RunToolAuthorization,
    ToolProxyAuditRecord,
    ToolProxyAuditStatus,
    ToolProxyError,
    ToolProxyErrorCode,
)
from vital_relay.domain.incidents import IncidentState
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.persistence.database import (
    DemoScopeUnavailableError,
    POSTGRES_CONNECT_TIMEOUT_SECONDS,
    require_active_scope,
)
from vital_relay.persistence.models import (
    AgentActivePolicyRow,
    AgentRunRow,
    AgentRunToolBudgetRow,
    AgentToolIdempotencyRow,
    AgentToolProxyAuditRow,
    IncidentRow,
    PersonaAccountRow,
    PersonaSessionRow,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JSON_ADAPTER = TypeAdapter(JsonValue)
DEFAULT_AGENT_RUN_LEASE = timedelta(minutes=10)
AGENT_RUN_FENCE_WAIT_SECONDS = 30


@dataclass(slots=True)
class _ProcessGateEntry:
    lock: RLock = field(default_factory=RLock)
    references: int = 0


_PROCESS_GATE_REGISTRY_LOCK = Lock()
_PROCESS_GATE_REGISTRY: dict[tuple[UUID, str, str], _ProcessGateEntry] = {}


class PostgresAgentRunRepository:
    """Persist exactly one model execution for an atomically started run."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        *,
        run_lease: timedelta = DEFAULT_AGENT_RUN_LEASE,
    ) -> None:
        if not timedelta(seconds=1) <= run_lease <= timedelta(minutes=15):
            raise ValueError("run_lease must be between 1 second and 15 minutes")
        self._sessions = sessions
        self.scope_id = scope_id
        self._run_lease = run_lease
        bind = sessions.kw.get("bind")
        if not isinstance(bind, Engine):
            raise ValueError("agent run repository requires an Engine bind")
        self._lock_engine = _dedicated_lock_engine(bind)

    def start(
        self,
        request: AgentRunRequest,
        *,
        requested_by: PersonaPrincipal,
        policy_snapshot: CoordinationPolicySnapshot,
        model_id: str,
        sandbox: SandboxKind,
        created_at: datetime,
    ) -> AgentRunStart:
        now = _utc(created_at, field_name="created_at")
        normalized_model_id = _model_id(model_id)
        if requested_by.scope_id != self.scope_id:
            raise AgentRunConflictError("wrong_scope")
        if requested_by.persona is not Persona.COMMAND:
            raise AgentRunConflictError("persona_not_authorized")
        if request.requested_at > now:
            raise AgentRunConflictError("invalid_run_time")
        if policy_snapshot.reference != request.policy:
            raise AgentRunConflictError("policy_snapshot_mismatch")

        incident_identifier = str(request.incident.incident_id)
        with _process_gate(
            self.scope_id,
            "agent-run-start",
            incident_identifier,
        ):
            with _database_advisory_fence(
                self._lock_engine,
                scope_id=self.scope_id,
                namespace="agent-run-start",
                identifier=incident_identifier,
            ):
                # Resolve the currently running ID without retaining a pooled
                # connection while waiting for its invocation fence.
                with _process_gate(
                    self.scope_id,
                    "agent-run-invocation-fence",
                    str(request.run_id),
                ):
                    with self._sessions() as observation:
                        require_active_scope(observation, self.scope_id)
                        observed_running_id = observation.scalar(
                            select(AgentRunRow.run_id).where(
                                AgentRunRow.scope_id == self.scope_id,
                                AgentRunRow.incident_id
                                == request.incident.incident_id,
                                AgentRunRow.status
                                == PersistedAgentRunStatus.RUNNING.value,
                            )
                        )

                fenced_run_ids = sorted(
                    {request.run_id, observed_running_id} - {None},
                    key=str,
                )
                with ExitStack() as fences:
                    for fenced_run_id in fenced_run_ids:
                        fences.enter_context(
                            _process_gate(
                                self.scope_id,
                                "agent-run-invocation-fence",
                                str(fenced_run_id),
                            )
                        )
                    for fenced_run_id in fenced_run_ids:
                        fences.enter_context(
                            _database_advisory_fence(
                                self._lock_engine,
                                scope_id=self.scope_id,
                                namespace="agent-run-invocation-fence",
                                identifier=str(fenced_run_id),
                            )
                        )
                    return self._start_with_fences(
                        request,
                        requested_by=requested_by,
                        policy_snapshot=policy_snapshot,
                        model_id=normalized_model_id,
                        sandbox=sandbox,
                        created_at=now,
                    )

    def _start_with_fences(
        self,
        request: AgentRunRequest,
        *,
        requested_by: PersonaPrincipal,
        policy_snapshot: CoordinationPolicySnapshot,
        model_id: str,
        sandbox: SandboxKind,
        created_at: datetime,
    ) -> AgentRunStart:
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            database_now = _database_now(session)
            effective_now = max(created_at, database_now)
            existing = session.scalar(
                select(AgentRunRow)
                .where(
                    AgentRunRow.scope_id == self.scope_id,
                    AgentRunRow.run_id == request.run_id,
                )
                .with_for_update()
            )
            if existing is not None:
                if not _same_start(
                    existing,
                    session=session,
                    request=request,
                    requested_by=requested_by,
                    policy_snapshot=policy_snapshot,
                    model_id=model_id,
                    sandbox=sandbox,
                ):
                    raise AgentRunConflictError("agent_run_id_conflict")
                if (
                    existing.status == PersistedAgentRunStatus.RUNNING.value
                    and existing.lease_expires_at <= database_now
                ):
                    _expire_running(existing, session=session)
                    session.flush()
                return AgentRunStart(record=_run_record(existing), created=False)

            _require_active_command_principal(
                session,
                scope_id=self.scope_id,
                principal=requested_by,
                as_of=effective_now,
            )
            incident = session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.incident_id == request.incident.incident_id,
                )
                .with_for_update(read=True)
            )
            if incident is None:
                raise AgentRunConflictError("incident_not_found")
            if incident.current_state not in {
                IncidentState.ESCALATING.value,
                IncidentState.RESPONSE_ACTIVE.value,
            }:
                raise AgentRunConflictError("incident_not_active")
            if (
                incident.state_version != request.incident.state_version
                or incident.kind != request.incident.kind.value
                or incident.current_state != request.incident.state.value
                or incident.opened_at != request.incident.opened_at
            ):
                raise AgentRunConflictError("incident_state_conflict")

            active_policy = session.scalar(
                select(AgentActivePolicyRow)
                .where(AgentActivePolicyRow.scope_id == self.scope_id)
                .with_for_update(read=True)
            )
            if active_policy is None or not _same_policy(
                active_policy,
                request.policy,
            ):
                raise AgentRunConflictError("policy_not_active")

            running = session.scalar(
                select(AgentRunRow)
                .where(
                    AgentRunRow.scope_id == self.scope_id,
                    AgentRunRow.incident_id == request.incident.incident_id,
                    AgentRunRow.status == PersistedAgentRunStatus.RUNNING.value,
                )
                .with_for_update()
            )
            if running is not None:
                if running.lease_expires_at > database_now:
                    raise AgentRunConflictError("incident_run_in_progress")
                _expire_running(running, session=session)
                session.flush()

            row = AgentRunRow(
                scope_id=self.scope_id,
                run_id=request.run_id,
                incident_id=request.incident.incident_id,
                incident_state_version=request.incident.state_version,
                schema_version=request.schema_version,
                objective=request.objective,
                requested_at=request.requested_at,
                created_at=effective_now,
                lease_expires_at=effective_now + self._run_lease,
                requested_by_account_id=requested_by.account_id,
                requested_by_session_id=requested_by.session_id,
                policy_id=request.policy.policy_id,
                policy_version=request.policy.version,
                policy_sha256=request.policy.sha256,
                max_total_tool_calls=(
                    policy_snapshot.tool_budget.max_total_calls
                ),
                max_mutating_tool_calls=(
                    policy_snapshot.tool_budget.max_mutating_calls
                ),
                total_tool_calls=0,
                mutating_tool_calls=0,
                model_id=model_id,
                sandbox=sandbox.value,
                status=PersistedAgentRunStatus.RUNNING.value,
                started_at=None,
                finished_at=None,
                tool_trace=[],
                action_summary=None,
                failure_code=None,
            )
            session.add(row)
            session.flush()
            session.add_all(
                AgentRunToolBudgetRow(
                    scope_id=self.scope_id,
                    run_id=request.run_id,
                    incident_id=request.incident.incident_id,
                    tool_name=rule.name,
                    effect=rule.effect.value,
                    max_calls=rule.max_calls,
                    calls_used=0,
                )
                for rule in policy_snapshot.tool_budget.tools
            )
            session.flush()
            return AgentRunStart(record=_run_record(row), created=True)

    def finish(
        self,
        result: AgentRunResult,
        *,
        received_at: datetime,
    ) -> AgentRunRecord:
        received = _utc(received_at, field_name="received_at")
        with _run_invocation_fence(
            self._lock_engine,
            scope_id=self.scope_id,
            run_id=result.run_id,
        ):
            with self._sessions.begin() as session:
                require_active_scope(session, self.scope_id, lock=True)
                row = session.scalar(
                    select(AgentRunRow)
                    .where(
                        AgentRunRow.scope_id == self.scope_id,
                        AgentRunRow.run_id == result.run_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AgentRunNotFoundError()
                effective_received = max(received, _database_now(session))
                if row.status != PersistedAgentRunStatus.RUNNING.value:
                    record = _run_record(row)
                    if record.to_result() == result:
                        return record
                    evidence = _tool_audits_for_run(
                        session,
                        scope_id=self.scope_id,
                        run_id=result.run_id,
                    )
                    retried_result = reconcile_agent_result(
                        result,
                        _terminal_evidence_records(
                            evidence,
                            record.tool_trace,
                        ),
                        _agent_run_evidence_context(row),
                    )
                    if record.to_result() == retried_result:
                        return record
                    raise AgentRunConflictError("agent_run_result_conflict")
                evidence = _tool_audits_for_run(
                    session,
                    scope_id=self.scope_id,
                    run_id=result.run_id,
                )
                result = reconcile_agent_result(
                    result,
                    evidence,
                    _agent_run_evidence_context(row),
                )
                if effective_received < row.created_at:
                    raise AgentRunConflictError("invalid_result_receipt_time")
                if effective_received >= row.lease_expires_at:
                    _expire_running(row, tool_trace=result.tool_trace)
                    session.flush()
                    return _run_record(row)
                if (
                    row.incident_id != result.incident_id
                    or row.schema_version != result.schema_version
                    or row.model_id != result.model_id
                    or row.sandbox != result.sandbox.value
                    or row.policy_id != result.policy.policy_id
                    or row.policy_version != result.policy.version
                    or not compare_digest(row.policy_sha256, result.policy.sha256)
                    or result.started_at < row.requested_at
                    or result.finished_at > effective_received
                ):
                    _fail_running(
                        row,
                        finished_at=effective_received,
                        tool_trace=result.tool_trace,
                    )
                    session.flush()
                    return _run_record(row)

                row.status = result.status.value
                row.started_at = result.started_at
                row.finished_at = result.finished_at
                row.tool_trace = [
                    trace.model_dump(mode="json") for trace in result.tool_trace
                ]
                row.action_summary = (
                    result.conclusion.action_summary
                    if result.conclusion is not None
                    else None
                )
                row.failure_code = (
                    result.failure_code.value
                    if result.failure_code is not None
                    else None
                )
                session.flush()
                return _run_record(row)

    def get(self, run_id: UUID) -> AgentRunRecord:
        with _run_invocation_fence(
            self._lock_engine,
            scope_id=self.scope_id,
            run_id=run_id,
        ):
            with self._sessions.begin() as session:
                require_active_scope(session, self.scope_id, lock=True)
                row = session.scalar(
                    select(AgentRunRow)
                    .where(
                        AgentRunRow.scope_id == self.scope_id,
                        AgentRunRow.run_id == run_id,
                    )
                    .with_for_update()
                )
                if row is None:
                    raise AgentRunNotFoundError()
                _reconcile_if_expired(session, row)
                return _run_record(row)

    def list_for_incident(
        self,
        incident_id: UUID,
        *,
        limit: int = 50,
    ) -> tuple[AgentRunRecord, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            run_ids = session.scalars(
                select(AgentRunRow.run_id)
                .where(
                    AgentRunRow.scope_id == self.scope_id,
                    AgentRunRow.incident_id == incident_id,
                )
                .order_by(AgentRunRow.requested_at.desc(), AgentRunRow.run_id.desc())
                .limit(limit)
            ).all()
        return tuple(self.get(run_id) for run_id in run_ids)


class PostgresActivePolicyAuthorization:
    """Atomic active-policy pointer and fail-closed A2 authorization adapter."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._sessions = sessions
        self.scope_id = scope_id
        bind = sessions.kw.get("bind")
        if not isinstance(bind, Engine):
            raise ValueError("agent policy authorization requires an Engine bind")
        self._lock_engine = _dedicated_lock_engine(bind)

    def initialize(
        self,
        policy: AgentPolicyReference,
        *,
        activated_at: datetime,
        activated_by_account_id: UUID,
    ) -> ActiveAgentPolicyRecord:
        now = _utc(activated_at, field_name="activated_at")
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="agent-policy-activation",
                identifier="active",
            )
            existing = session.get(AgentActivePolicyRow, self.scope_id)
            if existing is not None:
                if _same_policy(existing, policy):
                    return _active_policy_record(existing)
                raise ActiveAgentPolicyConflictError("active_policy_already_initialized")
            _require_command_account(
                session,
                scope_id=self.scope_id,
                account_id=activated_by_account_id,
            )
            row = AgentActivePolicyRow(
                scope_id=self.scope_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                policy_sha256=policy.sha256,
                revision=1,
                activated_at=now,
                activated_by_account_id=activated_by_account_id,
            )
            session.add(row)
            session.flush()
            return _active_policy_record(row)

    def compare_and_set(
        self,
        expected_policy_sha256: str,
        policy: AgentPolicyReference,
        *,
        activated_at: datetime,
        activated_by_account_id: UUID,
    ) -> ActiveAgentPolicyRecord:
        expected = _sha256(expected_policy_sha256)
        now = _utc(activated_at, field_name="activated_at")
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            row = session.scalar(
                select(AgentActivePolicyRow)
                .where(AgentActivePolicyRow.scope_id == self.scope_id)
                .with_for_update()
            )
            if row is None:
                raise ActiveAgentPolicyConflictError("active_policy_missing")
            if not compare_digest(row.policy_sha256, expected):
                raise ActiveAgentPolicyConflictError("active_policy_changed")
            if compare_digest(row.policy_sha256, policy.sha256):
                raise ActiveAgentPolicyConflictError("active_policy_unchanged")
            if now < row.activated_at:
                raise ActiveAgentPolicyConflictError("activation_time_regressed")
            _require_command_account(
                session,
                scope_id=self.scope_id,
                account_id=activated_by_account_id,
            )
            row.policy_id = policy.policy_id
            row.policy_version = policy.version
            row.policy_sha256 = policy.sha256
            row.revision += 1
            row.activated_at = now
            row.activated_by_account_id = activated_by_account_id
            session.flush()
            return _active_policy_record(row)

    def get_active(self) -> ActiveAgentPolicyRecord | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            row = session.get(AgentActivePolicyRow, self.scope_id)
            return _active_policy_record(row) if row is not None else None

    def is_authorized(self, policy_sha256: str) -> bool:
        """Return false for invalid input, missing state, or database failure."""

        try:
            candidate = _sha256(policy_sha256)
            active = self.get_active()
        except (
            ValueError,
            ValidationError,
            DemoScopeUnavailableError,
            SQLAlchemyError,
        ):
            return False
        return active is not None and compare_digest(active.policy.sha256, candidate)

    def authorize_tool(self, request: RunToolAuthorization) -> None:
        """Atomically validate the run and reserve one pinned budget slot."""

        self._run_tool_authorization(request, reserve=True)

    def check_tool(self, request: RunToolAuthorization) -> None:
        """Validate lifecycle and pinned authority without spending budget."""

        self._run_tool_authorization(request, reserve=False)

    @contextmanager
    def invocation_fence(self, run_id: UUID):
        """Serialize a complete proxy call with terminal evidence capture."""

        try:
            with _run_invocation_fence(
                self._lock_engine,
                scope_id=self.scope_id,
                run_id=run_id,
            ):
                yield
        except _AgentRunFenceUnavailable as exc:
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED) from exc

    def _run_tool_authorization(
        self,
        request: RunToolAuthorization,
        *,
        reserve: bool,
    ) -> None:
        self._validate_tool_authorization_scope(request)
        try:
            with self._sessions.begin() as session:
                require_active_scope(session, self.scope_id, lock=reserve)
                self._authorize_tool_in_session(
                    session,
                    request,
                    reserve=reserve,
                )
        except ToolProxyError:
            raise
        except (DemoScopeUnavailableError, SQLAlchemyError) as exc:
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED) from exc

    def _authorize_tool_in_session(
        self,
        session: Session,
        request: RunToolAuthorization,
        *,
        reserve: bool,
    ) -> None:
        run = session.scalar(
            select(AgentRunRow)
            .where(
                AgentRunRow.scope_id == self.scope_id,
                AgentRunRow.run_id == request.run_id,
            )
            .with_for_update(read=not reserve)
        )
        if run is None:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_RUN)
        database_now = _database_now(session)
        if (
            run.status != PersistedAgentRunStatus.RUNNING.value
            or run.lease_expires_at <= database_now
        ):
            raise ToolProxyError(ToolProxyErrorCode.RUN_NOT_ACTIVE)
        if run.incident_id != request.incident_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_INCIDENT)
        if run.incident_state_version != request.state_version:
            raise ToolProxyError(ToolProxyErrorCode.STALE_STATE)
        if not compare_digest(run.policy_sha256, request.policy_sha256):
            raise ToolProxyError(ToolProxyErrorCode.POLICY_MISMATCH)

        active_policy = session.scalar(
            select(AgentActivePolicyRow)
            .where(AgentActivePolicyRow.scope_id == self.scope_id)
            .with_for_update(read=True)
        )
        if active_policy is None or not _same_policy(
            active_policy,
            AgentPolicyReference(
                policy_id=run.policy_id,
                version=run.policy_version,
                sha256=run.policy_sha256,
            ),
        ):
            raise ToolProxyError(ToolProxyErrorCode.POLICY_MISMATCH)

        tool_budget = session.scalar(
            select(AgentRunToolBudgetRow)
            .where(
                AgentRunToolBudgetRow.scope_id == self.scope_id,
                AgentRunToolBudgetRow.run_id == request.run_id,
                AgentRunToolBudgetRow.tool_name == request.tool_name,
            )
            .with_for_update(read=not reserve)
        )
        if (
            tool_budget is None
            or tool_budget.incident_id != request.incident_id
            or tool_budget.effect != request.effect.value
        ):
            raise ToolProxyError(ToolProxyErrorCode.TOOL_NOT_ALLOWED)
        if not reserve:
            return
        if (
            run.total_tool_calls >= run.max_total_tool_calls
            or tool_budget.calls_used >= tool_budget.max_calls
            or (
                request.effect is ToolEffect.MUTATE
                and run.mutating_tool_calls >= run.max_mutating_tool_calls
            )
        ):
            raise ToolProxyError(ToolProxyErrorCode.TOOL_BUDGET_EXCEEDED)

        run.total_tool_calls += 1
        if request.effect is ToolEffect.MUTATE:
            run.mutating_tool_calls += 1
        session.flush()
        tool_budget.calls_used += 1
        session.flush()

    def _validate_tool_authorization_scope(
        self,
        request: RunToolAuthorization,
    ) -> None:
        try:
            requested_scope_id = UUID(request.scope_id)
        except ValueError as exc:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_SCOPE) from exc
        if requested_scope_id != self.scope_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_SCOPE)


class PostgresAppendOnlyToolAudit:
    """Store one privacy-bounded A2 proxy audit record per insert."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._sessions = sessions
        self.scope_id = scope_id

    def append(self, record: ToolProxyAuditRecord) -> None:
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            session.add(
                AgentToolProxyAuditRow(
                    scope_id=self.scope_id,
                    audit_id=record.audit_id,
                    invocation_id=record.invocation_id,
                    occurred_at=record.occurred_at,
                    requested_scope_id=record.requested_scope_id,
                    requested_run_id=record.requested_run_id,
                    requested_incident_id=record.requested_incident_id,
                    requested_policy_sha256=record.requested_policy_sha256,
                    granted_scope_id=record.granted_scope_id,
                    granted_run_id=record.granted_run_id,
                    granted_incident_id=record.granted_incident_id,
                    granted_state_version=record.granted_state_version,
                    granted_policy_sha256=record.granted_policy_sha256,
                    tool_name=record.tool_name,
                    effect=record.effect.value if record.effect is not None else None,
                    status=record.status.value,
                    idempotency_key=record.idempotency_key,
                    request_sha256=record.request_sha256,
                    result_sha256=record.result_sha256,
                    error_code=(
                        record.error_code.value
                        if record.error_code is not None
                        else None
                    ),
                )
            )

    def for_run(self, run_id: UUID) -> tuple[ToolProxyAuditRecord, ...]:
        """Return immutable host evidence associated with one requested/granted run."""

        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            rows = session.scalars(
                select(AgentToolProxyAuditRow)
                .where(
                    AgentToolProxyAuditRow.scope_id == self.scope_id,
                    AgentToolProxyAuditRow.granted_run_id == run_id,
                )
                .order_by(
                    AgentToolProxyAuditRow.occurred_at,
                    AgentToolProxyAuditRow.audit_id,
                )
            ).all()
            return tuple(_tool_proxy_audit_record(row) for row in rows)


class PostgresDurableIdempotencyExecutor:
    """Persist a marker before mutation and never retry an ambiguous outcome."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        *,
        max_entries: int = 100_000,
    ) -> None:
        if not 1 <= max_entries <= 1_000_000:
            raise ValueError("max_entries must be between 1 and 1000000")
        self._sessions = sessions
        self.scope_id = scope_id
        self._max_entries = max_entries

    def execute(
        self,
        scope: IdempotencyScope,
        *,
        request_sha256: str,
        as_of: datetime,
        expires_at: datetime,
        operation: Callable[[], JsonValue],
    ) -> IdempotencyOutcome:
        request_hash = _sha256(request_sha256)
        now = _utc(as_of, field_name="as_of")
        expiry = _utc(expires_at, field_name="expires_at")
        if expiry <= now:
            raise ValueError("expires_at must follow as_of")
        try:
            requested_scope_id = UUID(scope.scope_id)
        except ValueError as exc:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_SCOPE) from exc
        if requested_scope_id != self.scope_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_SCOPE)

        replay = self._reserve_or_replay(
            scope,
            request_sha256=request_hash,
            as_of=now,
            expires_at=expiry,
        )
        if replay is not None:
            return replay

        try:
            result = _JSON_ADAPTER.validate_python(operation())
        except ValidationError as exc:
            raise ToolProxyError(ToolProxyErrorCode.INVALID_RESULT) from exc

        # The reservation is already committed. If this completion write fails,
        # the durable marker remains in_doubt and all retries fail closed.
        try:
            with self._sessions.begin() as session:
                require_active_scope(session, self.scope_id, lock=True)
                row = session.scalar(
                    select(AgentToolIdempotencyRow)
                    .where(*_idempotency_predicates(self.scope_id, scope))
                    .with_for_update()
                )
                if (
                    row is None
                    or row.status != "in_doubt"
                    or not compare_digest(row.request_sha256, request_hash)
                ):
                    raise ToolProxyError(
                        ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT
                    )
                row.status = "completed"
                row.result = {"value": result}
                row.completed_at = max(now, row.created_at)
                session.flush()
        except ToolProxyError:
            raise
        except SQLAlchemyError as exc:
            raise ToolProxyError(
                ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT
            ) from exc
        return IdempotencyOutcome(result=result, replayed=False)

    def _reserve_or_replay(
        self,
        scope: IdempotencyScope,
        *,
        request_sha256: str,
        as_of: datetime,
        expires_at: datetime,
    ) -> IdempotencyOutcome | None:
        try:
            with self._sessions.begin() as session:
                require_active_scope(session, self.scope_id, lock=True)
                _transaction_lock(
                    session,
                    scope_id=self.scope_id,
                    namespace="agent-idempotency-capacity",
                    identifier="scope",
                )
                session.execute(
                    delete(AgentToolIdempotencyRow).where(
                        AgentToolIdempotencyRow.scope_id == self.scope_id,
                        AgentToolIdempotencyRow.expires_at <= as_of,
                    )
                )
                existing = session.scalar(
                    select(AgentToolIdempotencyRow)
                    .where(*_idempotency_predicates(self.scope_id, scope))
                    .with_for_update()
                )
                if existing is not None:
                    if not compare_digest(
                        existing.request_sha256,
                        request_sha256,
                    ):
                        raise ToolProxyError(
                            ToolProxyErrorCode.IDEMPOTENCY_CONFLICT
                        )
                    if existing.status != "completed":
                        raise ToolProxyError(
                            ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT
                        )
                    return IdempotencyOutcome(
                        result=_stored_result(existing.result),
                        replayed=True,
                    )

                count = session.scalar(
                    select(func.count())
                    .select_from(AgentToolIdempotencyRow)
                    .where(AgentToolIdempotencyRow.scope_id == self.scope_id)
                )
                if count is None or count >= self._max_entries:
                    raise ToolProxyError(
                        ToolProxyErrorCode.IDEMPOTENCY_CAPACITY_EXCEEDED
                    )
                session.add(
                    AgentToolIdempotencyRow(
                        scope_id=self.scope_id,
                        run_id=scope.run_id,
                        incident_id=scope.incident_id,
                        tool_name=scope.tool_name,
                        idempotency_key=scope.key,
                        request_sha256=request_sha256,
                        status="in_doubt",
                        result=None,
                        created_at=as_of,
                        completed_at=None,
                        expires_at=expires_at,
                    )
                )
                session.flush()
                return None
        except ToolProxyError:
            raise
        except SQLAlchemyError as exc:
            raise ToolProxyError(
                ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT
            ) from exc


def _tool_proxy_audit_record(
    row: AgentToolProxyAuditRow,
) -> ToolProxyAuditRecord:
    return ToolProxyAuditRecord(
        audit_id=row.audit_id,
        invocation_id=row.invocation_id,
        occurred_at=row.occurred_at,
        requested_scope_id=row.requested_scope_id,
        requested_run_id=row.requested_run_id,
        requested_incident_id=row.requested_incident_id,
        requested_policy_sha256=row.requested_policy_sha256,
        granted_scope_id=row.granted_scope_id,
        granted_run_id=row.granted_run_id,
        granted_incident_id=row.granted_incident_id,
        granted_state_version=row.granted_state_version,
        granted_policy_sha256=row.granted_policy_sha256,
        tool_name=row.tool_name,
        effect=ToolEffect(row.effect) if row.effect is not None else None,
        status=ToolProxyAuditStatus(row.status),
        idempotency_key=row.idempotency_key,
        request_sha256=row.request_sha256,
        result_sha256=row.result_sha256,
        error_code=(
            ToolProxyErrorCode(row.error_code)
            if row.error_code is not None
            else None
        ),
    )


def _tool_audits_for_run(
    session: Session,
    *,
    scope_id: UUID,
    run_id: UUID,
) -> tuple[ToolProxyAuditRecord, ...]:
    rows = session.scalars(
        select(AgentToolProxyAuditRow)
        .where(
            AgentToolProxyAuditRow.scope_id == scope_id,
            AgentToolProxyAuditRow.granted_run_id == run_id,
        )
        .order_by(
            AgentToolProxyAuditRow.occurred_at,
            AgentToolProxyAuditRow.audit_id,
        )
    ).all()
    return tuple(_tool_proxy_audit_record(row) for row in rows)


def _agent_run_evidence_context(row: AgentRunRow) -> AgentRunEvidenceContext:
    return AgentRunEvidenceContext(
        scope_id=str(row.scope_id),
        run_id=row.run_id,
        incident_id=row.incident_id,
        state_version=row.incident_state_version,
        policy_sha256=row.policy_sha256,
    )


def _terminal_evidence_records(
    records: tuple[ToolProxyAuditRecord, ...],
    persisted_trace: tuple[AgentToolTrace, ...],
) -> tuple[ToolProxyAuditRecord, ...]:
    """Select the immutable evidence captured by the first terminal write.

    Later calls are denied and audited, but those post-terminal rows must not
    make an exact retry of the original runtime result conflict.
    """

    by_audit_id = {record.audit_id: record for record in records}
    selected_ids: set[UUID] = set()
    for trace in persisted_trace:
        terminal = by_audit_id.get(trace.tool_call_id)
        if terminal is None:
            return ()
        selected_ids.add(terminal.audit_id)
        if terminal.effect is not ToolEffect.MUTATE:
            continue
        started = next(
            (
                record
                for record in records
                if record.audit_id not in selected_ids
                and record.status is ToolProxyAuditStatus.STARTED
                and record.invocation_id == terminal.invocation_id
                and record.tool_name == terminal.tool_name
                and record.request_sha256 == terminal.request_sha256
                and record.occurred_at == trace.started_at
            ),
            None,
        )
        if started is not None:
            selected_ids.add(started.audit_id)
    return tuple(record for record in records if record.audit_id in selected_ids)


def _expired_run_tool_trace(
    session: Session,
    row: AgentRunRow,
) -> tuple[AgentToolTrace, ...]:
    try:
        return host_audit_trace(
            _tool_audits_for_run(
                session,
                scope_id=row.scope_id,
                run_id=row.run_id,
            ),
            _agent_run_evidence_context(row),
        )
    except (TypeError, ValueError, ValidationError):
        return ()


def _expire_running(
    row: AgentRunRow,
    *,
    session: Session | None = None,
    tool_trace: tuple[AgentToolTrace, ...] = (),
) -> None:
    """Close a crashed/overdue execution without choosing a coordination action."""

    if session is not None and not tool_trace:
        tool_trace = _expired_run_tool_trace(session, row)
    _fail_running(
        row,
        finished_at=row.lease_expires_at,
        tool_trace=tool_trace,
    )


def _fail_running(
    row: AgentRunRow,
    *,
    finished_at: datetime,
    tool_trace: tuple[AgentToolTrace, ...] = (),
) -> None:
    if row.status != PersistedAgentRunStatus.RUNNING.value:
        return
    row.status = PersistedAgentRunStatus.MANUAL_REQUIRED.value
    row.started_at = row.created_at
    row.finished_at = max(finished_at, row.created_at)
    row.tool_trace = [
        trace.model_dump(mode="json") for trace in tool_trace
    ]
    row.action_summary = None
    row.failure_code = "runner_error"


def _reconcile_if_expired(session: Session, row: AgentRunRow) -> None:
    if (
        row.status == PersistedAgentRunStatus.RUNNING.value
        and row.lease_expires_at <= _database_now(session)
    ):
        _expire_running(row, session=session)
        session.flush()


def _database_now(session: Session) -> datetime:
    value = session.scalar(select(func.statement_timestamp()))
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise RuntimeError("PostgreSQL did not return an aware statement timestamp")
    return value.astimezone(UTC)


def _run_record(row: AgentRunRow) -> AgentRunRecord:
    return AgentRunRecord(
        schema_version=row.schema_version,
        run_id=row.run_id,
        incident_id=row.incident_id,
        incident_state_version=row.incident_state_version,
        objective=row.objective,
        requested_at=row.requested_at,
        created_at=row.created_at,
        lease_expires_at=row.lease_expires_at,
        requested_by_account_id=row.requested_by_account_id,
        requested_by_session_id=row.requested_by_session_id,
        policy=AgentPolicyReference(
            policy_id=row.policy_id,
            version=row.policy_version,
            sha256=row.policy_sha256,
        ),
        model_id=row.model_id,
        sandbox=SandboxKind(row.sandbox),
        status=PersistedAgentRunStatus(row.status),
        started_at=row.started_at,
        finished_at=row.finished_at,
        tool_trace=tuple(row.tool_trace),
        action_summary=row.action_summary,
        failure_code=row.failure_code,
    )


def _active_policy_record(row: AgentActivePolicyRow) -> ActiveAgentPolicyRecord:
    return ActiveAgentPolicyRecord(
        policy=AgentPolicyReference(
            policy_id=row.policy_id,
            version=row.policy_version,
            sha256=row.policy_sha256,
        ),
        revision=row.revision,
        activated_at=row.activated_at,
        activated_by_account_id=row.activated_by_account_id,
    )


def _same_start(
    row: AgentRunRow,
    *,
    session: Session,
    request: AgentRunRequest,
    requested_by: PersonaPrincipal,
    policy_snapshot: CoordinationPolicySnapshot,
    model_id: str,
    sandbox: SandboxKind,
) -> bool:
    if not (
        row.incident_id == request.incident.incident_id
        and row.incident_state_version == request.incident.state_version
        and row.schema_version == request.schema_version
        and row.objective == request.objective
        and row.requested_by_account_id == requested_by.account_id
        and row.requested_by_session_id == requested_by.session_id
        and row.policy_id == request.policy.policy_id
        and row.policy_version == request.policy.version
        and compare_digest(row.policy_sha256, request.policy.sha256)
        and row.max_total_tool_calls
        == policy_snapshot.tool_budget.max_total_calls
        and row.max_mutating_tool_calls
        == policy_snapshot.tool_budget.max_mutating_calls
        and row.model_id == model_id
        and row.sandbox == sandbox.value
    ):
        return False
    persisted = session.scalars(
        select(AgentRunToolBudgetRow)
        .where(
            AgentRunToolBudgetRow.scope_id == row.scope_id,
            AgentRunToolBudgetRow.run_id == row.run_id,
        )
        .order_by(AgentRunToolBudgetRow.tool_name)
    ).all()
    expected = sorted(
        (
            rule.name,
            rule.effect.value,
            rule.max_calls,
        )
        for rule in policy_snapshot.tool_budget.tools
    )
    return [
        (budget.tool_name, budget.effect, budget.max_calls)
        for budget in persisted
    ] == expected


def _same_policy(
    row: AgentActivePolicyRow,
    policy: AgentPolicyReference,
) -> bool:
    return (
        row.policy_id == policy.policy_id
        and row.policy_version == policy.version
        and compare_digest(row.policy_sha256, policy.sha256)
    )


def _require_active_command_principal(
    session: Session,
    *,
    scope_id: UUID,
    principal: PersonaPrincipal,
    as_of: datetime,
) -> None:
    row = session.execute(
        select(PersonaSessionRow, PersonaAccountRow)
        .join(
            PersonaAccountRow,
            (PersonaAccountRow.scope_id == PersonaSessionRow.scope_id)
            & (PersonaAccountRow.account_id == PersonaSessionRow.account_id),
        )
        .where(
            PersonaSessionRow.scope_id == scope_id,
            PersonaSessionRow.session_id == principal.session_id,
            PersonaSessionRow.account_id == principal.account_id,
        )
        .with_for_update(read=True)
    ).one_or_none()
    if row is None:
        raise AgentRunConflictError("principal_not_active")
    persona_session, account = row
    if (
        persona_session.installation_id != principal.installation_id
        or persona_session.status != "active"
        or persona_session.rotated_at > as_of
        or persona_session.access_expires_at <= as_of
        or account.status != "active"
        or account.persona != Persona.COMMAND.value
    ):
        raise AgentRunConflictError("principal_not_active")


def _require_command_account(
    session: Session,
    *,
    scope_id: UUID,
    account_id: UUID,
) -> None:
    account = session.scalar(
        select(PersonaAccountRow)
        .where(
            PersonaAccountRow.scope_id == scope_id,
            PersonaAccountRow.account_id == account_id,
        )
        .with_for_update(read=True)
    )
    if (
        account is None
        or account.status != "active"
        or account.persona != Persona.COMMAND.value
    ):
        raise ActiveAgentPolicyConflictError("activation_actor_not_authorized")


def _idempotency_predicates(
    scope_id: UUID,
    scope: IdempotencyScope,
) -> tuple[Any, ...]:
    return (
        AgentToolIdempotencyRow.scope_id == scope_id,
        AgentToolIdempotencyRow.run_id == scope.run_id,
        AgentToolIdempotencyRow.incident_id == scope.incident_id,
        AgentToolIdempotencyRow.tool_name == scope.tool_name,
        AgentToolIdempotencyRow.idempotency_key == scope.key,
    )


def _stored_result(value: Any) -> JsonValue:
    if not isinstance(value, dict) or set(value) != {"value"}:
        raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT)
    try:
        return _JSON_ADAPTER.validate_python(value["value"])
    except ValidationError as exc:
        raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT) from exc


class _AgentRunFenceUnavailable(RuntimeError):
    """A bounded local or PostgreSQL fence wait could not be acquired."""


def _dedicated_lock_engine(bind: Engine) -> Engine:
    """Build non-pooled connections so fence waiters cannot starve app work."""

    engine = create_engine(
        bind.url,
        future=True,
        poolclass=NullPool,
        pool_pre_ping=True,
        connect_args={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS},
    )

    @event.listens_for(engine, "connect")
    def set_connection_timezone(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET TIME ZONE 'UTC'")
        finally:
            cursor.close()
        dbapi_connection.commit()  # type: ignore[attr-defined]

    return engine


@contextmanager
def _process_gate(
    scope_id: UUID,
    namespace: str,
    identifier: str,
):
    """Share one bounded in-process gate across repository/proxy instances."""

    registry_key = (scope_id, namespace, identifier)
    with _PROCESS_GATE_REGISTRY_LOCK:
        entry = _PROCESS_GATE_REGISTRY.setdefault(
            registry_key,
            _ProcessGateEntry(),
        )
        entry.references += 1
    acquired = entry.lock.acquire(timeout=AGENT_RUN_FENCE_WAIT_SECONDS)
    if not acquired:
        _release_process_gate_reference(registry_key, entry)
        raise _AgentRunFenceUnavailable("agent run process fence timed out")
    try:
        yield
    finally:
        entry.lock.release()
        _release_process_gate_reference(registry_key, entry)


def _release_process_gate_reference(
    registry_key: tuple[UUID, str, str],
    entry: _ProcessGateEntry,
) -> None:
    with _PROCESS_GATE_REGISTRY_LOCK:
        entry.references -= 1
        if (
            entry.references == 0
            and _PROCESS_GATE_REGISTRY.get(registry_key) is entry
        ):
            del _PROCESS_GATE_REGISTRY[registry_key]


@contextmanager
def _database_advisory_fence(
    engine: Engine,
    *,
    scope_id: UUID,
    namespace: str,
    identifier: str,
):
    """Acquire one bounded session lock on a dedicated NullPool connection."""

    key = _advisory_key(
        scope_id=scope_id,
        namespace=namespace,
        identifier=identifier,
    )
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('statement_timeout', :timeout, false)"),
                {"timeout": f"{AGENT_RUN_FENCE_WAIT_SECONDS * 1000}ms"},
            )
            connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": key},
            )
            connection.commit()
            try:
                yield
            finally:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"),
                        {"key": key},
                    )
                    connection.commit()
                except SQLAlchemyError:
                    # Closing a failed dedicated connection also releases every
                    # session-level advisory lock held by that connection.
                    connection.invalidate()
    except _AgentRunFenceUnavailable:
        raise
    except SQLAlchemyError as exc:
        raise _AgentRunFenceUnavailable(
            "agent run PostgreSQL fence unavailable"
        ) from exc


@contextmanager
def _run_invocation_fence(
    engine: Engine,
    *,
    scope_id: UUID,
    run_id: UUID,
):
    with _process_gate(
        scope_id,
        "agent-run-invocation-fence",
        str(run_id),
    ):
        with _database_advisory_fence(
            engine,
            scope_id=scope_id,
            namespace="agent-run-invocation-fence",
            identifier=str(run_id),
        ):
            yield


def _transaction_lock(
    session: Session,
    *,
    scope_id: UUID,
    namespace: str,
    identifier: str,
) -> None:
    key = _advisory_key(
        scope_id=scope_id,
        namespace=namespace,
        identifier=identifier,
    )
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _advisory_key(
    *,
    scope_id: UUID,
    namespace: str,
    identifier: str,
) -> int:
    digest = sha256(f"{scope_id}:{namespace}:{identifier}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _sha256(value: str) -> str:
    if not _SHA256_RE.fullmatch(value):
        raise ValueError("value must be a lowercase SHA-256 digest")
    return value


def _model_id(value: str) -> str:
    if value != value.strip() or not 1 <= len(value) <= 200:
        raise AgentRunConflictError("invalid_model_id")
    return value


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
