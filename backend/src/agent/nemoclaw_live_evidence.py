"""Live NemoClaw/OpenShell policy and product-path acceptance evidence.

This module is deliberately an external acceptance harness, not a test double.
It executes one fixed command graph, drives the real command API, and inspects
the real PostgreSQL control plane.  Raw policy documents, subprocess
diagnostics, model/provider bodies, capabilities, and persona credentials are
never copied into the emitted evidence.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import http.client
import json
import multiprocessing
import os
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Final, Literal, Self
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text

from vital_relay.adapters.postgres_agent_control import (
    PostgresAppendOnlyToolAudit,
)
from vital_relay.agent.capabilities import ToolCapabilityAuthority
from vital_relay.agent.contracts import (
    SHA256_PATTERN,
    AgentRunStatus,
    SandboxKind,
    ToolTraceEvidenceSource,
)
from vital_relay.agent.http_tools import (
    AGENT_CAPABILITY_HEADER,
)
from vital_relay.agent.sandbox import (
    NEMOCLAW_HOST_CLI_EXECUTABLE,
    NEMOCLAW_MANAGED_EXEC_LAUNCHER,
    NEMOCLAW_WORKER_EXECUTABLE,
)
from vital_relay.agent.source_manifest import (
    NEMOCLAW_AGENT_SOURCE_MANIFEST,
    capture_reviewed_source_snapshot,
)
from vital_relay.agent.tool_contracts import GET_INCIDENT
from vital_relay.application.agent_control import AgentRunRecord
from vital_relay.application.agent_evidence import (
    AgentRunEvidenceContext,
    host_audit_trace,
)
from vital_relay.application.tool_proxy import ToolProxyInvocation
from vital_relay.persistence.database import (
    create_postgres_engine,
    create_session_factory,
)
from vital_relay.persistence.models import (
    AgentActivePolicyRow,
    AgentRunRow,
    AgentToolProxyAuditRow,
    PersonaSessionRow,
)


EVIDENCE_SCHEMA_VERSION: Final = 2
LANE_NAME: Final = "nemoclaw-openshell-live-policy-attestation"
SANDBOX_NAME: Final = "vital-relay-acceptance"
OPEN_SHELL_EXECUTABLE: Final = "/usr/local/bin/openshell"
STAGED_PYTHON: Final = "/sandbox/vital-relay-runtime/bin/python3.14"
STAGED_WORKER: Final = NEMOCLAW_WORKER_EXECUTABLE
UNLISTED_BINARY: Final = "/usr/bin/curl"
MANAGED_INFERENCE_ORIGIN: Final = "https://inference.local"
MANAGED_MODELS_URL: Final = f"{MANAGED_INFERENCE_ORIGIN}/v1/models"
TOOL_PROXY_ORIGIN: Final = "https://vital-relay.internal:8443"
TOOL_PROXY_PATH: Final = "/internal/v1/agent/tools/invoke"
TOOL_PROXY_URL: Final = f"{TOOL_PROXY_ORIGIN}{TOOL_PROXY_PATH}"
WRONG_TOOL_PATH_PREFIX: Final = "/health/vital-relay-evidence/"
DEDICATED_API_ORIGIN: Final = "http://127.0.0.1:8017"
DEDICATED_API_HEALTH_URL: Final = f"{DEDICATED_API_ORIGIN}/health"
MAX_COMMAND_STDOUT_BYTES: Final = 8 * 1024 * 1024
MAX_HTTP_BODY_BYTES: Final = 2 * 1024 * 1024
MAX_POLICY_BYTES: Final = 4 * 1024 * 1024
MAX_OCSF_LOG_FILES: Final = 32
MAX_RUNTIME_FILES: Final = 100_000
MAX_RUNTIME_BYTES: Final = 4 * 1024 * 1024 * 1024
SERVER_START_TIMEOUT_SECONDS: Final = 120.0
RUN_START_TIMEOUT_SECONDS: Final = 30.0
OCSF_LOG_NAME_RE: Final = re.compile(
    r"^openshell-ocsf\.[0-9]{4}-[0-9]{2}-[0-9]{2}\.log$"
)
EVIDENCE_SIGNATURE_DOMAIN: Final = (
    b"vital-relay:nemoclaw-openshell-live-evidence:v2\x00"
)
CHALLENGE_DOMAIN: Final = b"vital-relay:nemoclaw-probe-challenge:v1\x00"
DOCKER_SOCKET: Final = "/var/run/docker.sock"
DOCKER_API_VERSION: Final = "v1.47"
DOCKER_MANAGED_LABEL: Final = "openshell.ai/managed-by"
DOCKER_SANDBOX_LABEL: Final = "openshell.ai/sandbox-name"
EXPECTED_AGENT: Final = "langchain-deepagents-code"
EXPECTED_AGENT_RUNTIME: Final = "terminal"
AGENT_SOURCE_MANIFEST_NAME: Final = NEMOCLAW_AGENT_SOURCE_MANIFEST.name
AGENT_SOURCE_ENTRYPOINT: Final = NEMOCLAW_AGENT_SOURCE_MANIFEST.entrypoint
OCSF_EXPORT_PATH: Final = "/var/log"
HARNESS_GRAPH_RELATIVE_PATHS: Final = (
    "backend/src/vital_relay/agent/nemoclaw_live_evidence.py",
    "backend/src/vital_relay/agent/nemoclaw_probe.py",
    "infrastructure/nemoclaw/assert_effective_policy.py",
)
STAGED_PROBE: Final = "/sandbox/vital-relay-runtime/nemoclaw_probe.py"

_SHA256_RE = re.compile(SHA256_PATTERN)
_VERSION_RE = re.compile(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"
    r"\bv[0-9]+\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{32,}\b|"
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b)"
)
_SENSITIVE_KEY_PARTS = (
    "access_token",
    "api_key",
    "authorization",
    "capability",
    "coordinate",
    "credential",
    "health_data",
    "hidden_reasoning",
    "latitude",
    "longitude",
    "password",
    "private_key",
    "provider_payload",
    "raw_policy",
    "refresh_token",
    "secret",
    "token",
)
_FORBIDDEN_NETWORK_TERMS = (
    "api.github.com",
    "discord",
    "files.pythonhosted.org",
    "github.com",
    "langsmith",
    "messag",
    "npm",
    "observability",
    "openrouter",
    "otlp",
    "package",
    "pypi",
    "search",
    "slack",
    "tavily",
    "teams",
    "telegram",
    "whatsapp",
)
_INFERENCE_RULES = frozenset(
    {
        ("GET", "/v1/models"),
        ("POST", "/v1/chat/completions"),
    }
)
_OPTIONAL_INFERENCE_RULES = frozenset({("POST", "/v1/embeddings")})
_TOOL_RULES = frozenset({("POST", TOOL_PROXY_PATH)})
_ALLOWED_NETWORK_BINARIES = frozenset({STAGED_PYTHON, STAGED_WORKER})


class EvidenceError(RuntimeError):
    """Privacy-safe failure carrying only a closed blocker code."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,63}", code) is None:
            raise ValueError("invalid evidence blocker code")
        self.code = code
        super().__init__(code)


class FixedCommand(StrEnum):
    NEMOCLAW_VERSION = "nemoclaw_version"
    OPENSHELL_VERSION = "openshell_version"
    STATUS = "nemoclaw_status"
    DOCTOR = "nemoclaw_doctor"
    SANDBOX_INVENTORY = "openshell_sandbox_inventory"
    BASE_POLICY = "base_policy"
    EFFECTIVE_POLICY = "effective_policy"
    RUNTIME_INFERENCE = "runtime_inference"
    UNLISTED_HOST = "unlisted_host"
    WRONG_TOOL_ROUTE = "wrong_tool_route"
    PROTECTED_FILE = "protected_file"
    UNLISTED_BINARY_PRESENT = "unlisted_binary_present"
    UNLISTED_BINARY_NETWORK = "unlisted_binary_network"
    OCSF_EXPORT = "ocsf_export"
    EXACT_PROCESS = "exact_process"


class Probe(StrEnum):
    """Closed acceptance set; one observation is required for every member."""

    NEMOCLAW_VERSION = "nemoclaw_version"
    OPENSHELL_VERSION = "openshell_version"
    NEMOCLAW_STATUS = "nemoclaw_status"
    NEMOCLAW_DOCTOR = "nemoclaw_doctor"
    SANDBOX_IMAGE_INVENTORY = "sandbox_image_inventory"
    BASE_POLICY_ALLOWLIST = "base_policy_allowlist"
    EFFECTIVE_POLICY_ALLOWLIST = "effective_policy_allowlist"
    IMMUTABLE_RUNTIME_INVENTORY = "immutable_runtime_inventory"
    MANAGED_INFERENCE = "managed_inference"
    ACTUAL_SANDBOX_TRANSPORT = "actual_sandbox_transport"
    UNLISTED_HOST_DENIED = "unlisted_host_denied"
    WRONG_TOOL_ROUTE_DENIED = "wrong_tool_route_denied"
    PROTECTED_FILE_DENIED = "protected_file_denied"
    UNLISTED_BINARY_DENIED = "unlisted_binary_denied"
    PRODUCT_WORKER_TOOL_PROXY = "product_worker_tool_proxy"
    EXACT_RETRY_NO_INFERENCE = "exact_retry_no_inference"
    HOST_AUDIT_CORRELATED = "host_audit_correlated"
    EXPIRED_CAPABILITY_DENIED = "expired_capability_denied"
    CROSS_RUN_CAPABILITY_DENIED = "cross_run_capability_denied"
    CROSS_SCOPE_CAPABILITY_DENIED = "cross_scope_capability_denied"
    STALE_STATE_DENIED = "stale_state_denied"
    REVOKED_POLICY_DENIED = "revoked_policy_denied"
    UNKNOWN_TOOL_DENIED = "unknown_tool_denied"
    KILL_LEASE_RECONCILED = "kill_lease_reconciled"
    CLEANUP_CUSTODY = "cleanup_custody"


PROBE_ORDER: Final = tuple(Probe)


def _nemo_exec(
    *inner: str,
    timeout: str = "30",
    stdin: bool = False,
) -> tuple[str, ...]:
    return (
        NEMOCLAW_HOST_CLI_EXECUTABLE,
        SANDBOX_NAME,
        "exec",
        "--no-tty",
        "--timeout",
        timeout,
        "--stdin" if stdin else "--no-stdin",
        "--",
        *inner,
    )


FIXED_COMMANDS: Final[Mapping[FixedCommand, tuple[str, ...]]] = {
    FixedCommand.NEMOCLAW_VERSION: (
        NEMOCLAW_HOST_CLI_EXECUTABLE,
        "--version",
    ),
    FixedCommand.OPENSHELL_VERSION: (OPEN_SHELL_EXECUTABLE, "--version"),
    FixedCommand.STATUS: (
        NEMOCLAW_HOST_CLI_EXECUTABLE,
        "sandbox",
        "status",
        SANDBOX_NAME,
        "--json",
    ),
    FixedCommand.DOCTOR: (
        NEMOCLAW_HOST_CLI_EXECUTABLE,
        SANDBOX_NAME,
        "doctor",
        "--json",
    ),
    FixedCommand.SANDBOX_INVENTORY: (
        OPEN_SHELL_EXECUTABLE,
        "sandbox",
        "list",
        "-o",
        "json",
    ),
    FixedCommand.BASE_POLICY: (
        OPEN_SHELL_EXECUTABLE,
        "policy",
        "get",
        SANDBOX_NAME,
        "--base",
    ),
    FixedCommand.EFFECTIVE_POLICY: (
        OPEN_SHELL_EXECUTABLE,
        "policy",
        "get",
        SANDBOX_NAME,
        "--full",
    ),
    FixedCommand.RUNTIME_INFERENCE: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "runtime-inference",
        stdin=True,
    ),
    FixedCommand.UNLISTED_HOST: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "unlisted-host",
        stdin=True,
    ),
    FixedCommand.WRONG_TOOL_ROUTE: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "wrong-tool-route",
        stdin=True,
    ),
    FixedCommand.PROTECTED_FILE: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "protected-file",
    ),
    FixedCommand.UNLISTED_BINARY_PRESENT: _nemo_exec(
        UNLISTED_BINARY,
        "--version",
    ),
    FixedCommand.UNLISTED_BINARY_NETWORK: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "unlisted-binary",
        stdin=True,
    ),
    FixedCommand.OCSF_EXPORT: _nemo_exec(
        STAGED_PYTHON,
        STAGED_PROBE,
        "ocsf-export",
        stdin=True,
    ),
    FixedCommand.EXACT_PROCESS: _nemo_exec(
        NEMOCLAW_MANAGED_EXEC_LAUNCHER,
        STAGED_PYTHON,
        STAGED_PROBE,
        "exact-process",
        stdin=True,
    ),
}


class CommandOutput(BaseModel):
    """Bounded command output retained only inside the attestor process."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    returncode: int
    stdout: bytes = Field(repr=False)


@dataclass(frozen=True, slots=True)
class BoundedHTTPResponse:
    status_code: int
    content: bytes


class InferenceRouteV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=200)


class ProviderHealthV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    probed: bool
    providerLabel: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=2_048)
    detail: str = Field(min_length=1, max_length=4_096)
    failureLabel: Literal["unreachable", "unhealthy", "unauthorized"] | None = None
    probeLabel: str | None = Field(default=None, min_length=1, max_length=200)
    okLabel: str | None = Field(default=None, min_length=1, max_length=200)
    subprobes: tuple["ProviderHealthV1", ...] | None = None


class TerminalRuntimeHealthV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["ok"]
    oomKillCount: Literal[0]
    source: str | None = Field(default=None, min_length=1, max_length=4_096)


class SandboxGpuProofV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["verified", "unverified", "failed"]
    cudaVerified: bool
    label: str | None = Field(default=None, max_length=200)
    detail: str | None = Field(default=None, max_length=4_096)
    at: str = Field(min_length=1, max_length=100)


class NemoStatusV1(BaseModel):
    """Closed schema-v1 report published by current NemoClaw status."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1]
    name: Literal[SANDBOX_NAME]
    found: Literal[True]
    agent: Literal[EXPECTED_AGENT]
    agentDisplayName: str = Field(min_length=1, max_length=200)
    agentRuntime: Literal[EXPECTED_AGENT_RUNTIME]
    dcodeAutoApprovalMode: Literal["disabled"]
    model: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=200)
    recordedRoute: InferenceRouteV1
    liveRoute: InferenceRouteV1
    routeDrift: None
    phase: Literal["Ready"]
    gatewayState: Literal["present"]
    inferenceHealth: ProviderHealthV1
    rpcIssue: None
    hostGpuDetected: bool
    sandboxGpuEnabled: bool
    sandboxGpuMode: str | None
    sandboxGpuDevice: str | None
    sandboxGpuProof: SandboxGpuProofV1 | None
    openshellDriver: str = Field(min_length=1, max_length=64)
    openshellVersion: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    policies: tuple[str, ...]
    failureLayer: None
    terminalRuntimeHealth: TerminalRuntimeHealthV1
    servingProcessHealth: None
    dockerPaused: Literal[False]


class DoctorCheckV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    status: Literal["ok", "info"]
    detail: str = Field(min_length=1, max_length=4_096)
    hint: str | None = Field(default=None, min_length=1, max_length=4_096)


class NemoDoctorV1(BaseModel):
    """Closed schema-v1 doctor report with no warning/degraded state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal[1]
    sandbox: Literal[SANDBOX_NAME]
    status: Literal["ok"]
    failed: Literal[0]
    warnings: Literal[0]
    checks: tuple[DoctorCheckV1, ...] = Field(min_length=1)


class OpenShellSandboxRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    labels: dict[str, str]
    annotations: dict[str, str]
    resource_version: int = Field(ge=1)
    created_at: str = Field(min_length=1, max_length=100)
    phase: str = Field(min_length=1, max_length=32)
    current_policy_version: int = Field(ge=1)


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Probe
    status: Literal["passed"] = "passed"
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    count: int | None = Field(default=None, ge=0)


class PinnedInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    nemoclaw_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    openshell_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    ocsf_schema_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    ocsf_vendor: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$")
    ocsf_export_path: Literal[OCSF_EXPORT_PATH]
    ocsf_export_command_sha256: str = Field(pattern=SHA256_PATTERN)
    sandbox_image_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sandbox_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    openshell_policy_revision: int = Field(ge=1)
    runtime_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    base_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    effective_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    tls_ca_sha256: str = Field(pattern=SHA256_PATTERN)
    sandbox_transport_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_transport_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    model_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    harness_source_sha256: str = Field(pattern=SHA256_PATTERN)
    sandbox_probe_source_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_source_manifest_name: Literal[AGENT_SOURCE_MANIFEST_NAME]
    agent_source_entrypoint: Literal[AGENT_SOURCE_ENTRYPOINT]
    agent_source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_source_snapshot_sha256: str = Field(pattern=SHA256_PATTERN)
    agent_source_file_count: int = Field(ge=1, le=10_000)
    fixed_command_graph_sha256: str = Field(pattern=SHA256_PATTERN)
    coordination_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    coordination_policy_revision: int = Field(ge=1)
    runner: Literal["nemoclaw"]
    proc_self_exe: Literal[STAGED_PYTHON]


class CleanupCustody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["complete"] = "complete"
    owned_resources: tuple[
        Literal[
            "dedicated_backend_process",
            "dedicated_acceptance_sandbox_worker_handle",
        ], ...
    ]
    worker_handle_sha256: str = Field(pattern=SHA256_PATTERN)
    completed_checks: tuple[str, ...]
    unresolved_checks: tuple[str, ...] = ()


class LiveEvidenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION]
    lane: Literal[LANE_NAME]
    evidence_kind: Literal["live"]
    outcome: Literal["passed"]
    captured_at: datetime
    inventory: PinnedInventory
    observations: tuple[Observation, ...]
    required_probes: tuple[Probe, ...]
    attempt_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    product_run_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    killed_run_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    cleanup_custody: CleanupCustody


class EvidenceAuthentication(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme: Literal["hmac-sha256"]
    domain: Literal["vital-relay:nemoclaw-openshell-live-evidence:v2"]
    issuer: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$")
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    value: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class LiveEvidenceArtifact(LiveEvidenceBody):
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)
    authentication: EvidenceAuthentication


class FailureArtifactBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION]
    lane: Literal[LANE_NAME]
    evidence_kind: Literal["live_attempt"]
    outcome: Literal["failed"]
    blockers: tuple[str, ...] = Field(min_length=1)


class FailureArtifact(FailureArtifactBody):
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)


class SandboxRuntimeReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal["runtime_inference"]
    passed: Literal[True]
    proc_self_exe: Literal[STAGED_PYTHON]
    runtime_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    sandbox_probe_source_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_file_count: int = Field(ge=1, le=MAX_RUNTIME_FILES)
    runtime_directory_count: int = Field(ge=1, le=MAX_RUNTIME_FILES)
    runtime_bytes: int = Field(ge=1, le=MAX_RUNTIME_BYTES)
    runtime_mount_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    model_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    transport_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    actual_ca_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_write_denied: Literal[True]
    mount_read_only: Literal[True]
    statvfs_read_only: Literal[True]


class SandboxAttemptReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal[
        "unlisted_host",
        "wrong_tool_route",
        "protected_file",
        "unlisted_binary",
    ]
    attempted: Literal[True]
    challenge_sha256: str = Field(pattern=SHA256_PATTERN)
    process_pid: int = Field(ge=1)
    client_outcome: Literal[
        "http_response",
        "transport_error",
        "permission_denied",
        "process_nonzero",
    ]


class OcsfCursorFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=OCSF_LOG_NAME_RE.pattern)
    device: int = Field(ge=0)
    inode: int = Field(ge=1)
    size: int = Field(ge=0)
    mtime_ns: int = Field(ge=0)
    prefix_sha256: str = Field(pattern=SHA256_PATTERN)


class OcsfCursorReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal["ocsf_cursor"]
    files: tuple[OcsfCursorFile, ...] = Field(
        min_length=1, max_length=MAX_OCSF_LOG_FILES
    )


class SandboxOcsfExportReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal["ocsf_export"]
    action: Literal["capture", "delta"]
    export_path: Literal[OCSF_EXPORT_PATH]
    expected_schema_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"
    )
    expected_vendor: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}$"
    )
    expected_openshell_version: str = Field(
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$"
    )
    cursor: OcsfCursorReceipt
    events: tuple[dict[str, object], ...] = Field(max_length=100_000)


class OcsfDeltaReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal["ocsf_delta"]
    challenge_sha256: str = Field(pattern=SHA256_PATTERN)
    correlated_event_sha256: str = Field(pattern=SHA256_PATTERN)
    correlated_count: int = Field(ge=0, le=100_000)
    process_pid: int | None = Field(default=None, ge=1)


class SandboxProcessReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    probe: Literal["exact_process"]
    action: Literal["inspect", "terminate"]
    process_pid: int = Field(ge=1)
    start_time_ticks: int = Field(ge=1)
    process_handle_sha256: str = Field(pattern=SHA256_PATTERN)
    exact_executable: Literal[STAGED_PYTHON]
    exact_command_sha256: str = Field(pattern=SHA256_PATTERN)
    actual_ca_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_transport_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    absent_after_termination: bool


@dataclass(frozen=True, slots=True)
class LiveEvidenceConfig:
    expected_nemoclaw_version: str
    expected_openshell_version: str
    expected_ocsf_schema_version: str
    expected_ocsf_vendor: str
    expected_ocsf_export_path: str
    expected_image_digest: str
    expected_runtime_sha256: str
    expected_base_policy_sha256: str
    expected_effective_policy_sha256: str
    expected_tls_ca_sha256: str
    expected_transport_identity_sha256: str
    expected_worker_transport_identity_sha256: str
    expected_worker_command_sha256: str
    expected_openshell_driver: str
    expected_openshell_policy_revision: int
    expected_harness_source_sha256: str
    expected_sandbox_probe_source_sha256: str
    expected_agent_source_snapshot_sha256: str
    model: str
    database_url: str
    scope_id: UUID
    incident_id: UUID
    incident_state_version: int
    product_run_id: UUID
    killed_run_id: UUID
    command_token: str
    capability_key: bytes
    evidence_key: bytes
    evidence_issuer: str
    evidence_key_id: str
    tls_ca_file: Path

    @classmethod
    def from_environment(cls) -> Self:
        values = {
            "expected_nemoclaw_version": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_NEMOCLAW_VERSION"
            ),
            "expected_openshell_version": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_VERSION"
            ),
            "expected_ocsf_schema_version": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_OCSF_SCHEMA_VERSION"
            ),
            "expected_ocsf_vendor": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_OCSF_VENDOR"
            ),
            "expected_ocsf_export_path": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_OCSF_EXPORT_PATH"
            ),
            "expected_image_digest": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_IMAGE_DIGEST"
            ),
            "expected_runtime_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_RUNTIME_SHA256"
            ),
            "expected_base_policy_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_BASE_POLICY_SHA256"
            ),
            "expected_effective_policy_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_EFFECTIVE_POLICY_SHA256"
            ),
            "expected_tls_ca_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_SHA256"
            ),
            "expected_transport_identity_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_TRANSPORT_IDENTITY_SHA256"
            ),
            "expected_worker_transport_identity_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_WORKER_TRANSPORT_IDENTITY_SHA256"
            ),
            "expected_worker_command_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_WORKER_COMMAND_SHA256"
            ),
            "expected_harness_source_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_HARNESS_SOURCE_SHA256"
            ),
            "expected_sandbox_probe_source_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_SANDBOX_PROBE_SOURCE_SHA256"
            ),
            "expected_agent_source_snapshot_sha256": _required_environment(
                "VITAL_RELAY_LIVE_EVIDENCE_AGENT_SOURCE_SNAPSHOT_SHA256"
            ),
        }
        for name in (
            "expected_nemoclaw_version",
            "expected_openshell_version",
        ):
            if _VERSION_RE.fullmatch(values[name]) is None:
                raise EvidenceError("version_pin_invalid")
        if (
            _VERSION_RE.fullmatch(values["expected_ocsf_schema_version"])
            is None
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}",
                values["expected_ocsf_vendor"],
            )
            is None
            or values["expected_ocsf_export_path"] != OCSF_EXPORT_PATH
        ):
            raise EvidenceError("openshell_ocsf_export_contract_invalid")
        for name in (
            "expected_runtime_sha256",
            "expected_base_policy_sha256",
            "expected_effective_policy_sha256",
            "expected_tls_ca_sha256",
            "expected_transport_identity_sha256",
            "expected_worker_transport_identity_sha256",
            "expected_worker_command_sha256",
            "expected_harness_source_sha256",
            "expected_sandbox_probe_source_sha256",
            "expected_agent_source_snapshot_sha256",
        ):
            if _SHA256_RE.fullmatch(values[name]) is None:
                raise EvidenceError("sha256_pin_invalid")
        if (
            re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                values["expected_image_digest"],
            )
            is None
        ):
            raise EvidenceError("image_digest_pin_invalid")

        model = _required_environment("VITAL_RELAY_VLLM_MODEL")
        expected_openshell_driver = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_DRIVER"
        )
        if expected_openshell_driver != "docker":
            raise EvidenceError("openshell_driver_unsupported")
        raw_policy_revision = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_POLICY_REVISION"
        )
        try:
            expected_openshell_policy_revision = int(raw_policy_revision)
        except ValueError as exc:
            raise EvidenceError("openshell_policy_revision_invalid") from exc
        if expected_openshell_policy_revision < 1:
            raise EvidenceError("openshell_policy_revision_invalid")
        database_url = _required_environment("VITAL_RELAY_DATABASE_URL")
        scope_id = _uuid_environment("VITAL_RELAY_DEMO_SCOPE_ID")
        incident_id = _uuid_environment("VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_ID")
        product_run_id = _uuid_environment("VITAL_RELAY_LIVE_EVIDENCE_RUN_ID")
        killed_run_id = _uuid_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_KILL_RUN_ID"
        )
        if product_run_id == killed_run_id:
            raise EvidenceError("run_identity_conflict")
        raw_state_version = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_STATE_VERSION"
        )
        try:
            incident_state_version = int(raw_state_version)
        except ValueError as exc:
            raise EvidenceError("incident_state_version_invalid") from exc
        if not 1 <= incident_state_version <= 2_147_483_647:
            raise EvidenceError("incident_state_version_invalid")
        command_token = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_COMMAND_TOKEN"
        )
        # A bearer token is expected here, but it must be supplied as the
        # opaque token only, without a "Bearer " prefix.
        if (
            not 16 <= len(command_token) <= 8_192
            or command_token.lower().startswith("bearer ")
            or any(character.isspace() for character in command_token)
        ):
            raise EvidenceError("command_token_invalid")
        capability_key = _decode_signing_key(
            _required_environment("VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY")
        )
        evidence_key = _decode_signing_key(
            _required_environment("VITAL_RELAY_LIVE_EVIDENCE_HMAC_KEY")
        )
        evidence_issuer = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_ISSUER"
        )
        evidence_key_id = _required_environment(
            "VITAL_RELAY_LIVE_EVIDENCE_KEY_ID"
        )
        if re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}", evidence_issuer
        ) is None:
            raise EvidenceError("evidence_issuer_invalid")
        if re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}", evidence_key_id
        ) is None:
            raise EvidenceError("evidence_key_id_invalid")
        if hmac.compare_digest(capability_key, evidence_key):
            raise EvidenceError("evidence_key_separation_invalid")
        tls_ca_file = Path(
            _required_environment("VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_FILE")
        )
        if not tls_ca_file.is_absolute():
            raise EvidenceError("tls_ca_path_invalid")
        return cls(
            **values,
            expected_openshell_driver=expected_openshell_driver,
            expected_openshell_policy_revision=expected_openshell_policy_revision,
            model=model,
            database_url=database_url,
            scope_id=scope_id,
            incident_id=incident_id,
            incident_state_version=incident_state_version,
            product_run_id=product_run_id,
            killed_run_id=killed_run_id,
            command_token=command_token,
            capability_key=capability_key,
            evidence_key=evidence_key,
            evidence_issuer=evidence_issuer,
            evidence_key_id=evidence_key_id,
            tls_ca_file=tls_ca_file,
        )


class FixedCommandExecutor:
    """Run only reviewed argv vectors with bounded stdout and discarded stderr."""

    def run(
        self,
        command: FixedCommand,
        *,
        input_bytes: bytes = b"",
        timeout_seconds: float = 60.0,
    ) -> CommandOutput:
        argv = FIXED_COMMANDS[command]
        try:
            completed = subprocess.run(
                argv,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=timeout_seconds,
                env=_fixed_host_environment(argv),
            )
        except FileNotFoundError as exc:
            raise EvidenceError(f"{command.value}_unavailable") from exc
        except subprocess.TimeoutExpired as exc:
            raise EvidenceError(f"{command.value}_timeout") from exc
        stdout = completed.stdout
        if len(stdout) > MAX_COMMAND_STDOUT_BYTES:
            raise EvidenceError(f"{command.value}_output_too_large")
        return CommandOutput(returncode=completed.returncode, stdout=stdout)


def canonical_json_bytes(value: object) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def content_addressed_artifact(
    body: LiveEvidenceBody,
    *,
    evidence_key: bytes,
    issuer: str,
    key_id: str,
) -> LiveEvidenceArtifact:
    if body.required_probes != PROBE_ORDER:
        raise EvidenceError("probe_coverage_invalid")
    assert_probe_coverage(body.observations)
    ensure_privacy_safe(body.model_dump(mode="json"))
    evidence_sha256 = canonical_sha256(body)
    unsigned = {
        **body.model_dump(mode="json"),
        "evidence_sha256": evidence_sha256,
    }
    authenticated = {
        "issuer": issuer,
        "key_id": key_id,
        "payload": unsigned,
    }
    signature = hmac.new(
        evidence_key,
        EVIDENCE_SIGNATURE_DOMAIN + canonical_json_bytes(authenticated),
        hashlib.sha256,
    ).digest()
    return LiveEvidenceArtifact(
        **body.model_dump(mode="python"),
        evidence_sha256=evidence_sha256,
        authentication=EvidenceAuthentication(
            scheme="hmac-sha256",
            domain="vital-relay:nemoclaw-openshell-live-evidence:v2",
            issuer=issuer,
            key_id=key_id,
            value=base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
        ),
    )


def verify_live_evidence_artifact(
    artifact: LiveEvidenceArtifact | Mapping[str, object],
    *,
    evidence_key: bytes,
    issuer: str,
    key_id: str,
) -> LiveEvidenceArtifact:
    """Verify canonical content address and domain-separated host HMAC."""

    try:
        parsed = (
            artifact
            if isinstance(artifact, LiveEvidenceArtifact)
            else LiveEvidenceArtifact.model_validate(artifact)
        )
        if (
            parsed.authentication.issuer != issuer
            or parsed.authentication.key_id != key_id
        ):
            raise EvidenceError("evidence_authentication_invalid")
        if parsed.required_probes != PROBE_ORDER:
            raise EvidenceError("probe_coverage_invalid")
        assert_probe_coverage(parsed.observations)
        body = LiveEvidenceBody.model_validate(
            parsed.model_dump(
                exclude={"evidence_sha256", "authentication"},
                mode="python",
            )
        )
        if not hmac.compare_digest(parsed.evidence_sha256, canonical_sha256(body)):
            raise EvidenceError("evidence_content_address_invalid")
        unsigned = {
            **body.model_dump(mode="json"),
            "evidence_sha256": parsed.evidence_sha256,
        }
        authenticated = {
            "issuer": parsed.authentication.issuer,
            "key_id": parsed.authentication.key_id,
            "payload": unsigned,
        }
        expected = hmac.new(
            evidence_key,
            EVIDENCE_SIGNATURE_DOMAIN + canonical_json_bytes(authenticated),
            hashlib.sha256,
        ).digest()
        supplied = base64.b64decode(
            parsed.authentication.value + "=",
            altchars=b"-_",
            validate=True,
        )
        if not hmac.compare_digest(supplied, expected):
            raise EvidenceError("evidence_authentication_invalid")
    except EvidenceError:
        raise
    except (ValidationError, ValueError, binascii.Error) as exc:
        raise EvidenceError("evidence_authentication_invalid") from exc
    return parsed


def failure_artifact(blockers: Sequence[str]) -> FailureArtifact:
    normalized = tuple(sorted(dict.fromkeys(blockers)))
    body = FailureArtifactBody(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        lane=LANE_NAME,
        evidence_kind="live_attempt",
        outcome="failed",
        blockers=normalized,
    )
    return FailureArtifact(
        **body.model_dump(mode="python"),
        evidence_sha256=canonical_sha256(body),
    )


def ensure_privacy_safe(value: object, *, exact_secrets: Sequence[str] = ()) -> None:
    """Reject secret-shaped or prohibited raw material before serialization."""

    secrets = tuple(secret for secret in exact_secrets if secret)

    def inspect(active: object, key_path: tuple[str, ...]) -> None:
        if isinstance(active, Mapping):
            for raw_key, child in active.items():
                key = str(raw_key).lower()
                if any(part in key for part in _SENSITIVE_KEY_PARTS):
                    raise EvidenceError("privacy_boundary_violation")
                inspect(child, (*key_path, key))
            return
        if isinstance(active, (list, tuple)):
            for child in active:
                inspect(child, key_path)
            return
        if isinstance(active, str):
            if _SECRET_VALUE_RE.search(active) or any(
                secret in active for secret in secrets
            ):
                raise EvidenceError("privacy_boundary_violation")

    inspect(value, ())


def parse_version_output(raw: bytes, *, expected: str) -> str:
    if not raw or len(raw) > 1_024:
        raise EvidenceError("version_output_invalid")
    try:
        text_value = raw.decode("utf-8")
    except UnicodeError as exc:
        raise EvidenceError("version_output_invalid") from exc
    if _SECRET_VALUE_RE.search(text_value):
        raise EvidenceError("version_output_invalid")
    matches = _VERSION_RE.findall(text_value)
    if len(matches) != 1 or matches[0] != expected:
        raise EvidenceError("version_pin_mismatch")
    return matches[0]


def parse_json_document(raw: bytes, *, code: str) -> object:
    if not raw or len(raw) > MAX_COMMAND_STDOUT_BYTES:
        raise EvidenceError(code)
    try:
        return json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(code) from exc


@dataclass(frozen=True, slots=True)
class AgentSourceBoundary:
    """Privacy-safe identity of the reviewed in-tree worker source snapshot."""

    manifest_name: str
    entrypoint: str
    manifest_sha256: str
    snapshot_sha256: str
    file_count: int


def capture_agent_source_boundary(
    project_root: Path,
    *,
    expected_snapshot_sha256: str,
) -> AgentSourceBoundary:
    """Capture and pin the exact authoritative NemoClaw worker manifest."""

    if _SHA256_RE.fullmatch(expected_snapshot_sha256) is None:
        raise EvidenceError("agent_source_snapshot_pin_invalid")
    try:
        snapshot = capture_reviewed_source_snapshot(
            project_root,
            NEMOCLAW_AGENT_SOURCE_MANIFEST,
        )
    except (OSError, ValueError) as exc:
        raise EvidenceError("agent_source_snapshot_invalid") from exc
    if snapshot.manifest is not NEMOCLAW_AGENT_SOURCE_MANIFEST:
        raise EvidenceError("agent_source_manifest_contract_invalid")
    actual_paths = tuple(path for path, _raw in snapshot.files)
    if actual_paths != NEMOCLAW_AGENT_SOURCE_MANIFEST.source_paths:
        raise EvidenceError("agent_source_manifest_contract_invalid")
    if not hmac.compare_digest(snapshot.digest, expected_snapshot_sha256):
        raise EvidenceError("agent_source_snapshot_pin_mismatch")
    manifest_sha256 = canonical_sha256(
        {
            "entrypoint": NEMOCLAW_AGENT_SOURCE_MANIFEST.entrypoint,
            "name": NEMOCLAW_AGENT_SOURCE_MANIFEST.name,
            "source_paths": list(NEMOCLAW_AGENT_SOURCE_MANIFEST.source_paths),
        }
    )
    return AgentSourceBoundary(
        manifest_name=NEMOCLAW_AGENT_SOURCE_MANIFEST.name,
        entrypoint=NEMOCLAW_AGENT_SOURCE_MANIFEST.entrypoint,
        manifest_sha256=manifest_sha256,
        snapshot_sha256=snapshot.digest,
        file_count=len(snapshot.files),
    )


def parse_and_assert_policy(
    raw: bytes,
    *,
    expected_sha256: str,
) -> tuple[dict[str, object], str]:
    """Parse, canonicalize, hash, and mechanically enforce the network allowlist."""

    if not raw or len(raw) > MAX_POLICY_BYTES:
        raise EvidenceError("policy_document_invalid")
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise EvidenceError("policy_document_invalid") from exc
    if not isinstance(document, dict):
        raise EvidenceError("policy_document_invalid")
    canonical_hash = canonical_sha256(document)
    if canonical_hash != expected_sha256:
        raise EvidenceError("policy_hash_mismatch")
    assert_effective_policy(document)
    return document, canonical_hash


def assert_effective_policy(document: Mapping[str, object]) -> None:
    """Require only managed inference and the one reviewed tool POST route."""

    network = document.get("network_policies")
    if not isinstance(network, Mapping) or not network:
        raise EvidenceError("network_policy_missing")
    seen_hosts: set[str] = set()
    inference_rules: set[tuple[str, str]] = set()
    tool_rules: set[tuple[str, str]] = set()
    for raw_name, raw_entry in network.items():
        name = str(raw_name).lower()
        if any(term in name for term in _FORBIDDEN_NETWORK_TERMS):
            raise EvidenceError("forbidden_network_policy")
        if not isinstance(raw_entry, Mapping):
            raise EvidenceError("network_policy_invalid")
        if set(raw_entry) - {"name", "endpoints", "binaries"}:
            raise EvidenceError("network_policy_invalid")
        display_name = raw_entry.get("name", raw_name)
        if not isinstance(display_name, str) or any(
            term in display_name.lower() for term in _FORBIDDEN_NETWORK_TERMS
        ):
            raise EvidenceError("forbidden_network_policy")
        binaries = _policy_binaries(raw_entry.get("binaries"))
        if not binaries or not binaries.issubset(_ALLOWED_NETWORK_BINARIES):
            raise EvidenceError("unreviewed_network_binary")
        if STAGED_PYTHON not in binaries:
            raise EvidenceError("staged_python_not_authorized")
        endpoints = raw_entry.get("endpoints")
        if not isinstance(endpoints, list) or not endpoints:
            raise EvidenceError("network_endpoint_missing")
        for endpoint in endpoints:
            if not isinstance(endpoint, Mapping):
                raise EvidenceError("network_endpoint_invalid")
            if set(endpoint) != {
                "host",
                "port",
                "protocol",
                "enforcement",
                "rules",
            }:
                raise EvidenceError("network_endpoint_invalid")
            host = endpoint.get("host")
            port = endpoint.get("port")
            protocol = endpoint.get("protocol")
            enforcement = endpoint.get("enforcement", "enforce")
            if (
                not isinstance(host, str)
                or "*" in host
                or any(term in host.lower() for term in _FORBIDDEN_NETWORK_TERMS)
                or protocol != "rest"
                or enforcement != "enforce"
            ):
                raise EvidenceError("network_endpoint_invalid")
            rules = _policy_rules(endpoint.get("rules"))
            if host == "inference.local" and port == 443:
                inference_rules.update(rules)
            elif host == "vital-relay.internal" and port == 8443:
                tool_rules.update(rules)
            else:
                raise EvidenceError("unlisted_network_host")
            seen_hosts.add(host)
    if seen_hosts != {"inference.local", "vital-relay.internal"}:
        raise EvidenceError("network_allowlist_incomplete")
    if not _INFERENCE_RULES.issubset(inference_rules) or not inference_rules.issubset(
        _INFERENCE_RULES | _OPTIONAL_INFERENCE_RULES
    ):
        raise EvidenceError("managed_inference_rules_invalid")
    if frozenset(tool_rules) != _TOOL_RULES:
        raise EvidenceError("tool_proxy_rules_invalid")


def _policy_binaries(raw: object) -> set[str]:
    if not isinstance(raw, list):
        raise EvidenceError("network_binaries_invalid")
    binaries: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"path"}:
            raise EvidenceError("network_binaries_invalid")
        path = item.get("path")
        if not isinstance(path, str) or not path.startswith("/") or "*" in path:
            raise EvidenceError("network_binaries_invalid")
        if path in binaries:
            raise EvidenceError("network_binaries_invalid")
        binaries.add(path)
    return binaries


def _policy_rules(raw: object) -> set[tuple[str, str]]:
    if not isinstance(raw, list) or not raw:
        raise EvidenceError("network_rules_invalid")
    rules: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"allow"}:
            raise EvidenceError("network_rules_invalid")
        allow = item.get("allow")
        if not isinstance(allow, Mapping) or set(allow) != {"method", "path"}:
            raise EvidenceError("network_rules_invalid")
        method = allow.get("method")
        path = allow.get("path")
        if (
            not isinstance(method, str)
            or not isinstance(path, str)
            or method != method.upper()
            or not path.startswith("/")
            or "*" in path
        ):
            raise EvidenceError("network_rules_invalid")
        rule = (method, path)
        if rule in rules:
            raise EvidenceError("network_rules_invalid")
        rules.add(rule)
    return rules


def assert_status_document(
    payload: object,
    *,
    model: str,
    openshell_version: str,
    openshell_driver: str,
) -> NemoStatusV1:
    try:
        report = NemoStatusV1.model_validate(payload)
    except ValidationError as exc:
        raise EvidenceError("nemoclaw_status_schema_invalid") from exc
    if (
        report.model != model
        or report.recordedRoute.model != model
        or report.liveRoute.model != model
        or report.recordedRoute.provider != report.liveRoute.provider
        or report.provider != report.liveRoute.provider
        or report.openshellVersion != openshell_version
        or report.openshellDriver != openshell_driver
    ):
        raise EvidenceError("nemoclaw_status_pin_mismatch")
    if any(
        term in policy.lower()
        for policy in report.policies
        for term in _FORBIDDEN_NETWORK_TERMS
    ):
        raise EvidenceError("nemoclaw_status_forbidden_policy")

    def require_health(health: ProviderHealthV1) -> None:
        if not health.ok or not health.probed or health.failureLabel is not None:
            raise EvidenceError("nemoclaw_status_unhealthy")
        for subprobe in health.subprobes or ():
            require_health(subprobe)

    require_health(report.inferenceHealth)
    if report.inferenceHealth.endpoint != MANAGED_MODELS_URL:
        raise EvidenceError("nemoclaw_status_route_mismatch")
    if report.sandboxGpuEnabled and (
        report.sandboxGpuProof is None
        or report.sandboxGpuProof.status != "verified"
        or not report.sandboxGpuProof.cudaVerified
    ):
        raise EvidenceError("nemoclaw_status_gpu_degraded")
    return report


def assert_doctor_document(payload: object) -> NemoDoctorV1:
    try:
        report = NemoDoctorV1.model_validate(payload)
    except ValidationError as exc:
        raise EvidenceError("nemoclaw_doctor_schema_invalid") from exc
    labels = {check.label for check in report.checks}
    if not {
        "OpenShell status",
        "Live sandbox",
        "Inference route (gateway)",
    }.issubset(labels):
        raise EvidenceError("nemoclaw_doctor_checks_incomplete")
    return report


def parse_openshell_sandbox_inventory(
    payload: object,
    *,
    expected_policy_revision: int,
) -> OpenShellSandboxRefV1:
    if not isinstance(payload, list):
        raise EvidenceError("openshell_sandbox_inventory_invalid")
    try:
        sandboxes = tuple(
            OpenShellSandboxRefV1.model_validate(item) for item in payload
        )
    except ValidationError as exc:
        raise EvidenceError("openshell_sandbox_inventory_invalid") from exc
    matches = tuple(item for item in sandboxes if item.name == SANDBOX_NAME)
    if len(matches) != 1:
        raise EvidenceError("acceptance_sandbox_identity_invalid")
    sandbox = matches[0]
    if (
        sandbox.phase != "Ready"
        or sandbox.current_policy_version != expected_policy_revision
    ):
        raise EvidenceError("acceptance_sandbox_not_ready")
    return sandbox


def parse_sandbox_receipt(
    raw: bytes,
    model: type[BaseModel],
) -> BaseModel:
    try:
        return model.model_validate_json(raw)
    except (UnicodeError, ValidationError) as exc:
        raise EvidenceError("sandbox_probe_receipt_invalid") from exc


def _assert_ocsf_provenance(
    event: Mapping[str, object],
    *,
    schema_version: str,
    vendor: str,
    openshell_version: str,
) -> None:
    metadata = event.get("metadata")
    if not isinstance(metadata, Mapping):
        raise EvidenceError("openshell_ocsf_event_invalid")
    product = metadata.get("product")
    if (
        metadata.get("version") != schema_version
        or not isinstance(product, Mapping)
        or product.get("name") != "OpenShell Sandbox Supervisor"
        or product.get("vendor_name") != vendor
        or product.get("version") != openshell_version
    ):
        raise EvidenceError("openshell_ocsf_event_invalid")


def _ocsf_actor(event: Mapping[str, object]) -> tuple[str, int] | None:
    actor = event.get("actor")
    if not isinstance(actor, Mapping):
        return None
    process = actor.get("process")
    if not isinstance(process, Mapping):
        return None
    name = process.get("name")
    pid = process.get("pid")
    return (name, pid) if isinstance(name, str) and isinstance(pid, int) else None


def _ocsf_destination(event: Mapping[str, object]) -> tuple[str, int] | None:
    endpoint = event.get("dst_endpoint")
    if not isinstance(endpoint, Mapping):
        return None
    host = endpoint.get("domain")
    port = endpoint.get("port")
    return (host, port) if isinstance(host, str) and isinstance(port, int) else None


def _is_denied(event: Mapping[str, object]) -> bool:
    detail = event.get("status_detail")
    controls_denied = (
        event.get("action_id") == 2
        and event.get("action") == "Denied"
        and event.get("disposition_id") == 2
        and event.get("disposition") == "Blocked"
    )
    if not controls_denied:
        return False
    if (
        event.get("status_id") == 2
        and event.get("status") == "Failure"
        and isinstance(detail, str)
        and ("no matching" in detail.lower() or "deny" in detail.lower())
    ):
        return True
    message = event.get("message")
    return (
        event.get("class_uid") == 4002
        and "status_id" not in event
        and "status" not in event
        and "status_detail" not in event
        and isinstance(message, str)
        and message.startswith("L7_REQUEST deny ")
        and " reason=no matching" in message.lower()
    )


def assert_correlated_ocsf_denial(
    events: Sequence[Mapping[str, object]],
    *,
    sandbox_identity: str,
    challenge: str,
    process_pid: int,
    binary: str,
    host: str,
    schema_version: str,
    vendor: str,
    openshell_version: str,
    path: str | None = None,
) -> OcsfDeltaReceipt:
    """Require exactly one post-cursor denial tied to this trusted attempt."""

    matches: list[Mapping[str, object]] = []
    for event in events:
        _assert_ocsf_provenance(
            event,
            schema_version=schema_version,
            vendor=vendor,
            openshell_version=openshell_version,
        )
        metadata = event.get("metadata")
        product = metadata.get("product") if isinstance(metadata, Mapping) else None
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("uid") != sandbox_identity
            or not isinstance(product, Mapping)
            or product.get("version") != openshell_version
        ):
            continue
        if not _is_denied(event) or _ocsf_destination(event) != (
            host,
            8443 if host == "vital-relay.internal" else 443,
        ):
            continue
        if path is None:
            if _ocsf_actor(event) != (binary, process_pid):
                continue
            if challenge not in host and not (
                host == "inference.local" and binary == UNLISTED_BINARY
            ):
                continue
        else:
            request = event.get("http_request")
            url = request.get("url") if isinstance(request, Mapping) else None
            if (
                event.get("class_uid") != 4002
                or not isinstance(request, Mapping)
                or request.get("http_method") != "GET"
                or not isinstance(url, Mapping)
                or url.get("hostname") != host
                or url.get("path") != path
                or challenge not in path
            ):
                continue
        matches.append(event)
    if len(matches) != 1:
        raise EvidenceError("openshell_correlated_denial_count_invalid")
    selected = matches[0]
    return OcsfDeltaReceipt(
        schema_version=1,
        probe="ocsf_delta",
        challenge_sha256=hashlib.sha256(challenge.encode()).hexdigest(),
        correlated_event_sha256=canonical_sha256(selected),
        correlated_count=1,
        process_pid=process_pid,
    )


def assert_probe_coverage(observations: Sequence[Observation]) -> None:
    names = tuple(item.name for item in observations)
    if names != PROBE_ORDER:
        raise EvidenceError("probe_coverage_invalid")


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or value != value.strip() or "\x00" in value:
        raise EvidenceError("configuration_missing")
    return value


def configuration_blockers(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Report absent external prerequisite groups without reading secret values."""

    active = os.environ if environment is None else environment
    blockers: list[str] = []

    def missing(*names: str) -> bool:
        return any(not active.get(name) for name in names)

    if not Path(NEMOCLAW_HOST_CLI_EXECUTABLE).is_file():
        blockers.append("nemoclaw_cli_unavailable")
    if not Path(OPEN_SHELL_EXECUTABLE).is_file():
        blockers.append("openshell_cli_unavailable")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_NEMOCLAW_VERSION",
        "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_VERSION",
    ):
        blockers.append("version_pins_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_OCSF_SCHEMA_VERSION",
        "VITAL_RELAY_LIVE_EVIDENCE_OCSF_VENDOR",
        "VITAL_RELAY_LIVE_EVIDENCE_OCSF_EXPORT_PATH",
    ):
        blockers.append("openshell_ocsf_export_schema_missing")
    if missing("VITAL_RELAY_LIVE_EVIDENCE_IMAGE_DIGEST"):
        blockers.append("image_pin_missing")
    if missing("VITAL_RELAY_LIVE_EVIDENCE_RUNTIME_SHA256"):
        blockers.append("runtime_pin_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_HARNESS_SOURCE_SHA256",
        "VITAL_RELAY_LIVE_EVIDENCE_SANDBOX_PROBE_SOURCE_SHA256",
    ):
        blockers.append("source_pins_missing")
    if missing("VITAL_RELAY_LIVE_EVIDENCE_AGENT_SOURCE_SNAPSHOT_SHA256"):
        blockers.append("agent_source_snapshot_pin_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_BASE_POLICY_SHA256",
        "VITAL_RELAY_LIVE_EVIDENCE_EFFECTIVE_POLICY_SHA256",
    ):
        blockers.append("policy_pins_missing")
    if missing("VITAL_RELAY_VLLM_MODEL"):
        blockers.append("model_configuration_missing")
    if missing("VITAL_RELAY_DATABASE_URL", "VITAL_RELAY_DEMO_SCOPE_ID"):
        blockers.append("postgres_configuration_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_FILE",
        "VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_SHA256",
        "VITAL_RELAY_LIVE_EVIDENCE_TRANSPORT_IDENTITY_SHA256",
        "VITAL_RELAY_LIVE_EVIDENCE_WORKER_TRANSPORT_IDENTITY_SHA256",
        "VITAL_RELAY_LIVE_EVIDENCE_WORKER_COMMAND_SHA256",
    ):
        blockers.append("tls_configuration_missing")
    if missing("VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY"):
        blockers.append("signing_key_configuration_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_HMAC_KEY",
        "VITAL_RELAY_LIVE_EVIDENCE_ISSUER",
        "VITAL_RELAY_LIVE_EVIDENCE_KEY_ID",
    ):
        blockers.append("evidence_authentication_configuration_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_DRIVER",
        "VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_POLICY_REVISION",
    ):
        blockers.append("openshell_inventory_configuration_missing")
    if missing(
        "VITAL_RELAY_AGENT_ENABLED",
        "VITAL_RELAY_AGENT_SANDBOX",
        "VITAL_RELAY_AGENT_SANDBOX_NAME",
        "VITAL_RELAY_AGENT_TOOL_PROXY_ENDPOINT",
        "VITAL_RELAY_VLLM_BASE_URL",
    ):
        blockers.append("agent_runtime_configuration_missing")
    if missing(
        "VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_ID",
        "VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_STATE_VERSION",
        "VITAL_RELAY_LIVE_EVIDENCE_RUN_ID",
        "VITAL_RELAY_LIVE_EVIDENCE_KILL_RUN_ID",
    ):
        blockers.append("live_identity_configuration_missing")
    if missing("VITAL_RELAY_LIVE_EVIDENCE_COMMAND_TOKEN"):
        blockers.append("command_credential_missing")
    return tuple(sorted(blockers))


def _uuid_environment(name: str) -> UUID:
    try:
        return UUID(_required_environment(name))
    except ValueError as exc:
        raise EvidenceError("identity_configuration_invalid") from exc


def _decode_signing_key(raw: str) -> bytes:
    if "=" in raw:
        raise EvidenceError("signing_key_invalid")
    try:
        key = base64.b64decode(
            raw + "=" * (-len(raw) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeError, ValueError) as exc:
        raise EvidenceError("signing_key_invalid") from exc
    if not 32 <= len(key) <= 64:
        raise EvidenceError("signing_key_invalid")
    return key


def _fixed_host_environment(argv: Sequence[str]) -> dict[str, str]:
    executable_parent = str(Path(argv[0]).parent)
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.pathsep.join(
            dict.fromkeys(
                (executable_parent, "/usr/local/bin", "/usr/bin", "/bin")
            )
        ),
    }
    for name in (
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    ):
        value = os.environ.get(name)
        if value and "\x00" not in value and len(value) <= 4_096:
            if name.endswith("_HOME") or name in {"HOME", "TMPDIR"}:
                if not Path(value).is_absolute():
                    continue
            environment[name] = value
    return environment


def _read_stable_file(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise EvidenceError("required_file_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceError("required_file_unsafe")
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > maximum_bytes:
                raise EvidenceError("required_file_too_large")
        final_metadata = os.fstat(descriptor)
        if _stable_stat_tuple(final_metadata) != _stable_stat_tuple(metadata):
            raise EvidenceError("required_file_changed")
    finally:
        os.close(descriptor)
    return bytes(raw)


def _hash_file(path: Path, *, maximum_bytes: int | None = None) -> str:
    maximum = maximum_bytes if maximum_bytes is not None else 64 * 1024 * 1024
    return hashlib.sha256(
        _read_stable_file(path, maximum_bytes=maximum)
    ).hexdigest()


def _stable_stat_tuple(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _reviewed_harness_graph_sha256(
    repository_root: Path,
    relative_paths: Sequence[str],
) -> str:
    entries: list[dict[str, object]] = []
    for relative in relative_paths:
        path = repository_root / relative
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise EvidenceError("harness_source_unavailable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise EvidenceError("harness_source_unsafe")
        entries.append(
            {
                "path": relative,
                "mode": stat.S_IMODE(metadata.st_mode),
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "size": metadata.st_size,
                "sha256": _hash_file(path, maximum_bytes=8 * 1024 * 1024),
            }
        )
    return canonical_sha256(entries)


class _DockerUnixConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost", timeout=10.0)

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(DOCKER_SOCKET)


def _docker_json(path: str) -> object:
    if not path.startswith(f"/{DOCKER_API_VERSION}/") or any(
        character in path for character in ("\r", "\n", "\x00")
    ):
        raise EvidenceError("docker_runtime_inventory_invalid")
    connection = _DockerUnixConnection()
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise EvidenceError("docker_runtime_inventory_unavailable")
        raw_length = response.getheader("content-length")
        if raw_length is not None and int(raw_length) > MAX_HTTP_BODY_BYTES:
            raise EvidenceError("docker_runtime_inventory_too_large")
        raw = response.read(MAX_HTTP_BODY_BYTES + 1)
        if len(raw) > MAX_HTTP_BODY_BYTES:
            raise EvidenceError("docker_runtime_inventory_too_large")
        return json.loads(raw)
    except EvidenceError:
        raise
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        http.client.HTTPException,
    ) as exc:
        raise EvidenceError("docker_runtime_inventory_unavailable") from exc
    finally:
        connection.close()


def docker_image_inventory(expected_image_digest: str) -> dict[str, object]:
    filters = canonical_json_bytes(
        {
            "label": [
                f"{DOCKER_MANAGED_LABEL}=openshell",
                f"{DOCKER_SANDBOX_LABEL}={SANDBOX_NAME}",
            ]
        }
    ).decode("ascii")
    containers = _docker_json(
        f"/{DOCKER_API_VERSION}/containers/json?all=1&filters={quote(filters, safe='')}"
    )
    if not isinstance(containers, list) or len(containers) != 1:
        raise EvidenceError("acceptance_sandbox_container_identity_invalid")
    summary = containers[0]
    if not isinstance(summary, Mapping):
        raise EvidenceError("docker_runtime_inventory_invalid")
    container_id = summary.get("Id")
    if (
        not isinstance(container_id, str)
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
    ):
        raise EvidenceError("docker_runtime_inventory_invalid")
    detail = _docker_json(f"/{DOCKER_API_VERSION}/containers/{container_id}/json")
    if not isinstance(detail, Mapping):
        raise EvidenceError("docker_runtime_inventory_invalid")
    config = detail.get("Config")
    state = detail.get("State")
    if not isinstance(config, Mapping) or not isinstance(state, Mapping):
        raise EvidenceError("docker_runtime_inventory_invalid")
    labels = config.get("Labels")
    image = detail.get("Image")
    configured_image = config.get("Image")
    if (
        not isinstance(labels, Mapping)
        or labels.get(DOCKER_MANAGED_LABEL) != "openshell"
        or labels.get(DOCKER_SANDBOX_LABEL) != SANDBOX_NAME
        or image != expected_image_digest
        or state.get("Running") is not True
        or not isinstance(configured_image, str)
    ):
        raise EvidenceError("sandbox_image_pin_mismatch")
    return {
        "image_digest": image,
        "container_identity_sha256": hashlib.sha256(
            container_id.encode()
        ).hexdigest(),
        "configured_image_sha256": hashlib.sha256(
            configured_image.encode()
        ).hexdigest(),
        "running": True,
    }


def _identity_sha256(scope_id: UUID, run_id: UUID, incident_id: UUID) -> str:
    return canonical_sha256(
        {
            "scope_id": str(scope_id),
            "run_id": str(run_id),
            "incident_id": str(incident_id),
        }
    )


def _probe_uuid(run_id: UUID, label: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"urn:vital-relay:live-evidence:{run_id}:{label}",
    )


class DedicatedBackend:
    """One harness-owned backend process used for the crash/lease probe."""

    def __init__(self) -> None:
        self._process: multiprocessing.Process | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    def start(self) -> None:
        if self._process is not None:
            raise EvidenceError("dedicated_backend_state_invalid")
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            target=_serve_dedicated_backend,
            name="vital-relay-live-evidence-backend",
            daemon=False,
        )
        process.start()
        self._process = process
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if not process.is_alive():
                process.join(timeout=1.0)
                self._process = None
                raise EvidenceError("dedicated_backend_start_failed")
            try:
                with httpx.Client(
                    timeout=1.0,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    with client.stream("GET", DEDICATED_API_HEALTH_URL) as response:
                        status_code = response.status_code
            except httpx.HTTPError:
                time.sleep(0.1)
                continue
            if 200 <= status_code < 500:
                return
            time.sleep(0.1)
        self.kill()
        raise EvidenceError("dedicated_backend_start_timeout")

    def kill(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.is_alive():
            process.kill()
        process.join(timeout=10.0)
        if process.is_alive():
            raise EvidenceError("dedicated_backend_kill_failed")

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=30.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=10.0)
        if process.is_alive():
            raise EvidenceError("dedicated_backend_cleanup_failed")

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        self.close()


def _serve_dedicated_backend() -> None:
    """Child target; suppress every diagnostic because it can contain secrets."""

    null_descriptor = os.open(os.devnull, os.O_RDWR)
    try:
        os.dup2(null_descriptor, 1)
        os.dup2(null_descriptor, 2)
    finally:
        if null_descriptor > 2:
            os.close(null_descriptor)
    try:
        import uvicorn

        uvicorn.run(
            "vital_relay.main:create_app",
            factory=True,
            host="127.0.0.1",
            port=8017,
            access_log=False,
            log_level="critical",
        )
    except BaseException:
        os._exit(70)


@dataclass(frozen=True, slots=True)
class AttemptAuthority:
    account_id: UUID
    session_id: UUID
    policy_id: str
    policy_version: str
    policy_sha256: str
    policy_revision: int
    absence_checked_at: datetime
    product_request_sha256: str
    killed_request_sha256: str


class NemoClawLiveAttestor:
    """Execute the reviewed live graph and sign only complete evidence."""

    def __init__(
        self,
        config: LiveEvidenceConfig,
        *,
        executor: FixedCommandExecutor | None = None,
    ) -> None:
        self._config = config
        self._executor = executor or FixedCommandExecutor()
        self._engine = create_postgres_engine(config.database_url)
        self._sessions = create_session_factory(self._engine)
        self._backend = DedicatedBackend()
        self._attempt_nonce = secrets.token_bytes(32)
        self._authority: AttemptAuthority | None = None
        self._orphan_authority_not_after: datetime | None = None
        self._owned_worker: SandboxProcessReceipt | None = None
        self._cleanup_checks: list[str] = []
        self._repository_root = Path(__file__).resolve().parents[4]
        self._harness_source_sha256 = ""
        self._sandbox_probe_source_sha256 = ""
        self._agent_source_manifest_sha256 = ""
        self._agent_source_snapshot_sha256 = ""
        self._agent_source_file_count = 0

    def close(self) -> None:
        cleanup_error: EvidenceError | None = None
        try:
            self._backend.close()
            self._cleanup_checks.append("dedicated_backend_process_absent")
        except EvidenceError as exc:
            cleanup_error = exc
        try:
            if self._owned_worker is not None:
                if self._orphan_authority_not_after is None:
                    raise EvidenceError("worker_cleanup_authority_unknown")
                _wait_until(self._orphan_authority_not_after)
                self._terminate_owned_worker()
        except EvidenceError as exc:
            cleanup_error = cleanup_error or exc
        finally:
            self._engine.dispose()
        if cleanup_error is not None:
            raise cleanup_error

    def run(self) -> LiveEvidenceArtifact:
        self._preflight()
        authority = self._require_authority()
        observations: dict[Probe, Observation] = {}

        def add(observation: Observation) -> None:
            if observation.name in observations:
                raise EvidenceError("probe_coverage_invalid")
            observations[observation.name] = observation

        nemoclaw = self._checked_command(FixedCommand.NEMOCLAW_VERSION)
        nemoclaw_version = parse_version_output(
            nemoclaw.stdout, expected=self._config.expected_nemoclaw_version
        )
        add(_observation(Probe.NEMOCLAW_VERSION, nemoclaw.stdout))

        openshell = self._checked_command(FixedCommand.OPENSHELL_VERSION)
        openshell_version = parse_version_output(
            openshell.stdout, expected=self._config.expected_openshell_version
        )
        add(_observation(Probe.OPENSHELL_VERSION, openshell.stdout))

        status = self._checked_command(FixedCommand.STATUS, timeout_seconds=120.0)
        status_report = assert_status_document(
            parse_json_document(status.stdout, code="nemoclaw_status_invalid"),
            model=self._config.model,
            openshell_version=openshell_version,
            openshell_driver=self._config.expected_openshell_driver,
        )
        add(
            Observation(
                name=Probe.NEMOCLAW_STATUS,
                content_sha256=canonical_sha256(status_report),
            )
        )

        doctor = self._checked_command(FixedCommand.DOCTOR, timeout_seconds=180.0)
        doctor_report = assert_doctor_document(
            parse_json_document(doctor.stdout, code="nemoclaw_doctor_invalid")
        )
        add(
            Observation(
                name=Probe.NEMOCLAW_DOCTOR,
                content_sha256=canonical_sha256(doctor_report),
                count=len(doctor_report.checks),
            )
        )

        sandbox_inventory_output = self._checked_command(
            FixedCommand.SANDBOX_INVENTORY
        )
        sandbox = parse_openshell_sandbox_inventory(
            parse_json_document(
                sandbox_inventory_output.stdout,
                code="openshell_sandbox_inventory_invalid",
            ),
            expected_policy_revision=self._config.expected_openshell_policy_revision,
        )
        image_inventory = docker_image_inventory(
            self._config.expected_image_digest
        )
        add(
            Observation(
                name=Probe.SANDBOX_IMAGE_INVENTORY,
                content_sha256=canonical_sha256(
                    {
                        "openshell": sandbox.model_dump(mode="json"),
                        "runtime": image_inventory,
                    }
                ),
                count=1,
            )
        )

        base_policy = self._checked_command(FixedCommand.BASE_POLICY)
        _, base_hash = parse_and_assert_policy(
            base_policy.stdout,
            expected_sha256=self._config.expected_base_policy_sha256,
        )
        add(
            Observation(
                name=Probe.BASE_POLICY_ALLOWLIST,
                content_sha256=base_hash,
            )
        )
        effective_policy = self._checked_command(FixedCommand.EFFECTIVE_POLICY)
        _, effective_hash = parse_and_assert_policy(
            effective_policy.stdout,
            expected_sha256=self._config.expected_effective_policy_sha256,
        )
        add(
            Observation(
                name=Probe.EFFECTIVE_POLICY_ALLOWLIST,
                content_sha256=effective_hash,
            )
        )

        runtime = self._runtime_and_inference_probe()
        add(
            Observation(
                name=Probe.IMMUTABLE_RUNTIME_INVENTORY,
                content_sha256=canonical_sha256(
                    {
                        "bytes": runtime.runtime_bytes,
                        "directories": runtime.runtime_directory_count,
                        "files": runtime.runtime_file_count,
                        "manifest": runtime.runtime_manifest_sha256,
                        "mount": runtime.runtime_mount_identity_sha256,
                    }
                ),
                count=runtime.runtime_file_count + runtime.runtime_directory_count,
            )
        )
        add(
            Observation(
                name=Probe.MANAGED_INFERENCE,
                content_sha256=runtime.model_identity_sha256,
                count=1,
            )
        )
        add(
            Observation(
                name=Probe.ACTUAL_SANDBOX_TRANSPORT,
                content_sha256=canonical_sha256(
                    {
                        "ca": runtime.actual_ca_sha256,
                        "identity": runtime.transport_identity_sha256,
                    }
                ),
                count=1,
            )
        )

        for observation in self._containment_probes(sandbox.id):
            add(observation)

        self._backend.start()
        for observation in self._product_and_retry_probe(sandbox.id):
            add(observation)
        for observation in self._capability_denial_probes():
            add(observation)
        kill_observation, worker_transport = self._kill_lease_probe(sandbox.id)
        add(kill_observation)
        self._backend.close()
        self._cleanup_checks.append("dedicated_backend_process_absent")
        if self._owned_worker is not None:
            _wait_until(self._orphan_authority_not_after or datetime.now(UTC))
            self._terminate_owned_worker()
        worker_handle = self._last_worker_handle()
        cleanup = CleanupCustody(
            owned_resources=(
                "dedicated_backend_process",
                "dedicated_acceptance_sandbox_worker_handle",
            ),
            worker_handle_sha256=worker_handle,
            completed_checks=tuple(dict.fromkeys(self._cleanup_checks)),
        )
        add(
            Observation(
                name=Probe.CLEANUP_CUSTODY,
                content_sha256=canonical_sha256(cleanup),
                count=len(cleanup.completed_checks),
            )
        )

        self._revalidate_sandbox_policy(
            sandbox.id,
            image_inventory,
            base_hash,
            effective_hash,
        )
        self._revalidate_authority()
        ordered = tuple(
            observations[probe] for probe in PROBE_ORDER if probe in observations
        )
        assert_probe_coverage(ordered)
        fixed_graph_sha256 = canonical_sha256(
            {command.value: list(argv) for command, argv in FIXED_COMMANDS.items()}
        )
        attempt_identity = hmac.new(
            self._config.evidence_key,
            CHALLENGE_DOMAIN + self._attempt_nonce,
            hashlib.sha256,
        ).hexdigest()
        body = LiveEvidenceBody(
            schema_version=EVIDENCE_SCHEMA_VERSION,
            lane=LANE_NAME,
            evidence_kind="live",
            outcome="passed",
            captured_at=datetime.now(UTC),
            inventory=PinnedInventory(
                nemoclaw_version=nemoclaw_version,
                openshell_version=openshell_version,
                ocsf_schema_version=self._config.expected_ocsf_schema_version,
                ocsf_vendor=self._config.expected_ocsf_vendor,
                ocsf_export_path=OCSF_EXPORT_PATH,
                ocsf_export_command_sha256=canonical_sha256(
                    list(FIXED_COMMANDS[FixedCommand.OCSF_EXPORT])
                ),
                sandbox_image_digest=self._config.expected_image_digest,
                sandbox_identity_sha256=hashlib.sha256(sandbox.id.encode()).hexdigest(),
                openshell_policy_revision=sandbox.current_policy_version,
                runtime_manifest_sha256=runtime.runtime_manifest_sha256,
                base_policy_sha256=base_hash,
                effective_policy_sha256=effective_hash,
                tls_ca_sha256=runtime.actual_ca_sha256,
                sandbox_transport_identity_sha256=runtime.transport_identity_sha256,
                worker_transport_identity_sha256=worker_transport,
                model_identity_sha256=runtime.model_identity_sha256,
                harness_source_sha256=self._harness_source_sha256,
                sandbox_probe_source_sha256=self._sandbox_probe_source_sha256,
                agent_source_manifest_name=AGENT_SOURCE_MANIFEST_NAME,
                agent_source_entrypoint=AGENT_SOURCE_ENTRYPOINT,
                agent_source_manifest_sha256=self._agent_source_manifest_sha256,
                agent_source_snapshot_sha256=self._agent_source_snapshot_sha256,
                agent_source_file_count=self._agent_source_file_count,
                fixed_command_graph_sha256=fixed_graph_sha256,
                coordination_policy_sha256=authority.policy_sha256,
                coordination_policy_revision=authority.policy_revision,
                runner="nemoclaw",
                proc_self_exe=runtime.proc_self_exe,
            ),
            observations=ordered,
            required_probes=PROBE_ORDER,
            attempt_identity_sha256=attempt_identity,
            product_run_identity_sha256=_identity_sha256(
                self._config.scope_id,
                self._config.product_run_id,
                self._config.incident_id,
            ),
            killed_run_identity_sha256=_identity_sha256(
                self._config.scope_id,
                self._config.killed_run_id,
                self._config.incident_id,
            ),
            cleanup_custody=cleanup,
        )
        artifact = content_addressed_artifact(
            body,
            evidence_key=self._config.evidence_key,
            issuer=self._config.evidence_issuer,
            key_id=self._config.evidence_key_id,
        )
        verify_live_evidence_artifact(
            artifact,
            evidence_key=self._config.evidence_key,
            issuer=self._config.evidence_issuer,
            key_id=self._config.evidence_key_id,
        )
        ensure_privacy_safe(
            artifact.model_dump(mode="json"),
            exact_secrets=(
                self._config.command_token,
                os.environ.get("VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY", ""),
                os.environ.get("VITAL_RELAY_LIVE_EVIDENCE_HMAC_KEY", ""),
                self._config.database_url,
            ),
        )
        return artifact

    def _preflight(self) -> None:
        if os.environ.get("VITAL_RELAY_AGENT_ENABLED") != "true":
            raise EvidenceError("agent_runtime_not_enabled")
        required_exact = {
            "VITAL_RELAY_AGENT_SANDBOX": "nemoclaw",
            "VITAL_RELAY_AGENT_SANDBOX_NAME": SANDBOX_NAME,
            "VITAL_RELAY_AGENT_TOOL_PROXY_ENDPOINT": TOOL_PROXY_URL,
            "VITAL_RELAY_VLLM_BASE_URL": f"{MANAGED_INFERENCE_ORIGIN}/v1",
        }
        if any(os.environ.get(name) != value for name, value in required_exact.items()):
            raise EvidenceError("agent_runtime_route_mismatch")
        for executable in (NEMOCLAW_HOST_CLI_EXECUTABLE, OPEN_SHELL_EXECUTABLE):
            path = Path(executable)
            try:
                metadata = path.stat()
            except OSError as exc:
                raise EvidenceError("nemoclaw_openshell_unavailable") from exc
            if not stat.S_ISREG(metadata.st_mode) or not os.access(path, os.X_OK):
                raise EvidenceError("nemoclaw_openshell_unavailable")
        if _hash_file(
            self._config.tls_ca_file, maximum_bytes=4 * 1024 * 1024
        ) != self._config.expected_tls_ca_sha256:
            raise EvidenceError("tls_ca_hash_mismatch")
        self._harness_source_sha256 = _reviewed_harness_graph_sha256(
            self._repository_root, HARNESS_GRAPH_RELATIVE_PATHS
        )
        self._sandbox_probe_source_sha256 = _hash_file(
            self._repository_root
            / "backend/src/vital_relay/agent/nemoclaw_probe.py",
            maximum_bytes=2 * 1024 * 1024,
        )
        if (
            self._harness_source_sha256
            != self._config.expected_harness_source_sha256
            or self._sandbox_probe_source_sha256
            != self._config.expected_sandbox_probe_source_sha256
        ):
            raise EvidenceError("source_graph_pin_mismatch")
        source_boundary = capture_agent_source_boundary(
            self._repository_root,
            expected_snapshot_sha256=(
                self._config.expected_agent_source_snapshot_sha256
            ),
        )
        self._agent_source_manifest_sha256 = source_boundary.manifest_sha256
        self._agent_source_snapshot_sha256 = source_boundary.snapshot_sha256
        self._agent_source_file_count = source_boundary.file_count
        try:
            with self._sessions() as session:
                session.connection(
                    execution_options={"isolation_level": "SERIALIZABLE"}
                )
                session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": f"vital-relay-live-evidence:{self._config.scope_id}"},
                )
                database_now = session.scalar(text("SELECT clock_timestamp()"))
                rows = session.scalars(
                    select(AgentRunRow).where(
                        AgentRunRow.scope_id == self._config.scope_id,
                        AgentRunRow.run_id.in_(
                            (
                                self._config.product_run_id,
                                self._config.killed_run_id,
                            )
                        ),
                    )
                ).all()
                if rows:
                    raise EvidenceError("acceptance_run_identity_not_fresh")
                token_hash = hashlib.sha256(
                    self._config.command_token.encode("utf-8")
                ).hexdigest()
                principal = session.scalar(
                    select(PersonaSessionRow)
                    .where(
                        PersonaSessionRow.scope_id == self._config.scope_id,
                        PersonaSessionRow.access_token_hash == token_hash,
                    )
                    .with_for_update(read=True)
                )
                policy = session.scalar(
                    select(AgentActivePolicyRow)
                    .where(AgentActivePolicyRow.scope_id == self._config.scope_id)
                    .with_for_update(read=True)
                )
                if (
                    principal is None
                    or principal.status != "active"
                    or not isinstance(database_now, datetime)
                    or principal.access_expires_at <= database_now
                ):
                    raise EvidenceError("command_principal_invalid")
                if policy is None:
                    raise EvidenceError("active_coordination_policy_missing")
                def request_sha256(run_id: UUID) -> str:
                    return canonical_sha256(
                        {
                            "body": {
                                "expected_state_version": (
                                    self._config.incident_state_version
                                ),
                                "run_id": str(run_id),
                                "schema_version": 1,
                            },
                            "method": "POST",
                            "path": (
                                f"/v1/incidents/{self._config.incident_id}"
                                "/agent-runs"
                            ),
                        }
                    )
                self._authority = AttemptAuthority(
                    account_id=principal.account_id,
                    session_id=principal.session_id,
                    policy_id=policy.policy_id,
                    policy_version=policy.policy_version,
                    policy_sha256=policy.policy_sha256,
                    policy_revision=policy.revision,
                    absence_checked_at=database_now,
                    product_request_sha256=request_sha256(
                        self._config.product_run_id
                    ),
                    killed_request_sha256=request_sha256(
                        self._config.killed_run_id
                    ),
                )
                session.commit()
        except EvidenceError:
            raise
        except Exception as exc:
            raise EvidenceError("postgres_preflight_failed") from exc

    def _require_authority(self) -> AttemptAuthority:
        if self._authority is None:
            raise EvidenceError("attempt_authority_missing")
        return self._authority

    def _revalidate_authority(self) -> None:
        authority = self._require_authority()
        try:
            with self._sessions() as session:
                policy = session.scalar(
                    select(AgentActivePolicyRow).where(
                        AgentActivePolicyRow.scope_id == self._config.scope_id
                    )
                )
                principal = session.scalar(
                    select(PersonaSessionRow).where(
                        PersonaSessionRow.scope_id == self._config.scope_id,
                        PersonaSessionRow.session_id == authority.session_id,
                    )
                )
                database_now = session.scalar(text("SELECT clock_timestamp()"))
        except Exception as exc:
            raise EvidenceError("postgres_authority_revalidation_failed") from exc
        if (
            policy is None
            or policy.policy_id != authority.policy_id
            or policy.policy_version != authority.policy_version
            or policy.policy_sha256 != authority.policy_sha256
            or policy.revision != authority.policy_revision
            or principal is None
            or principal.account_id != authority.account_id
            or principal.status != "active"
            or not isinstance(database_now, datetime)
            or principal.access_expires_at <= database_now
        ):
            raise EvidenceError("attempt_authority_changed")

    def _revalidate_sandbox_policy(
        self,
        sandbox_identity: str,
        image_inventory: Mapping[str, object],
        base_hash: str,
        effective_hash: str,
    ) -> None:
        inventory = self._checked_command(FixedCommand.SANDBOX_INVENTORY)
        sandbox = parse_openshell_sandbox_inventory(
            parse_json_document(
                inventory.stdout,
                code="openshell_sandbox_inventory_invalid",
            ),
            expected_policy_revision=self._config.expected_openshell_policy_revision,
        )
        if sandbox.id != sandbox_identity:
            raise EvidenceError("acceptance_sandbox_identity_changed")
        if docker_image_inventory(
            self._config.expected_image_digest
        ) != dict(image_inventory):
            raise EvidenceError("sandbox_image_changed_during_attempt")
        final_base = self._checked_command(FixedCommand.BASE_POLICY)
        _, final_base_hash = parse_and_assert_policy(
            final_base.stdout,
            expected_sha256=self._config.expected_base_policy_sha256,
        )
        final_effective = self._checked_command(FixedCommand.EFFECTIVE_POLICY)
        _, final_effective_hash = parse_and_assert_policy(
            final_effective.stdout,
            expected_sha256=self._config.expected_effective_policy_sha256,
        )
        if final_base_hash != base_hash or final_effective_hash != effective_hash:
            raise EvidenceError("openshell_policy_changed_during_attempt")

    def _checked_command(
        self,
        command: FixedCommand,
        *,
        input_bytes: bytes = b"",
        timeout_seconds: float = 60.0,
    ) -> CommandOutput:
        output = self._executor.run(
            command,
            input_bytes=input_bytes,
            timeout_seconds=timeout_seconds,
        )
        if output.returncode != 0:
            raise EvidenceError(f"{command.value}_failed")
        return output

    def _ocsf_export(
        self,
        *,
        action: Literal["capture", "delta"],
        cursor: OcsfCursorReceipt | None,
    ) -> SandboxOcsfExportReceipt:
        request = canonical_json_bytes(
            {
                "action": action,
                "cursor": (
                    None if cursor is None else cursor.model_dump(mode="json")
                ),
                "expected_openshell_version": (
                    self._config.expected_openshell_version
                ),
                "expected_schema_version": (
                    self._config.expected_ocsf_schema_version
                ),
                "expected_vendor": self._config.expected_ocsf_vendor,
                "export_path": self._config.expected_ocsf_export_path,
            }
        )
        try:
            output = self._executor.run(
                FixedCommand.OCSF_EXPORT,
                input_bytes=request,
                timeout_seconds=60.0,
            )
            if output.returncode != 0:
                raise EvidenceError(
                    "openshell_ocsf_version_export_schema_invalid"
                )
            parsed = parse_sandbox_receipt(
                output.stdout,
                SandboxOcsfExportReceipt,
            )
        except EvidenceError as exc:
            if exc.code == "openshell_ocsf_version_export_schema_invalid":
                raise
            raise EvidenceError(
                "openshell_ocsf_version_export_schema_invalid"
            ) from exc
        assert isinstance(parsed, SandboxOcsfExportReceipt)
        if (
            parsed.action != action
            or parsed.export_path != self._config.expected_ocsf_export_path
            or parsed.expected_schema_version
            != self._config.expected_ocsf_schema_version
            or parsed.expected_vendor != self._config.expected_ocsf_vendor
            or parsed.expected_openshell_version
            != self._config.expected_openshell_version
            or (action == "capture" and parsed.events)
            or (cursor is not None and parsed.cursor != cursor)
        ):
            raise EvidenceError("openshell_ocsf_version_export_schema_invalid")
        for event in parsed.events:
            _assert_ocsf_provenance(
                event,
                schema_version=self._config.expected_ocsf_schema_version,
                vendor=self._config.expected_ocsf_vendor,
                openshell_version=self._config.expected_openshell_version,
            )
        return parsed

    def _capture_ocsf_cursor(self) -> OcsfCursorReceipt:
        return self._ocsf_export(action="capture", cursor=None).cursor

    def _read_ocsf_delta(
        self,
        cursor: OcsfCursorReceipt,
    ) -> tuple[dict[str, object], ...]:
        return self._ocsf_export(action="delta", cursor=cursor).events

    def _runtime_and_inference_probe(self) -> SandboxRuntimeReceipt:
        request = canonical_json_bytes(
            {
                "expected_ca_sha256": self._config.expected_tls_ca_sha256,
                "expected_model": self._config.model,
                "expected_probe_source_sha256": (
                    self._config.expected_sandbox_probe_source_sha256
                ),
                "expected_runtime_sha256": self._config.expected_runtime_sha256,
                "expected_transport_identity_sha256": (
                    self._config.expected_transport_identity_sha256
                ),
            }
        )
        output = self._checked_command(
            FixedCommand.RUNTIME_INFERENCE,
            input_bytes=request,
            timeout_seconds=180.0,
        )
        receipt = parse_sandbox_receipt(
            output.stdout,
            SandboxRuntimeReceipt,
        )
        assert isinstance(receipt, SandboxRuntimeReceipt)
        if receipt.runtime_manifest_sha256 != self._config.expected_runtime_sha256:
            raise EvidenceError("runtime_manifest_hash_mismatch")
        if receipt.model_identity_sha256 != canonical_sha256(self._config.model):
            raise EvidenceError("managed_model_identity_mismatch")
        if (
            receipt.actual_ca_sha256 != self._config.expected_tls_ca_sha256
            or receipt.sandbox_probe_source_sha256
            != self._config.expected_sandbox_probe_source_sha256
            or receipt.transport_identity_sha256
            != self._config.expected_transport_identity_sha256
            or not receipt.mount_read_only
            or not receipt.statvfs_read_only
            or not receipt.runtime_write_denied
        ):
            raise EvidenceError("sandbox_runtime_identity_mismatch")
        return receipt

    def _challenge(self, label: str) -> str:
        return hmac.new(
            self._config.evidence_key,
            CHALLENGE_DOMAIN + self._attempt_nonce + label.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()[:32]

    def _containment_probes(self, sandbox_identity: str) -> list[Observation]:
        observations: list[Observation] = []
        probes = (
            (
                FixedCommand.UNLISTED_HOST,
                Probe.UNLISTED_HOST_DENIED,
                lambda challenge: f"{challenge}.github.com",
                STAGED_PYTHON,
                lambda challenge: None,
            ),
            (
                FixedCommand.WRONG_TOOL_ROUTE,
                Probe.WRONG_TOOL_ROUTE_DENIED,
                lambda challenge: "vital-relay.internal",
                STAGED_PYTHON,
                lambda challenge: WRONG_TOOL_PATH_PREFIX + challenge,
            ),
        )
        for command, observation_name, host_factory, binary, path_factory in probes:
            challenge = self._challenge(command.value)
            cursor = self._capture_ocsf_cursor()
            output = self._checked_command(
                command,
                input_bytes=canonical_json_bytes({"challenge": challenge}),
                timeout_seconds=60.0,
            )
            receipt = parse_sandbox_receipt(output.stdout, SandboxAttemptReceipt)
            assert isinstance(receipt, SandboxAttemptReceipt)
            if (
                receipt.probe != command.value
                or receipt.challenge_sha256
                != hashlib.sha256(challenge.encode()).hexdigest()
            ):
                raise EvidenceError("sandbox_attempt_receipt_mismatch")
            denial = self._wait_for_denial(
                cursor,
                sandbox_identity=sandbox_identity,
                challenge=challenge,
                process_pid=receipt.process_pid,
                binary=binary,
                host=host_factory(challenge),
                path=path_factory(challenge),
            )
            observations.append(
                Observation(
                    name=observation_name,
                    content_sha256=canonical_sha256(
                        {
                            "attempt": receipt.model_dump(mode="json"),
                            "denial": denial.model_dump(mode="json"),
                        }
                    ),
                    count=1,
                )
            )

        protected = self._checked_command(FixedCommand.PROTECTED_FILE)
        protected_receipt = parse_sandbox_receipt(
            protected.stdout,
            SandboxAttemptReceipt,
        )
        assert isinstance(protected_receipt, SandboxAttemptReceipt)
        if (
            protected_receipt.probe != "protected_file"
            or protected_receipt.client_outcome != "permission_denied"
        ):
            raise EvidenceError("protected_file_not_denied")
        observations.append(
            Observation(
                name=Probe.PROTECTED_FILE_DENIED,
                content_sha256=canonical_sha256(protected_receipt),
                count=1,
            )
        )

        self._checked_command(FixedCommand.UNLISTED_BINARY_PRESENT)
        challenge = self._challenge(FixedCommand.UNLISTED_BINARY_NETWORK.value)
        cursor = self._capture_ocsf_cursor()
        denied = self._checked_command(
            FixedCommand.UNLISTED_BINARY_NETWORK,
            input_bytes=canonical_json_bytes({"challenge": challenge}),
            timeout_seconds=60.0,
        )
        receipt = parse_sandbox_receipt(denied.stdout, SandboxAttemptReceipt)
        assert isinstance(receipt, SandboxAttemptReceipt)
        if (
            receipt.probe != "unlisted_binary"
            or receipt.challenge_sha256
            != hashlib.sha256(challenge.encode()).hexdigest()
        ):
            raise EvidenceError("sandbox_attempt_receipt_mismatch")
        denial = self._wait_for_denial(
            cursor,
            sandbox_identity=sandbox_identity,
            challenge=challenge,
            process_pid=receipt.process_pid,
            host="inference.local",
            binary=UNLISTED_BINARY,
        )
        observations.append(
            Observation(
                name=Probe.UNLISTED_BINARY_DENIED,
                content_sha256=canonical_sha256(
                    {
                        "attempt": receipt.model_dump(mode="json"),
                        "denial": denial.model_dump(mode="json"),
                    }
                ),
                count=1,
            )
        )
        return observations

    def _wait_for_denial(
        self,
        cursor: OcsfCursorReceipt,
        *,
        sandbox_identity: str,
        challenge: str,
        process_pid: int,
        binary: str,
        host: str,
        openshell_version: str | None = None,
        path: str | None = None,
    ) -> OcsfDeltaReceipt:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            events = self._read_ocsf_delta(cursor)
            try:
                return assert_correlated_ocsf_denial(
                    events,
                    sandbox_identity=sandbox_identity,
                    challenge=challenge,
                    process_pid=process_pid,
                    binary=binary,
                    host=host,
                    schema_version=self._config.expected_ocsf_schema_version,
                    vendor=self._config.expected_ocsf_vendor,
                    openshell_version=(
                        openshell_version
                        or self._config.expected_openshell_version
                    ),
                    path=path,
                )
            except EvidenceError as exc:
                if exc.code != "openshell_correlated_denial_count_invalid":
                    raise
            time.sleep(0.05)
        raise EvidenceError("openshell_correlated_denial_missing")

    def _product_and_retry_probe(self, sandbox_identity: str) -> list[Observation]:
        cursor = self._capture_ocsf_cursor()
        response = self._post_run(self._config.product_run_id)
        if response.status_code != 201:
            raise EvidenceError("product_run_not_created")
        record = _parse_run_record(response.content)
        self._assert_terminal_product_record(record)
        activity_hash, activity_count, _ = self._wait_for_product_ocsf(
            cursor, sandbox_identity=sandbox_identity
        )

        retry_cursor = self._capture_ocsf_cursor()
        retry = self._post_run(self._config.product_run_id)
        if retry.status_code != 200:
            raise EvidenceError("exact_retry_failed")
        retried_record = _parse_run_record(retry.content)
        if canonical_json_bytes(retried_record) != canonical_json_bytes(record):
            raise EvidenceError("exact_retry_record_changed")
        retry_deadline = time.monotonic() + 2.0
        retry_events: tuple[dict[str, object], ...] = ()
        while time.monotonic() < retry_deadline:
            retry_events = self._read_ocsf_delta(retry_cursor)
            if self._relevant_worker_events(retry_events, sandbox_identity):
                break
            time.sleep(0.05)
        if self._relevant_worker_events(retry_events, sandbox_identity):
            raise EvidenceError("exact_retry_reinvoked_model")

        audit_digest, audit_count = self._assert_host_audit_correlation(record)
        return [
            Observation(
                name=Probe.PRODUCT_WORKER_TOOL_PROXY,
                content_sha256=canonical_sha256(
                    {
                        "activity": activity_hash,
                        "record": canonical_sha256(record),
                        "request": self._require_authority().product_request_sha256,
                    }
                ),
                count=activity_count,
            ),
            Observation(
                name=Probe.EXACT_RETRY_NO_INFERENCE,
                content_sha256=canonical_sha256(retried_record),
                count=0,
            ),
            Observation(
                name=Probe.HOST_AUDIT_CORRELATED,
                content_sha256=audit_digest,
                count=audit_count,
            ),
        ]

    def _wait_for_product_ocsf(
        self,
        cursor: OcsfCursorReceipt,
        *,
        sandbox_identity: str,
    ) -> tuple[str, int, int]:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            events = self._read_ocsf_delta(cursor)
            try:
                return self._assert_product_ocsf(
                    events, sandbox_identity=sandbox_identity
                )
            except EvidenceError as exc:
                if exc.code not in {
                    "product_worker_launch_correlation_invalid",
                    "product_worker_route_correlation_invalid",
                }:
                    raise
            time.sleep(0.05)
        raise EvidenceError("product_worker_ocsf_missing")

    def _relevant_worker_events(
        self,
        events: Sequence[Mapping[str, object]],
        sandbox_identity: str,
    ) -> tuple[Mapping[str, object], ...]:
        relevant: list[Mapping[str, object]] = []
        for event in events:
            metadata = event.get("metadata")
            product = metadata.get("product") if isinstance(metadata, Mapping) else None
            if (
                not isinstance(metadata, Mapping)
                or metadata.get("uid") != sandbox_identity
                or not isinstance(product, Mapping)
                or product.get("version")
                != self._config.expected_openshell_version
            ):
                continue
            process = event.get("process")
            if (
                event.get("class_uid") == 1007
                and event.get("activity_name") == "Launch"
                and isinstance(process, Mapping)
                and process.get("name") == NEMOCLAW_MANAGED_EXEC_LAUNCHER
            ):
                relevant.append(event)
                continue
            destination = _ocsf_destination(event)
            if destination in {
                ("inference.local", 443),
                ("vital-relay.internal", 8443),
            }:
                relevant.append(event)
        return tuple(relevant)

    def _assert_product_ocsf(
        self,
        events: Sequence[Mapping[str, object]],
        *,
        sandbox_identity: str,
    ) -> tuple[str, int, int]:
        relevant = self._relevant_worker_events(events, sandbox_identity)
        launches = []
        for event in relevant:
            process = event.get("process")
            if (
                event.get("class_uid") == 1007
                and event.get("activity_name") == "Launch"
                and event.get("action") == "Allowed"
                and event.get("status") == "Success"
                and isinstance(process, Mapping)
                and process.get("name") == NEMOCLAW_MANAGED_EXEC_LAUNCHER
                and isinstance(process.get("pid"), int)
            ):
                launches.append(event)
        if len(launches) != 1:
            raise EvidenceError("product_worker_launch_correlation_invalid")
        process = launches[0]["process"]
        assert isinstance(process, Mapping)
        worker_pid = process["pid"]
        assert isinstance(worker_pid, int)
        allowed_connects = {
            _ocsf_destination(event)
            for event in relevant
            if event.get("class_uid") == 4001
            and event.get("action") == "Allowed"
            and _ocsf_actor(event) == (STAGED_PYTHON, worker_pid)
        }
        http_routes: set[tuple[str, str, str]] = set()
        for event in relevant:
            request = event.get("http_request")
            url = request.get("url") if isinstance(request, Mapping) else None
            if (
                event.get("class_uid") == 4002
                and event.get("action") == "Allowed"
                and _ocsf_actor(event) == (STAGED_PYTHON, worker_pid)
                and isinstance(request, Mapping)
                and isinstance(url, Mapping)
                and isinstance(request.get("http_method"), str)
                and isinstance(url.get("hostname"), str)
                and isinstance(url.get("path"), str)
            ):
                http_routes.add(
                    (request["http_method"], url["hostname"], url["path"])
                )
        if not {
            ("inference.local", 443),
            ("vital-relay.internal", 8443),
        }.issubset(allowed_connects) or not {
            ("POST", "inference.local", "/v1/chat/completions"),
            ("POST", "vital-relay.internal", TOOL_PROXY_PATH),
        }.issubset(http_routes):
            raise EvidenceError("product_worker_route_correlation_invalid")
        return canonical_sha256(relevant), len(relevant), worker_pid

    def _assert_terminal_product_record(self, record: AgentRunRecord) -> None:
        if (
            record.run_id != self._config.product_run_id
            or record.incident_id != self._config.incident_id
            or record.incident_state_version
            != self._config.incident_state_version
            or record.model_id != self._config.model
            or record.sandbox is not SandboxKind.NEMOCLAW
            or record.status.value != AgentRunStatus.COMPLETED.value
            or not record.tool_trace
            or record.objective != "coordinate_emergency_response"
        ):
            raise EvidenceError("product_run_failed")
        self._assert_record_authority(record)
        if any(
            trace.evidence_source is not ToolTraceEvidenceSource.HOST_PROXY_AUDIT
            or trace.arguments
            or trace.result is not None
            or trace.request_sha256 is None
            for trace in record.tool_trace
        ):
            raise EvidenceError("product_run_evidence_invalid")

    def _assert_host_audit_correlation(
        self,
        record: AgentRunRecord,
    ) -> tuple[str, int]:
        audit = PostgresAppendOnlyToolAudit(
            self._sessions,
            self._config.scope_id,
        ).for_run(record.run_id)
        expected = host_audit_trace(
            audit,
            AgentRunEvidenceContext(
                scope_id=str(self._config.scope_id),
                run_id=record.run_id,
                incident_id=record.incident_id,
                state_version=record.incident_state_version,
                policy_sha256=record.policy.sha256,
            ),
        )
        if expected != record.tool_trace:
            raise EvidenceError("host_audit_correlation_failed")
        return canonical_sha256(
            [item.model_dump(mode="json") for item in audit]
        ), len(audit)

    def _capability_denial_probes(self) -> list[Observation]:
        record = self._load_run(self._config.product_run_id)
        now = datetime.now(UTC)
        authority = ToolCapabilityAuthority(self._config.capability_key)
        valid = authority.issue(
            run_id=record.run_id,
            scope_id=str(self._config.scope_id),
            incident_id=record.incident_id,
            state_version=record.incident_state_version,
            policy_sha256=record.policy_sha256,
            allowed_tools=(GET_INCIDENT,),
            issued_at=now,
            lifetime=timedelta(minutes=1),
        )
        expired = authority.issue(
            run_id=record.run_id,
            scope_id=str(self._config.scope_id),
            incident_id=record.incident_id,
            state_version=record.incident_state_version,
            policy_sha256=record.policy_sha256,
            allowed_tools=(GET_INCIDENT,),
            issued_at=now - timedelta(minutes=2),
            lifetime=timedelta(minutes=1),
        )
        wrong_scope = authority.issue(
            run_id=record.run_id,
            scope_id="cross-scope-live-evidence",
            incident_id=record.incident_id,
            state_version=record.incident_state_version,
            policy_sha256=record.policy_sha256,
            allowed_tools=(GET_INCIDENT,),
            issued_at=now,
            lifetime=timedelta(minutes=1),
        )
        revoked_policy_hash = hashlib.sha256(
            b"vital-relay:revoked-policy-probe"
        ).hexdigest()
        revoked_policy = authority.issue(
            run_id=record.run_id,
            scope_id=str(self._config.scope_id),
            incident_id=record.incident_id,
            state_version=record.incident_state_version,
            policy_sha256=revoked_policy_hash,
            allowed_tools=(GET_INCIDENT,),
            issued_at=now,
            lifetime=timedelta(minutes=1),
        )
        base_arguments = {
            "incident_id": str(record.incident_id),
            "expected_state_version": record.incident_state_version,
        }
        probes = (
            (
                Probe.EXPIRED_CAPABILITY_DENIED,
                expired.raw_capability.get_secret_value(),
                record.run_id,
                GET_INCIDENT,
                base_arguments,
                "expired_capability",
            ),
            (
                Probe.CROSS_RUN_CAPABILITY_DENIED,
                valid.raw_capability.get_secret_value(),
                _probe_uuid(record.run_id, "cross_run_requested_run"),
                GET_INCIDENT,
                base_arguments,
                "wrong_run",
            ),
            (
                Probe.CROSS_SCOPE_CAPABILITY_DENIED,
                wrong_scope.raw_capability.get_secret_value(),
                record.run_id,
                GET_INCIDENT,
                base_arguments,
                record.policy_sha256,
                "wrong_scope",
            ),
            (
                Probe.STALE_STATE_DENIED,
                valid.raw_capability.get_secret_value(),
                record.run_id,
                GET_INCIDENT,
                {
                    **base_arguments,
                    "expected_state_version": record.incident_state_version + 1,
                },
                record.policy_sha256,
                "stale_state",
            ),
            (
                Probe.REVOKED_POLICY_DENIED,
                revoked_policy.raw_capability.get_secret_value(),
                record.run_id,
                GET_INCIDENT,
                base_arguments,
                revoked_policy_hash,
                "policy_mismatch",
            ),
            (
                Probe.UNKNOWN_TOOL_DENIED,
                valid.raw_capability.get_secret_value(),
                record.run_id,
                "unregistered_probe",
                base_arguments,
                record.policy_sha256,
                "tool_not_registered",
            ),
        )
        observations: list[Observation] = []
        for item in probes:
            if len(item) == 6:
                name, token, requested_run, tool, arguments, expected_code = item
                policy_sha256 = record.policy_sha256
            else:
                (
                    name,
                    token,
                    requested_run,
                    tool,
                    arguments,
                    policy_sha256,
                    expected_code,
                ) = item
            invocation = ToolProxyInvocation(
                invocation_id=_probe_uuid(record.run_id, name.value),
                scope_id=str(self._config.scope_id),
                run_id=requested_run,
                incident_id=record.incident_id,
                policy_sha256=policy_sha256,
                tool_name=tool,
                arguments=arguments,
            )
            self._invoke_expected_denial(token, invocation, expected_code)
            audit_hash = self._assert_denial_audit(invocation, expected_code)
            observations.append(
                Observation(name=name, content_sha256=audit_hash, count=1)
            )
        return observations

    def _invoke_expected_denial(
        self,
        token: str,
        invocation: ToolProxyInvocation,
        expected_code: str,
    ) -> None:
        client = httpx.Client(
            verify=str(self._config.tls_ca_file),
            follow_redirects=False,
            trust_env=False,
            timeout=10.0,
        )
        try:
            with client.stream(
                "POST",
                TOOL_PROXY_URL,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    AGENT_CAPABILITY_HEADER: token,
                },
                content=invocation.model_dump_json().encode("utf-8"),
            ) as streamed:
                response = BoundedHTTPResponse(
                    status_code=streamed.status_code,
                    content=_bounded_http_body(streamed),
                )
        except httpx.HTTPError as exc:
            raise EvidenceError("tls_tool_proxy_unavailable") from exc
        finally:
            client.close()
        try:
            actual_code = json.loads(response.content)["detail"]["code"]
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("tool_proxy_denial_invalid") from exc
        if response.status_code == 200 or actual_code != expected_code:
            raise EvidenceError("tool_proxy_denial_mismatch")

    def _assert_denial_audit(
        self,
        invocation: ToolProxyInvocation,
        expected_code: str,
    ) -> str:
        with self._sessions() as session:
            rows = session.scalars(
                select(AgentToolProxyAuditRow).where(
                    AgentToolProxyAuditRow.scope_id == self._config.scope_id,
                    AgentToolProxyAuditRow.invocation_id == invocation.invocation_id,
                )
            ).all()
        if len(rows) != 1:
            raise EvidenceError("tool_proxy_denial_audit_missing")
        row = rows[0]
        if (
            row.status != "denied"
            or row.error_code != expected_code
            or row.result_sha256 is not None
            or row.requested_scope_id != invocation.scope_id
            or row.requested_run_id != invocation.run_id
            or row.requested_incident_id != invocation.incident_id
            or row.requested_policy_sha256 != invocation.policy_sha256
            or row.tool_name != invocation.tool_name
            or row.idempotency_key is not None
        ):
            raise EvidenceError("tool_proxy_denial_audit_invalid")
        return canonical_sha256(
            {
                "status": row.status,
                "error_code": row.error_code,
                "request_sha256": row.request_sha256,
                "granted": row.granted_run_id is not None,
            }
        )

    def _kill_lease_probe(
        self,
        sandbox_identity: str,
    ) -> tuple[Observation, str]:
        outcome: dict[str, object] = {}

        def invoke() -> None:
            try:
                response = self._post_run_raw(self._config.killed_run_id)
            except httpx.RemoteProtocolError:
                outcome["transport_outcome"] = "remote_protocol_closed"
                return
            except Exception:
                outcome["transport_outcome"] = "unexpected_transport_failure"
                return
            outcome["status_code"] = response.status_code

        cursor = self._capture_ocsf_cursor()
        thread = threading.Thread(target=invoke, name="live-evidence-kill-run")
        thread.start()
        row = self._wait_for_running_run(self._config.killed_run_id)
        self._orphan_authority_not_after = row.lease_expires_at
        worker_pid = self._wait_for_worker_launch(cursor, sandbox_identity)
        inspected = self._inspect_worker(worker_pid)
        self._owned_worker = inspected
        self._cleanup_checks.append("exact_worker_handle_inspected")
        self._backend.kill()
        self._cleanup_checks.append("crash_backend_exact_process_reaped")
        thread.join(timeout=30.0)
        if thread.is_alive():
            raise EvidenceError("killed_request_thread_unresolved")
        if outcome.get("transport_outcome") != "remote_protocol_closed":
            raise EvidenceError("killed_request_transport_outcome_invalid")

        self._backend.start()
        before_expiry = self._post_run(self._config.killed_run_id)
        if before_expiry.status_code != 409 or _error_code(before_expiry.content) != (
            "agent_run_in_progress"
        ):
            raise EvidenceError("run_reconciled_before_lease_expiry")

        _wait_until(row.lease_expires_at)
        after_expiry = self._post_run(self._config.killed_run_id)
        if after_expiry.status_code != 200:
            raise EvidenceError("expired_run_reconciliation_failed")
        terminal = _parse_run_record(after_expiry.content)
        if (
            terminal.status.value != AgentRunStatus.MANUAL_REQUIRED.value
            or terminal.failure_code is None
            or terminal.finished_at != terminal.lease_expires_at
        ):
            raise EvidenceError("expired_run_reconciliation_invalid")
        self._assert_record_authority(terminal)
        return (
            Observation(
                name=Probe.KILL_LEASE_RECONCILED,
                content_sha256=canonical_sha256(
                    {
                        "initiating_transport_outcome": "remote_protocol_closed",
                        "pre_expiry_status": before_expiry.status_code,
                        "terminal_status": terminal.status.value,
                        "failure_code": terminal.failure_code.value,
                        "reconciled_at_lease": True,
                        "request_sha256": (
                            self._require_authority().killed_request_sha256
                        ),
                        "worker_handle_sha256": inspected.process_handle_sha256,
                    }
                ),
                count=len(terminal.tool_trace),
            ),
            inspected.worker_transport_identity_sha256,
        )

    def _wait_for_running_run(self, run_id: UUID) -> AgentRunRow:
        deadline = time.monotonic() + RUN_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            with self._sessions() as session:
                row = session.scalar(
                    select(AgentRunRow).where(
                        AgentRunRow.scope_id == self._config.scope_id,
                        AgentRunRow.run_id == run_id,
                    )
                )
                if row is not None and row.status == "running":
                    self._assert_running_row(row, run_id)
                    session.expunge(row)
                    return row
            time.sleep(0.01)
        raise EvidenceError("kill_run_did_not_start")

    def _wait_for_worker_launch(
        self,
        cursor: OcsfCursorReceipt,
        sandbox_identity: str,
    ) -> int:
        deadline = time.monotonic() + RUN_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            events = self._read_ocsf_delta(cursor)
            launches: list[int] = []
            for event in events:
                metadata = event.get("metadata")
                product = (
                    metadata.get("product")
                    if isinstance(metadata, Mapping)
                    else None
                )
                process = event.get("process")
                if (
                    isinstance(metadata, Mapping)
                    and metadata.get("uid") == sandbox_identity
                    and isinstance(product, Mapping)
                    and product.get("version")
                    == self._config.expected_openshell_version
                    and event.get("class_uid") == 1007
                    and event.get("activity_name") == "Launch"
                    and event.get("action") == "Allowed"
                    and event.get("status") == "Success"
                    and isinstance(process, Mapping)
                    and process.get("name") == NEMOCLAW_MANAGED_EXEC_LAUNCHER
                    and isinstance(process.get("pid"), int)
                ):
                    launches.append(process["pid"])
            if len(launches) > 1:
                raise EvidenceError("worker_launch_correlation_invalid")
            if launches:
                return launches[0]
            time.sleep(0.05)
        raise EvidenceError("staged_worker_not_observed")

    def _inspect_worker(self, process_pid: int) -> SandboxProcessReceipt:
        request = canonical_json_bytes(
            {
                "action": "inspect",
                "expected_ca_sha256": self._config.expected_tls_ca_sha256,
                "expected_command_sha256": self._config.expected_worker_command_sha256,
                "expected_start_time_ticks": 0,
                "expected_transport_identity_sha256": (
                    self._config.expected_worker_transport_identity_sha256
                ),
                "process_pid": process_pid,
            }
        )
        output = self._checked_command(
            FixedCommand.EXACT_PROCESS,
            input_bytes=request,
            timeout_seconds=60.0,
        )
        receipt = parse_sandbox_receipt(output.stdout, SandboxProcessReceipt)
        assert isinstance(receipt, SandboxProcessReceipt)
        if (
            receipt.action != "inspect"
            or receipt.process_pid != process_pid
            or receipt.absent_after_termination
            or receipt.actual_ca_sha256 != self._config.expected_tls_ca_sha256
            or receipt.worker_transport_identity_sha256
            != self._config.expected_worker_transport_identity_sha256
        ):
            raise EvidenceError("exact_worker_identity_invalid")
        return receipt

    def _assert_running_row(self, row: AgentRunRow, run_id: UUID) -> None:
        authority = self._require_authority()
        if (
            row.scope_id != self._config.scope_id
            or row.run_id != run_id
            or row.incident_id != self._config.incident_id
            or row.incident_state_version != self._config.incident_state_version
            or row.schema_version != 1
            or row.objective != "coordinate_emergency_response"
            or row.requested_by_account_id != authority.account_id
            or row.requested_by_session_id != authority.session_id
            or row.policy_id != authority.policy_id
            or row.policy_version != authority.policy_version
            or row.policy_sha256 != authority.policy_sha256
            or row.model_id != self._config.model
            or row.sandbox != SandboxKind.NEMOCLAW.value
            or row.status != "running"
            or row.requested_at < authority.absence_checked_at
            or row.created_at < authority.absence_checked_at
            or row.total_tool_calls != 0
            or row.mutating_tool_calls != 0
            or row.tool_trace != []
        ):
            raise EvidenceError("killed_run_binding_invalid")

    def _assert_record_authority(self, record: AgentRunRecord) -> None:
        authority = self._require_authority()
        if (
            record.incident_id != self._config.incident_id
            or record.incident_state_version != self._config.incident_state_version
            or record.requested_by_account_id != authority.account_id
            or record.requested_by_session_id != authority.session_id
            or record.policy.policy_id != authority.policy_id
            or record.policy.version != authority.policy_version
            or record.policy.sha256 != authority.policy_sha256
            or record.model_id != self._config.model
            or record.sandbox is not SandboxKind.NEMOCLAW
            or record.requested_at < authority.absence_checked_at
            or record.created_at < authority.absence_checked_at
        ):
            raise EvidenceError("agent_run_authority_binding_invalid")

    def _load_run(self, run_id: UUID) -> AgentRunRow:
        with self._sessions() as session:
            row = session.scalar(
                select(AgentRunRow).where(
                    AgentRunRow.scope_id == self._config.scope_id,
                    AgentRunRow.run_id == run_id,
                )
            )
            if row is None:
                raise EvidenceError("agent_run_missing")
            session.expunge(row)
            return row

    def _post_run(self, run_id: UUID) -> BoundedHTTPResponse:
        try:
            return self._post_run_raw(run_id)
        except httpx.HTTPError as exc:
            raise EvidenceError("dedicated_backend_request_failed") from exc

    def _post_run_raw(self, run_id: UUID) -> BoundedHTTPResponse:
        client = httpx.Client(
            timeout=330.0,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            with client.stream(
                "POST",
                (
                    f"{DEDICATED_API_ORIGIN}/v1/incidents/"
                    f"{self._config.incident_id}/agent-runs"
                ),
                headers={
                    "Authorization": f"Bearer {self._config.command_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "schema_version": 1,
                    "run_id": str(run_id),
                    "expected_state_version": self._config.incident_state_version,
                },
            ) as streamed:
                return BoundedHTTPResponse(
                    status_code=streamed.status_code,
                    content=_bounded_http_body(streamed),
                )
        finally:
            client.close()

    def _terminate_owned_worker(self) -> None:
        owned = self._owned_worker
        if owned is None:
            return
        request = canonical_json_bytes(
            {
                "action": "terminate",
                "expected_ca_sha256": self._config.expected_tls_ca_sha256,
                "expected_command_sha256": owned.exact_command_sha256,
                "expected_start_time_ticks": owned.start_time_ticks,
                "expected_transport_identity_sha256": (
                    owned.worker_transport_identity_sha256
                ),
                "process_pid": owned.process_pid,
            }
        )
        output = self._checked_command(
            FixedCommand.EXACT_PROCESS,
            input_bytes=request,
            timeout_seconds=60.0,
        )
        terminated = parse_sandbox_receipt(
            output.stdout, SandboxProcessReceipt
        )
        assert isinstance(terminated, SandboxProcessReceipt)
        if (
            terminated.action != "terminate"
            or terminated.process_handle_sha256 != owned.process_handle_sha256
            or not terminated.absent_after_termination
        ):
            raise EvidenceError("exact_worker_cleanup_unconfirmed")
        self._completed_worker_handle = owned.process_handle_sha256
        self._owned_worker = None
        self._orphan_authority_not_after = None
        self._cleanup_checks.append("exact_worker_handle_absent")

    def _last_worker_handle(self) -> str:
        if not hasattr(self, "_completed_worker_handle"):
            raise EvidenceError("worker_cleanup_handle_missing")
        value = self._completed_worker_handle
        if not isinstance(value, str):
            raise EvidenceError("worker_cleanup_handle_missing")
        return value


def _observation(name: str, raw: bytes, *, count: int | None = None) -> Observation:
    return Observation(
        name=name,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        count=count,
    )


def _parse_run_record(raw: bytes) -> AgentRunRecord:
    if not raw or len(raw) > MAX_HTTP_BODY_BYTES:
        raise EvidenceError("agent_run_response_invalid")
    try:
        return AgentRunRecord.model_validate_json(raw)
    except (UnicodeError, ValidationError) as exc:
        raise EvidenceError("agent_run_response_invalid") from exc


def _error_code(raw: bytes) -> str | None:
    if not raw or len(raw) > MAX_HTTP_BODY_BYTES:
        return None
    try:
        value = json.loads(raw)
        code = value["detail"]["code"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        return None
    return code if isinstance(code, str) else None


def _bounded_http_body(
    response: httpx.Response,
    *,
    maximum_bytes: int = MAX_HTTP_BODY_BYTES,
) -> bytes:
    raw_length = response.headers.get("content-length")
    if raw_length is not None:
        try:
            content_length = int(raw_length)
        except ValueError as exc:
            raise EvidenceError("http_response_invalid") from exc
        if content_length < 0 or content_length > maximum_bytes:
            raise EvidenceError("http_response_too_large")
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
        if len(body) > maximum_bytes:
            raise EvidenceError("http_response_too_large")
    return bytes(body)


def _wait_until(deadline: datetime) -> None:
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        raise EvidenceError("lease_deadline_invalid")
    normalized = deadline.astimezone(UTC)
    remaining = (normalized - datetime.now(UTC)).total_seconds()
    if remaining > 15 * 60 + 5:
        raise EvidenceError("lease_deadline_invalid")
    while remaining > 0:
        time.sleep(min(remaining, 1.0))
        remaining = (normalized - datetime.now(UTC)).total_seconds()


def main(argv: Sequence[str] | None = None) -> int:
    active_argv = tuple(sys.argv[1:] if argv is None else argv)
    if active_argv:
        artifact = failure_artifact(("fixed_argv_required",))
        sys.stdout.buffer.write(canonical_json_bytes(artifact) + b"\n")
        return 2

    blockers = configuration_blockers()
    if blockers:
        artifact = failure_artifact(blockers)
        sys.stdout.buffer.write(canonical_json_bytes(artifact) + b"\n")
        return 1

    attestor: NemoClawLiveAttestor | None = None
    artifact: LiveEvidenceArtifact | FailureArtifact
    try:
        config = LiveEvidenceConfig.from_environment()
        attestor = NemoClawLiveAttestor(config)
        artifact = attestor.run()
    except EvidenceError as exc:
        artifact = failure_artifact((exc.code,))
    except Exception:
        artifact = failure_artifact(("unexpected_live_harness_failure",))
    except BaseException:
        artifact = failure_artifact(("live_harness_interrupted",))
    finally:
        if attestor is not None:
            try:
                attestor.close()
            except Exception:
                # A cleanup failure must not be mistaken for passing evidence.
                artifact = failure_artifact(("cleanup_custody_unresolved",))
    sys.stdout.buffer.write(canonical_json_bytes(artifact) + b"\n")
    return 0 if artifact.outcome == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
