"""Framework-neutral durable control-plane contracts for live agent runs."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from vital_relay.agent.contracts import (
    AgentFailureCode,
    AgentPolicyReference,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentToolTrace,
    SandboxKind,
)
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.persona_sessions import PersonaPrincipal


class PersistedAgentRunStatus(StrEnum):
    """Control-plane lifecycle including the pre-result running state."""

    RUNNING = "running"
    COMPLETED = AgentRunStatus.COMPLETED.value
    MANUAL_REQUIRED = AgentRunStatus.MANUAL_REQUIRED.value


class AgentRunRecord(BaseModel):
    """Observable run metadata; no credentials or hidden reasoning are stored."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=SCHEMA_VERSION, ge=1)
    run_id: UUID
    incident_id: UUID
    incident_state_version: int = Field(ge=1)
    objective: str = Field(min_length=1, max_length=64)
    requested_at: AwareDatetime
    created_at: AwareDatetime
    lease_expires_at: AwareDatetime
    requested_by_account_id: UUID
    requested_by_session_id: UUID
    policy: AgentPolicyReference
    model_id: str = Field(min_length=1, max_length=200)
    sandbox: SandboxKind
    status: PersistedAgentRunStatus
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    tool_trace: tuple[AgentToolTrace, ...] = ()
    action_summary: str | None = Field(default=None, min_length=1, max_length=500)
    failure_code: AgentFailureCode | None = None

    @field_validator(
        "requested_at",
        "created_at",
        "lease_expires_at",
        "started_at",
        "finished_at",
    )
    @classmethod
    def normalize_times(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.created_at < self.requested_at:
            raise ValueError("created_at cannot precede requested_at")
        if self.lease_expires_at <= self.created_at:
            raise ValueError("lease_expires_at must follow created_at")
        if self.status is PersistedAgentRunStatus.RUNNING:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.finished_at,
                    self.action_summary,
                    self.failure_code,
                )
            ) or self.tool_trace:
                raise ValueError("running agent runs cannot contain a result")
            return self
        if self.started_at is None or self.finished_at is None:
            raise ValueError("terminal agent runs require result timestamps")
        if self.started_at < self.requested_at:
            raise ValueError("started_at cannot precede requested_at")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.finished_at > self.lease_expires_at:
            raise ValueError("finished_at cannot exceed the durable run lease")
        if self.status is PersistedAgentRunStatus.COMPLETED:
            if self.action_summary is None or self.failure_code is not None:
                raise ValueError("completed agent runs require only an action summary")
        elif self.action_summary is not None or self.failure_code is None:
            raise ValueError("manual-required runs require only a failure code")
        return self

    def to_result(self) -> AgentRunResult | None:
        """Reconstruct the frozen runtime result for a terminal record."""

        if self.status is PersistedAgentRunStatus.RUNNING:
            return None
        from vital_relay.agent.contracts import AgentConclusion

        return AgentRunResult(
            schema_version=self.schema_version,
            run_id=self.run_id,
            incident_id=self.incident_id,
            policy=self.policy,
            model_id=self.model_id,
            sandbox=self.sandbox,
            status=AgentRunStatus(self.status.value),
            started_at=self.started_at,
            finished_at=self.finished_at,
            tool_trace=self.tool_trace,
            conclusion=(
                AgentConclusion(
                    action_summary=self.action_summary,
                    requires_human_review=False,
                )
                if self.action_summary is not None
                else None
            ),
            failure_code=self.failure_code,
        )


class AgentRunStart(BaseModel):
    """Atomic start receipt; only the creator may invoke the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record: AgentRunRecord
    created: bool


class ActiveAgentPolicyRecord(BaseModel):
    """Current scope-local activation pointer and monotonic revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy: AgentPolicyReference
    revision: int = Field(ge=1)
    activated_at: AwareDatetime
    activated_by_account_id: UUID

    @field_validator("activated_at")
    @classmethod
    def normalize_activated_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentRunRepositoryError(RuntimeError):
    """Bounded repository failure safe for application error mapping."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AgentRunConflictError(AgentRunRepositoryError):
    """The requested start or result conflicts with durable state."""


class AgentRunNotFoundError(AgentRunRepositoryError):
    def __init__(self) -> None:
        super().__init__("agent_run_not_found")


class ActiveAgentPolicyConflictError(AgentRunRepositoryError):
    """Activation did not match the expected policy pointer revision."""


class AgentRunRepository(Protocol):
    """Scope-bound atomic persistence boundary used by live orchestration."""

    def start(
        self,
        request: AgentRunRequest,
        *,
        requested_by: PersonaPrincipal,
        policy_snapshot: CoordinationPolicySnapshot,
        model_id: str,
        sandbox: SandboxKind,
        created_at: datetime,
    ) -> AgentRunStart: ...

    def finish(
        self,
        result: AgentRunResult,
        *,
        received_at: datetime,
    ) -> AgentRunRecord:
        """Drain run authority and persist only reconciled host-audit evidence."""
        ...

    def get(self, run_id: UUID) -> AgentRunRecord: ...

    def list_for_incident(
        self,
        incident_id: UUID,
        *,
        limit: int = 50,
    ) -> tuple[AgentRunRecord, ...]: ...
