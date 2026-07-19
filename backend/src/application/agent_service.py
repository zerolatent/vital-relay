"""Host-side orchestration for one live, sandboxed coordination-agent run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from vital_relay.agent.capabilities import ToolCapabilityAuthority
from vital_relay.agent.contracts import (
    AgentFailureCode,
    AgentIncidentSummary,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentToolTrace,
    SandboxKind,
)
from vital_relay.agent.policy import (
    CoordinationPolicySnapshot,
    PolicyVerificationError,
    allowed_tools_for_state,
)
from vital_relay.agent.runner import AgentRunner
from vital_relay.agent.tools import BoundedToolGateway, Clock
from vital_relay.application.agent_control import (
    AgentRunNotFoundError,
    AgentRunRecord,
    AgentRunRepository,
    PersistedAgentRunStatus,
)
from vital_relay.application.agent_evidence import (
    result_contains_credential_material,
)
from vital_relay.application.incident_service import IncidentService
from vital_relay.application.protocol_service import (
    ProtocolNotFoundError,
    ProtocolService,
)
from vital_relay.application.tool_proxy import PolicyAuthorizationPort
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentState, TimelineEventType
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.evolution.ace.selection import GeneratorContextSelector


DEFAULT_AGENT_CAPABILITY_LIFETIME = timedelta(minutes=5)


class AgentRunCommand(BaseModel):
    """Idempotent operator intent; none of its fields grant authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    run_id: UUID
    expected_state_version: int = Field(ge=1)


class AgentRunExecution(BaseModel):
    """Service receipt used by HTTP to distinguish creation from replay."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: AgentRunRecord
    replayed: bool = False


class ActivePolicySnapshotProvider(Protocol):
    def get_active(self) -> CoordinationPolicySnapshot: ...


class StaticActivePolicySnapshotProvider:
    """Serve one verified artifact while consulting the live pointer each time."""

    def __init__(
        self,
        snapshot: CoordinationPolicySnapshot,
        authorization: PolicyAuthorizationPort,
    ) -> None:
        self._snapshot = snapshot
        self._authorization = authorization

    def get_active(self) -> CoordinationPolicySnapshot:
        if not self._authorization.is_authorized(self._snapshot.sha256):
            raise PolicyVerificationError("active_policy_artifact_unavailable")
        return self._snapshot


class AgentRunServiceError(RuntimeError):
    """Bounded orchestration error safe for API mapping."""

    def __init__(
        self,
        code: str,
        *,
        current_state: IncidentState | None = None,
        current_state_version: int | None = None,
    ) -> None:
        self.code = code
        self.current_state = current_state
        self.current_state_version = current_state_version
        super().__init__(code)

    def as_detail(self) -> dict[str, str | int]:
        detail: dict[str, str | int] = {"code": self.code}
        if self.current_state is not None:
            detail["current_state"] = self.current_state.value
        if self.current_state_version is not None:
            detail["current_state_version"] = self.current_state_version
        return detail


class AgentRunService:
    """Compose authority, durable evidence, proxy tools, and one AgentRunner."""

    def __init__(
        self,
        *,
        scope_id: UUID,
        incident_service: IncidentService,
        protocol_service: ProtocolService,
        repository: AgentRunRepository,
        policy_provider: ActivePolicySnapshotProvider,
        capability_authority: ToolCapabilityAuthority,
        runner: AgentRunner,
        tools: BoundedToolGateway,
        model_id: str,
        sandbox: SandboxKind,
        clock: Clock,
        context_selector: GeneratorContextSelector,
        capability_lifetime: timedelta = DEFAULT_AGENT_CAPABILITY_LIFETIME,
    ) -> None:
        if not model_id or len(model_id) > 200 or model_id != model_id.strip():
            raise ValueError("invalid agent model ID")
        if (
            context_selector.model_identity.provider != "vllm"
            or context_selector.model_identity.model_id != model_id
        ):
            raise ValueError("Generator context model identity does not match runner")
        if not timedelta(seconds=1) <= capability_lifetime <= timedelta(minutes=15):
            raise ValueError("agent capability lifetime must be 1 second to 15 minutes")
        self._scope_id = scope_id
        self._incidents = incident_service
        self._protocols = protocol_service
        self._repository = repository
        self._policy_provider = policy_provider
        self._authority = capability_authority
        self._runner = runner
        self._tools = tools
        self._model_id = model_id
        self._sandbox = sandbox
        self._clock = clock
        self._context_selector = context_selector
        self._capability_lifetime = capability_lifetime

    def start(
        self,
        incident_id: UUID,
        command: AgentRunCommand,
        principal: PersonaPrincipal,
    ) -> AgentRunExecution:
        self._require_command(principal)
        existing = self._existing_execution(
            incident_id,
            command,
            principal,
        )
        if existing is not None:
            return existing
        incident = self._incidents.get(incident_id)
        if incident.state_version != command.expected_state_version:
            raise AgentRunServiceError(
                "incident_state_version_mismatch",
                current_state=incident.state,
                current_state_version=incident.state_version,
            )
        if incident.state not in {
            IncidentState.ESCALATING,
            IncidentState.RESPONSE_ACTIVE,
        }:
            raise AgentRunServiceError(
                "incident_not_agent_eligible",
                current_state=incident.state,
                current_state_version=incident.state_version,
            )
        try:
            policy = self._policy_provider.get_active()
            allowed_tools = allowed_tools_for_state(policy, incident.state)
        except PolicyVerificationError as exc:
            raise AgentRunServiceError("active_agent_policy_unavailable") from exc

        requested_at = self._aware_now()
        request = AgentRunRequest(
            schema_version=SCHEMA_VERSION,
            run_id=command.run_id,
            objective="coordinate_emergency_response",
            requested_at=requested_at,
            incident=self._incident_summary(incident),
            policy=policy.reference,
        )
        started = self._repository.start(
            request,
            requested_by=principal,
            policy_snapshot=policy,
            model_id=self._model_id,
            sandbox=self._sandbox,
            created_at=self._aware_now(),
        )
        if not started.created:
            if started.record.status is PersistedAgentRunStatus.RUNNING:
                raise AgentRunServiceError("agent_run_in_progress")
            return AgentRunExecution(record=started.record, replayed=True)

        issued_at = self._aware_now()
        remaining_lease = started.record.lease_expires_at - issued_at
        if remaining_lease <= timedelta(0):
            result = self._manual_result(
                request,
                started_at=issued_at,
                failure_code=AgentFailureCode.RUNNER_ERROR,
            )
            record = self._repository.finish(
                result,
                received_at=self._aware_now(),
            )
            return AgentRunExecution(record=record, replayed=False)
        try:
            selected_context = self._context_selector.select(
                request,
                available_tools=tuple(sorted(allowed_tools)),
            )
        except Exception:
            result = self._manual_result(
                request,
                started_at=issued_at,
                failure_code=AgentFailureCode.RUNNER_ERROR,
            )
            record = self._repository.finish(
                result,
                received_at=self._aware_now(),
            )
            return AgentRunExecution(record=record, replayed=False)
        invocation_context = self._authority.issue(
            run_id=request.run_id,
            scope_id=str(self._scope_id),
            incident_id=incident.incident_id,
            state_version=incident.state_version,
            policy_sha256=policy.sha256,
            allowed_tools=tuple(sorted(allowed_tools)),
            issued_at=issued_at,
            lifetime=min(self._capability_lifetime, remaining_lease),
        )
        run_started_at = self._aware_now()
        try:
            result = self._runner.run(
                request,
                self._tools,
                policy_snapshot=policy,
                invocation_context=invocation_context,
                selected_context=selected_context,
            )
        except Exception:
            result = self._manual_result(
                request,
                started_at=run_started_at,
                failure_code=AgentFailureCode.RUNNER_ERROR,
            )
        if (
            not self._result_matches_request(result, request)
            or result_contains_credential_material(
                result,
                exact_secret=(
                    invocation_context.raw_capability.get_secret_value()
                ),
            )
        ):
            result = self._manual_result(
                request,
                started_at=run_started_at,
                failure_code=AgentFailureCode.RUNNER_ERROR,
            )
        record = self._repository.finish(
            result,
            received_at=self._aware_now(),
        )
        return AgentRunExecution(record=record, replayed=False)

    def _existing_execution(
        self,
        incident_id: UUID,
        command: AgentRunCommand,
        principal: PersonaPrincipal,
    ) -> AgentRunExecution | None:
        """Resolve an exact retry before consulting mutable incident/policy state."""

        try:
            record = self._repository.get(command.run_id)
        except AgentRunNotFoundError:
            return None
        if record.incident_id != incident_id:
            raise AgentRunServiceError("agent_run_not_found")
        if (
            record.incident_state_version != command.expected_state_version
            or record.requested_by_account_id != principal.account_id
            or record.requested_by_session_id != principal.session_id
        ):
            raise AgentRunServiceError("agent_run_id_conflict")
        if record.status is PersistedAgentRunStatus.RUNNING:
            raise AgentRunServiceError("agent_run_in_progress")
        return AgentRunExecution(record=record, replayed=True)

    def get(
        self,
        incident_id: UUID,
        run_id: UUID,
        principal: PersonaPrincipal,
    ) -> AgentRunRecord:
        self._require_command(principal)
        record = self._repository.get(run_id)
        if record.incident_id != incident_id:
            raise AgentRunServiceError("agent_run_not_found")
        return record

    def list_for_incident(
        self,
        incident_id: UUID,
        principal: PersonaPrincipal,
        *,
        limit: int = 50,
    ) -> tuple[AgentRunRecord, ...]:
        self._require_command(principal)
        if not 1 <= limit <= 50:
            raise ValueError("agent run list limit must be between 1 and 50")
        return self._repository.list_for_incident(incident_id, limit=limit)

    def _incident_summary(self, incident) -> AgentIncidentSummary:
        timeline = self._incidents.timeline(incident.incident_id)
        responder_search_active = (
            incident.state is IncidentState.ESCALATING
            and any(
                item.event_type is TimelineEventType.RESPONDER_SEARCH_STARTED
                for item in timeline
            )
        )
        fixed_protocol_available = False
        if incident.state is IncidentState.RESPONSE_ACTIVE:
            try:
                self._protocols.get_for_command(incident.incident_id)
            except ProtocolNotFoundError:
                fixed_protocol_available = False
            else:
                fixed_protocol_available = True
        return AgentIncidentSummary(
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            kind=incident.kind,
            state=incident.state,
            state_version=incident.state_version,
            opened_at=incident.opened_at,
            responder_search_active=responder_search_active,
            accepted_responder_present=(
                incident.state is IncidentState.RESPONSE_ACTIVE
            ),
            fixed_protocol_available=fixed_protocol_available,
        )

    def _require_command(self, principal: PersonaPrincipal) -> None:
        if principal.scope_id != self._scope_id:
            raise AgentRunServiceError("agent_scope_not_authorized")
        if principal.persona is not Persona.COMMAND:
            raise AgentRunServiceError("agent_persona_not_authorized")

    def _manual_result(
        self,
        request: AgentRunRequest,
        *,
        started_at: datetime,
        failure_code: AgentFailureCode,
        tool_trace: tuple[AgentToolTrace, ...] = (),
    ) -> AgentRunResult:
        return AgentRunResult(
            schema_version=request.schema_version,
            run_id=request.run_id,
            incident_id=request.incident.incident_id,
            policy=request.policy,
            model_id=self._model_id,
            sandbox=self._sandbox,
            status=AgentRunStatus.MANUAL_REQUIRED,
            started_at=started_at,
            finished_at=self._aware_now(),
            tool_trace=tool_trace,
            failure_code=failure_code,
        )

    def _result_matches_request(
        self,
        result: AgentRunResult,
        request: AgentRunRequest,
    ) -> bool:
        return (
            result.run_id == request.run_id
            and result.incident_id == request.incident.incident_id
            and result.policy == request.policy
            and result.model_id == self._model_id
            and result.sandbox is self._sandbox
            and result.started_at >= request.requested_at
        )

    def _aware_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise AgentRunServiceError("agent_clock_invalid")
        return value.astimezone(UTC)
