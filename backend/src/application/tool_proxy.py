"""Authenticated, deny-by-default application boundary for agent tools.

This module defines only ports and a local foundation. It has no HTTP route,
database adapter, sandbox credential, or claim of production deployment.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Protocol
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from vital_relay.agent.capabilities import (
    CapabilityError,
    CapabilityErrorCode,
    ToolCapabilityAuthority,
    ToolCapabilityClaims,
    ToolInvocationContext,
)
from vital_relay.agent.capability_runtime import SCOPE_ID_PATTERN
from vital_relay.agent.contracts import SHA256_PATTERN, TOOL_NAME_PATTERN, ToolEffect
from vital_relay.agent.tool_contracts import (
    COORDINATE_DISPATCH,
    GET_DISPATCH_COORDINATION,
    GET_FIXED_PROTOCOL,
    GET_INCIDENT,
    GET_INCIDENT_TIMELINE,
    AgentDispatchToolView,
    AgentIncidentToolView,
    AgentInvitedResponderView,
    AgentProtocolReferenceToolView,
    AgentTimelineEntry,
    AgentTimelineToolResult,
    IncidentBoundToolInput,
    TimelineToolInput,
)
from vital_relay.agent.tool_identity import mutation_operation_id
from vital_relay.agent.tool_transport import (
    ToolProxyErrorCode,
    ToolProxyInvocation,
)
from vital_relay.agent.tools import BoundedToolGateway, ToolBinding
from vital_relay.application.dispatch_service import DispatchConflictError
from vital_relay.domain.dispatch import DispatchCoordinationView, InvitationStatus
from vital_relay.domain.incidents import (
    IncidentState,
    IncidentTimelineEntry,
    IncidentView,
    TimelineEventType,
)
from vital_relay.domain.protocols import ProtocolPresentationView


_JSON_ADAPTER = TypeAdapter(JsonValue)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])


class IncidentToolPort(Protocol):
    """Scope-bound application service; it must not return cross-tenant data."""

    def get(self, incident_id: UUID) -> IncidentView: ...

    def timeline(self, incident_id: UUID) -> tuple[IncidentTimelineEntry, ...]: ...


class DispatchToolPort(Protocol):
    """Scope-bound dispatch application service, never a repository adapter."""

    def get_coordination(self, incident_id: UUID) -> DispatchCoordinationView: ...

    def coordinate(
        self,
        incident_id: UUID,
        *,
        expected_state_version: int,
    ) -> DispatchCoordinationView: ...


class ProtocolToolPort(Protocol):
    """Scope-bound fixed-protocol application service."""

    def get_for_command(self, incident_id: UUID) -> ProtocolPresentationView: ...


class ToolProxyClock(Protocol):
    def now(self) -> datetime: ...


class ToolProxyAuditSink(Protocol):
    """Append-only observable audit port; implementations must not upsert."""

    def append(self, record: ToolProxyAuditRecord) -> None: ...


class ToolProxyAuditSource(Protocol):
    """Read host-authored evidence for one durable agent run."""

    def for_run(self, run_id: UUID) -> tuple[ToolProxyAuditRecord, ...]: ...


class PolicyAuthorizationPort(Protocol):
    """Host-side durable run and active-policy authority for every invocation."""

    def is_authorized(self, policy_sha256: str) -> bool: ...

    def check_tool(self, request: RunToolAuthorization) -> None: ...

    def authorize_tool(self, request: RunToolAuthorization) -> None: ...

    def invocation_fence(self, run_id: UUID) -> AbstractContextManager[None]: ...


@dataclass(frozen=True, slots=True)
class RunToolAuthorization:
    """Trusted host request to reserve one call against a durable run budget."""

    scope_id: str
    run_id: UUID
    incident_id: UUID
    state_version: int
    policy_sha256: str
    tool_name: str
    effect: ToolEffect


class InMemoryActivePolicyAuthorization:
    """Bounded local/test authority; live composition uses PostgreSQL."""

    def __init__(
        self,
        active_policy_sha256: str,
        *,
        max_total_calls: int = 50,
        max_mutating_calls: int = 10,
        max_calls_per_tool: int = 20,
    ) -> None:
        if not 1 <= max_total_calls <= 50:
            raise ValueError("max_total_calls must be between 1 and 50")
        if not 0 <= max_mutating_calls <= min(10, max_total_calls):
            raise ValueError("invalid max_mutating_calls")
        if not 1 <= max_calls_per_tool <= 20:
            raise ValueError("max_calls_per_tool must be between 1 and 20")
        self._active = _validate_sha256(active_policy_sha256)
        self._lock = RLock()
        self._max_total_calls = max_total_calls
        self._max_mutating_calls = max_mutating_calls
        self._max_calls_per_tool = max_calls_per_tool
        self._total_by_run: dict[UUID, int] = {}
        self._mutating_by_run: dict[UUID, int] = {}
        self._calls_by_run_tool: dict[tuple[UUID, str], int] = {}
        self._inactive_runs: set[UUID] = set()

    def activate(self, policy_sha256: str) -> None:
        with self._lock:
            self._active = _validate_sha256(policy_sha256)

    def is_authorized(self, policy_sha256: str) -> bool:
        with self._lock:
            return hmac_compare(self._active, policy_sha256)

    def revoke_run(self, run_id: UUID) -> None:
        """Test/local lifecycle seam mirroring a durable terminal transition."""

        with self._lock:
            self._inactive_runs.add(run_id)

    def authorize_tool(self, request: RunToolAuthorization) -> None:
        """Mirror production budget semantics for local and unit execution."""

        with self._lock:
            self.check_tool(request)
            total = self._total_by_run.get(request.run_id, 0)
            mutating = self._mutating_by_run.get(request.run_id, 0)
            per_tool_key = (request.run_id, request.tool_name)
            per_tool = self._calls_by_run_tool.get(per_tool_key, 0)
            if (
                total >= self._max_total_calls
                or per_tool >= self._max_calls_per_tool
                or (
                    request.effect is ToolEffect.MUTATE
                    and mutating >= self._max_mutating_calls
                )
            ):
                raise ToolProxyError(ToolProxyErrorCode.TOOL_BUDGET_EXCEEDED)
            self._total_by_run[request.run_id] = total + 1
            self._calls_by_run_tool[per_tool_key] = per_tool + 1
            if request.effect is ToolEffect.MUTATE:
                self._mutating_by_run[request.run_id] = mutating + 1

    def check_tool(self, request: RunToolAuthorization) -> None:
        with self._lock:
            if request.run_id in self._inactive_runs:
                raise ToolProxyError(ToolProxyErrorCode.RUN_NOT_ACTIVE)
            if not hmac_compare(self._active, request.policy_sha256):
                raise ToolProxyError(ToolProxyErrorCode.POLICY_MISMATCH)

    @contextmanager
    def invocation_fence(self, run_id: UUID):
        del run_id
        with self._lock:
            yield


class ToolProxyError(Exception):
    """Closed proxy failure that never carries provider or credential text."""

    def __init__(self, code: ToolProxyErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class ToolProxyAuditStatus(StrEnum):
    STARTED = "started"
    COMPLETED = "completed"
    REPLAYED = "replayed"
    DENIED = "denied"
    FAILED = "failed"


def tool_proxy_audit_status_for_error(
    error_code: ToolProxyErrorCode,
) -> ToolProxyAuditStatus:
    """Keep host audit status aligned with the sandbox gateway taxonomy."""

    if error_code in {
        ToolProxyErrorCode.APPLICATION_FAILED,
        ToolProxyErrorCode.INVALID_RESULT,
        ToolProxyErrorCode.AUDIT_UNAVAILABLE,
        ToolProxyErrorCode.IDEMPOTENCY_CAPACITY_EXCEEDED,
        ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT,
    }:
        return ToolProxyAuditStatus.FAILED
    return ToolProxyAuditStatus.DENIED


class ToolProxyAuditRecord(BaseModel):
    """Append-only metadata record with hashes instead of sensitive payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    audit_id: UUID
    invocation_id: UUID
    occurred_at: AwareDatetime
    requested_scope_id: str = Field(pattern=SCOPE_ID_PATTERN)
    requested_run_id: UUID
    requested_incident_id: UUID
    requested_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    granted_scope_id: str | None = Field(default=None, pattern=SCOPE_ID_PATTERN)
    granted_run_id: UUID | None = None
    granted_incident_id: UUID | None = None
    granted_state_version: int | None = Field(default=None, ge=1)
    granted_policy_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    tool_name: str = Field(pattern=TOOL_NAME_PATTERN)
    effect: ToolEffect | None
    status: ToolProxyAuditStatus
    idempotency_key: UUID | None
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    result_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    error_code: ToolProxyErrorCode | None

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audit time must be timezone-aware")
        return value.astimezone(UTC)


ToolProxyHandler = Callable[[BaseModel, IncidentView], BaseModel]


@dataclass(frozen=True, slots=True)
class ToolProxyBinding:
    name: str
    effect: ToolEffect
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    description: str
    handler: ToolProxyHandler


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    scope_id: str
    run_id: UUID
    incident_id: UUID
    tool_name: str
    key: UUID


@dataclass(frozen=True, slots=True)
class IdempotencyOutcome:
    result: JsonValue
    replayed: bool


class IdempotencyExecutor(Protocol):
    def execute(
        self,
        scope: IdempotencyScope,
        *,
        request_sha256: str,
        as_of: datetime,
        expires_at: datetime,
        operation: Callable[[], JsonValue],
    ) -> IdempotencyOutcome: ...


@dataclass(frozen=True, slots=True)
class _StoredIdempotencyResult:
    request_sha256: str
    expires_at: datetime
    result: JsonValue | None
    in_doubt: bool = False


class BoundedInMemoryIdempotencyExecutor:
    """Bounded local/test executor; live composition requires a durable adapter."""

    def __init__(self, *, max_entries: int = 1_000) -> None:
        if not 1 <= max_entries <= 10_000:
            raise ValueError("max_entries must be between 1 and 10000")
        self._max_entries = max_entries
        self._entries: dict[IdempotencyScope, _StoredIdempotencyResult] = {}
        self._lock = RLock()

    def execute(
        self,
        scope: IdempotencyScope,
        *,
        request_sha256: str,
        as_of: datetime,
        expires_at: datetime,
        operation: Callable[[], JsonValue],
    ) -> IdempotencyOutcome:
        with self._lock:
            self._entries = {
                key: value
                for key, value in self._entries.items()
                if value.expires_at > as_of
            }
            existing = self._entries.get(scope)
            if existing is not None:
                if existing.request_sha256 != request_sha256:
                    raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_CONFLICT)
                if existing.in_doubt:
                    raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_IN_DOUBT)
                return IdempotencyOutcome(result=existing.result, replayed=True)
            if len(self._entries) >= self._max_entries:
                raise ToolProxyError(
                    ToolProxyErrorCode.IDEMPOTENCY_CAPACITY_EXCEEDED
                )
            try:
                result = operation()
            except Exception:
                # A synchronous failure cannot prove that an application-owned
                # transaction did not commit. Preserve ambiguity and require
                # reconciliation instead of blindly executing the mutation again.
                self._entries[scope] = _StoredIdempotencyResult(
                    request_sha256=request_sha256,
                    expires_at=expires_at,
                    result=None,
                    in_doubt=True,
                )
                raise
            self._entries[scope] = _StoredIdempotencyResult(
                request_sha256=request_sha256,
                expires_at=expires_at,
                result=result,
            )
            return IdempotencyOutcome(result=result, replayed=False)


class InMemoryAppendOnlyToolAudit:
    """Inspection-only test/local sink; records cannot be updated or cleared."""

    def __init__(self) -> None:
        self._records: list[ToolProxyAuditRecord] = []

    @property
    def records(self) -> tuple[ToolProxyAuditRecord, ...]:
        return tuple(self._records)

    def append(self, record: ToolProxyAuditRecord) -> None:
        self._records.append(record)

    def for_run(self, run_id: UUID) -> tuple[ToolProxyAuditRecord, ...]:
        return tuple(
            record
            for record in self._records
            if record.granted_run_id == run_id
        )


class InternalAgentToolProxy:
    """Authenticate, authorize, state-check, invoke, and audit one tool call."""

    def __init__(
        self,
        *,
        capability_authority: ToolCapabilityAuthority,
        scope_id: str,
        policy_authorization: PolicyAuthorizationPort,
        incident_port: IncidentToolPort,
        bindings: tuple[ToolProxyBinding, ...],
        audit_sink: ToolProxyAuditSink,
        idempotency: IdempotencyExecutor,
        clock: ToolProxyClock,
        identifier_factory: Callable[[], UUID],
        idempotency_retention: timedelta = timedelta(hours=24),
    ) -> None:
        if not timedelta(hours=1) <= idempotency_retention <= timedelta(days=30):
            raise ValueError("idempotency_retention must be between 1 hour and 30 days")
        if re.fullmatch(SCOPE_ID_PATTERN, scope_id) is None:
            raise ValueError("invalid proxy scope_id")
        registry: dict[str, ToolProxyBinding] = {}
        for binding in bindings:
            if binding.name in registry:
                raise ValueError(f"duplicate proxy tool: {binding.name}")
            if binding.name in {"close_incident", "handoff_incident"}:
                raise ValueError("close and handoff are not agent tools")
            registry[binding.name] = binding
        self._authority = capability_authority
        self._scope_id = scope_id
        self._policy_authorization = policy_authorization
        self._incident_port = incident_port
        self._bindings = registry
        self._audit = audit_sink
        self._idempotency = idempotency
        self._clock = clock
        self._identifier_factory = identifier_factory
        self._idempotency_retention = idempotency_retention

    def gateway(self) -> BoundedToolGateway:
        """Expose proxy-backed callbacks with no capability/idempotency arguments."""

        bindings: list[ToolBinding] = []
        for binding in self._bindings.values():
            bindings.append(
                ToolBinding(
                    name=binding.name,
                    description=binding.description,
                    input_model=binding.input_model,
                    handler=lambda arguments, context, name=binding.name: (
                        self._invoke_from_runtime(name, arguments, context)
                    ),
                    effect=binding.effect,
                )
            )
        return BoundedToolGateway(tuple(bindings))

    def invoke(
        self,
        raw_capability: str,
        invocation: ToolProxyInvocation,
    ) -> JsonValue:
        now = self._aware_now()
        request_sha256 = _sha256_json(invocation.arguments)
        binding = self._bindings.get(invocation.tool_name)
        try:
            claims = self._authority.authenticate(raw_capability, now=now)
        except CapabilityError as exc:
            error = ToolProxyError(
                ToolProxyErrorCode.EXPIRED_CAPABILITY
                if exc.code is CapabilityErrorCode.EXPIRED_CAPABILITY
                else ToolProxyErrorCode.INVALID_CAPABILITY
            )
            self._append_error_audit(
                invocation,
                claims=None,
                binding=binding,
                request_sha256=request_sha256,
                error=error,
            )
            raise error from exc

        with self._policy_authorization.invocation_fence(claims.run_id):
            try:
                fenced_now = self._aware_now()
                try:
                    fenced_claims = self._authority.authenticate(
                        raw_capability,
                        now=fenced_now,
                    )
                except CapabilityError as exc:
                    raise ToolProxyError(
                        ToolProxyErrorCode.EXPIRED_CAPABILITY
                        if exc.code is CapabilityErrorCode.EXPIRED_CAPABILITY
                        else ToolProxyErrorCode.INVALID_CAPABILITY
                    ) from exc
                if fenced_claims != claims:
                    raise ToolProxyError(ToolProxyErrorCode.INVALID_CAPABILITY)
                return self._invoke_authenticated(
                    invocation,
                    claims=fenced_claims,
                    binding=binding,
                    now=fenced_now,
                    request_sha256=request_sha256,
                )
            except ToolProxyError as exc:
                self._append_error_audit(
                    invocation,
                    claims=claims,
                    binding=binding,
                    request_sha256=request_sha256,
                    error=exc,
                )
                raise

    def _invoke_authenticated(
        self,
        invocation: ToolProxyInvocation,
        *,
        claims: ToolCapabilityClaims,
        binding: ToolProxyBinding | None,
        now: datetime,
        request_sha256: str,
    ) -> JsonValue:
        self._authorize(claims, invocation, binding)
        parsed = self._parse_arguments(binding, invocation)
        if binding is None:  # narrowed by _authorize
            raise ToolProxyError(ToolProxyErrorCode.TOOL_NOT_REGISTERED)
        if parsed.expected_state_version != claims.state_version:
            raise ToolProxyError(ToolProxyErrorCode.STALE_STATE)
        canonical_arguments = _JSON_OBJECT_ADAPTER.validate_python(
            parsed.model_dump(mode="json")
        )
        if binding.effect is ToolEffect.MUTATE:
            if invocation.idempotency_key is None:
                raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_REQUIRED)
            expected_idempotency_key = mutation_operation_id(
                claims.run_id,
                binding.name,
                canonical_arguments,
            )
            if invocation.idempotency_key != expected_idempotency_key:
                raise ToolProxyError(ToolProxyErrorCode.IDEMPOTENCY_CONFLICT)
        elif invocation.idempotency_key is not None:
            raise ToolProxyError(ToolProxyErrorCode.INVALID_ARGUMENTS)

        authorization = RunToolAuthorization(
            scope_id=claims.scope_id,
            run_id=claims.run_id,
            incident_id=claims.incident_id,
            state_version=claims.state_version,
            policy_sha256=claims.policy_sha256,
            tool_name=binding.name,
            effect=binding.effect,
        )
        self._policy_authorization.check_tool(authorization)
        if binding.effect is ToolEffect.MUTATE:
            scope = IdempotencyScope(
                scope_id=claims.scope_id,
                run_id=claims.run_id,
                incident_id=claims.incident_id,
                tool_name=binding.name,
                key=invocation.idempotency_key,
            )
            outcome = self._idempotency.execute(
                scope,
                request_sha256=request_sha256,
                as_of=now,
                expires_at=now + self._idempotency_retention,
                operation=lambda: self._execute_fresh_authorized_mutation(
                    authorization,
                    invocation,
                    claims=claims,
                    binding=binding,
                    parsed=parsed,
                    request_sha256=request_sha256,
                ),
            )
        else:
            self._policy_authorization.authorize_tool(authorization)
            incident = self._load_current_incident(claims)
            self._validate_expected_state(parsed, incident)
            outcome = IdempotencyOutcome(
                result=self._call(binding, parsed, incident),
                replayed=False,
            )

        self._append_audit(
            invocation,
            claims=claims,
            binding=binding,
            status=(
                ToolProxyAuditStatus.REPLAYED
                if outcome.replayed
                else ToolProxyAuditStatus.COMPLETED
            ),
            request_sha256=request_sha256,
            result_sha256=_sha256_json(outcome.result),
        )
        return outcome.result

    def _append_error_audit(
        self,
        invocation: ToolProxyInvocation,
        *,
        claims: ToolCapabilityClaims | None,
        binding: ToolProxyBinding | None,
        request_sha256: str,
        error: ToolProxyError,
    ) -> None:
        status = tool_proxy_audit_status_for_error(error.code)
        if error.code is not ToolProxyErrorCode.AUDIT_UNAVAILABLE:
            self._append_audit_best_effort(
                invocation,
                claims=claims,
                binding=binding,
                status=status,
                request_sha256=request_sha256,
                error_code=error.code,
            )

    def _execute_fresh_authorized_mutation(
        self,
        authorization: RunToolAuthorization,
        invocation: ToolProxyInvocation,
        *,
        claims: ToolCapabilityClaims,
        binding: ToolProxyBinding,
        parsed: BaseModel,
        request_sha256: str,
    ) -> JsonValue:
        self._policy_authorization.authorize_tool(authorization)
        return self._execute_fresh_mutation(
            invocation,
            claims=claims,
            binding=binding,
            parsed=parsed,
            request_sha256=request_sha256,
        )

    def _execute_fresh_mutation(
        self,
        invocation: ToolProxyInvocation,
        *,
        claims: ToolCapabilityClaims,
        binding: ToolProxyBinding,
        parsed: BaseModel,
        request_sha256: str,
    ) -> JsonValue:
        """Run fresh-state checks only after idempotency found no exact replay."""

        incident = self._load_current_incident(claims)
        self._validate_expected_state(parsed, incident)
        self._append_audit(
            invocation,
            claims=claims,
            binding=binding,
            status=ToolProxyAuditStatus.STARTED,
            request_sha256=request_sha256,
        )
        return self._call(binding, parsed, incident)

    @staticmethod
    def _validate_expected_state(
        parsed: BaseModel,
        incident: IncidentView,
    ) -> None:
        if (
            isinstance(parsed, IncidentBoundToolInput)
            and parsed.expected_state_version != incident.state_version
        ):
            raise ToolProxyError(ToolProxyErrorCode.STALE_STATE)

    def _invoke_from_runtime(
        self,
        tool_name: str,
        arguments: BaseModel,
        context: ToolInvocationContext,
    ) -> JsonValue:
        binding = self._bindings[tool_name]
        return self.invoke(
            context.raw_capability.get_secret_value(),
            ToolProxyInvocation(
                invocation_id=context.idempotency_key
                or self._identifier_factory(),
                scope_id=context.scope_id,
                run_id=context.run_id,
                incident_id=context.incident_id,
                policy_sha256=context.policy_sha256,
                tool_name=tool_name,
                arguments=_JSON_OBJECT_ADAPTER.validate_python(
                    arguments.model_dump(mode="json")
                ),
                idempotency_key=(
                    context.idempotency_key
                    if binding.effect is ToolEffect.MUTATE
                    else None
                ),
            ),
        )

    def _authorize(
        self,
        claims: ToolCapabilityClaims,
        invocation: ToolProxyInvocation,
        binding: ToolProxyBinding | None,
    ) -> None:
        if claims.run_id != invocation.run_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_RUN)
        if (
            claims.scope_id != invocation.scope_id
            or claims.scope_id != self._scope_id
        ):
            raise ToolProxyError(ToolProxyErrorCode.WRONG_SCOPE)
        if claims.incident_id != invocation.incident_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_INCIDENT)
        if (
            claims.policy_sha256 != invocation.policy_sha256
        ):
            raise ToolProxyError(ToolProxyErrorCode.POLICY_MISMATCH)
        if binding is None:
            raise ToolProxyError(ToolProxyErrorCode.TOOL_NOT_REGISTERED)
        if binding.name not in claims.allowed_tools:
            raise ToolProxyError(ToolProxyErrorCode.TOOL_NOT_ALLOWED)

    def _parse_arguments(
        self,
        binding: ToolProxyBinding | None,
        invocation: ToolProxyInvocation,
    ) -> BaseModel:
        if binding is None:
            raise ToolProxyError(ToolProxyErrorCode.TOOL_NOT_REGISTERED)
        try:
            parsed = binding.input_model.model_validate(invocation.arguments)
        except ValidationError as exc:
            raise ToolProxyError(ToolProxyErrorCode.INVALID_ARGUMENTS) from exc
        if not isinstance(parsed, IncidentBoundToolInput):
            raise ToolProxyError(ToolProxyErrorCode.INVALID_ARGUMENTS)
        if parsed.incident_id != invocation.incident_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_INCIDENT)
        return parsed

    def _load_current_incident(self, claims: ToolCapabilityClaims) -> IncidentView:
        try:
            incident = self._incident_port.get(claims.incident_id)
        except Exception as exc:
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED) from exc
        if incident.incident_id != claims.incident_id:
            raise ToolProxyError(ToolProxyErrorCode.WRONG_INCIDENT)
        if incident.state_version != claims.state_version:
            raise ToolProxyError(ToolProxyErrorCode.STALE_STATE)
        if incident.state not in {
            IncidentState.ESCALATING,
            IncidentState.RESPONSE_ACTIVE,
        }:
            raise ToolProxyError(ToolProxyErrorCode.INCIDENT_NOT_ACTIVE)
        return incident

    def _call(
        self,
        binding: ToolProxyBinding,
        parsed: BaseModel,
        incident: IncidentView,
    ) -> JsonValue:
        try:
            raw_result = binding.handler(parsed, incident)
            result = binding.output_model.model_validate(raw_result)
            return _JSON_ADAPTER.validate_python(result.model_dump(mode="json"))
        except ToolProxyError:
            raise
        except ValidationError as exc:
            raise ToolProxyError(ToolProxyErrorCode.INVALID_RESULT) from exc
        except Exception as exc:
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED) from exc

    def _append_audit(
        self,
        invocation: ToolProxyInvocation,
        *,
        claims: ToolCapabilityClaims | None,
        binding: ToolProxyBinding | None,
        status: ToolProxyAuditStatus,
        request_sha256: str,
        result_sha256: str | None = None,
        error_code: ToolProxyErrorCode | None = None,
    ) -> None:
        try:
            self._audit.append(
                ToolProxyAuditRecord(
                    audit_id=self._identifier_factory(),
                    invocation_id=invocation.invocation_id,
                    occurred_at=self._aware_now(),
                    requested_scope_id=invocation.scope_id,
                    requested_run_id=invocation.run_id,
                    requested_incident_id=invocation.incident_id,
                    requested_policy_sha256=invocation.policy_sha256,
                    granted_scope_id=claims.scope_id if claims else None,
                    granted_run_id=claims.run_id if claims else None,
                    granted_incident_id=claims.incident_id if claims else None,
                    granted_state_version=claims.state_version if claims else None,
                    granted_policy_sha256=(
                        claims.policy_sha256 if claims else None
                    ),
                    tool_name=invocation.tool_name,
                    effect=binding.effect if binding else None,
                    status=status,
                    idempotency_key=invocation.idempotency_key,
                    request_sha256=request_sha256,
                    result_sha256=result_sha256,
                    error_code=error_code,
                )
            )
        except Exception as exc:
            raise ToolProxyError(ToolProxyErrorCode.AUDIT_UNAVAILABLE) from exc

    def _append_audit_best_effort(self, *args, **kwargs) -> None:
        try:
            self._append_audit(*args, **kwargs)
        except ToolProxyError:
            return

    def _aware_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED)
        return value.astimezone(UTC)


def initial_tool_proxy_bindings(
    *,
    incident_port: IncidentToolPort,
    dispatch_port: DispatchToolPort,
    protocol_port: ProtocolToolPort,
) -> tuple[ToolProxyBinding, ...]:
    """Build the initial port-backed tool registry; no live adapter is implied."""

    def get_incident(
        arguments: BaseModel,
        incident: IncidentView,
    ) -> AgentIncidentToolView:
        _validate_expected_state(arguments, incident)
        return AgentIncidentToolView(
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            kind=incident.kind,
            state=incident.state,
            state_version=incident.state_version,
            opened_at=incident.opened_at,
            updated_at=incident.updated_at,
        )

    def get_timeline(
        arguments: BaseModel,
        incident: IncidentView,
    ) -> AgentTimelineToolResult:
        parsed = TimelineToolInput.model_validate(arguments)
        _validate_expected_state(parsed, incident)
        entries = incident_port.timeline(incident.incident_id)
        if any(entry.incident_id != incident.incident_id for entry in entries):
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED)
        if any(
            current.sequence <= previous.sequence
            for previous, current in zip(entries, entries[1:], strict=False)
        ):
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED)
        eligible = [entry for entry in entries if entry.sequence > parsed.after_sequence]
        selected = eligible[: parsed.limit]
        return AgentTimelineToolResult(
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            state_version=incident.state_version,
            entries=tuple(
                AgentTimelineEntry(
                    sequence=entry.sequence,
                    event_type=entry.event_type,
                    occurred_at=entry.occurred_at,
                    state=entry.state,
                    summary=_safe_timeline_summary(entry.event_type),
                )
                for entry in selected
            ),
            has_more=len(eligible) > len(selected),
        )

    def get_dispatch(
        arguments: BaseModel,
        incident: IncidentView,
    ) -> AgentDispatchToolView:
        _validate_expected_state(arguments, incident)
        coordination = dispatch_port.get_coordination(incident.incident_id)
        _validate_dispatch_projection(coordination, incident)
        return _dispatch_projection(coordination, incident.state_version)

    def coordinate_dispatch(
        arguments: BaseModel,
        incident: IncidentView,
    ) -> AgentDispatchToolView:
        _validate_expected_state(arguments, incident)
        if incident.state is not IncidentState.ESCALATING:
            raise ToolProxyError(ToolProxyErrorCode.INCIDENT_NOT_ACTIVE)
        try:
            coordination = dispatch_port.coordinate(
                incident.incident_id,
                expected_state_version=incident.state_version,
            )
        except DispatchConflictError as exc:
            if exc.code == "incident_state_version_mismatch":
                raise ToolProxyError(ToolProxyErrorCode.STALE_STATE) from exc
            raise
        current = incident_port.get(incident.incident_id)
        _validate_dispatch_projection(coordination, current)
        return _dispatch_projection(coordination, current.state_version)

    def get_protocol(
        arguments: BaseModel,
        incident: IncidentView,
    ) -> AgentProtocolReferenceToolView:
        _validate_expected_state(arguments, incident)
        if incident.state is not IncidentState.RESPONSE_ACTIVE:
            raise ToolProxyError(ToolProxyErrorCode.INCIDENT_NOT_ACTIVE)
        presentation = protocol_port.get_for_command(incident.incident_id)
        if (
            presentation.incident_id != incident.incident_id
            or presentation.protocol.emergency_kind is not incident.kind
        ):
            raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED)
        return AgentProtocolReferenceToolView(
            schema_version=presentation.schema_version,
            incident_id=presentation.incident_id,
            state_version=incident.state_version,
            presentation_id=presentation.presentation_id,
            protocol_id=presentation.protocol.protocol_id,
            protocol_version=presentation.protocol.version,
            emergency_kind=presentation.protocol.emergency_kind,
            content_sha256=presentation.protocol.content_sha256,
            title=presentation.protocol.title,
            presented_at=presentation.presented_at,
        )

    return (
        ToolProxyBinding(
            name=GET_INCIDENT,
            effect=ToolEffect.READ,
            input_model=IncidentBoundToolInput,
            output_model=AgentIncidentToolView,
            description="Read the current privacy-bounded incident state.",
            handler=get_incident,
        ),
        ToolProxyBinding(
            name=GET_INCIDENT_TIMELINE,
            effect=ToolEffect.READ,
            input_model=TimelineToolInput,
            output_model=AgentTimelineToolResult,
            description="Read a bounded page of observable incident timeline entries.",
            handler=get_timeline,
        ),
        ToolProxyBinding(
            name=GET_DISPATCH_COORDINATION,
            effect=ToolEffect.READ,
            input_model=IncidentBoundToolInput,
            output_model=AgentDispatchToolView,
            description="Read coarse responder-search and invitation state.",
            handler=get_dispatch,
        ),
        ToolProxyBinding(
            name=COORDINATE_DISPATCH,
            effect=ToolEffect.MUTATE,
            input_model=IncidentBoundToolInput,
            output_model=AgentDispatchToolView,
            description=(
                "Atomically advance bounded responder coordination using the "
                "application service's authorization and recipient rules."
            ),
            handler=coordinate_dispatch,
        ),
        ToolProxyBinding(
            name=GET_FIXED_PROTOCOL,
            effect=ToolEffect.READ,
            input_model=IncidentBoundToolInput,
            output_model=AgentProtocolReferenceToolView,
            description=(
                "Read only the immutable fixed-protocol identity for an active "
                "accepted response; no medical content is returned."
            ),
            handler=get_protocol,
        ),
    )


def _validate_expected_state(arguments: BaseModel, incident: IncidentView) -> None:
    parsed = IncidentBoundToolInput.model_validate(arguments)
    if parsed.incident_id != incident.incident_id:
        raise ToolProxyError(ToolProxyErrorCode.WRONG_INCIDENT)
    if parsed.expected_state_version != incident.state_version:
        raise ToolProxyError(ToolProxyErrorCode.STALE_STATE)


def _validate_dispatch_projection(
    coordination: DispatchCoordinationView,
    incident: IncidentView,
) -> None:
    if (
        coordination.incident_id != incident.incident_id
        or coordination.state is not incident.state
    ):
        raise ToolProxyError(ToolProxyErrorCode.APPLICATION_FAILED)


def _safe_timeline_summary(event_type: TimelineEventType) -> str:
    """Render host-owned labels instead of forwarding ID-bearing audit prose."""

    return {
        TimelineEventType.WEARABLE_EVENT_RECEIVED: "Wearable safety event received.",
        TimelineEventType.INCIDENT_OPENED: "Incident opened.",
        TimelineEventType.VERIFICATION_STARTED: "Wearer verification started.",
        TimelineEventType.CHECK_IN_RECORDED: "Wearer check-in recorded.",
        TimelineEventType.VERIFICATION_TIMED_OUT: "Wearer verification timed out.",
        TimelineEventType.STATE_TRANSITIONED: "Incident state changed.",
        TimelineEventType.RESPONDER_SEARCH_STARTED: (
            "Responder and AED discovery started."
        ),
        TimelineEventType.RESPONDER_INVITED: "An allowlisted responder was invited.",
        TimelineEventType.RESPONDER_DECLINED: "An invited responder declined.",
        TimelineEventType.RESPONDER_ACCEPTED: "An invited responder accepted.",
        TimelineEventType.DISPATCH_ACTIVATED: "Responder dispatch activated.",
    }[event_type]


def _dispatch_projection(
    value: DispatchCoordinationView,
    state_version: int,
) -> AgentDispatchToolView:
    pending = sum(
        invitation.status is InvitationStatus.PENDING
        for invitation in value.invitations
    )
    declined = sum(
        invitation.status is InvitationStatus.DECLINED
        for invitation in value.invitations
    )
    latest = value.invitations[-1] if value.invitations else None
    return AgentDispatchToolView(
        schema_version=value.schema_version,
        incident_id=value.incident_id,
        state=value.state,
        state_version=state_version,
        candidate_count=len(value.candidates),
        pending_invitation_count=pending,
        declined_invitation_count=declined,
        accepted_responder_present=value.accepted_responder_id is not None,
        nearest_aed_available=value.nearest_aed.available,
        latest_invitation=(
            AgentInvitedResponderView(
                role=latest.responder.role,
                skills=latest.responder.skills,
                distance_band=latest.responder.distance_band,
                status=latest.status,
            )
            if latest is not None
            else None
        ),
        updated_at=value.updated_at,
    )


def _sha256_json(value: JsonValue | Mapping[str, JsonValue]) -> str:
    payload = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def hmac_compare(left: str, right: str) -> bool:
    """Avoid timing differences in active-policy hash comparisons."""

    import hmac

    return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))


def _validate_sha256(value: str) -> str:
    if re.fullmatch(SHA256_PATTERN, value) is None:
        raise ValueError("active policy identity must be a SHA-256 digest")
    return value
