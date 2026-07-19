"""Sandbox-safe stdin/stdout contracts for one process-isolated agent run."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from vital_relay.agent.capability_runtime import ToolInvocationContext
from vital_relay.agent.contracts import (
    AgentRunRequest,
    SandboxKind,
    VLLMSettings,
)
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.evolution.ace.contracts import SelectedContext
from vital_relay.evolution.ace.selection import verify_selected_context


SANDBOX_WIRE_SCHEMA_VERSION = 2
MAX_SANDBOX_REQUEST_BYTES = 128 * 1024
MAX_SANDBOX_RESULT_BYTES = 2 * 1024 * 1024
NEMOCLAW_INFERENCE_BASE_URL = "https://inference.local/v1"
NEMOCLAW_MANAGED_INFERENCE_API_KEY = "nemoclaw-managed-inference"
DOCKER_INFERENCE_BASE_URL = "http://vllm-gateway:8080/v1"
DOCKER_TOOL_PROXY_ENDPOINT = (
    "http://tool-proxy-gateway:8080/internal/v1/agent/tools/invoke"
)


class SandboxInvocationEnvelope(BaseModel):
    """Serializable trusted context; the capability is redacted in repr/dumps."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    scope_id: str
    incident_id: UUID
    state_version: int = Field(ge=1)
    policy_sha256: str
    issued_at: datetime
    expires_at: datetime
    allowed_tools: tuple[str, ...]
    raw_capability: SecretStr = Field(repr=False)

    @classmethod
    def from_context(cls, value: ToolInvocationContext) -> SandboxInvocationEnvelope:
        return cls(
            run_id=value.run_id,
            scope_id=value.scope_id,
            incident_id=value.incident_id,
            state_version=value.state_version,
            policy_sha256=value.policy_sha256,
            issued_at=value.issued_at,
            expires_at=value.expires_at,
            allowed_tools=value.allowed_tools,
            raw_capability=value.raw_capability,
        )

    def to_context(self) -> ToolInvocationContext:
        return ToolInvocationContext(
            run_id=self.run_id,
            scope_id=self.scope_id,
            incident_id=self.incident_id,
            state_version=self.state_version,
            policy_sha256=self.policy_sha256,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            allowed_tools=self.allowed_tools,
            raw_capability=self.raw_capability,
        )


class SandboxVLLMEnvelope(BaseModel):
    """vLLM connection settings with a serialization-redacted API key."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    base_url: str
    model: str
    api_key: SecretStr = Field(repr=False)
    timeout_seconds: float
    max_retries: int
    temperature: float

    @classmethod
    def from_settings(cls, value: VLLMSettings) -> SandboxVLLMEnvelope:
        return cls(**value.model_dump(mode="python"))

    def to_settings(self) -> VLLMSettings:
        return VLLMSettings.model_validate(self.model_dump(mode="python"))


class SandboxWorkerEnvelope(BaseModel):
    """One complete, bounded worker request passed over an intentional pipe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SANDBOX_WIRE_SCHEMA_VERSION]
    request: AgentRunRequest
    selected_context: SelectedContext
    policy_snapshot: CoordinationPolicySnapshot
    invocation: SandboxInvocationEnvelope
    vllm: SandboxVLLMEnvelope
    tool_proxy_endpoint: str = Field(min_length=1, max_length=2_048)
    sandbox: SandboxKind

    @field_validator("tool_proxy_endpoint")
    @classmethod
    def validate_tool_proxy_endpoint(cls, value: str) -> str:
        if value != value.strip() or value.endswith("/"):
            raise ValueError("tool proxy endpoint must be normalized")
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("tool proxy endpoint must be absolute HTTP(S)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("tool proxy endpoint cannot carry credentials or metadata")
        if parsed.path != "/internal/v1/agent/tools/invoke":
            raise ValueError("tool proxy endpoint must use the fixed internal route")
        if parsed.scheme == "http" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "tool-proxy-gateway",
        }:
            raise ValueError("non-loopback tool proxy endpoints must use HTTPS")
        return value

    @field_validator("sandbox")
    @classmethod
    def require_process_sandbox(cls, value: SandboxKind) -> SandboxKind:
        if value is SandboxKind.IN_PROCESS:
            raise ValueError("worker envelope requires a process sandbox")
        return value

    @model_validator(mode="after")
    def validate_runtime_boundary(self) -> SandboxWorkerEnvelope:
        """Bind runtime-specific routes without providing a fallback choice."""

        self.policy_snapshot.verify_reference(self.request.policy)
        verify_selected_context(
            self.selected_context,
            self.request,
            available_tools=self.invocation.allowed_tools,
            model_id=self.vllm.model,
        )
        if (
            self.invocation.run_id != self.request.run_id
            or self.invocation.incident_id != self.request.incident.incident_id
            or self.invocation.state_version != self.request.incident.state_version
            or self.invocation.policy_sha256 != self.request.policy.sha256
            or not set(self.invocation.allowed_tools).issubset(
                self.policy_snapshot.allowed_tools
            )
        ):
            raise ValueError("sandbox invocation does not match the request")
        if self.vllm.max_retries != 0 or self.vllm.temperature != 0.0:
            raise ValueError("process sandbox inference cannot retry or sample")
        if self.sandbox is SandboxKind.DOCKER:
            if self.vllm.base_url != DOCKER_INFERENCE_BASE_URL:
                raise ValueError("Docker inference must use its fixed gateway")
            if self.tool_proxy_endpoint != DOCKER_TOOL_PROXY_ENDPOINT:
                raise ValueError("Docker tools must use their fixed gateway")
            return self
        if self.vllm.base_url != NEMOCLAW_INFERENCE_BASE_URL:
            raise ValueError("NemoClaw inference must use its managed route")
        if self.vllm.api_key.get_secret_value() != NEMOCLAW_MANAGED_INFERENCE_API_KEY:
            raise ValueError("NemoClaw inference must use its managed identity")
        parsed_proxy = urlparse(self.tool_proxy_endpoint)
        if (
            parsed_proxy.scheme != "https"
            or parsed_proxy.hostname != "vital-relay.internal"
            or parsed_proxy.port != 8443
        ):
            raise ValueError("NemoClaw tools must use the reviewed TLS route")
        return self

    def to_wire_bytes(self) -> bytes:
        """Serialize only at the process boundary and deliberately unmask secrets."""

        payload = self.model_dump(mode="json")
        payload["invocation"]["raw_capability"] = (
            self.invocation.raw_capability.get_secret_value()
        )
        payload["vllm"]["api_key"] = self.vllm.api_key.get_secret_value()
        encoded = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > MAX_SANDBOX_REQUEST_BYTES:
            raise ValueError("sandbox request exceeds the wire limit")
        return encoded

    @classmethod
    def from_wire_bytes(cls, value: bytes) -> SandboxWorkerEnvelope:
        if not value or len(value) > MAX_SANDBOX_REQUEST_BYTES:
            raise ValueError("invalid sandbox request size")
        try:
            payload = json.loads(value.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid sandbox request JSON") from exc
        return cls.model_validate(payload)
