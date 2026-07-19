"""Normalized, privacy-bounded contracts for one coordination-agent run."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    field_validator,
    model_validator,
)

from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentKind, IncidentState


SHA256_PATTERN = r"^[0-9a-f]{64}$"
TOOL_NAME_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class AgentRunStatus(StrEnum):
    """Externally visible run outcomes.

    There is intentionally no deterministic-coordinator fallback outcome. If
    the model, runner, or a bounded tool cannot complete safely, control returns
    to an operator through ``manual_required``.
    """

    COMPLETED = "completed"
    MANUAL_REQUIRED = "manual_required"


class AgentFailureCode(StrEnum):
    MODEL_TIMEOUT = "model_timeout"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    TOOL_DENIED = "tool_denied"
    TOOL_FAILED = "tool_failed"
    AGENT_REQUESTED_HUMAN = "agent_requested_human"
    POLICY_INVALID = "policy_invalid"
    RUNNER_ERROR = "runner_error"


class ToolTraceStatus(StrEnum):
    COMPLETED = "completed"
    DENIED = "denied"
    FAILED = "failed"


class ToolTraceEvidenceSource(StrEnum):
    """Authority that authored the observable trace stored for a run."""

    RUNTIME = "runtime"
    HOST_PROXY_AUDIT = "host_proxy_audit"


class ToolEffect(StrEnum):
    """Whether a registered tool can change authoritative application state."""

    READ = "read"
    MUTATE = "mutate"


class SandboxKind(StrEnum):
    IN_PROCESS = "in_process"
    NEMOCLAW = "nemoclaw"
    DOCKER = "docker"


class AgentIncidentSummary(BaseModel):
    """Small approved incident projection, never a raw physiological stream."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    kind: IncidentKind
    state: IncidentState
    state_version: int = Field(ge=1)
    opened_at: AwareDatetime
    responder_search_active: bool
    accepted_responder_present: bool
    fixed_protocol_available: bool

    @field_validator("opened_at")
    @classmethod
    def normalize_opened_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_coordination_state(self) -> Self:
        if self.state not in {
            IncidentState.ESCALATING,
            IncidentState.RESPONSE_ACTIVE,
        }:
            raise ValueError("agent runs require an active coordination state")
        if self.responder_search_active and self.accepted_responder_present:
            raise ValueError("accepted response and responder search cannot coexist")
        if self.state is IncidentState.RESPONSE_ACTIVE:
            if not self.accepted_responder_present:
                raise ValueError("response_active requires an accepted responder")
        elif self.accepted_responder_present or self.fixed_protocol_available:
            raise ValueError(
                "escalating incidents cannot claim an accepted responder or protocol"
            )
        if self.fixed_protocol_available and not self.accepted_responder_present:
            raise ValueError("fixed protocol requires an accepted responder")
        return self


class AgentPolicyReference(BaseModel):
    """Immutable identity of the coordination policy used for a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    sha256: str = Field(pattern=SHA256_PATTERN)


class AgentRunRequest(BaseModel):
    """One immutable request handed to an agent runner."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    run_id: UUID
    objective: Literal["coordinate_emergency_response"]
    requested_at: AwareDatetime
    incident: AgentIncidentSummary
    policy: AgentPolicyReference

    @field_validator("requested_at")
    @classmethod
    def normalize_requested_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentToolDefinition(BaseModel):
    """Schema-visible tool metadata supplied to the model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str = Field(min_length=1, max_length=500)
    effect: ToolEffect = ToolEffect.READ
    input_schema: dict[str, JsonValue]


class AgentToolTrace(BaseModel):
    """Observable tool call/result record; hidden reasoning is never persisted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool_call_id: UUID
    tool_name: str = Field(pattern=TOOL_NAME_PATTERN)
    arguments: dict[str, JsonValue]
    status: ToolTraceStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime
    result: JsonValue | None = None
    error_code: str | None = Field(default=None, max_length=100)
    evidence_source: ToolTraceEvidenceSource = Field(
        default=ToolTraceEvidenceSource.RUNTIME,
        exclude_if=lambda value: value is ToolTraceEvidenceSource.RUNTIME,
    )
    request_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    result_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    proxy_invocation_id: UUID | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    effect: ToolEffect | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("tool finished_at cannot precede started_at")
        if self.status is ToolTraceStatus.COMPLETED:
            if self.error_code is not None:
                raise ValueError("completed tool traces cannot include an error")
        elif self.error_code is None or self.result is not None:
            raise ValueError("denied and failed tool traces require only an error code")
        if self.evidence_source is ToolTraceEvidenceSource.HOST_PROXY_AUDIT:
            if self.arguments or self.result is not None:
                raise ValueError(
                    "host proxy traces expose hashes instead of request/result bodies"
                )
            if self.request_sha256 is None:
                raise ValueError("host proxy traces require a request hash")
            if self.proxy_invocation_id is None or self.effect is None:
                raise ValueError(
                    "host proxy traces require invocation identity and effect"
                )
            if (
                self.status is ToolTraceStatus.COMPLETED
                and self.result_sha256 is None
            ):
                raise ValueError("completed host proxy traces require a result hash")
            if (
                self.status is not ToolTraceStatus.COMPLETED
                and self.result_sha256 is not None
            ):
                raise ValueError(
                    "failed host proxy traces cannot include a result hash"
                )
        elif any(
            value is not None
            for value in (
                self.request_sha256,
                self.result_sha256,
                self.proxy_invocation_id,
                self.effect,
            )
        ):
            raise ValueError("runtime traces cannot claim host proxy evidence fields")
        return self


class AgentConclusion(BaseModel):
    """Bounded model output; it contains no chain-of-thought or medical text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_summary: str = Field(min_length=1, max_length=500)
    requires_human_review: bool = False


class AgentRunResult(BaseModel):
    """Runtime-neutral result envelope shared by local and sandbox runners."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    run_id: UUID
    incident_id: UUID
    policy: AgentPolicyReference
    model_id: str = Field(min_length=1, max_length=200)
    sandbox: SandboxKind
    status: AgentRunStatus
    started_at: AwareDatetime
    finished_at: AwareDatetime
    tool_trace: tuple[AgentToolTrace, ...] = ()
    conclusion: AgentConclusion | None = None
    failure_code: AgentFailureCode | None = None

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_run_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("agent finished_at cannot precede started_at")
        if self.status is AgentRunStatus.COMPLETED:
            if self.conclusion is None or self.failure_code is not None:
                raise ValueError("completed runs require a conclusion and no failure")
            if self.conclusion.requires_human_review:
                raise ValueError("human-review conclusions must fail closed")
        elif self.conclusion is not None or self.failure_code is None:
            raise ValueError(
                "manual-required runs require a failure code and no conclusion"
            )
        return self


class VLLMSettings(BaseModel):
    """Connection configuration for an OpenAI-compatible vLLM service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str = "http://127.0.0.1:8001/v1"
    model: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$",
    )
    api_key: SecretStr = SecretStr("local-vllm")
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)
    max_retries: int = Field(default=0, ge=0, le=2)
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        from urllib.parse import urlparse

        if value != value.strip() or value.endswith("/"):
            raise ValueError("base_url must be normalized and omit the trailing slash")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url cannot contain credentials, query, or fragment")
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "vllm-gateway",
        }:
            raise ValueError(
                "non-loopback vLLM endpoints must use HTTPS unless they use "
                "the isolated Docker vllm-gateway"
            )
        if not parsed.path.endswith("/v1"):
            raise ValueError("base_url must end with /v1")
        return value
