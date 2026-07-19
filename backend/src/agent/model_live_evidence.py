"""Trusted-host evidence for one real Docker-worker local-model tool round."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest, new as new_hmac
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vital_relay.agent.contracts import (
    SHA256_PATTERN,
    AgentIncidentSummary,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    AgentToolDefinition,
    AgentToolTrace,
    SandboxKind,
    ToolEffect,
    ToolTraceEvidenceSource,
    ToolTraceStatus,
    VLLMSettings,
)
from vital_relay.agent.deep_agent import (
    MODEL_HTTP_TRANSPORT_POLICY_VERSION,
    build_explicit_model_http_client,
)
from vital_relay.agent.http_tools import INITIAL_HTTP_TOOL_CONTRACTS
from vital_relay.agent.policy import (
    CoordinationPolicySnapshot,
    allowed_tools_for_state,
    load_pinned_policy_snapshot,
)
from vital_relay.agent.sandbox import (
    DOCKER_INFERENCE_BASE_URL,
    DOCKER_TOOL_PROXY_ENDPOINT,
)
from vital_relay.agent.source_manifest import (
    DOCKER_AGENT_SOURCE_MANIFEST,
    capture_reviewed_source_snapshot,
)
from vital_relay.agent.tool_contracts import (
    GET_INCIDENT,
    AgentIncidentToolView,
    IncidentBoundToolInput,
)
from vital_relay.application.agent_control import (
    AgentRunRecord,
    PersistedAgentRunStatus,
)
from vital_relay.config import (
    AGENT_MODEL_ARTIFACT_SHA256_ENV,
    AGENT_MODEL_REVISION_ENV,
    build_generator_context_selector,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import (
    IncidentState,
    IncidentTimelineEntry,
    IncidentView,
    TimelineEventType,
)
from vital_relay.evolution.ace.contracts import SelectedContext
from vital_relay.evolution.ace.selection import GeneratorContextSelector
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256


VLLM_BASE_URL_ENV = "VITAL_RELAY_VLLM_BASE_URL"
VLLM_MODEL_ENV = "VITAL_RELAY_VLLM_MODEL"
VLLM_API_KEY_ENV = "VITAL_RELAY_DOCKER_VLLM_API_KEY"
AGENT_ENABLED_ENV = "VITAL_RELAY_AGENT_ENABLED"
AGENT_SANDBOX_ENV = "VITAL_RELAY_AGENT_SANDBOX"
AGENT_TIMEOUT_ENV = "VITAL_RELAY_AGENT_TIMEOUT_SECONDS"
AGENT_SIGNING_KEY_ENV = "VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY"
AGENT_POLICY_PATH_ENV = "VITAL_RELAY_AGENT_POLICY_PATH"
AGENT_POLICY_DIGEST_PATH_ENV = "VITAL_RELAY_AGENT_POLICY_DIGEST_PATH"
DATABASE_URL_ENV = "VITAL_RELAY_DATABASE_URL"
PRODUCT_COMMAND_TOKEN_ENV = "VITAL_RELAY_MODEL_EVIDENCE_COMMAND_TOKEN"
PRODUCT_INCIDENT_ID_ENV = "VITAL_RELAY_MODEL_EVIDENCE_INCIDENT_ID"
PRODUCT_STATE_VERSION_ENV = "VITAL_RELAY_MODEL_EVIDENCE_STATE_VERSION"
EVIDENCE_HMAC_KEY_ENV = "VITAL_RELAY_MODEL_EVIDENCE_HMAC_KEY"
EVIDENCE_KEY_ID_ENV = "VITAL_RELAY_MODEL_EVIDENCE_KEY_ID"
EVIDENCE_ISSUER_ENV = "VITAL_RELAY_MODEL_EVIDENCE_ISSUER"
MODEL_SANDBOX_CONTAINER_ID_ENV = "VITAL_RELAY_VLLM_SANDBOX_CONTAINER_ID"
MODEL_SANDBOX_IMAGE_SHA256_ENV = "VITAL_RELAY_VLLM_SANDBOX_IMAGE_SHA256"
MODEL_SANDBOX_INSPECT_SHA256_ENV = "VITAL_RELAY_VLLM_SANDBOX_INSPECT_SHA256"
DOCKER_CLI_PATH_ENV = "VITAL_RELAY_DOCKER_CLI_PATH"
DOCKER_CLI_SHA256_ENV = "VITAL_RELAY_DOCKER_CLI_SHA256"
AGENT_WORKER_MANIFEST_SHA256_ENV = (
    "VITAL_RELAY_MODEL_EVIDENCE_WORKER_MANIFEST_SHA256"
)
AGENT_RUNTIME_SNAPSHOT_SHA256_ENV = (
    "VITAL_RELAY_MODEL_EVIDENCE_AGENT_RUNTIME_SNAPSHOT_SHA256"
)
AGENT_STARTUP_SHA256_ENV = "VITAL_RELAY_MODEL_EVIDENCE_AGENT_STARTUP_SHA256"

EXACT_VLLM_BASE_URL = "http://127.0.0.1:8001/v1"
EXACT_PRODUCT_BASE_URL = "http://127.0.0.1:8000"
VLLM_CONTAINER_PORT = "8000/tcp"
VLLM_HOST_PORT = "8001"
EXACT_DOCKER_HOST = "unix:///var/run/docker.sock"
DEFAULT_AGENT_TIMEOUT_SECONDS = 90.0
MAX_MODEL_CATALOG_BYTES = 1_048_576
MAX_PRODUCT_RESPONSE_BYTES = 2_097_152
MAX_DOCKER_INSPECT_BYTES = 2_097_152
EVIDENCE_KIND = "vital_relay_live_model_typed_tool_v3"
RUNNER_IMPLEMENTATION = (
    "vital_relay.agent.sandbox.ProcessSandboxAgentRunner"
)
WORKER_IMPLEMENTATION = "vital_relay.agent.worker.main"
PRODUCT_ADAPTER = "vital_relay.application.agent_service.AgentRunService"
PRODUCT_TOOL_PROXY = (
    "vital_relay.application.tool_proxy.InternalAgentToolProxy"
)
PRODUCT_RUN_HEADER = "X-Vital-Relay-Agent-Worker-Manifest-SHA256"
RUNTIME_SNAPSHOT_HEADER = "X-Vital-Relay-Agent-Runtime-Snapshot-SHA256"
STARTUP_SNAPSHOT_HEADER = "X-Vital-Relay-Agent-Startup-SHA256"
WORKER_REQUEST_HEADER = "X-Vital-Relay-Agent-Worker-Request-SHA256"
WORKER_RESULT_HEADER = "X-Vital-Relay-Agent-Worker-Result-SHA256"
PRODUCT_BOUNDARY_HMAC_HEADER = "X-Vital-Relay-Agent-Boundary-HMAC-SHA256"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_AGENT_POLICY_PATH = (
    PROJECT_ROOT / "agents/policies/baseline/coordination_policy.yaml"
)
DEFAULT_AGENT_POLICY_DIGEST_PATH = (
    PROJECT_ROOT / "agents/policies/baseline/coordination_policy.sha256"
)

_IDENTITY_PATTERN = r"^[a-z][a-z0-9._:-]{0,127}$"
_CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43,256}$")
_NETWORK_ENV_NAMES = frozenset(
    {
        "ALL_PROXY",
        "CURL_CA_BUNDLE",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
        "OPENAI_BASE_URL",
        "OPENAI_PROXY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "https_proxy",
        "http_proxy",
        "no_proxy",
    }
)
_BOUND_SOURCE_FILES = (
    "backend/src/vital_relay/agent/capabilities.py",
    "backend/src/vital_relay/agent/capability_runtime.py",
    "backend/src/vital_relay/agent/contracts.py",
    "backend/src/vital_relay/agent/deep_agent.py",
    "backend/src/vital_relay/agent/http_tools.py",
    "backend/src/vital_relay/agent/model_live_evidence.py",
    "backend/src/vital_relay/agent/policy.py",
    "backend/src/vital_relay/agent/sandbox.py",
    "backend/src/vital_relay/agent/sandbox_wire.py",
    "backend/src/vital_relay/agent/source_manifest.py",
    "backend/src/vital_relay/agent/tool_contracts.py",
    "backend/src/vital_relay/agent/tool_identity.py",
    "backend/src/vital_relay/agent/tool_transport.py",
    "backend/src/vital_relay/agent/tools.py",
    "backend/src/vital_relay/agent/worker.py",
    "backend/src/vital_relay/api/agent_runs.py",
    "backend/src/vital_relay/application/agent_control.py",
    "backend/src/vital_relay/application/agent_evidence.py",
    "backend/src/vital_relay/application/agent_service.py",
    "backend/src/vital_relay/application/tool_proxy.py",
    "backend/src/vital_relay/config.py",
    "backend/src/vital_relay/domain/incidents.py",
    "backend/src/vital_relay/evolution/ace/selection.py",
    "backend/src/vital_relay/evolution/hashing.py",
    "infrastructure/docker-agent/Dockerfile",
    "infrastructure/docker-agent/compose.yaml",
    "infrastructure/docker-agent/tool_proxy_gateway.py",
    "infrastructure/docker-agent/vllm_gateway.py",
)


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceAuthority(_EvidenceModel):
    issuer: str = Field(pattern=_IDENTITY_PATTERN)
    key_id: str = Field(pattern=_IDENTITY_PATTERN)
    algorithm: Literal["hmac-sha256"] = "hmac-sha256"


class OperatorPinnedModelClaim(_EvidenceModel):
    """Operator claim authenticated by the host, not an endpoint attestation."""

    claim_kind: Literal["operator_pinned_not_endpoint_attested"] = (
        "operator_pinned_not_endpoint_attested"
    )
    provider: Literal["vllm"] = "vllm"
    model_id: str = Field(min_length=1, max_length=160)
    revision: str = Field(min_length=1, max_length=96)
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    catalog_inference_config_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_inference_config_sha256: str = Field(pattern=SHA256_PATTERN)


class ModelCatalogEvidence(_EvidenceModel):
    endpoint: Literal["http://127.0.0.1:8001/v1/models"] = (
        "http://127.0.0.1:8001/v1/models"
    )
    selected_claimed_model_exact_match: Literal[True] = True
    artifact_attested_by_endpoint: Literal[False] = False
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: int = Field(ge=0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("catalog timing is inverted")
        return self


class ReviewedModelServiceEvidence(_EvidenceModel):
    kind: Literal["docker_vllm_service"] = "docker_vllm_service"
    endpoint_binding: Literal["127.0.0.1:8001->8000/tcp"] = (
        "127.0.0.1:8001->8000/tcp"
    )
    container_id_sha256: str = Field(pattern=SHA256_PATTERN)
    image_sha256: str = Field(pattern=SHA256_PATTERN)
    docker_cli_sha256: str = Field(pattern=SHA256_PATTERN)
    normalized_inspect_sha256: str = Field(pattern=SHA256_PATTERN)
    checked_at: AwareDatetime

    @field_validator("checked_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentExecutionEvidence(_EvidenceModel):
    kind: Literal[SandboxKind.DOCKER] = SandboxKind.DOCKER
    runner: Literal[RUNNER_IMPLEMENTATION] = RUNNER_IMPLEMENTATION
    worker: Literal[WORKER_IMPLEMENTATION] = WORKER_IMPLEMENTATION
    product_adapter: Literal[PRODUCT_ADAPTER] = PRODUCT_ADAPTER
    tool_proxy_adapter: Literal[PRODUCT_TOOL_PROXY] = PRODUCT_TOOL_PROXY
    worker_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    startup_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_source_sha256: str = Field(pattern=SHA256_PATTERN)
    runner_source_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_command_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    product_boundary_authentication: Literal["hmac-sha256"] = "hmac-sha256"
    product_boundary_hmac_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_proxy_endpoint: Literal[DOCKER_TOOL_PROXY_ENDPOINT] = (
        DOCKER_TOOL_PROXY_ENDPOINT
    )
    product_route: Literal["POST /v1/incidents/{incident_id}/agent-runs"] = (
        "POST /v1/incidents/{incident_id}/agent-runs"
    )
    startup_gate_passed: Literal[True] = True
    fresh_product_run: Literal[True] = True


class RunBindingEvidence(_EvidenceModel):
    run_id: UUID
    freshness_nonce: str = Field(pattern=SHA256_PATTERN)
    incident_id_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_run_request_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_envelope_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_result_sha256: str = Field(pattern=SHA256_PATTERN)
    product_request_sha256: str = Field(pattern=SHA256_PATTERN)
    product_response_sha256: str = Field(pattern=SHA256_PATTERN)
    normalized_result_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_fresh_run_identity(self) -> Self:
        raw = bytes.fromhex(self.freshness_nonce)
        if self.run_id != UUID(bytes=raw[:16], version=4):
            raise ValueError("freshness nonce does not bind run identity")
        return self


class SelectedContextEvidence(_EvidenceModel):
    playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_input_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_context_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_item_ids: tuple[str, ...] = Field(min_length=1)
    selected_item_sha256s: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_parallel_identities(self) -> Self:
        if len(self.selected_item_ids) != len(self.selected_item_sha256s):
            raise ValueError("selected context identities are incomplete")
        if len(set(self.selected_item_ids)) != len(self.selected_item_ids):
            raise ValueError("selected context IDs must be unique")
        return self


class ToolDefinitionEvidence(_EvidenceModel):
    tool_names: tuple[str, ...] = Field(min_length=1, max_length=10)
    definitions_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_product_surface(self) -> Self:
        if GET_INCIDENT not in self.tool_names:
            raise ValueError("live evidence requires get_incident availability")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise ValueError("tool definitions must be unique")
        return self


class ToolCallBudgetEvidence(_EvidenceModel):
    tool_name: str
    max_calls: int = Field(ge=1)


class RetryBudgetEvidence(_EvidenceModel):
    catalog_attempts: Literal[1] = 1
    product_run_attempts: Literal[1] = 1
    model_max_retries: Literal[0] = 0
    transport_max_retries: Literal[0] = 0
    model_timeout_seconds: float = Field(gt=0, le=300)
    product_timeout_seconds: float = Field(gt=0, le=315)
    max_total_tool_calls: int = Field(ge=1)
    max_mutating_tool_calls: int = Field(ge=0)
    per_tool: tuple[ToolCallBudgetEvidence, ...] = Field(min_length=1)


class SourceFileEvidence(_EvidenceModel):
    path: str = Field(min_length=1, max_length=240)
    sha256: str = Field(pattern=SHA256_PATTERN)


class TransportPolicyEvidence(_EvidenceModel):
    policy_version: Literal[MODEL_HTTP_TRANSPORT_POLICY_VERSION] = (
        MODEL_HTTP_TRANSPORT_POLICY_VERSION
    )
    catalog_base_url: Literal[EXACT_VLLM_BASE_URL] = EXACT_VLLM_BASE_URL
    worker_model_base_url: Literal[DOCKER_INFERENCE_BASE_URL] = (
        DOCKER_INFERENCE_BASE_URL
    )
    product_base_url: Literal[EXACT_PRODUCT_BASE_URL] = EXACT_PRODUCT_BASE_URL
    tool_proxy_endpoint: Literal[DOCKER_TOOL_PROXY_ENDPOINT] = (
        DOCKER_TOOL_PROXY_ENDPOINT
    )
    trust_env: Literal[False] = False
    redirects: Literal[False] = False
    transport_retries: Literal[0] = 0
    model_retries: Literal[0] = 0
    custom_ca: Literal[False] = False
    proxy: Literal[False] = False
    host_catalog_and_worker_use_same_policy: Literal[True] = True
    distinct_process_clients: Literal[True] = True


class ExecutionConfigurationEvidence(_EvidenceModel):
    source_files: tuple[SourceFileEvidence, ...] = Field(min_length=1)
    transport: TransportPolicyEvidence
    catalog_inference_config_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_inference_config_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_context_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_definitions_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    model_service_image_sha256: str = Field(pattern=SHA256_PATTERN)
    model_service_inspect_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_worker_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_runtime_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_startup_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_content_address(self, info: ValidationInfo) -> Self:
        if info.context and info.context.get("build_content_address"):
            return self
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"configuration_sha256"})
        )
        if not compare_digest(self.configuration_sha256, expected):
            raise ValueError("configuration_sha256 does not match configuration")
        return self

    @classmethod
    def create(cls, **values: object) -> ExecutionConfigurationEvidence:
        material = cls.model_validate(
            {**values, "configuration_sha256": "0" * 64},
            context={"build_content_address": True},
        )
        return cls.model_validate(
            {
                **values,
                "configuration_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json",
                        exclude={"configuration_sha256"},
                    )
                ),
            }
        )


class NormalizedToolTrace(_EvidenceModel):
    sequence: int = Field(ge=1)
    tool_name: str
    effect: ToolEffect
    status: Literal[ToolTraceStatus.COMPLETED] = ToolTraceStatus.COMPLETED
    evidence_source: Literal[ToolTraceEvidenceSource.HOST_PROXY_AUDIT] = (
        ToolTraceEvidenceSource.HOST_PROXY_AUDIT
    )
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    proxy_invocation_id_sha256: str = Field(pattern=SHA256_PATTERN)
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: int = Field(ge=0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("tool timing is inverted")
        return self


class RunOutcomeEvidence(_EvidenceModel):
    status: Literal[AgentRunStatus.COMPLETED] = AgentRunStatus.COMPLETED
    sandbox: Literal[SandboxKind.DOCKER] = SandboxKind.DOCKER
    started_at: AwareDatetime
    finished_at: AwareDatetime
    duration_ms: int = Field(ge=0)
    trace: tuple[NormalizedToolTrace, ...] = Field(min_length=1, max_length=1)

    @field_validator("started_at", "finished_at")
    @classmethod
    def normalize_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_complete_round(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("run timing is inverted")
        if self.trace[0].tool_name != GET_INCIDENT:
            raise ValueError("live evidence requires one get_incident trace")
        return self


class LiveModelEvidence(_EvidenceModel):
    """Authenticated body-free evidence for one product Docker-worker run."""

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    evidence_kind: Literal[EVIDENCE_KIND] = EVIDENCE_KIND
    model_claim: OperatorPinnedModelClaim
    catalog: ModelCatalogEvidence
    model_service: ReviewedModelServiceEvidence
    agent_execution: AgentExecutionEvidence
    run: RunBindingEvidence
    selected_context: SelectedContextEvidence
    tool_definitions: ToolDefinitionEvidence
    retry_budget: RetryBudgetEvidence
    execution_configuration: ExecutionConfigurationEvidence
    outcome: RunOutcomeEvidence
    authority: EvidenceAuthority
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    attestation_hmac_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_content_address(self, info: ValidationInfo) -> Self:
        if info.context and info.context.get("build_content_address"):
            return self
        if not compare_digest(self.evidence_sha256, _evidence_sha256(self)):
            raise ValueError("evidence_sha256 does not match canonical evidence")
        return self

    @classmethod
    def create(
        cls,
        *,
        signing_key: bytes,
        **values: object,
    ) -> LiveModelEvidence:
        _validate_signing_key(signing_key)
        material = cls.model_validate(
            {
                **values,
                "evidence_sha256": "0" * 64,
                "attestation_hmac_sha256": "0" * 64,
            },
            context={"build_content_address": True},
        )
        evidence_sha256 = _evidence_sha256(material)
        unsigned = cls.model_validate(
            {
                **values,
                "evidence_sha256": evidence_sha256,
                "attestation_hmac_sha256": "0" * 64,
            },
            context={"build_content_address": True},
        )
        signature = new_hmac(
            signing_key,
            _attestation_payload(unsigned),
            sha256,
        ).hexdigest()
        return cls.model_validate(
            {
                **values,
                "evidence_sha256": evidence_sha256,
                "attestation_hmac_sha256": signature,
            }
        )


class FailureEnvelope(_EvidenceModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    evidence_kind: Literal[EVIDENCE_KIND] = EVIDENCE_KIND
    status: Literal["failed"] = "failed"
    failure_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,99}$")


class LiveModelEvidenceError(RuntimeError):
    """A bounded non-secret failure from the live evidence lane."""

    def __init__(self, code: str, exit_code: int) -> None:
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _DockerSandboxConfiguration:
    container_id: str
    image_sha256: str
    inspect_sha256: str
    cli_path: Path
    cli_sha256: str


@dataclass(frozen=True, slots=True)
class _AgentRuntimePins:
    worker_manifest_sha256: str
    runtime_snapshot_sha256: str
    startup_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class _AgentBoundaryHeaders:
    worker_envelope_sha256: str
    worker_result_sha256: str
    boundary_hmac_sha256: str


@dataclass(frozen=True, slots=True)
class _LiveConfiguration:
    catalog_settings: VLLMSettings
    worker_settings: VLLMSettings
    revision: str
    artifact_sha256: str
    authority: EvidenceAuthority
    model_sandbox: _DockerSandboxConfiguration
    agent_runtime_pins: _AgentRuntimePins
    policy_path: Path
    policy_digest_path: Path
    command_token: SecretStr = field(repr=False)
    incident_id: UUID
    expected_state_version: int
    product_timeout_seconds: float
    product_boundary_key: bytes = field(repr=False)
    signing_key: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class _ProductObservation:
    request: AgentRunRequest
    result: AgentRunResult
    record: AgentRunRecord
    policy: CoordinationPolicySnapshot
    selected_context: SelectedContext
    definitions: tuple[AgentToolDefinition, ...]
    freshness_nonce: str
    product_request_sha256: str
    product_response_sha256: str
    expected_tool_request_sha256: str
    expected_tool_result_sha256: str
    worker_envelope_sha256: str
    worker_result_sha256: str
    product_boundary_hmac_sha256: str


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export authenticated evidence for one real Docker-worker local "
            "vLLM typed-tool round"
        )
    )
    parser.add_argument(
        "--sandbox",
        choices=[entry.value for entry in SandboxKind],
        default=SandboxKind.IN_PROCESS.value,
    )
    args = parser.parse_args(argv)
    try:
        sandbox = SandboxKind(args.sandbox)
        if sandbox is SandboxKind.IN_PROCESS:
            raise LiveModelEvidenceError("in_process_not_live_evidence", 8)
        if sandbox is not SandboxKind.DOCKER:
            raise LiveModelEvidenceError("reviewed_agent_sandbox_required", 8)
        configuration = _configuration_from_environment(os.environ)
        evidence = export_live_model_evidence(configuration)
    except LiveModelEvidenceError as exc:
        _print_canonical(FailureEnvelope(failure_code=exc.code))
        return exc.exit_code
    except Exception:
        _print_canonical(FailureEnvelope(failure_code="evidence_export_failed"))
        return 10
    _print_canonical(evidence)
    return 0


def export_live_model_evidence(
    configuration: _LiveConfiguration,
) -> LiveModelEvidence:
    """Run the non-injectable product path; no host Deep Agent is accepted."""

    catalog_settings = VLLMSettings.model_validate(configuration.catalog_settings)
    worker_settings = VLLMSettings.model_validate(configuration.worker_settings)
    if (
        catalog_settings.base_url != EXACT_VLLM_BASE_URL
        or worker_settings.base_url != DOCKER_INFERENCE_BASE_URL
        or catalog_settings.model != worker_settings.model
        or catalog_settings.max_retries != 0
        or worker_settings.max_retries != 0
    ):
        raise LiveModelEvidenceError("unreviewed_transport_configuration", 2)
    _require_current_worker_manifest(
        configuration.agent_runtime_pins.worker_manifest_sha256
    )
    selector = build_generator_context_selector(
        worker_settings,
        revision=configuration.revision,
        artifact_sha256=configuration.artifact_sha256,
    )
    try:
        policy = load_pinned_policy_snapshot(
            configuration.policy_path,
            configuration.policy_digest_path,
        )
    except Exception as exc:
        raise LiveModelEvidenceError("reviewed_policy_unavailable", 2) from exc
    model_service = _inspect_reviewed_model_sandbox(
        configuration.model_sandbox
    )
    with build_explicit_model_http_client(
        timeout_seconds=catalog_settings.timeout_seconds,
    ) as catalog_client:
        catalog = _verify_model_catalog(
            catalog_settings,
            client=catalog_client,
        )
    with _build_product_http_client(
        timeout_seconds=configuration.product_timeout_seconds,
    ) as product_client:
        observation = _run_product_path(
            configuration,
            client=product_client,
            policy=policy,
            selector=selector,
        )
    _require_current_worker_manifest(
        configuration.agent_runtime_pins.worker_manifest_sha256
    )
    return _build_evidence(
        configuration=configuration,
        catalog=catalog,
        model_service=model_service,
        observation=observation,
    )


def verify_live_model_evidence(
    evidence: LiveModelEvidence | Mapping[str, object],
    *,
    signing_key: bytes,
    expected_issuer: str,
    expected_key_id: str,
) -> LiveModelEvidence:
    """Verify schema, content address, authority identity, and host HMAC."""

    _validate_signing_key(signing_key)
    try:
        validated = LiveModelEvidence.model_validate(evidence)
    except Exception as exc:
        raise LiveModelEvidenceError("invalid_evidence", 7) from exc
    if (
        validated.authority.issuer != expected_issuer
        or validated.authority.key_id != expected_key_id
    ):
        raise LiveModelEvidenceError("evidence_authority_mismatch", 7)
    expected = new_hmac(
        signing_key,
        _attestation_payload(validated),
        sha256,
    ).hexdigest()
    if not compare_digest(expected, validated.attestation_hmac_sha256):
        raise LiveModelEvidenceError("invalid_evidence_attestation", 7)
    return validated


def _configuration_from_environment(
    environment: Mapping[str, str],
) -> _LiveConfiguration:
    try:
        _reject_ambient_network_configuration(environment)
        if VLLM_BASE_URL_ENV in environment:
            raise ValueError("Docker worker forbids host model URL overrides")
        if environment.get(AGENT_ENABLED_ENV, "").lower() != "true":
            raise ValueError("agent product composition must be enabled")
        if environment.get(AGENT_SANDBOX_ENV) != SandboxKind.DOCKER.value:
            raise ValueError("agent product composition must select Docker")
        _required_clean(environment, DATABASE_URL_ENV)
        product_boundary_key = _decode_signing_key(
            _required_clean(environment, AGENT_SIGNING_KEY_ENV)
        )
        model = _required_clean(environment, VLLM_MODEL_ENV)
        revision = _required_clean(environment, AGENT_MODEL_REVISION_ENV)
        artifact_sha256 = _required_sha256(
            environment,
            AGENT_MODEL_ARTIFACT_SHA256_ENV,
        )
        api_key = SecretStr(_required_clean(environment, VLLM_API_KEY_ENV))
        agent_timeout = _bounded_float(
            environment.get(AGENT_TIMEOUT_ENV),
            default=DEFAULT_AGENT_TIMEOUT_SECONDS,
            minimum=10.0,
            maximum=300.0,
        )
        model_timeout = max(1.0, agent_timeout - 5.0)
        catalog_settings = VLLMSettings(
            base_url=EXACT_VLLM_BASE_URL,
            model=model,
            api_key=api_key,
            timeout_seconds=model_timeout,
            max_retries=0,
            temperature=0.0,
        )
        worker_settings = VLLMSettings(
            base_url=DOCKER_INFERENCE_BASE_URL,
            model=model,
            api_key=api_key,
            timeout_seconds=model_timeout,
            max_retries=0,
            temperature=0.0,
        )
        signing_key = _decode_signing_key(
            _required_clean(environment, EVIDENCE_HMAC_KEY_ENV)
        )
        authority = EvidenceAuthority(
            issuer=_required_clean(environment, EVIDENCE_ISSUER_ENV),
            key_id=_required_clean(environment, EVIDENCE_KEY_ID_ENV),
        )
        model_sandbox = _DockerSandboxConfiguration(
            container_id=_required_container_id(
                environment,
                MODEL_SANDBOX_CONTAINER_ID_ENV,
            ),
            image_sha256=_required_sha256(
                environment,
                MODEL_SANDBOX_IMAGE_SHA256_ENV,
            ),
            inspect_sha256=_required_sha256(
                environment,
                MODEL_SANDBOX_INSPECT_SHA256_ENV,
            ),
            cli_path=_required_absolute_path(environment, DOCKER_CLI_PATH_ENV),
            cli_sha256=_required_sha256(environment, DOCKER_CLI_SHA256_ENV),
        )
        reviewed_worker_manifest_sha256 = _required_sha256(
            environment,
            AGENT_WORKER_MANIFEST_SHA256_ENV,
        )
        actual_worker_manifest_sha256 = (
            _reviewed_agent_worker_manifest_sha256()
        )
        if not compare_digest(
            reviewed_worker_manifest_sha256,
            actual_worker_manifest_sha256,
        ):
            raise ValueError("reviewed worker manifest does not match source")
        runtime_pins = _AgentRuntimePins(
            worker_manifest_sha256=actual_worker_manifest_sha256,
            runtime_snapshot_sha256=_required_sha256(
                environment,
                AGENT_RUNTIME_SNAPSHOT_SHA256_ENV,
            ),
            startup_snapshot_sha256=_required_sha256(
                environment,
                AGENT_STARTUP_SHA256_ENV,
            ),
        )
        command_token = _required_clean(environment, PRODUCT_COMMAND_TOKEN_ENV)
        if _OPAQUE_TOKEN_PATTERN.fullmatch(command_token) is None:
            raise ValueError("command token is not a product session token")
        incident_id = UUID(_required_clean(environment, PRODUCT_INCIDENT_ID_ENV))
        expected_state_version = int(
            _required_clean(environment, PRODUCT_STATE_VERSION_ENV)
        )
        if expected_state_version < 1:
            raise ValueError("incident state version must be positive")
        policy_path = Path(
            environment.get(
                AGENT_POLICY_PATH_ENV,
                str(DEFAULT_AGENT_POLICY_PATH),
            )
        )
        policy_digest_path = Path(
            environment.get(
                AGENT_POLICY_DIGEST_PATH_ENV,
                str(DEFAULT_AGENT_POLICY_DIGEST_PATH),
            )
        )
        if not policy_path.is_absolute() or not policy_digest_path.is_absolute():
            raise ValueError("reviewed policy paths must be absolute")
        build_generator_context_selector(
            worker_settings,
            revision=revision,
            artifact_sha256=artifact_sha256,
        )
    except Exception as exc:
        raise LiveModelEvidenceError("unreviewed_live_configuration", 2) from exc
    return _LiveConfiguration(
        catalog_settings=catalog_settings,
        worker_settings=worker_settings,
        revision=revision,
        artifact_sha256=artifact_sha256,
        authority=authority,
        model_sandbox=model_sandbox,
        agent_runtime_pins=runtime_pins,
        policy_path=policy_path,
        policy_digest_path=policy_digest_path,
        command_token=SecretStr(command_token),
        incident_id=incident_id,
        expected_state_version=expected_state_version,
        product_timeout_seconds=min(315.0, agent_timeout + 15.0),
        product_boundary_key=product_boundary_key,
        signing_key=signing_key,
    )


def _reviewed_agent_worker_manifest_sha256() -> str:
    """Capture the exact integrated sandbox-safe worker source manifest."""

    return capture_reviewed_source_snapshot(
        PROJECT_ROOT,
        DOCKER_AGENT_SOURCE_MANIFEST,
    ).digest


def _require_current_worker_manifest(expected_sha256: str) -> None:
    """Reject source changes before or during a live product-path run."""

    try:
        actual_sha256 = _reviewed_agent_worker_manifest_sha256()
    except Exception as exc:
        raise LiveModelEvidenceError(
            "agent_worker_manifest_unavailable",
            9,
        ) from exc
    if not compare_digest(actual_sha256, expected_sha256):
        raise LiveModelEvidenceError("agent_worker_manifest_changed", 9)


def _verify_model_catalog(
    settings: VLLMSettings,
    *,
    client: httpx.Client,
) -> ModelCatalogEvidence:
    started_at = datetime.now(UTC)
    try:
        response = client.get(
            f"{settings.base_url}/models",
            headers={
                "Authorization": "Bearer " + settings.api_key.get_secret_value(),
                "Accept": "application/json",
            },
        )
        response.raise_for_status()
    except Exception as exc:
        raise LiveModelEvidenceError("model_endpoint_unavailable", 3) from exc
    finished_at = datetime.now(UTC)
    try:
        payload = _bounded_json(response, MAX_MODEL_CATALOG_BYTES)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("invalid catalog")
        model_ids = tuple(
            entry.get("id")
            for entry in payload["data"]
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        )
    except Exception as exc:
        raise LiveModelEvidenceError("invalid_model_catalog", 3) from exc
    if settings.model not in model_ids:
        raise LiveModelEvidenceError("configured_model_not_listed", 4)
    return ModelCatalogEvidence(
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=_duration_ms(started_at, finished_at),
    )


def _run_product_path(
    configuration: _LiveConfiguration,
    *,
    client: httpx.Client,
    policy: CoordinationPolicySnapshot,
    selector: GeneratorContextSelector,
) -> _ProductObservation:
    token = configuration.command_token.get_secret_value()
    incident_path = f"/v1/incidents/{configuration.incident_id}"
    common_headers = {
        "Accept": "application/json",
        "X-Vital-Relay-Device-Token": token,
    }
    try:
        incident_response = client.get(incident_path, headers=common_headers)
        incident_response.raise_for_status()
        incident = IncidentView.model_validate(
            _bounded_json(incident_response, MAX_PRODUCT_RESPONSE_BYTES)
        )
        timeline_response = client.get(
            f"{incident_path}/timeline",
            headers=common_headers,
        )
        timeline_response.raise_for_status()
        timeline = TypeAdapter(tuple[IncidentTimelineEntry, ...]).validate_python(
            _bounded_json(timeline_response, MAX_PRODUCT_RESPONSE_BYTES)
        )
        fixed_protocol_available = False
        if incident.state is IncidentState.RESPONSE_ACTIVE:
            protocol_response = client.get(
                f"{incident_path}/protocol",
                headers=common_headers,
            )
            if protocol_response.status_code == 200:
                _bounded_content(protocol_response, MAX_PRODUCT_RESPONSE_BYTES)
                fixed_protocol_available = True
            elif protocol_response.status_code != 404:
                protocol_response.raise_for_status()
        _validate_preflight(configuration, incident, timeline)
    except Exception as exc:
        raise LiveModelEvidenceError("product_preflight_unavailable", 5) from exc

    freshness = secrets.token_bytes(32)
    run_id = UUID(bytes=freshness[:16], version=4)
    command = {
        "schema_version": SCHEMA_VERSION,
        "run_id": str(run_id),
        "expected_state_version": configuration.expected_state_version,
    }
    run_path = f"{incident_path}/agent-runs"
    product_request_material = {
        "method": "POST",
        "path": run_path,
        "body": command,
    }
    try:
        response = client.post(
            run_path,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
            content=canonical_json_bytes(command),
        )
        if response.status_code != 201:
            raise ValueError("product run was unavailable or replayed")
        payload = _bounded_json(response, MAX_PRODUCT_RESPONSE_BYTES)
        record = AgentRunRecord.model_validate(payload)
        boundary_headers = _verify_agent_runtime_headers(
            response,
            configuration.agent_runtime_pins,
            product_boundary_key=configuration.product_boundary_key,
            run_id=run_id,
            product_response_sha256=canonical_sha256(payload),
        )
    except LiveModelEvidenceError:
        raise
    except Exception as exc:
        raise LiveModelEvidenceError("product_agent_run_failed", 5) from exc

    request = AgentRunRequest(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        objective="coordinate_emergency_response",
        requested_at=record.requested_at,
        incident=AgentIncidentSummary(
            schema_version=SCHEMA_VERSION,
            incident_id=incident.incident_id,
            kind=incident.kind,
            state=incident.state,
            state_version=incident.state_version,
            opened_at=incident.opened_at,
            responder_search_active=(
                incident.state is IncidentState.ESCALATING
                and any(
                    entry.event_type
                    is TimelineEventType.RESPONDER_SEARCH_STARTED
                    for entry in timeline
                )
            ),
            accepted_responder_present=(
                incident.state is IncidentState.RESPONSE_ACTIVE
            ),
            fixed_protocol_available=fixed_protocol_available,
        ),
        policy=record.policy,
    )
    try:
        policy.verify_reference(record.policy)
        allowed_tools = allowed_tools_for_state(policy, incident.state)
        selected_context = selector.select(
            request,
            available_tools=tuple(sorted(allowed_tools)),
        )
        definitions = _product_tool_definitions(allowed_tools)
        result = record.to_result()
        if result is None:
            raise ValueError("product record is not terminal")
        expected_arguments = IncidentBoundToolInput(
            incident_id=incident.incident_id,
            expected_state_version=incident.state_version,
        )
        expected_result = AgentIncidentToolView(
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            kind=incident.kind,
            state=incident.state,
            state_version=incident.state_version,
            opened_at=incident.opened_at,
            updated_at=incident.updated_at,
        )
        expected_request_sha256 = canonical_sha256(expected_arguments)
        expected_result_sha256 = canonical_sha256(expected_result)
        _validate_result(
            result,
            request=request,
            settings=configuration.worker_settings,
            policy=policy,
            expected_tool_request_sha256=expected_request_sha256,
            expected_tool_result_sha256=expected_result_sha256,
        )
        if (
            record.status is not PersistedAgentRunStatus.COMPLETED
            or record.run_id != request.run_id
            or record.incident_id != request.incident.incident_id
            or record.incident_state_version != request.incident.state_version
            or record.model_id != configuration.worker_settings.model
            or record.sandbox is not SandboxKind.DOCKER
        ):
            raise ValueError("durable product record does not bind the worker run")
    except LiveModelEvidenceError:
        raise
    except Exception as exc:
        raise LiveModelEvidenceError("invalid_product_agent_result", 5) from exc
    return _ProductObservation(
        request=request,
        result=result,
        record=record,
        policy=policy,
        selected_context=selected_context,
        definitions=definitions,
        freshness_nonce=freshness.hex(),
        product_request_sha256=canonical_sha256(product_request_material),
        product_response_sha256=canonical_sha256(payload),
        expected_tool_request_sha256=expected_request_sha256,
        expected_tool_result_sha256=expected_result_sha256,
        worker_envelope_sha256=boundary_headers.worker_envelope_sha256,
        worker_result_sha256=boundary_headers.worker_result_sha256,
        product_boundary_hmac_sha256=(
            boundary_headers.boundary_hmac_sha256
        ),
    )


def _validate_preflight(
    configuration: _LiveConfiguration,
    incident: IncidentView,
    timeline: tuple[IncidentTimelineEntry, ...],
) -> None:
    if (
        incident.incident_id != configuration.incident_id
        or incident.state_version != configuration.expected_state_version
        or incident.state
        not in {IncidentState.ESCALATING, IncidentState.RESPONSE_ACTIVE}
        or any(entry.incident_id != incident.incident_id for entry in timeline)
    ):
        raise ValueError("incident is not eligible for the reviewed product run")


def _verify_agent_runtime_headers(
    response: httpx.Response,
    expected: _AgentRuntimePins,
    *,
    product_boundary_key: bytes,
    run_id: UUID,
    product_response_sha256: str,
) -> _AgentBoundaryHeaders:
    observed = (
        response.headers.get(PRODUCT_RUN_HEADER),
        response.headers.get(RUNTIME_SNAPSHOT_HEADER),
        response.headers.get(STARTUP_SNAPSHOT_HEADER),
    )
    required = (
        expected.worker_manifest_sha256,
        expected.runtime_snapshot_sha256,
        expected.startup_snapshot_sha256,
    )
    if any(value is None for value in observed) or not all(
        compare_digest(str(actual), wanted)
        for actual, wanted in zip(observed, required, strict=True)
    ):
        raise LiveModelEvidenceError(
            "agent_execution_snapshot_unavailable",
            9,
        )
    worker_envelope_sha256 = response.headers.get(WORKER_REQUEST_HEADER, "")
    worker_result_sha256 = response.headers.get(WORKER_RESULT_HEADER, "")
    boundary_hmac_sha256 = response.headers.get(
        PRODUCT_BOUNDARY_HMAC_HEADER,
        "",
    )
    if (
        re.fullmatch(SHA256_PATTERN, worker_envelope_sha256) is None
        or re.fullmatch(SHA256_PATTERN, worker_result_sha256) is None
    ):
        raise LiveModelEvidenceError(
            "agent_worker_binding_unavailable",
            9,
        )
    expected_hmac = new_hmac(
        product_boundary_key,
        _agent_boundary_payload(
            run_id=run_id,
            worker_manifest_sha256=expected.worker_manifest_sha256,
            runtime_snapshot_sha256=expected.runtime_snapshot_sha256,
            startup_snapshot_sha256=expected.startup_snapshot_sha256,
            worker_envelope_sha256=worker_envelope_sha256,
            worker_result_sha256=worker_result_sha256,
            product_response_sha256=product_response_sha256,
        ),
        sha256,
    ).hexdigest()
    if not compare_digest(expected_hmac, boundary_hmac_sha256):
        raise LiveModelEvidenceError(
            "agent_boundary_authentication_failed",
            9,
        )
    return _AgentBoundaryHeaders(
        worker_envelope_sha256=worker_envelope_sha256,
        worker_result_sha256=worker_result_sha256,
        boundary_hmac_sha256=boundary_hmac_sha256,
    )


def _agent_boundary_payload(
    *,
    run_id: UUID,
    worker_manifest_sha256: str,
    runtime_snapshot_sha256: str,
    startup_snapshot_sha256: str,
    worker_envelope_sha256: str,
    worker_result_sha256: str,
    product_response_sha256: str,
) -> bytes:
    return canonical_json_bytes(
        {
            "schema_version": SCHEMA_VERSION,
            "run_id": str(run_id),
            "worker_manifest_sha256": worker_manifest_sha256,
            "runtime_snapshot_sha256": runtime_snapshot_sha256,
            "startup_snapshot_sha256": startup_snapshot_sha256,
            "worker_envelope_sha256": worker_envelope_sha256,
            "worker_result_sha256": worker_result_sha256,
            "product_response_sha256": product_response_sha256,
        }
    )


def _product_tool_definitions(
    allowed_tools: frozenset[str],
) -> tuple[AgentToolDefinition, ...]:
    definitions = tuple(
        AgentToolDefinition(
            name=contract.name,
            description=contract.description,
            effect=contract.effect,
            input_schema=contract.input_model.model_json_schema(),
        )
        for contract in INITIAL_HTTP_TOOL_CONTRACTS
        if contract.name in allowed_tools
    )
    if {definition.name for definition in definitions} != set(allowed_tools):
        raise ValueError("worker tool manifest does not cover the active policy")
    return definitions


def _build_evidence(
    *,
    configuration: _LiveConfiguration,
    catalog: ModelCatalogEvidence,
    model_service: ReviewedModelServiceEvidence,
    observation: _ProductObservation,
) -> LiveModelEvidence:
    result = observation.result
    request = observation.request
    policy = observation.policy
    context = observation.selected_context
    trace = _validate_result(
        result,
        request=request,
        settings=configuration.worker_settings,
        policy=policy,
        expected_tool_request_sha256=observation.expected_tool_request_sha256,
        expected_tool_result_sha256=observation.expected_tool_result_sha256,
    )
    catalog_hash = canonical_sha256(
        configuration.catalog_settings.model_dump(
            mode="json",
            exclude={"api_key"},
        )
    )
    worker_hash = canonical_sha256(
        configuration.worker_settings.model_dump(
            mode="json",
            exclude={"api_key"},
        )
    )
    if (
        context.model_identity.provider != "vllm"
        or context.model_identity.model_id != configuration.worker_settings.model
        or context.model_identity.revision != configuration.revision
        or context.model_identity.artifact_sha256
        != configuration.artifact_sha256
        or context.model_identity.inference_config_sha256 != worker_hash
    ):
        raise LiveModelEvidenceError("model_identity_mismatch", 5)
    definitions_sha256 = canonical_sha256(
        [item.model_dump(mode="json") for item in observation.definitions]
    )
    pins = configuration.agent_runtime_pins
    try:
        execution_configuration = ExecutionConfigurationEvidence.create(
            source_files=_source_file_evidence(),
            transport=TransportPolicyEvidence(),
            catalog_inference_config_sha256=catalog_hash,
            worker_inference_config_sha256=worker_hash,
            selected_context_sha256=context.selected_context_sha256,
            tool_definitions_sha256=definitions_sha256,
            policy_sha256=policy.sha256,
            model_service_image_sha256=model_service.image_sha256,
            model_service_inspect_sha256=(
                model_service.normalized_inspect_sha256
            ),
            agent_worker_manifest_sha256=pins.worker_manifest_sha256,
            agent_runtime_snapshot_sha256=pins.runtime_snapshot_sha256,
            agent_startup_snapshot_sha256=pins.startup_snapshot_sha256,
        )
        evidence = LiveModelEvidence.create(
            signing_key=configuration.signing_key,
            model_claim=OperatorPinnedModelClaim(
                model_id=context.model_identity.model_id,
                revision=context.model_identity.revision,
                artifact_sha256=context.model_identity.artifact_sha256,
                catalog_inference_config_sha256=catalog_hash,
                worker_inference_config_sha256=worker_hash,
            ),
            catalog=catalog,
            model_service=model_service,
            agent_execution=AgentExecutionEvidence(
                worker_manifest_sha256=pins.worker_manifest_sha256,
                runtime_snapshot_sha256=pins.runtime_snapshot_sha256,
                startup_snapshot_sha256=pins.startup_snapshot_sha256,
                worker_source_sha256=_bound_source_hash(
                    "backend/src/vital_relay/agent/worker.py"
                ),
                runner_source_sha256=_bound_source_hash(
                    "backend/src/vital_relay/agent/sandbox.py"
                ),
                worker_command_policy_sha256=canonical_sha256(
                    {
                        "runner": RUNNER_IMPLEMENTATION,
                        "sandbox": SandboxKind.DOCKER.value,
                        "argv_policy": (
                            "docker compose run --rm --no-deps --pull never "
                            "--no-TTY --name <project>-worker-<run_id> agent"
                        ),
                        "wire_protocol": "SandboxWorkerEnvelope",
                    }
                ),
                product_boundary_hmac_sha256=(
                    observation.product_boundary_hmac_sha256
                ),
            ),
            run=RunBindingEvidence(
                run_id=request.run_id,
                freshness_nonce=observation.freshness_nonce,
                incident_id_sha256=sha256(
                    str(request.incident.incident_id).encode()
                ).hexdigest(),
                agent_run_request_sha256=canonical_sha256(request),
                worker_envelope_sha256=observation.worker_envelope_sha256,
                worker_result_sha256=observation.worker_result_sha256,
                product_request_sha256=observation.product_request_sha256,
                product_response_sha256=observation.product_response_sha256,
                normalized_result_sha256=canonical_sha256(result),
                policy_sha256=policy.sha256,
            ),
            selected_context=SelectedContextEvidence(
                playbook_sha256=context.playbook_sha256,
                generator_input_sha256=context.generator_input_sha256,
                selected_context_sha256=context.selected_context_sha256,
                selected_item_ids=context.selected_item_ids,
                selected_item_sha256s=context.selected_item_sha256s,
            ),
            tool_definitions=ToolDefinitionEvidence(
                tool_names=tuple(item.name for item in observation.definitions),
                definitions_sha256=definitions_sha256,
            ),
            retry_budget=RetryBudgetEvidence(
                model_timeout_seconds=(
                    configuration.worker_settings.timeout_seconds
                ),
                product_timeout_seconds=configuration.product_timeout_seconds,
                max_total_tool_calls=policy.tool_budget.max_total_calls,
                max_mutating_tool_calls=policy.tool_budget.max_mutating_calls,
                per_tool=tuple(
                    ToolCallBudgetEvidence(
                        tool_name=rule.name,
                        max_calls=rule.max_calls,
                    )
                    for rule in policy.tool_budget.tools
                ),
            ),
            execution_configuration=execution_configuration,
            outcome=RunOutcomeEvidence(
                started_at=result.started_at,
                finished_at=result.finished_at,
                duration_ms=_duration_ms(result.started_at, result.finished_at),
                trace=(_normalize_trace(1, trace),),
            ),
            authority=configuration.authority,
        )
        verify_live_model_evidence(
            evidence,
            signing_key=configuration.signing_key,
            expected_issuer=configuration.authority.issuer,
            expected_key_id=configuration.authority.key_id,
        )
        _assert_privacy_boundary(
            evidence,
            exact_secrets=(
                configuration.catalog_settings.api_key.get_secret_value().encode(),
                configuration.command_token.get_secret_value().encode(),
                configuration.product_boundary_key,
                configuration.signing_key,
            ),
        )
    except LiveModelEvidenceError:
        raise
    except Exception as exc:
        raise LiveModelEvidenceError("invalid_evidence", 7) from exc
    return evidence


def _validate_result(
    result: AgentRunResult,
    *,
    request: AgentRunRequest,
    settings: VLLMSettings,
    policy: CoordinationPolicySnapshot,
    expected_tool_request_sha256: str,
    expected_tool_result_sha256: str,
) -> AgentToolTrace:
    if (
        result.run_id != request.run_id
        or result.incident_id != request.incident.incident_id
        or result.policy != policy.reference
        or result.model_id != settings.model
        or result.sandbox is not SandboxKind.DOCKER
    ):
        raise LiveModelEvidenceError("runner_binding_mismatch", 5)
    if result.status is not AgentRunStatus.COMPLETED:
        code = (
            f"runner_{result.failure_code.value}"
            if result.failure_code is not None
            else "runner_incomplete"
        )
        raise LiveModelEvidenceError(code, 5)
    if len(result.tool_trace) != 1:
        raise LiveModelEvidenceError("incomplete_tool_trace", 6)
    trace = result.tool_trace[0]
    if (
        trace.tool_name != GET_INCIDENT
        or trace.status is not ToolTraceStatus.COMPLETED
        or trace.evidence_source is not ToolTraceEvidenceSource.HOST_PROXY_AUDIT
        or trace.effect is not ToolEffect.READ
        or trace.arguments
        or trace.result is not None
        or trace.request_sha256 is None
        or trace.result_sha256 is None
        or trace.proxy_invocation_id is None
    ):
        raise LiveModelEvidenceError("incomplete_host_proxy_trace", 6)
    if (
        not compare_digest(
            trace.request_sha256,
            expected_tool_request_sha256,
        )
        or not compare_digest(
            trace.result_sha256,
            expected_tool_result_sha256,
        )
    ):
        raise LiveModelEvidenceError("tool_proxy_hash_mismatch", 6)
    return trace


def _normalize_trace(
    sequence: int,
    trace: AgentToolTrace,
) -> NormalizedToolTrace:
    assert trace.effect is not None
    assert trace.request_sha256 is not None
    assert trace.result_sha256 is not None
    assert trace.proxy_invocation_id is not None
    return NormalizedToolTrace(
        sequence=sequence,
        tool_name=trace.tool_name,
        effect=trace.effect,
        request_sha256=trace.request_sha256,
        result_sha256=trace.result_sha256,
        proxy_invocation_id_sha256=sha256(
            str(trace.proxy_invocation_id).encode()
        ).hexdigest(),
        started_at=trace.started_at,
        finished_at=trace.finished_at,
        duration_ms=_duration_ms(trace.started_at, trace.finished_at),
    )


def _inspect_reviewed_model_sandbox(
    configuration: _DockerSandboxConfiguration,
) -> ReviewedModelServiceEvidence:
    try:
        cli_stat = configuration.cli_path.lstat()
        if (
            configuration.cli_path.is_symlink()
            or not stat.S_ISREG(cli_stat.st_mode)
            or not (cli_stat.st_mode & 0o111)
        ):
            raise ValueError("unreviewed Docker CLI")
        cli_sha256 = sha256(configuration.cli_path.read_bytes()).hexdigest()
        if not compare_digest(cli_sha256, configuration.cli_sha256):
            raise ValueError("Docker CLI digest mismatch")
        completed = subprocess.run(
            (
                str(configuration.cli_path),
                "--host",
                EXACT_DOCKER_HOST,
                "--config",
                "/nonexistent",
                "inspect",
                "--type",
                "container",
                configuration.container_id,
            ),
            check=False,
            capture_output=True,
            timeout=5,
            env={
                "DOCKER_CONFIG": "/nonexistent",
                "HOME": "/nonexistent",
                "LANG": "C",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
            },
        )
        if (
            completed.returncode != 0
            or not completed.stdout
            or len(completed.stdout) > MAX_DOCKER_INSPECT_BYTES
        ):
            raise ValueError("Docker inspect failed")
        payload = json.loads(completed.stdout)
        normalized = _validated_docker_inspect_payload(payload, configuration)
    except Exception as exc:
        raise LiveModelEvidenceError("reviewed_model_service_unavailable", 9) from exc
    return ReviewedModelServiceEvidence(
        container_id_sha256=sha256(configuration.container_id.encode()).hexdigest(),
        image_sha256=configuration.image_sha256,
        docker_cli_sha256=configuration.cli_sha256,
        normalized_inspect_sha256=canonical_sha256(normalized),
        checked_at=datetime.now(UTC),
    )


def _validated_docker_inspect_payload(
    payload: object,
    configuration: _DockerSandboxConfiguration,
) -> dict[str, object]:
    normalized = _normalized_docker_inspect_payload(payload, configuration)
    if not compare_digest(
        canonical_sha256(normalized),
        configuration.inspect_sha256,
    ):
        raise ValueError("vLLM service inspect digest mismatch")
    return normalized


def _normalized_docker_inspect_payload(
    payload: object,
    configuration: _DockerSandboxConfiguration,
) -> dict[str, object]:
    if not isinstance(payload, list) or len(payload) != 1:
        raise ValueError("invalid Docker inspect payload")
    item = payload[0]
    if not isinstance(item, dict):
        raise ValueError("invalid Docker inspect object")
    state = item.get("State")
    host = item.get("HostConfig")
    network = item.get("NetworkSettings")
    mounts = item.get("Mounts")
    container_config = item.get("Config")
    if not all(
        isinstance(value, dict)
        for value in (state, host, network, container_config)
    ) or not isinstance(mounts, list):
        raise ValueError("incomplete Docker inspect object")
    assert isinstance(state, dict)
    assert isinstance(host, dict)
    assert isinstance(network, dict)
    assert isinstance(container_config, dict)
    expected_binding = [{"HostIp": "127.0.0.1", "HostPort": VLLM_HOST_PORT}]
    ports = network.get("Ports")
    port_bindings = host.get("PortBindings")
    if not isinstance(ports, dict) or not isinstance(port_bindings, dict):
        raise ValueError("missing Docker port bindings")
    active_ports = {key: value for key, value in ports.items() if value is not None}
    active_bindings = {
        key: value for key, value in port_bindings.items() if value is not None
    }
    if active_ports != {VLLM_CONTAINER_PORT: expected_binding} or (
        active_bindings != {VLLM_CONTAINER_PORT: expected_binding}
    ):
        raise ValueError("vLLM service is not exact loopback-only")
    if item.get("Image") != f"sha256:{configuration.image_sha256}":
        raise ValueError("vLLM service image mismatch")
    if item.get("Id") != configuration.container_id or state.get("Running") is not True:
        raise ValueError("vLLM service is not the reviewed running container")
    network_mode = host.get("NetworkMode")
    security_options = host.get("SecurityOpt")
    cap_drop = host.get("CapDrop")
    if (
        host.get("Privileged") is not False
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapAdd") not in (None, [])
        or not isinstance(cap_drop, list)
        or "ALL" not in {str(value).upper() for value in cap_drop}
        or host.get("PidMode") not in (None, "")
        or host.get("IpcMode") == "host"
        or network_mode in (None, "", "host", "none")
        or not isinstance(security_options, list)
        or not any(
            option in {"no-new-privileges", "no-new-privileges:true"}
            for option in security_options
        )
    ):
        raise ValueError("vLLM service is not the reviewed separate sandbox")
    for mount in mounts:
        if not isinstance(mount, dict) or mount.get("RW") is not False:
            raise ValueError("vLLM service mounts must be read-only")
        source = mount.get("Source")
        destination = mount.get("Destination")
        if not isinstance(source, str) or not isinstance(destination, str):
            raise ValueError("vLLM service mount identity is incomplete")
        if _sensitive_mount_path(source) or _sensitive_mount_path(destination):
            raise ValueError("vLLM service mount crosses a protected boundary")
    return {
        "container_id_sha256": sha256(configuration.container_id.encode()).hexdigest(),
        "image_sha256": configuration.image_sha256,
        "running": True,
        "readonly_rootfs": True,
        "privileged": False,
        "cap_drop_all": True,
        "network_mode": str(network_mode),
        "no_new_privileges": True,
        "port_binding": "127.0.0.1:8001->8000/tcp",
        "container_config_sha256": canonical_sha256(container_config),
        "process_sha256": canonical_sha256(
            {"path": item.get("Path"), "args": item.get("Args")}
        ),
        "host_config_sha256": canonical_sha256(host),
        "mount_inventory_sha256": canonical_sha256(mounts),
    }


def _sensitive_mount_path(value: str) -> bool:
    protected = ("/dev", "/proc", "/run", "/sys")
    return value == "/" or any(
        value == prefix or value.startswith(prefix + "/") for prefix in protected
    )


def _build_product_http_client(*, timeout_seconds: float) -> httpx.Client:
    return httpx.Client(
        base_url=EXACT_PRODUCT_BASE_URL,
        transport=httpx.HTTPTransport(retries=0, verify=True),
        timeout=httpx.Timeout(timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


def _bounded_content(response: httpx.Response, maximum: int) -> bytes:
    content = response.content
    if not content or len(content) > maximum:
        raise ValueError("HTTP response size is outside the evidence boundary")
    return content


def _bounded_json(response: httpx.Response, maximum: int) -> object:
    return json.loads(_bounded_content(response, maximum))


def _source_file_evidence() -> tuple[SourceFileEvidence, ...]:
    return tuple(
        SourceFileEvidence(path=path, sha256=_bound_source_hash(path))
        for path in _BOUND_SOURCE_FILES
    )


def _bound_source_hash(relative_path: str) -> str:
    path = PROJECT_ROOT / relative_path
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(PROJECT_ROOT)
        if path.is_symlink() or not resolved.is_file():
            raise ValueError("bound source is not a regular project file")
        return sha256(resolved.read_bytes()).hexdigest()
    except Exception as exc:
        raise LiveModelEvidenceError("bound_source_unavailable", 7) from exc


def _assert_privacy_boundary(
    evidence: LiveModelEvidence,
    *,
    exact_secrets: tuple[bytes, ...],
) -> None:
    payload = evidence.model_dump(mode="json", exclude_none=True)
    forbidden_keys = {
        "action_summary",
        "api_key",
        "arguments",
        "authorization",
        "conclusion",
        "coordinates",
        "health",
        "incident_id",
        "latitude",
        "longitude",
        "password",
        "prompt",
        "raw_output",
        "reasoning",
        "result",
        "secret",
        "token",
        "tool_body",
    }

    def inspect_value(value: object) -> None:
        if isinstance(value, dict):
            if forbidden_keys.intersection(value):
                raise LiveModelEvidenceError("forbidden_evidence_field", 7)
            for nested in value.values():
                inspect_value(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect_value(nested)

    inspect_value(payload)
    encoded = canonical_json_bytes(payload)
    for secret in exact_secrets:
        candidates = {
            secret,
            secret.hex().encode(),
            base64.b64encode(secret),
            base64.urlsafe_b64encode(secret),
            base64.urlsafe_b64encode(secret).rstrip(b"="),
        }
        if any(candidate and candidate in encoded for candidate in candidates):
            raise LiveModelEvidenceError("secret_in_evidence", 7)


def _evidence_sha256(evidence: LiveModelEvidence) -> str:
    return canonical_sha256(
        evidence.model_dump(
            mode="json",
            exclude={"evidence_sha256", "attestation_hmac_sha256"},
            exclude_none=True,
        )
    )


def _attestation_payload(evidence: LiveModelEvidence) -> bytes:
    return canonical_json_bytes(
        evidence.model_dump(
            mode="json",
            exclude={"attestation_hmac_sha256"},
            exclude_none=True,
        )
    )


def _validate_signing_key(signing_key: bytes) -> None:
    if not 32 <= len(signing_key) <= 64:
        raise LiveModelEvidenceError("invalid_evidence_signing_key", 2)


def _decode_signing_key(value: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,86}", value):
        raise ValueError("invalid signing key encoding")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(
        value + padding,
        altchars=b"-_",
        validate=True,
    )
    _validate_signing_key(decoded)
    return decoded


def _reject_ambient_network_configuration(environment: Mapping[str, str]) -> None:
    if any(name in environment for name in _NETWORK_ENV_NAMES):
        raise ValueError("ambient proxy, endpoint, or CA configuration is forbidden")


def _required_clean(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value or value != value.strip():
        raise ValueError(f"{name} is required")
    return value


def _required_sha256(environment: Mapping[str, str], name: str) -> str:
    value = _required_clean(environment, name)
    if re.fullmatch(SHA256_PATTERN, value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _required_container_id(environment: Mapping[str, str], name: str) -> str:
    value = _required_clean(environment, name)
    if _CONTAINER_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a full lowercase container ID")
    return value


def _required_absolute_path(environment: Mapping[str, str], name: str) -> Path:
    value = _required_clean(environment, name)
    path = Path(value)
    if not path.is_absolute() or "\x00" in value:
        raise ValueError(f"{name} must be an absolute path")
    return path


def _bounded_float(
    value: str | None,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    parsed = default if value is None else float(value)
    if not minimum <= parsed <= maximum:
        raise ValueError("numeric configuration is outside its reviewed bounds")
    return parsed


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    return max(0, round((finished_at - started_at).total_seconds() * 1_000))


def _print_canonical(value: BaseModel) -> None:
    print(canonical_json_bytes(value).decode("utf-8"))  # noqa: T201


if __name__ == "__main__":  # pragma: no cover - module CLI edge
    raise SystemExit(main())
