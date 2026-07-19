"""Canonical live-evidence collector for the Docker agent product path.

The collector is intentionally host-only.  It starts the real application,
which selects :class:`ProcessSandboxAgentRunner` and calls
``validate_startup()`` itself, then drives the command HTTP API.  Docker fault
containers are evidence of boundary handling only; they can never satisfy the
successful product-run probe.

No subprocess diagnostic, bearer value, capability, health payload,
coordinate, provider response, prompt, or model prose is serialised.  Every
published document is canonical JSON named by the SHA-256 of its content.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import hashlib
import hmac
import http.client
from importlib import metadata as importlib_metadata
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from vital_relay.agent.capabilities import ToolCapabilityAuthority
from vital_relay.agent.contracts import (
    AgentRunResult,
    SHA256_PATTERN,
    SandboxKind,
)
from vital_relay.agent.policy import load_pinned_policy_snapshot
from vital_relay.agent.sandbox import (
    DOCKER_IMAGE_ID_PATTERN,
    DOCKER_TOOL_UPSTREAM_HOST,
    DOCKER_TOOL_UPSTREAM_PORT,
    DOCKER_VLLM_UPSTREAM_HOST,
    DOCKER_VLLM_UPSTREAM_PORT,
    DockerRuntimeEvidenceSnapshot,
    SandboxCleanupEvidence,
    SandboxOutputLimitExceeded,
    SandboxStartupEvidence,
    _communicate_bounded,
    _kill_process_group,
    _read_reviewed_docker_asset,
)
from vital_relay.agent.sandbox_wire import (
    DOCKER_INFERENCE_BASE_URL,
    DOCKER_TOOL_PROXY_ENDPOINT,
    SANDBOX_WIRE_SCHEMA_VERSION,
)
from vital_relay.agent.tool_contracts import GET_INCIDENT
from vital_relay.evolution.hashing import canonical_json_bytes


SCHEMA_VERSION = 1
LANE = "docker_containment_live_evidence"
SIGNATURE_DOMAIN = b"vital-relay/docker-live-evidence/v1\x00"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
EVIDENCE_ROOT = PROJECT_ROOT / "infrastructure/docker-agent/evidence"
PROBE_MANIFEST_PATH = EVIDENCE_ROOT / "probe_manifest.json"
CONTAINER_PROBE_PATH = EVIDENCE_ROOT / "container_probe.py"
FAULT_PROBE_PATH = EVIDENCE_ROOT / "fault_probe.py"
DEFAULT_OUTPUT_DIRECTORY = (
    Path(tempfile.gettempdir()) / "vital-relay-docker-live-evidence"
)
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_DOCKER_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_EXACT_CLEANUP_RETRIES = 2
API_HOST = "127.0.0.1"
API_BIND_HOST = "0.0.0.0"
API_PORT = 8000
VLLM_HOST = "127.0.0.1"
VLLM_PORT = 8001
EVIDENCE_COMMAND_ACCESS_ENV = "VITAL_RELAY_EVIDENCE_COMMAND_ACCESS_TOKEN"
EVIDENCE_INCIDENT_ID_ENV = "VITAL_RELAY_EVIDENCE_INCIDENT_ID"
EVIDENCE_INCIDENT_STATE_VERSION_ENV = (
    "VITAL_RELAY_EVIDENCE_INCIDENT_STATE_VERSION"
)
EVIDENCE_ISSUER_ENV = "VITAL_RELAY_EVIDENCE_ISSUER"
EVIDENCE_KEY_ID_ENV = "VITAL_RELAY_EVIDENCE_KEY_ID"
EVIDENCE_SIGNING_KEY_ENV = "VITAL_RELAY_EVIDENCE_SIGNING_KEY"
EVIDENCE_ENVIRONMENT_ENV = "VITAL_RELAY_EVIDENCE_ENVIRONMENT"
DOCKER_SENSITIVE_ENVIRONMENT_NAMES = frozenset(
    {
        "VITAL_RELAY_DATABASE_URL",
        "VITAL_RELAY_DEVICE_TOKEN",
        "VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY",
        "VITAL_RELAY_DOCKER_VLLM_API_KEY",
        "VITAL_RELAY_MAPBOX_ACCESS_TOKEN",
        "VITAL_RELAY_NOTIFICATION_TOKEN_ENCRYPTION_KEY",
    }
)
REQUIRED_PROBE_IDS = (
    "reviewed_probe_assets",
    "external_prerequisites",
    "startup_validation",
    "runtime_image_identity",
    "inspect_containment",
    "protected_path_denials",
    "internal_network_routes",
    "command_api_run",
    "exact_retry",
    "tool_denials",
    "host_audit_correlation",
    "crash_result",
    "timeout_result",
    "malformed_result",
    "lease_kill_custody",
    "exact_project_cleanup",
    "privacy_scan",
)
_FORBIDDEN_EVIDENCE_KEYS = frozenset(
    {
        "raw",
        "secret",
        "token",
        "password",
        "credential",
        "capability",
        "prompt",
        "reasoning",
        "chain_of_thought",
        "coordinate",
        "coordinates",
        "latitude",
        "longitude",
        "health_data",
        "provider_data",
        "subprocess",
        "stdout",
        "stderr",
        "diagnostic",
        "diagnostics",
        "payload",
        "headers",
    }
)
_PROBE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class EvidenceStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class EvidenceClass(StrEnum):
    TRUSTED_LIVE = "trusted_live"
    TEST_ONLY = "test_only"


class ExternalBlocker(StrEnum):
    DOCKER_CLI_UNAVAILABLE = "docker_cli_unavailable"
    DOCKER_DAEMON_UNAVAILABLE = "docker_daemon_unavailable"
    MODEL_CONFIGURATION_ABSENT = "model_configuration_absent"
    MODEL_UPSTREAM_UNAVAILABLE = "model_upstream_unavailable"
    MODEL_IDENTITY_MISMATCH = "model_identity_mismatch"
    POSTGRES_CONFIGURATION_ABSENT = "postgres_configuration_absent"
    POSTGRES_UNAVAILABLE = "postgres_unavailable"
    ENROLLED_COMMAND_SESSION_ABSENT = "enrolled_command_session_absent"
    INCIDENT_PREREQUISITE_ABSENT = "incident_prerequisite_absent"
    AGENT_CONFIGURATION_ABSENT = "agent_configuration_absent"
    API_PORT_UNAVAILABLE = "api_port_unavailable"


class ProbeEvidence(BaseModel):
    """One closed, privacy-safe assertion set."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    probe_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    status: EvidenceStatus
    assertions: tuple[str, ...] = Field(min_length=1, max_length=64)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("assertions")
    @classmethod
    def validate_assertions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or tuple(sorted(value)) != value:
            raise ValueError("evidence assertions must be unique and sorted")
        for assertion in value:
            if _PROBE_ID_PATTERN.fullmatch(assertion) is None:
                raise ValueError("invalid evidence assertion")
        return value

    @field_validator("attributes")
    @classmethod
    def validate_attributes(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_sensitive_evidence(value)
        return value


class EvidenceAttempt(BaseModel):
    """Unique trusted-host challenge for one non-replayable attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: UUID
    challenge: str = Field(pattern=SHA256_PATTERN)
    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def normalize_started_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attempt start must be timezone-aware")
        return value.astimezone(UTC)


class EvidenceAuthenticity(BaseModel):
    """Trusted-host authentication metadata covering the complete content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm: Literal["hmac-sha256"] = "hmac-sha256"
    issuer: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    key_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
    signature: str = Field(pattern=SHA256_PATTERN)


class EvidenceContent(BaseModel):
    """The exact object covered by ``content_sha256``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    lane: Literal[LANE]
    attempt: EvidenceAttempt
    generated_at: datetime
    status: EvidenceStatus
    probe_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    blockers: tuple[ExternalBlocker, ...]
    bindings: dict[str, JsonValue]
    probes: tuple[ProbeEvidence, ...]

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence time must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("blockers")
    @classmethod
    def validate_blockers(
        cls,
        value: tuple[ExternalBlocker, ...],
    ) -> tuple[ExternalBlocker, ...]:
        if tuple(sorted(value, key=str)) != value or len(set(value)) != len(value):
            raise ValueError("blockers must be unique and sorted")
        return value

    @field_validator("probes")
    @classmethod
    def validate_probes(
        cls,
        value: tuple[ProbeEvidence, ...],
    ) -> tuple[ProbeEvidence, ...]:
        identifiers = tuple(item.probe_id for item in value)
        if identifiers != REQUIRED_PROBE_IDS:
            raise ValueError("live evidence is missing a required fixed probe")
        return value

    @field_validator("bindings")
    @classmethod
    def validate_bindings(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _reject_sensitive_evidence(value)
        return value


class DockerLiveEvidenceBundle(BaseModel):
    """Canonical content-addressed evidence envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_class: EvidenceClass
    content_sha256: str = Field(pattern=SHA256_PATTERN)
    content: EvidenceContent
    authenticity: EvidenceAuthenticity | None = None

    @classmethod
    def create_test_only(
        cls,
        content: EvidenceContent,
    ) -> DockerLiveEvidenceBundle:
        digest = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        return cls(
            evidence_class=EvidenceClass.TEST_ONLY,
            content_sha256=digest,
            content=content,
            authenticity=None,
        )

    @classmethod
    def create_trusted_live(
        cls,
        content: EvidenceContent,
        *,
        signing_key: bytes,
        issuer: str,
        key_id: str,
    ) -> DockerLiveEvidenceBundle:
        _validate_evidence_signing_key(signing_key)
        digest = hashlib.sha256(canonical_json_bytes(content)).hexdigest()
        signature = hmac.new(
            signing_key,
            _signature_input(
                content_sha256=digest,
                content=content,
                issuer=issuer,
                key_id=key_id,
            ),
            hashlib.sha256,
        ).hexdigest()
        return cls(
            evidence_class=EvidenceClass.TRUSTED_LIVE,
            content_sha256=digest,
            content=content,
            authenticity=EvidenceAuthenticity(
                issuer=issuer,
                key_id=key_id,
                signature=signature,
            ),
        )

    def canonical_bytes(self) -> bytes:
        raw = canonical_json_bytes(self)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise ValueError("live evidence document exceeds its size limit")
        return raw + b"\n"

    def verify_structure(self) -> None:
        expected = hashlib.sha256(canonical_json_bytes(self.content)).hexdigest()
        if expected != self.content_sha256:
            raise ValueError("live evidence content address is invalid")
        expected_status = _bundle_status(self.content.probes)
        if expected_status is not self.content.status:
            raise ValueError("live evidence aggregate status is invalid")
        if bool(self.content.blockers) != any(
            probe.status is EvidenceStatus.BLOCKED for probe in self.content.probes
        ):
            raise ValueError("live evidence blockers do not match probe status")
        if self.evidence_class is EvidenceClass.TEST_ONLY:
            if self.authenticity is not None:
                raise ValueError("test-only evidence must remain unsigned")
        elif self.authenticity is None:
            raise ValueError("trusted live evidence requires host authenticity")
        else:
            _require_trusted_source_bindings(self.content)
        if self.content.status is EvidenceStatus.PASSED:
            _require_complete_live_bindings(self.content)
        _reject_sensitive_evidence(self.model_dump(mode="json"))

    def verify_trusted_live(
        self,
        *,
        signing_key: bytes,
        issuer: str,
        key_id: str,
    ) -> None:
        self.verify_structure()
        _validate_evidence_signing_key(signing_key)
        authenticity = self.authenticity
        if (
            self.evidence_class is not EvidenceClass.TRUSTED_LIVE
            or authenticity is None
            or authenticity.issuer != issuer
            or authenticity.key_id != key_id
        ):
            raise ValueError("evidence trusted-host identity is invalid")
        expected = hmac.new(
            signing_key,
            _signature_input(
                content_sha256=self.content_sha256,
                content=self.content,
                issuer=issuer,
                key_id=key_id,
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, authenticity.signature):
            raise ValueError("evidence trusted-host signature is invalid")


class ProbeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    probes: tuple[str, ...]
    assets: dict[str, str]

    @field_validator("probes")
    @classmethod
    def require_fixed_probes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != REQUIRED_PROBE_IDS:
            raise ValueError("probe manifest does not declare the fixed probe set")
        return value

    @field_validator("assets")
    @classmethod
    def validate_assets(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"container_probe.py", "fault_probe.py"}:
            raise ValueError("probe manifest assets are incomplete")
        if any(
            re.fullmatch(SHA256_PATTERN, digest) is None
            for digest in value.values()
        ):
            raise ValueError("probe manifest asset digest is invalid")
        return value


class ImageInspectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    image_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=SHA256_PATTERN)
    graph_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_user: str
    layer_count: int = Field(ge=1)
    sensitive_environment_absent: bool


class ContainerInspectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime
    assertions: tuple[str, ...]
    network_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("container creation time must be timezone-aware")
        return value.astimezone(UTC)


class DockerCommandError(RuntimeError):
    """A bounded Docker operation failed; diagnostics are deliberately absent."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"Docker evidence operation failed: {operation}")


class DockerCommandTimeout(DockerCommandError):
    """A reviewed Docker operation exceeded its fixed host deadline."""


class DockerLiveEvidenceCleanupError(RuntimeError):
    """Exact project cleanup failed while the retained owner remains retryable."""

    def __init__(
        self,
        *,
        retry: Callable[[], SandboxCleanupEvidence],
        observe: Callable[[], object],
        expected_project: str | None,
    ) -> None:
        self._retry = retry
        self._observe = observe
        self.project_sha256 = (
            hashlib.sha256(expected_project.encode("utf-8")).hexdigest()
            if expected_project
            else None
        )
        super().__init__("Docker live-evidence cleanup is incomplete")

    def retry_cleanup(self) -> SandboxCleanupEvidence:
        """Retry only the exact retained application-owned cleanup operation."""

        return self._retry()

    def observed_cleanup(self) -> SandboxCleanupEvidence | None:
        """Return only the latest host-authored cleanup evidence, if present."""

        observed = self._observe()
        return observed if isinstance(observed, SandboxCleanupEvidence) else None


@dataclass(frozen=True, slots=True)
class PreServerEvidenceState:
    probe_results: dict[str, ProbeEvidence]
    image_evidence: tuple[ImageInspectionEvidence, ...]
    internal_network: str
    gateway_container_ids: dict[str, str]
    containment: dict[str, Any]


class DockerDriver:
    """Run reviewed Docker argv with bounded stdout and no stderr capture."""

    def __init__(self, executable: str = "docker") -> None:
        self.executable = executable
        self.environment = _docker_cli_environment()

    def run(
        self,
        arguments: Sequence[str],
        *,
        input_bytes: bytes = b"",
        timeout_seconds: float = 30.0,
        operation: str,
        accepted_returncodes: frozenset[int] = frozenset({0}),
    ) -> bytes:
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(
                (self.executable, *arguments),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.environment,
                start_new_session=True,
            )
            output = _communicate_bounded(
                process,
                input_bytes=input_bytes,
                timeout_seconds=timeout_seconds,
                maximum_stdout_bytes=MAX_DOCKER_OUTPUT_BYTES,
            )
        except subprocess.TimeoutExpired as exc:
            if process is not None:
                _kill_process_group(process)
            raise DockerCommandTimeout(operation) from exc
        except (OSError, SandboxOutputLimitExceeded) as exc:
            if process is not None:
                _kill_process_group(process)
            raise DockerCommandError(operation) from exc
        if process.returncode not in accepted_returncodes:
            raise DockerCommandError(operation)
        return output


@dataclass(slots=True)
class ExactContainerCleanupCustody:
    """Retain one exact-name removal action until host absence is proved."""

    driver: DockerDriver
    name: str
    project: str
    operation: str
    attempt_count: int = 0
    last_evidence: SandboxCleanupEvidence | None = None

    def retry_cleanup(self) -> SandboxCleanupEvidence:
        self.attempt_count += 1
        completed_checks: list[str] = []
        failed_checks: list[str] = []
        try:
            self.driver.run(
                ("rm", "--force", self.name),
                operation=self.operation,
                accepted_returncodes=frozenset({0, 1}),
            )
        except BaseException:
            failed_checks.append("exact_container_rm")
        else:
            completed_checks.append("exact_container_rm")
        try:
            _require_container_absent(self.driver, self.name)
        except BaseException:
            failed_checks.append("exact_container_absence")
            unresolved_checks = ("exact_container_cleanup",)
        else:
            completed_checks.extend(("exact_container_absence", "cleanup_complete"))
            unresolved_checks = ()
        evidence = SandboxCleanupEvidence(
            sandbox=SandboxKind.DOCKER,
            checked_at=datetime.now(UTC),
            completed_checks=tuple(completed_checks),
            failed_checks=tuple(failed_checks),
            unresolved_checks=unresolved_checks,
            project_name=self.project,
            attempt_count=self.attempt_count,
            already_closed=self.attempt_count > 1,
        )
        self.last_evidence = evidence
        if unresolved_checks:
            raise DockerCommandError(self.operation)
        return evidence


def canonical_bundle_bytes(bundle: DockerLiveEvidenceBundle) -> bytes:
    """Verify and return the one permitted byte representation."""

    bundle.verify_structure()
    return bundle.canonical_bytes()


def load_probe_manifest(
    path: Path = PROBE_MANIFEST_PATH,
) -> tuple[ProbeManifest, str]:
    raw = _read_fixed_asset(path, maximum_bytes=32_768)
    try:
        parsed = json.loads(raw)
        manifest = ProbeManifest.model_validate(parsed)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("fixed probe manifest is invalid") from exc
    if canonical_json_bytes(parsed) != raw.rstrip(b"\n"):
        raise ValueError("fixed probe manifest is not canonical JSON")
    for name, expected in manifest.assets.items():
        observed = hashlib.sha256(
            _read_fixed_asset(EVIDENCE_ROOT / name, maximum_bytes=128_000)
        ).hexdigest()
        if observed != expected:
            raise ValueError("fixed probe asset digest changed")
    return manifest, hashlib.sha256(raw.rstrip(b"\n")).hexdigest()


def parse_image_inspection(
    value: bytes,
    *,
    expected_image_id: str,
) -> ImageInspectionEvidence:
    """Reduce raw ``docker image inspect`` output to safe content identities."""

    try:
        payload = json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Docker image inspection is not JSON") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise ValueError("Docker image inspection must contain one image")
    image = payload[0]
    image_id = image.get("Id")
    config = image.get("Config")
    rootfs = image.get("RootFS")
    if (
        image_id != expected_image_id
        or DOCKER_IMAGE_ID_PATTERN.fullmatch(str(image_id)) is None
        or not isinstance(config, dict)
        or not isinstance(rootfs, dict)
        or rootfs.get("Type") != "layers"
        or not isinstance(rootfs.get("Layers"), list)
        or not rootfs["Layers"]
        or any(
            DOCKER_IMAGE_ID_PATTERN.fullmatch(str(item)) is None
            for item in rootfs["Layers"]
        )
    ):
        raise ValueError("Docker image inspection identity is invalid")
    environment = config.get("Env") or []
    if not isinstance(environment, list) or any(
        not isinstance(item, str) for item in environment
    ):
        raise ValueError("Docker image environment is invalid")
    environment_names = {item.partition("=")[0] for item in environment}
    safe_graph = {
        "architecture": image.get("Architecture"),
        "os": image.get("Os"),
        "rootfs": rootfs,
        "user": config.get("User") or "",
        "entrypoint": config.get("Entrypoint") or [],
    }
    return ImageInspectionEvidence(
        image_id=image_id,
        config_sha256=image_id.removeprefix("sha256:"),
        graph_sha256=hashlib.sha256(canonical_json_bytes(safe_graph)).hexdigest(),
        runtime_user=str(config.get("User") or ""),
        layer_count=len(rootfs["Layers"]),
        sensitive_environment_absent=not bool(
            environment_names & DOCKER_SENSITIVE_ENVIRONMENT_NAMES
        ),
    )


def parse_container_inspection(
    value: bytes,
    *,
    expected_image_id: str,
    expected_network: str,
    expected_name: str | None = None,
    expected_labels: Mapping[str, str] | None = None,
) -> ContainerInspectionEvidence:
    """Validate every live worker-clone containment assertion."""

    try:
        payload = json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Docker container inspection is not JSON") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise ValueError("Docker container inspection must contain one container")
    container = payload[0]
    config = container.get("Config")
    host = container.get("HostConfig")
    network_settings = container.get("NetworkSettings")
    mounts = container.get("Mounts")
    try:
        container_id = str(container["Id"])
        created_at = datetime.fromisoformat(str(container["Created"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("Docker container identity is invalid") from exc
    if re.fullmatch(r"[0-9a-f]{64}", container_id) is None:
        raise ValueError("Docker container identity is invalid")
    if (
        not isinstance(config, dict)
        or not isinstance(host, dict)
        or not isinstance(network_settings, dict)
    ):
        raise ValueError("Docker container inspection is incomplete")
    networks = network_settings.get("Networks")
    ulimits = host.get("Ulimits")
    labels = config.get("Labels") or {}
    expected_label_values = dict(expected_labels or {})
    expected_ulimit = any(
        isinstance(item, dict)
        and item.get("Name") == "nofile"
        and item.get("Soft") == 256
        and item.get("Hard") == 256
        for item in (ulimits or [])
    )
    assertions = {
        "all_capabilities_dropped": host.get("CapDrop") == ["ALL"],
        "cpu_limit": host.get("NanoCpus") == 1_000_000_000,
        "exact_image_id": container.get("Image") == expected_image_id,
        "file_descriptor_limit": expected_ulimit,
        "internal_network_only": (
            isinstance(networks, dict) and set(networks) == {expected_network}
        ),
        "memory_limit": host.get("Memory") == 1_073_741_824,
        "no_bind_mounts": not host.get("Binds"),
        "no_mounts": mounts in (None, []),
        "no_new_privileges": "no-new-privileges" in (host.get("SecurityOpt") or []),
        "no_published_ports": (
            not host.get("PortBindings") and not config.get("ExposedPorts")
        ),
        "non_root_identity": config.get("User") == "65532:65532",
        "pid_limit": host.get("PidsLimit") == 96,
        "read_only_root": host.get("ReadonlyRootfs") is True,
        "runtime_init": host.get("Init") is True,
        "tmpfs_is_bounded": host.get("Tmpfs") == {
            "/tmp": "rw,noexec,nosuid,nodev,size=64m"
        },
    }
    if expected_name is not None:
        assertions["exact_container_name"] = (
            container.get("Name") == f"/{expected_name}"
        )
    if expected_label_values:
        assertions["exact_evidence_labels"] = (
            isinstance(labels, dict)
            and all(
                labels.get(name) == expected
                for name, expected in expected_label_values.items()
            )
        )
    failed = tuple(name for name, passed in assertions.items() if not passed)
    if failed:
        raise ValueError("Docker containment assertion failed")
    return ContainerInspectionEvidence(
        container_id=container_id,
        created_at=created_at,
        assertions=tuple(sorted(assertions)),
        network_sha256=hashlib.sha256(expected_network.encode("utf-8")).hexdigest(),
    )


def parse_container_probe(
    value: bytes,
    *,
    mode: Literal["containment", "tool"],
) -> dict[str, JsonValue]:
    """Parse only closed output from a real fixed probe container."""

    try:
        payload = json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixed container probe returned malformed JSON") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("mode") != mode
    ):
        raise ValueError("fixed container probe returned the wrong schema")
    if mode == "containment":
        checks = payload.get("checks")
        if (
            set(payload) != {"schema_version", "mode", "checks"}
            or not isinstance(checks, dict)
            or not checks
            or any(value is not True for value in checks.values())
            or any(_PROBE_ID_PATTERN.fullmatch(str(name)) is None for name in checks)
        ):
            raise ValueError("one or more fixed containment probes failed")
        return {"passed_assertions": len(checks)}
    results = payload.get("results")
    if set(payload) != {"schema_version", "mode", "results"} or not isinstance(
        results,
        dict,
    ):
        raise ValueError("fixed tool probe returned invalid results")
    reduced: dict[str, JsonValue] = {}
    for probe_id, result in sorted(results.items()):
        if (
            _PROBE_ID_PATTERN.fullmatch(str(probe_id)) is None
            or not isinstance(result, dict)
            or set(result) != {"status", "code"}
        ):
            raise ValueError("fixed tool probe result is invalid")
        status = result.get("status")
        code = result.get("code")
        if not isinstance(status, int) or not isinstance(code, str):
            raise ValueError("fixed tool probe result is invalid")
        reduced[str(probe_id)] = {"status": status, "code": code}
    return reduced


def classify_fault_probe(
    *,
    mode: Literal["crash", "timeout", "malformed"],
    returncode: int | None,
    output: bytes,
    timed_out: bool,
) -> str:
    """Classify observed Docker faults without accepting fixture success."""

    if mode == "crash" and not timed_out and returncode == 23 and not output:
        return "crash_observed"
    if mode == "timeout" and timed_out and returncode is None and not output:
        return "timeout_observed"
    if mode == "malformed" and not timed_out and returncode == 0:
        try:
            AgentRunResult.model_validate_json(output)
        except Exception:
            return "malformed_result_rejected"
    raise ValueError("fixed Docker fault probe did not produce its reviewed fault")


def publish_live_bundle(
    bundle: DockerLiveEvidenceBundle,
    output_directory: Path,
    *,
    signing_key: bytes,
    issuer: str,
    key_id: str,
) -> Path:
    """Publish one immutable digest-named bundle without replacing evidence."""

    bundle.verify_trusted_live(
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )
    raw = canonical_bundle_bytes(bundle)
    _validate_output_directory_path(output_directory)
    output_directory.mkdir(mode=0o700, exist_ok=True)
    _validate_output_directory_path(output_directory)
    target = output_directory / f"{bundle.content_sha256}.json"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o400)
    except FileExistsError:
        try:
            metadata = target.lstat()
        except OSError as exc:
            raise ValueError(
                "content-addressed evidence target is unreadable"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or target.read_bytes() != raw
        ):
            raise ValueError("content-addressed evidence target was replaced")
        return target
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


def verify_live_bundle_file(
    path: Path,
    *,
    signing_key: bytes,
    issuer: str,
    key_id: str,
) -> DockerLiveEvidenceBundle:
    """Verify canonical bytes, content address, issuer/key ID, and host HMAC."""

    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or not 0 < metadata.st_size <= MAX_DOCUMENT_BYTES
        ):
            raise ValueError("live evidence file is unsafe")
        raw = path.read_bytes()
        parsed = json.loads(raw)
        bundle = DockerLiveEvidenceBundle.model_validate(parsed)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("live evidence file is invalid") from exc
    if raw != bundle.canonical_bytes():
        raise ValueError("live evidence file is not canonical JSON")
    bundle.verify_trusted_live(
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )
    if path.name != f"{bundle.content_sha256}.json":
        raise ValueError("live evidence filename does not match its content")
    return bundle


def inspect_external_blockers(
    environment: Mapping[str, str] | None = None,
) -> tuple[ExternalBlocker, ...]:
    """Check every external prerequisite and return only closed blocker codes."""

    active = dict(os.environ if environment is None else environment)
    blockers: set[ExternalBlocker] = set()
    docker = shutil.which("docker", path=active.get("PATH"))
    if docker is None:
        blockers.add(ExternalBlocker.DOCKER_CLI_UNAVAILABLE)
    else:
        driver = DockerDriver(docker)
        try:
            driver.run(
                ("version", "--format", "{{.Server.Version}}"),
                timeout_seconds=5,
                operation="docker_daemon_preflight",
            )
        except DockerCommandError:
            blockers.add(ExternalBlocker.DOCKER_DAEMON_UNAVAILABLE)

    model = active.get("VITAL_RELAY_VLLM_MODEL")
    api_key = active.get("VITAL_RELAY_DOCKER_VLLM_API_KEY")
    provenance = (
        active.get("VITAL_RELAY_VLLM_MODEL_REVISION"),
        active.get("VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256"),
    )
    if not model or not api_key or not all(provenance):
        blockers.add(ExternalBlocker.MODEL_CONFIGURATION_ABSENT)
    else:
        model_availability = _model_availability(model=model, api_key=api_key)
        if model_availability == "unavailable":
            blockers.add(ExternalBlocker.MODEL_UPSTREAM_UNAVAILABLE)
        elif model_availability == "mismatch":
            blockers.add(ExternalBlocker.MODEL_IDENTITY_MISMATCH)

    database_url = active.get("VITAL_RELAY_DATABASE_URL")
    scope_id = active.get("VITAL_RELAY_DEMO_SCOPE_ID")
    if not database_url or not scope_id:
        blockers.add(ExternalBlocker.POSTGRES_CONFIGURATION_ABSENT)
    elif not _postgres_is_available(database_url, scope_id):
        blockers.add(ExternalBlocker.POSTGRES_UNAVAILABLE)

    command_access = active.get(EVIDENCE_COMMAND_ACCESS_ENV)
    if (
        not command_access
        or re.fullmatch(r"[A-Za-z0-9_-]{43,256}", command_access) is None
        or (
            bool(database_url and scope_id)
            and ExternalBlocker.POSTGRES_UNAVAILABLE not in blockers
            and not _enrolled_command_session_is_available(
                database_url=database_url,
                scope_id=scope_id,
                command_access=command_access,
            )
        )
    ):
        blockers.add(ExternalBlocker.ENROLLED_COMMAND_SESSION_ABSENT)
    if not _valid_incident_configuration(active) or (
        bool(database_url and scope_id)
        and ExternalBlocker.POSTGRES_UNAVAILABLE not in blockers
        and not _incident_is_available(
            database_url=database_url,
            scope_id=scope_id,
            incident_id=active.get(EVIDENCE_INCIDENT_ID_ENV, ""),
            expected_state_version=active.get(
                EVIDENCE_INCIDENT_STATE_VERSION_ENV,
                "",
            ),
        )
    ):
        blockers.add(ExternalBlocker.INCIDENT_PREREQUISITE_ABSENT)
    if not _valid_agent_configuration(active) or (
        bool(database_url and scope_id)
        and ExternalBlocker.POSTGRES_UNAVAILABLE not in blockers
        and not _active_policy_matches(
            environment=active,
            database_url=database_url,
            scope_id=scope_id,
        )
    ):
        blockers.add(ExternalBlocker.AGENT_CONFIGURATION_ABSENT)
    if not _port_is_available(API_HOST, API_PORT):
        blockers.add(ExternalBlocker.API_PORT_UNAVAILABLE)
    return tuple(sorted(blockers, key=str))


def build_blocked_bundle(
    *,
    manifest_sha256: str,
    blockers: tuple[ExternalBlocker, ...],
    attempt: EvidenceAttempt,
    bindings: dict[str, JsonValue],
    signing_key: bytes,
    issuer: str,
    key_id: str,
    generated_at: datetime | None = None,
) -> DockerLiveEvidenceBundle:
    """Build truthful non-passing evidence when live prerequisites are absent."""

    if not blockers:
        raise ValueError("a blocked bundle requires at least one blocker")
    probes: list[ProbeEvidence] = []
    for probe_id in REQUIRED_PROBE_IDS:
        if probe_id in {"reviewed_probe_assets", "privacy_scan"}:
            probes.append(
                _probe(
                    probe_id,
                    EvidenceStatus.PASSED,
                    f"{probe_id}_verified",
                )
            )
        else:
            probes.append(
                _probe(
                    probe_id,
                    EvidenceStatus.BLOCKED,
                    "external_prerequisite_required",
                )
            )
    content = EvidenceContent(
        schema_version=SCHEMA_VERSION,
        lane=LANE,
        attempt=attempt,
        generated_at=generated_at or datetime.now(UTC),
        status=EvidenceStatus.BLOCKED,
        probe_manifest_sha256=manifest_sha256,
        blockers=blockers,
        bindings=bindings,
        probes=tuple(probes),
    )
    return DockerLiveEvidenceBundle.create_trusted_live(
        content,
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )


def build_test_only_blocked_bundle(
    *,
    manifest_sha256: str,
    blockers: tuple[ExternalBlocker, ...],
    generated_at: datetime | None = None,
) -> DockerLiveEvidenceBundle:
    """Construct permanently unsigned parser-fixture material."""

    attempt = EvidenceAttempt(
        attempt_id=UUID("00000000-0000-4000-8000-000000000001"),
        challenge="0" * 64,
        started_at=generated_at or datetime(2026, 7, 19, tzinfo=UTC),
    )
    probes = tuple(
        _probe(
            probe_id,
            (
                EvidenceStatus.PASSED
                if probe_id in {"reviewed_probe_assets", "privacy_scan"}
                else EvidenceStatus.BLOCKED
            ),
            (
                f"{probe_id}_verified"
                if probe_id in {"reviewed_probe_assets", "privacy_scan"}
                else "external_prerequisite_required"
            ),
        )
        for probe_id in REQUIRED_PROBE_IDS
    )
    content = EvidenceContent(
        schema_version=SCHEMA_VERSION,
        lane=LANE,
        attempt=attempt,
        generated_at=generated_at or datetime.now(UTC),
        status=EvidenceStatus.BLOCKED,
        probe_manifest_sha256=manifest_sha256,
        blockers=blockers,
        bindings={"fixture_class": "parser_only"},
        probes=probes,
    )
    return DockerLiveEvidenceBundle.create_test_only(content)


async def collect_live_bundle(
    *,
    manifest_sha256: str,
    attempt: EvidenceAttempt,
    signing_key: bytes,
    issuer: str,
    key_id: str,
    output_directory: Path,
    environment: Mapping[str, str] | None = None,
) -> DockerLiveEvidenceBundle:
    """Drive the live application and every required fixed Docker probe."""

    active = dict(os.environ if environment is None else environment)
    _validate_core_live_invariants(
        environment=active,
        output_directory=output_directory,
        attempt=attempt,
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )
    source_bindings = _source_bindings(manifest_sha256)
    blockers = inspect_external_blockers(active)
    if blockers:
        return build_blocked_bundle(
            manifest_sha256=manifest_sha256,
            blockers=blockers,
            attempt=attempt,
            bindings=source_bindings,
            signing_key=signing_key,
            issuer=issuer,
            key_id=key_id,
        )
    # Imports stay behind preflight so a missing external runtime always yields
    # a truthful bundle instead of an import-time pseudo-success.
    import uvicorn

    from vital_relay.main import create_app

    database_url = active["VITAL_RELAY_DATABASE_URL"]
    scope_id = UUID(active["VITAL_RELAY_DEMO_SCOPE_ID"])
    incident_id = UUID(active[EVIDENCE_INCIDENT_ID_ENV])
    state_version = int(active[EVIDENCE_INCIDENT_STATE_VERSION_ENV])
    command_access = active[EVIDENCE_COMMAND_ACCESS_ENV]
    driver = DockerDriver(shutil.which("docker", path=active.get("PATH")) or "docker")
    try:
        app = create_app(
            database_url=database_url,
            demo_scope_id=scope_id,
            agent_enabled=True,
        )
    except BaseException as construction_failure:
        cleanup_error = _construction_cleanup_error(
            failure=construction_failure,
            driver=driver,
        )
        if cleanup_error is not None:
            raise cleanup_error from construction_failure
        raise
    runtime: DockerRuntimeEvidenceSnapshot | None = None
    try:
        startup = getattr(app.state, "agent_sandbox_startup_evidence", None)
        runtime = _require_docker_runtime_snapshot(startup)
    except BaseException as failure:
        await _cleanup_failed_pre_server_attempt(
            app=app,
            driver=driver,
            runtime=runtime,
            failure=failure,
        )
        raise AssertionError("pre-server cleanup returned unexpectedly")
    pre_server = await _collect_pre_server_with_custody(
        app=app,
        driver=driver,
        attempt=attempt,
        runtime=runtime,
        startup=startup,
        active=active,
    )
    try:
        config = uvicorn.Config(
            app,
            host=API_BIND_HOST,
            port=API_PORT,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        server = uvicorn.Server(config)
        server_task = asyncio.create_task(server.serve())
    except BaseException as failure:
        await _cleanup_failed_pre_server_attempt(
            app=app,
            driver=driver,
            runtime=runtime,
            failure=failure,
        )
        raise AssertionError("pre-server cleanup returned unexpectedly")
    probe_results = pre_server.probe_results
    image_evidence = pre_server.image_evidence
    internal_network = pre_server.internal_network
    gateway_container_ids = pre_server.gateway_container_ids
    containment = pre_server.containment
    operation_failure: BaseException | None = None
    try:
        await _wait_for_server(server, server_task)
        await _verify_enrolled_context(
            command_access=command_access,
            incident_id=incident_id,
            expected_state_version=state_version,
        )
        success_run = uuid4()
        event_since = int(time.time()) - 1
        first_status, first_record = await asyncio.to_thread(
            _command_api_run,
            incident_id,
            success_run,
            state_version,
            command_access,
        )
        _require_terminal_record(
            first_status,
            first_record,
            success=True,
            expected_run_id=success_run,
            expected_incident_id=incident_id,
            expected_state_version=state_version,
        )
        success_host_record = _host_run_evidence(
            database_url=database_url,
            scope_id=scope_id,
            attempt=attempt,
            expected_record=first_record,
        )
        probe_results["command_api_run"] = _probe(
            "command_api_run",
            EvidenceStatus.PASSED,
            "real_command_api_run_completed",
            attributes={
                **_public_record_summary(first_record),
                **success_host_record,
            },
        )
        retry_status, retry_record = await asyncio.to_thread(
            _command_api_run,
            incident_id,
            success_run,
            state_version,
            command_access,
        )
        event_until = int(time.time()) + 1
        first_record_sha256 = hashlib.sha256(
            canonical_json_bytes(first_record)
        ).hexdigest()
        retry_record_sha256 = hashlib.sha256(
            canonical_json_bytes(retry_record)
        ).hexdigest()
        if (
            retry_status != 200
            or not hmac.compare_digest(first_record_sha256, retry_record_sha256)
        ):
            raise ValueError("exact command retry did not return the stored result")
        retry_host_record = _host_run_evidence(
            database_url=database_url,
            scope_id=scope_id,
            attempt=attempt,
            expected_record=retry_record,
        )
        if retry_host_record != success_host_record:
            raise ValueError("exact command retry changed the durable host record")
        success_worker_ids = _worker_create_event_ids(
            driver,
            attempt=attempt,
            runtime=runtime,
            run_id=success_run,
            since=event_since,
            until=event_until,
        )
        if len(success_worker_ids) != 1:
            raise ValueError(
                "exact retry produced an unexpected worker invocation count"
            )
        probe_results["exact_retry"] = _probe(
            "exact_retry",
            EvidenceStatus.PASSED,
            "terminal_retry_reused_stored_result",
            attributes={
                "worker_container_ids": list(success_worker_ids),
                "worker_invocation_count": len(success_worker_ids),
                "host_corroborated": True,
                "terminal_record_sha256": first_record_sha256,
            },
        )

        crash_record, audit_evidence, crash_worker_id = (
            await _run_crash_and_tool_probes(
            driver=driver,
            attempt=attempt,
            runtime=runtime,
            active=active,
            database_url=database_url,
            scope_id=scope_id,
            incident_id=incident_id,
            state_version=state_version,
            command_access=command_access,
            )
        )
        probe_results["tool_denials"] = _probe(
            "tool_denials",
            EvidenceStatus.PASSED,
            "fixed_tool_denials_verified",
        )
        probe_results["host_audit_correlation"] = _probe(
            "host_audit_correlation",
            EvidenceStatus.PASSED,
            "host_audit_rows_correlated",
            attributes=audit_evidence,
        )
        probe_results["crash_result"] = _probe(
            "crash_result",
            EvidenceStatus.PASSED,
            "killed_worker_failed_closed",
            attributes={
                **_public_record_summary(crash_record),
                **_host_run_evidence(
                    database_url=database_url,
                    scope_id=scope_id,
                    attempt=attempt,
                    expected_record=crash_record,
                ),
                "worker_container_id": crash_worker_id,
            },
        )

        timeout_record, timeout_worker_id = await _run_timeout_probe(
            driver=driver,
            attempt=attempt,
            runtime=runtime,
            incident_id=incident_id,
            state_version=state_version,
            command_access=command_access,
        )
        probe_results["timeout_result"] = _probe(
            "timeout_result",
            EvidenceStatus.PASSED,
            "timed_out_worker_failed_closed",
            attributes={
                **_public_record_summary(timeout_record),
                **_host_run_evidence(
                    database_url=database_url,
                    scope_id=scope_id,
                    attempt=attempt,
                    expected_record=timeout_record,
                ),
                "worker_container_id": timeout_worker_id,
            },
        )
        malformed_classification, malformed_container_id = _run_fault_probe(
            driver,
            attempt=attempt,
            runtime=runtime,
            network=internal_network,
            mode="malformed",
        )
        probe_results["malformed_result"] = _probe(
            "malformed_result",
            EvidenceStatus.PASSED,
            malformed_classification,
            attributes={
                "host_corroborated": True,
                "worker_container_id": malformed_container_id,
            },
        )
        probe_results["lease_kill_custody"] = _probe(
            "lease_kill_custody",
            EvidenceStatus.PASSED,
            "exact_worker_kill_and_lease_settlement_verified",
        )
    except BaseException as failure:
        operation_failure = failure
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=30)
        except BaseException as shutdown_failure:
            if not server_task.done():
                server_task.cancel()
            if operation_failure is None:
                operation_failure = shutdown_failure
            else:
                operation_failure.add_note(
                    "API server shutdown also failed"
                )

    cleanup = _require_owned_cleanup(app=app, driver=driver, runtime=runtime)
    if operation_failure is not None:
        raise operation_failure
    probe_results["exact_project_cleanup"] = _probe(
        "exact_project_cleanup",
        EvidenceStatus.PASSED,
        "exact_compose_project_cleanup_verified",
        attributes={
            "cleanup_attempt_count": cleanup.attempt_count,
            "compose_project": runtime.compose_project,
            "host_corroborated": True,
        },
    )
    probe_results["privacy_scan"] = _probe(
        "privacy_scan",
        EvidenceStatus.PASSED,
        "privacy_safe_schema_verified",
    )
    bindings = _complete_live_bindings(
        source_bindings=source_bindings,
        active=active,
        driver=driver,
        attempt=attempt,
        runtime=runtime,
        image_evidence=image_evidence,
        gateway_container_ids=gateway_container_ids,
        containment_container_id=containment["inspection"].container_id,
        success_worker_ids=success_worker_ids,
        crash_worker_id=crash_worker_id,
        timeout_worker_id=timeout_worker_id,
        malformed_container_id=malformed_container_id,
        audit_evidence=audit_evidence,
        success_run_id=success_run,
        crash_run_id=UUID(str(crash_record["run_id"])),
        timeout_run_id=UUID(str(timeout_record["run_id"])),
    )
    ordered = tuple(probe_results[probe_id] for probe_id in REQUIRED_PROBE_IDS)
    content = EvidenceContent(
        schema_version=SCHEMA_VERSION,
        lane=LANE,
        attempt=attempt,
        generated_at=datetime.now(UTC),
        status=EvidenceStatus.PASSED,
        probe_manifest_sha256=manifest_sha256,
        blockers=(),
        bindings=bindings,
        probes=ordered,
    )
    bundle = DockerLiveEvidenceBundle.create_trusted_live(
        content,
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )
    bundle.verify_trusted_live(
        signing_key=signing_key,
        issuer=issuer,
        key_id=key_id,
    )
    return bundle


async def _collect_pre_server_with_custody(
    *,
    app: Any,
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    startup: SandboxStartupEvidence,
    active: Mapping[str, str],
) -> PreServerEvidenceState:
    try:
        return _collect_pre_server_evidence(
            driver=driver,
            attempt=attempt,
            runtime=runtime,
            startup=startup,
            active=active,
        )
    except BaseException as failure:
        await _cleanup_failed_pre_server_attempt(
            app=app,
            driver=driver,
            runtime=runtime,
            failure=failure,
        )
        raise AssertionError("pre-server cleanup returned unexpectedly")


def _collect_pre_server_evidence(
    *,
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    startup: SandboxStartupEvidence,
    active: Mapping[str, str],
) -> PreServerEvidenceState:
    probe_results: dict[str, ProbeEvidence] = {
        "reviewed_probe_assets": _probe(
            "reviewed_probe_assets",
            EvidenceStatus.PASSED,
            "reviewed_probe_assets_verified",
            attributes={"asset_count": 2},
        ),
        "startup_validation": _startup_probe(startup, runtime, attempt),
    }
    image_evidence = _inspect_images(driver, runtime)
    probe_results["runtime_image_identity"] = _probe(
        "runtime_image_identity",
        EvidenceStatus.PASSED,
        "immutable_images_verified",
        attributes={
            "compose_config_sha256": runtime.compose_config_sha256,
            "compose_project": runtime.compose_project,
            "image_count": len(image_evidence),
            "images": [
                item.model_dump(mode="json") for item in image_evidence
            ],
            "reviewed_snapshot_sha256": runtime.reviewed_snapshot_sha256,
            "reviewed_worker_tree_sha256": runtime.reviewed_worker_tree_sha256,
        },
    )
    internal_network = _resolve_internal_network(driver, runtime.compose_project)
    gateway_container_ids = {
        service: _gateway_container_id(
            driver,
            attempt=attempt,
            runtime=runtime,
            service=service,
        )
        for service in ("tool-proxy-gateway", "vllm-gateway")
    }
    containment = _run_container_probe(
        driver,
        attempt=attempt,
        runtime=runtime,
        network=internal_network,
        input_document={
            "mode": "containment",
            "model": active["VITAL_RELAY_VLLM_MODEL"],
        },
    )
    probe_results["inspect_containment"] = _probe(
        "inspect_containment",
        EvidenceStatus.PASSED,
        "docker_inspect_assertions_verified",
        attributes={
            "assertion_count": len(containment["inspection"].assertions),
            "assertions": list(containment["inspection"].assertions),
            "container_id": containment["inspection"].container_id,
            "host_corroborated": True,
        },
    )
    probe_results["protected_path_denials"] = _probe(
        "protected_path_denials",
        EvidenceStatus.PASSED,
        "protected_path_checks_host_corroborated",
        attributes={
            **containment["result"],
            "host_corroborated": True,
        },
    )
    probe_results["internal_network_routes"] = _probe(
        "internal_network_routes",
        EvidenceStatus.PASSED,
        "fixed_gateway_routes_verified",
        attributes={
            "host_corroborated": True,
            "network_sha256": containment["inspection"].network_sha256,
        },
    )
    probe_results["external_prerequisites"] = _probe(
        "external_prerequisites",
        EvidenceStatus.PASSED,
        "external_prerequisites_verified",
    )
    return PreServerEvidenceState(
        probe_results=probe_results,
        image_evidence=image_evidence,
        internal_network=internal_network,
        gateway_container_ids=gateway_container_ids,
        containment=containment,
    )


async def _cleanup_failed_pre_server_attempt(
    *,
    app: Any,
    driver: DockerDriver,
    runtime: DockerRuntimeEvidenceSnapshot | None,
    failure: BaseException,
) -> None:
    """Enter the sole app lifecycle long enough to close startup ownership."""

    try:
        async with app.router.lifespan_context(app):
            raise failure
    except BaseException:
        pass
    try:
        _require_owned_cleanup(app=app, driver=driver, runtime=runtime)
    except DockerLiveEvidenceCleanupError as cleanup_failure:
        raise cleanup_failure from failure
    raise failure


def _require_owned_cleanup(
    *,
    app: Any,
    driver: DockerDriver,
    runtime: DockerRuntimeEvidenceSnapshot | None,
) -> SandboxCleanupEvidence:
    cleanup = getattr(app.state, "agent_sandbox_cleanup_evidence", None)
    expected_project = (
        runtime.compose_project
        if runtime is not None
        else getattr(cleanup, "project_name", None)
    )
    try:
        if not isinstance(expected_project, str) or not expected_project:
            raise ValueError("exact Compose project identity is unavailable")
        _require_exact_cleanup(cleanup, expected_project)
        _require_project_absent(driver, expected_project)
    except BaseException as cleanup_failure:
        latest_evidence = _host_unresolved_cleanup_evidence(
            cleanup,
            expected_project=expected_project,
            minimum_attempt_count=1,
        )

        def retry_owned() -> SandboxCleanupEvidence:
            nonlocal latest_evidence
            try:
                candidate = _retry_owned_cleanup(
                    app=app,
                    driver=driver,
                    expected_project=expected_project,
                )
            except BaseException:
                latest_evidence = _host_unresolved_cleanup_evidence(
                    getattr(app.state, "agent_sandbox_cleanup_evidence", None),
                    expected_project=expected_project,
                    minimum_attempt_count=latest_evidence.attempt_count + 1,
                )
                raise
            latest_evidence = candidate
            return candidate

        raise DockerLiveEvidenceCleanupError(
            retry=retry_owned,
            observe=lambda: latest_evidence,
            expected_project=expected_project,
        ) from cleanup_failure
    return cleanup


def _host_unresolved_cleanup_evidence(
    cleanup: object,
    *,
    expected_project: object,
    minimum_attempt_count: int,
) -> SandboxCleanupEvidence:
    observed = cleanup if isinstance(cleanup, SandboxCleanupEvidence) else None
    completed = tuple(
        check
        for check in (observed.completed_checks if observed else ())
        if check != "cleanup_complete"
    )
    failed = tuple(
        dict.fromkeys(
            (
                *(observed.failed_checks if observed else ()),
                "exact_project_host_cleanup",
            )
        )
    )
    unresolved = tuple(
        dict.fromkeys(
            (
                *(observed.unresolved_checks if observed else ()),
                "exact_project_host_state",
            )
        )
    )
    project = (
        expected_project
        if isinstance(expected_project, str) and expected_project
        else getattr(observed, "project_name", None)
    )
    return SandboxCleanupEvidence(
        sandbox=SandboxKind.DOCKER,
        checked_at=datetime.now(UTC),
        completed_checks=completed,
        failed_checks=failed,
        unresolved_checks=unresolved,
        project_name=project if isinstance(project, str) else None,
        owned_temp_root_count=(observed.owned_temp_root_count if observed else 0),
        attempt_count=max(
            minimum_attempt_count,
            observed.attempt_count if observed else 1,
        ),
        already_closed=observed.already_closed if observed else False,
    )


def _retry_owned_cleanup(
    *,
    app: Any,
    driver: DockerDriver,
    expected_project: str | None,
) -> SandboxCleanupEvidence:
    retry = getattr(app.state, "agent_sandbox_cleanup_retry", None)
    if callable(retry):
        retry()
    cleanup = getattr(app.state, "agent_sandbox_cleanup_evidence", None)
    project = expected_project or getattr(cleanup, "project_name", None)
    if not isinstance(project, str) or not project:
        raise ValueError("exact Compose project identity is unavailable")
    if not callable(retry):
        _retry_exact_project_resources(driver, project)
    cleanup = getattr(app.state, "agent_sandbox_cleanup_evidence", None)
    _require_exact_cleanup(cleanup, project)
    _require_project_absent(driver, project)
    return cleanup


def _construction_cleanup_error(
    *,
    failure: BaseException,
    driver: DockerDriver,
) -> DockerLiveEvidenceCleanupError | None:
    """Lift construction-time runner custody before an app object exists."""

    retry = getattr(failure, "agent_sandbox_cleanup_retry", None)
    if not callable(retry):
        return None

    def observe() -> object:
        owner = getattr(retry, "__self__", None)
        return getattr(owner, "last_cleanup_evidence", None)

    observed = observe()
    expected_project = getattr(observed, "project_name", None)
    cleanup_state = {"exact_project_cleanup_required": False}
    return DockerLiveEvidenceCleanupError(
        retry=lambda: _retry_construction_cleanup(
            retry=retry,
            observe=observe,
            driver=driver,
            expected_project=expected_project,
            cleanup_state=cleanup_state,
        ),
        observe=observe,
        expected_project=(
            expected_project if isinstance(expected_project, str) else None
        ),
    )


def _retry_construction_cleanup(
    *,
    retry: Callable[[], object],
    observe: Callable[[], object],
    driver: DockerDriver,
    expected_project: object,
    cleanup_state: dict[str, bool],
) -> SandboxCleanupEvidence:
    candidate = retry()
    cleanup = (
        candidate
        if isinstance(candidate, SandboxCleanupEvidence)
        else observe()
    )
    project = expected_project or getattr(cleanup, "project_name", None)
    if not isinstance(project, str) or not project:
        raise ValueError("exact Compose project identity is unavailable")
    _require_exact_cleanup(cleanup, project)
    if cleanup_state["exact_project_cleanup_required"]:
        _retry_exact_project_resources(driver, project)
    try:
        _require_project_absent(driver, project)
    except BaseException:
        cleanup_state["exact_project_cleanup_required"] = True
        raise
    return cleanup


def _retry_exact_project_resources(driver: DockerDriver, project: str) -> None:
    """Mutate only resources carrying the exact retained Compose project label."""

    containers = _exact_project_resource_ids(
        driver,
        project=project,
        resource="containers",
    )
    for container_id in containers:
        driver.run(
            ("rm", "--force", container_id),
            operation="exact_project_container_cleanup",
            accepted_returncodes=frozenset({0, 1}),
        )
    networks = _exact_project_resource_ids(
        driver,
        project=project,
        resource="networks",
    )
    for network_id in networks:
        driver.run(
            ("network", "rm", network_id),
            operation="exact_project_network_cleanup",
            accepted_returncodes=frozenset({0, 1}),
        )


def _exact_project_resource_ids(
    driver: DockerDriver,
    *,
    project: str,
    resource: Literal["containers", "networks"],
) -> tuple[str, ...]:
    arguments = (
        (
            "ps",
            "--all",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        )
        if resource == "containers"
        else (
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        )
    )
    raw = driver.run(
        arguments,
        operation=f"exact_project_{resource}_cleanup_lookup",
    )
    identifiers = tuple(line for line in raw.decode("ascii").splitlines() if line)
    if any(re.fullmatch(r"[0-9a-f]{12,64}", item) is None for item in identifiers):
        raise ValueError("exact project cleanup lookup is invalid")
    return identifiers


def _startup_probe(
    startup: SandboxStartupEvidence,
    runtime: DockerRuntimeEvidenceSnapshot,
    attempt: EvidenceAttempt,
) -> ProbeEvidence:
    required = {
        "docker_daemon",
        "docker_build_agent",
        "docker_build_vllm_gateway",
        "docker_build_tool_proxy_gateway",
        "docker_compose_config",
        "docker_gateway_start",
        "docker_gateways",
    }
    if (
        startup.sandbox is not SandboxKind.DOCKER
        or startup.checked_at < attempt.started_at
        or not required.issubset(startup.completed_checks)
    ):
        raise ValueError("application startup did not complete Docker validation")
    if not hmac.compare_digest(
        runtime.reviewed_worker_tree_sha256,
        runtime.expected_worker_tree_sha256,
    ):
        raise ValueError("captured worker manifest does not match its policy pin")
    return _probe(
        "startup_validation",
        EvidenceStatus.PASSED,
        "process_sandbox_validate_startup_completed",
        attributes={
            "completed_check_count": len(startup.completed_checks),
            "image_count": len(runtime.image_ids),
        },
    )


def _inspect_images(
    driver: DockerDriver,
    runtime: DockerRuntimeEvidenceSnapshot,
) -> tuple[ImageInspectionEvidence, ...]:
    inspected: list[ImageInspectionEvidence] = []
    for _target, image_id in runtime.image_ids:
        raw = driver.run(
            ("image", "inspect", image_id),
            operation="image_inspect",
        )
        item = parse_image_inspection(raw, expected_image_id=image_id)
        if not item.sensitive_environment_absent:
            raise ValueError("reviewed image contains sensitive environment names")
        inspected.append(item)
    return tuple(inspected)


def _resolve_internal_network(driver: DockerDriver, project: str) -> str:
    raw = driver.run(
        (
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--filter",
            "label=com.docker.compose.network=agent-internal",
            "--format",
            "{{.Name}}",
        ),
        operation="internal_network_lookup",
    )
    try:
        names = tuple(line for line in raw.decode("utf-8").splitlines() if line)
    except UnicodeError as exc:
        raise ValueError("Docker internal network name is invalid") from exc
    if len(names) != 1 or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,199}", names[0]) is None:
        raise ValueError("exact internal Docker network is unavailable")
    inspected = driver.run(
        ("network", "inspect", names[0]),
        operation="internal_network_inspect",
    )
    _validate_internal_network(inspected, project=project, expected_name=names[0])
    return names[0]


def _validate_internal_network(
    value: bytes,
    *,
    project: str,
    expected_name: str,
) -> None:
    try:
        payload = json.loads(value)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Docker network inspection is invalid") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or not isinstance(payload[0], dict)
    ):
        raise ValueError("Docker network inspection is invalid")
    network = payload[0]
    labels = network.get("Labels") or {}
    if (
        network.get("Name") != expected_name
        or network.get("Internal") is not True
        or network.get("Attachable") is not False
        or not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.network") != "agent-internal"
    ):
        raise ValueError("Docker internal network assertion failed")


def _probe_container_create_command(
    *,
    name: str,
    image_id: str,
    network: str,
    script: str,
    attempt: EvidenceAttempt,
    project: str,
    purpose: str,
    arguments: Sequence[str] = (),
) -> tuple[str, ...]:
    return (
        "create",
        "--name",
        name,
        "--label",
        f"com.docker.compose.project={project}",
        "--label",
        f"vital-relay.evidence.attempt-id={attempt.attempt_id}",
        "--label",
        f"vital-relay.evidence.challenge={attempt.challenge}",
        "--label",
        f"vital-relay.evidence.purpose={purpose}",
        "--user",
        "65532:65532",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "96",
        "--memory",
        "1g",
        "--cpus",
        "1.0",
        "--ulimit",
        "nofile=256:256",
        "--init",
        "--stop-timeout",
        "2",
        "--network",
        network,
        "--entrypoint",
        "python",
        image_id,
        "-c",
        script,
        *arguments,
    )


def _evidence_container_labels(
    *,
    attempt: EvidenceAttempt,
    project: str,
    purpose: str,
) -> dict[str, str]:
    return {
        "com.docker.compose.project": project,
        "vital-relay.evidence.attempt-id": str(attempt.attempt_id),
        "vital-relay.evidence.challenge": attempt.challenge,
        "vital-relay.evidence.purpose": purpose,
    }


def _require_clean_probe_exit(driver: DockerDriver, name: str) -> None:
    """Corroborate untrusted probe output with host Docker state and diff."""

    raw_state = driver.run(
        ("inspect", "--format", "{{json .State}}", name),
        operation="probe_container_state",
    )
    try:
        state = json.loads(raw_state)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixed probe host state is invalid") from exc
    if (
        not isinstance(state, dict)
        or state.get("Status") != "exited"
        or state.get("ExitCode") != 0
        or state.get("OOMKilled") is not False
        or state.get("Dead") is not False
    ):
        raise ValueError("fixed probe did not exit cleanly on the host")
    filesystem_diff = driver.run(
        ("diff", name),
        operation="probe_container_filesystem_diff",
    )
    if filesystem_diff.strip():
        raise ValueError("fixed probe changed the immutable container filesystem")


def _run_container_probe(
    driver: DockerDriver,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    network: str,
    input_document: Mapping[str, object],
) -> dict[str, Any]:
    name = (
        f"{runtime.compose_project}-evidence-"
        f"{attempt.challenge[:12]}-{uuid4()}"
    )
    image_id = dict(runtime.image_ids)["agent"]
    purpose = str(input_document["mode"])
    expected_labels = _evidence_container_labels(
        attempt=attempt,
        project=runtime.compose_project,
        purpose=purpose,
    )
    script = _read_fixed_asset(
        CONTAINER_PROBE_PATH,
        maximum_bytes=128_000,
    ).decode("utf-8")
    try:
        driver.run(
            _probe_container_create_command(
                name=name,
                image_id=image_id,
                network=network,
                script=script,
                attempt=attempt,
                project=runtime.compose_project,
                purpose=purpose,
            ),
            operation="probe_container_create",
        )
        inspection = parse_container_inspection(
            driver.run(("inspect", name), operation="probe_container_inspect"),
            expected_image_id=image_id,
            expected_network=network,
            expected_name=name,
            expected_labels=expected_labels,
        )
        if inspection.created_at < attempt.started_at:
            raise ValueError("stale fixed probe container cannot satisfy evidence")
        raw = driver.run(
            ("start", "--attach", "--interactive", name),
            input_bytes=canonical_json_bytes(input_document),
            timeout_seconds=20,
            operation="probe_container_start",
        )
        mode = str(input_document["mode"])
        if mode not in {"containment", "tool"}:
            raise ValueError("unreviewed fixed probe mode")
        result = parse_container_probe(raw, mode=mode)  # type: ignore[arg-type]
        _require_clean_probe_exit(driver, name)
        return {"inspection": inspection, "result": result}
    finally:
        _require_exact_container_cleanup(
            driver=driver,
            name=name,
            project=runtime.compose_project,
            operation="probe_container_cleanup",
        )


async def _wait_for_server(server: Any, task: asyncio.Task[object]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if task.done():
            raise RuntimeError("live API server stopped during startup")
        if server.started:
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("live API server did not start")


async def _verify_enrolled_context(
    *,
    command_access: str,
    incident_id: UUID,
    expected_state_version: int,
) -> None:
    session_status, session = await asyncio.to_thread(
        _http_json,
        "GET",
        "/v1/persona-sessions/current",
        command_access,
        None,
    )
    if (
        session_status != 200
        or not isinstance(session, dict)
        or (session.get("account") or {}).get("persona") != "command"
    ):
        raise ValueError("enrolled command session is unavailable")
    incident_status, incident = await asyncio.to_thread(
        _http_json,
        "GET",
        f"/v1/incidents/{incident_id}",
        command_access,
        None,
    )
    if (
        incident_status != 200
        or not isinstance(incident, dict)
        or incident.get("state_version") != expected_state_version
        or incident.get("state") not in {"escalating", "response_active"}
    ):
        raise ValueError("configured evidence incident is not active")


def _command_api_run(
    incident_id: UUID,
    run_id: UUID,
    state_version: int,
    command_access: str,
) -> tuple[int, dict[str, Any]]:
    status, payload = _http_json(
        "POST",
        f"/v1/incidents/{incident_id}/agent-runs",
        command_access,
        {
            "schema_version": 1,
            "run_id": str(run_id),
            "expected_state_version": state_version,
        },
        timeout_seconds=330,
    )
    if not isinstance(payload, dict):
        raise ValueError("command API returned an invalid result")
    return status, payload


def _http_json(
    method: str,
    path: str,
    command_access: str,
    document: Mapping[str, object] | None,
    *,
    timeout_seconds: float = 10,
) -> tuple[int, JsonValue]:
    body = canonical_json_bytes(document) if document is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {command_access}",
        "Connection": "close",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection(API_HOST, API_PORT, timeout=timeout_seconds)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        connection.close()
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError("API evidence response exceeded its size limit")
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("API evidence response was not JSON") from exc
    return response.status, payload


def _require_terminal_record(
    status: int,
    record: Mapping[str, object],
    *,
    success: bool,
    expected_run_id: UUID,
    expected_incident_id: UUID,
    expected_state_version: int,
) -> None:
    from vital_relay.application.agent_control import AgentRunRecord

    if status not in {200, 201}:
        raise ValueError("command API run did not return a terminal record")
    try:
        validated = AgentRunRecord.model_validate(record)
    except Exception as exc:
        raise ValueError("command API returned an invalid durable record") from exc
    if (
        validated.run_id != expected_run_id
        or validated.incident_id != expected_incident_id
        or validated.incident_state_version != expected_state_version
    ):
        raise ValueError("command API terminal record has the wrong correlation")
    if validated.finished_at is None or (
        validated.finished_at > validated.lease_expires_at
    ):
        raise ValueError("command API terminal result escaped its durable lease")
    if success:
        if record.get("status") != "completed" or record.get("action_summary") is None:
            raise ValueError("real command API run did not complete")
    elif record.get("status") != "manual_required" or not record.get(
        "failure_code"
    ):
        raise ValueError("faulted command API run did not fail closed")


def _public_record_summary(record: Mapping[str, object]) -> dict[str, JsonValue]:
    """Retain only closed control-plane metadata, never model prose or IDs."""

    from vital_relay.application.agent_control import AgentRunRecord

    validated = AgentRunRecord.model_validate(record)
    return {
        "failure_code": str(record.get("failure_code") or "none"),
        "lease_bound_terminal": bool(
            validated.finished_at is not None
            and validated.finished_at <= validated.lease_expires_at
        ),
        "run_id": str(validated.run_id),
        "sandbox": str(record.get("sandbox") or "unknown"),
        "status": str(record.get("status") or "unknown"),
        "terminal_timestamps_present": bool(
            record.get("started_at") and record.get("finished_at")
        ),
    }


def _host_run_evidence(
    *,
    database_url: str,
    scope_id: UUID,
    attempt: EvidenceAttempt,
    expected_record: Mapping[str, object],
) -> dict[str, JsonValue]:
    """Corroborate an API result against the exact durable host row."""

    from sqlalchemy import select

    from vital_relay.application.agent_control import AgentRunRecord
    from vital_relay.persistence.database import create_postgres_engine
    from vital_relay.persistence.models import AgentRunRow

    expected = AgentRunRecord.model_validate(expected_record)
    engine = create_postgres_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                select(
                    AgentRunRow.run_id,
                    AgentRunRow.incident_id,
                    AgentRunRow.incident_state_version,
                    AgentRunRow.created_at,
                    AgentRunRow.lease_expires_at,
                    AgentRunRow.model_id,
                    AgentRunRow.sandbox,
                    AgentRunRow.status,
                    AgentRunRow.started_at,
                    AgentRunRow.finished_at,
                    AgentRunRow.failure_code,
                ).where(
                    AgentRunRow.scope_id == scope_id,
                    AgentRunRow.run_id == expected.run_id,
                )
            ).one_or_none()
    finally:
        engine.dispose()
    if row is None:
        raise ValueError("durable host run record is unavailable")
    host_summary: dict[str, JsonValue] = {
        "run_id": str(row.run_id),
        "incident_id": str(row.incident_id),
        "incident_state_version": row.incident_state_version,
        "created_at": row.created_at.astimezone(UTC).isoformat(),
        "lease_expires_at": row.lease_expires_at.astimezone(UTC).isoformat(),
        "model_id": row.model_id,
        "sandbox": row.sandbox,
        "status": row.status,
        "started_at": row.started_at.astimezone(UTC).isoformat(),
        "finished_at": row.finished_at.astimezone(UTC).isoformat(),
        "failure_code": row.failure_code or "none",
    }
    expected_summary: dict[str, JsonValue] = {
        "run_id": str(expected.run_id),
        "incident_id": str(expected.incident_id),
        "incident_state_version": expected.incident_state_version,
        "created_at": expected.created_at.isoformat(),
        "lease_expires_at": expected.lease_expires_at.isoformat(),
        "model_id": expected.model_id,
        "sandbox": expected.sandbox.value,
        "status": expected.status.value,
        "started_at": expected.started_at.isoformat(),
        "finished_at": expected.finished_at.isoformat(),
        "failure_code": expected.failure_code.value if expected.failure_code else "none",
    }
    if (
        host_summary != expected_summary
        or row.created_at.astimezone(UTC) < attempt.started_at
    ):
        raise ValueError("API result does not match its fresh durable host row")
    return {
        "durable_record_sha256": hashlib.sha256(
            canonical_json_bytes(host_summary)
        ).hexdigest(),
        "host_corroborated": True,
    }


def _worker_create_event_ids(
    driver: DockerDriver,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    run_id: UUID,
    since: int,
    until: int,
) -> tuple[str, ...]:
    name = f"{runtime.compose_project}-worker-{run_id}"
    raw = driver.run(
        (
            "events",
            "--since",
            str(since),
            "--until",
            str(until),
            "--filter",
            "type=container",
            "--filter",
            "event=create",
            "--filter",
            f"container={name}",
            "--format",
            "{{json .}}",
        ),
        timeout_seconds=max(5, until - since + 5),
        operation="worker_event_count",
    )
    container_ids: list[str] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("Docker worker event output is invalid") from exc
        actor = event.get("Actor") if isinstance(event, dict) else None
        attributes = actor.get("Attributes") if isinstance(actor, dict) else None
        container_id = actor.get("ID") if isinstance(actor, dict) else None
        event_nanoseconds = (
            event.get("timeNano") if isinstance(event, dict) else None
        )
        if (
            isinstance(attributes, dict)
            and attributes.get("name") == name
            and attributes.get("com.docker.compose.project")
            == runtime.compose_project
            and attributes.get("com.docker.compose.service") == "agent"
            and re.fullmatch(r"[0-9a-f]{64}", str(container_id)) is not None
            and isinstance(event_nanoseconds, int)
            and event_nanoseconds
            >= int(attempt.started_at.timestamp() * 1_000_000_000)
            and event_nanoseconds <= until * 1_000_000_000
        ):
            container_ids.append(str(container_id))
    return tuple(container_ids)


async def _run_crash_and_tool_probes(
    *,
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    active: Mapping[str, str],
    database_url: str,
    scope_id: UUID,
    incident_id: UUID,
    state_version: int,
    command_access: str,
) -> tuple[dict[str, Any], dict[str, JsonValue], str]:
    run_id = uuid4()
    container_name = f"{runtime.compose_project}-worker-{run_id}"
    run_task = asyncio.create_task(
        asyncio.to_thread(
            _command_api_run,
            incident_id,
            run_id,
            state_version,
            command_access,
        )
    )
    worker_id = await _wait_for_container(
        driver,
        attempt=attempt,
        runtime=runtime,
        name=container_name,
    )
    gateway_id = _gateway_container_id(
        driver,
        attempt=attempt,
        runtime=runtime,
        service="vllm-gateway",
    )
    driver.run(("pause", gateway_id), operation="vllm_gateway_pause")
    try:
        (
            tool_result,
            expected_invocation_ids,
            tool_probe_container_id,
            expected_policy_sha256,
        ) = (
            _live_tool_probe(
            driver=driver,
            attempt=attempt,
            runtime=runtime,
            active=active,
            scope_id=scope_id,
            incident_id=incident_id,
            state_version=state_version,
            run_id=run_id,
            )
        )
        _require_tool_probe_outcomes(tool_result)
        audit_evidence = _host_audit_evidence(
            database_url=database_url,
            scope_id=scope_id,
            attempt=attempt,
            run_id=run_id,
            incident_id=incident_id,
            state_version=state_version,
            policy_sha256=expected_policy_sha256,
            expected_invocation_ids=expected_invocation_ids,
        )
        driver.run(("kill", container_name), operation="exact_worker_kill")
    finally:
        driver.run(
            ("unpause", gateway_id),
            operation="vllm_gateway_unpause",
            accepted_returncodes=frozenset({0, 1}),
        )
    status, record = await run_task
    _require_terminal_record(
        status,
        record,
        success=False,
        expected_run_id=run_id,
        expected_incident_id=incident_id,
        expected_state_version=state_version,
    )
    if record.get("failure_code") != "runner_error":
        raise ValueError("killed worker did not settle as runner_error")
    _require_container_absent(driver, container_name)
    return (
        record,
        {
            **audit_evidence,
            "tool_probe_container_id": tool_probe_container_id,
        },
        worker_id,
    )


def _live_tool_probe(
    *,
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    active: Mapping[str, str],
    scope_id: UUID,
    incident_id: UUID,
    state_version: int,
    run_id: UUID,
) -> tuple[dict[str, JsonValue], tuple[UUID, ...], str, str]:
    signing_key = _decode_signing_key(
        active["VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY"]
    )
    policy_path = Path(
        active.get(
            "VITAL_RELAY_AGENT_POLICY_PATH",
            PROJECT_ROOT / "agents/policies/baseline/coordination_policy.yaml",
        )
    )
    digest_path = Path(
        active.get(
            "VITAL_RELAY_AGENT_POLICY_DIGEST_PATH",
            PROJECT_ROOT / "agents/policies/baseline/coordination_policy.sha256",
        )
    )
    policy = load_pinned_policy_snapshot(policy_path, digest_path)
    authority = ToolCapabilityAuthority(signing_key).issue(
        run_id=run_id,
        scope_id=str(scope_id),
        incident_id=incident_id,
        state_version=state_version,
        policy_sha256=policy.sha256,
        allowed_tools=policy.allowed_tools,
        issued_at=datetime.now(UTC) - timedelta(seconds=1),
        lifetime=timedelta(minutes=2),
    ).raw_capability.get_secret_value()
    base = {
        "invocation_id": str(uuid4()),
        "scope_id": str(scope_id),
        "run_id": str(run_id),
        "incident_id": str(incident_id),
        "policy_sha256": policy.sha256,
        "tool_name": GET_INCIDENT,
        "arguments": {
            "incident_id": str(incident_id),
            "expected_state_version": state_version,
        },
        "idempotency_key": None,
    }
    requests: list[dict[str, object]] = []
    for probe_id, updates in (
        ("allowed_tool_route", {}),
        (
            "wrong_run_denial",
            {"run_id": str(uuid4()), "invocation_id": str(uuid4())},
        ),
        (
            "unknown_tool_denial",
            {"tool_name": "unreviewed_tool", "invocation_id": str(uuid4())},
        ),
    ):
        invocation = {**base, **updates}
        requests.append(
            {"probe_id": probe_id, "authority": authority, "invocation": invocation}
        )
    stale = json.loads(json.dumps(base))
    stale["invocation_id"] = str(uuid4())
    stale["arguments"]["expected_state_version"] = state_version + 1
    requests.append(
        {"probe_id": "stale_state_denial", "authority": authority, "invocation": stale}
    )
    expired = ToolCapabilityAuthority(signing_key).issue(
        run_id=run_id,
        scope_id=str(scope_id),
        incident_id=incident_id,
        state_version=state_version,
        policy_sha256=policy.sha256,
        allowed_tools=policy.allowed_tools,
        issued_at=datetime.now(UTC) - timedelta(minutes=3),
        lifetime=timedelta(minutes=1),
    ).raw_capability.get_secret_value()
    requests.append(
        {
            "probe_id": "expired_authority_denial",
            "authority": expired,
            "invocation": {**base, "invocation_id": str(uuid4())},
        }
    )
    network = _resolve_internal_network(driver, runtime.compose_project)
    outcome = _run_container_probe(
        driver,
        attempt=attempt,
        runtime=runtime,
        network=network,
        input_document={"mode": "tool", "requests": requests},
    )
    invocation_ids = tuple(
        UUID(str(item["invocation"]["invocation_id"]))
        for item in requests
        if item["probe_id"] != "expired_authority_denial"
    )
    return (
        outcome["result"],
        invocation_ids,
        outcome["inspection"].container_id,
        policy.sha256,
    )


def _require_tool_probe_outcomes(results: Mapping[str, JsonValue]) -> None:
    expected = {
        "allowed_tool_route": (200, "success"),
        "expired_authority_denial": (401, "expired_capability"),
        "stale_state_denial": (409, "stale_state"),
        "unknown_tool_denial": (403, "tool_not_registered"),
        "wrong_run_denial": (403, "wrong_run"),
    }
    if set(results) != set(expected):
        raise ValueError("fixed tool probes are incomplete")
    for name, (status, code) in expected.items():
        actual = results[name]
        if (
            not isinstance(actual, dict)
            or actual.get("status") != status
            or actual.get("code") != code
        ):
            raise ValueError("fixed tool denial produced an unexpected result")


def _host_audit_evidence(
    *,
    database_url: str,
    scope_id: UUID,
    attempt: EvidenceAttempt,
    run_id: UUID,
    incident_id: UUID,
    state_version: int,
    policy_sha256: str,
    expected_invocation_ids: tuple[UUID, ...],
) -> dict[str, JsonValue]:
    from vital_relay.adapters.postgres_agent_control import PostgresAppendOnlyToolAudit
    from vital_relay.persistence.database import (
        create_postgres_engine,
        create_session_factory,
    )

    engine = create_postgres_engine(database_url)
    try:
        records = PostgresAppendOnlyToolAudit(
            create_session_factory(engine),
            scope_id,
        ).for_run(run_id)
    finally:
        engine.dispose()
    reduced = [
        {
            "error_code": record.error_code.value if record.error_code else "none",
            "request_sha256": record.request_sha256,
            "result_present": record.result_sha256 is not None,
            "status": record.status.value,
            "tool_name": record.tool_name,
        }
        for record in records
    ]
    expected_codes = {"none", "wrong_run", "tool_not_registered", "stale_state"}
    observed_invocation_ids = tuple(record.invocation_id for record in records)
    if (
        len(reduced) != 4
        or set(observed_invocation_ids) != set(expected_invocation_ids)
        or any(record.occurred_at < attempt.started_at for record in records)
        or any(record.granted_scope_id != str(scope_id) for record in records)
        or any(record.granted_run_id != run_id for record in records)
        or any(record.granted_incident_id != incident_id for record in records)
        or any(record.granted_state_version != state_version for record in records)
        or any(record.granted_policy_sha256 != policy_sha256 for record in records)
        or not expected_codes.issubset(
        {item["error_code"] for item in reduced}
        )
    ):
        raise ValueError("host audit rows do not correlate with fixed tool probes")
    return {
        "audit_ids": [str(record.audit_id) for record in records],
        "correlation_sha256": hashlib.sha256(
            canonical_json_bytes(reduced)
        ).hexdigest(),
        "invocation_ids": [
            str(identifier) for identifier in observed_invocation_ids
        ],
        "run_id": str(run_id),
    }


async def _run_timeout_probe(
    *,
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    incident_id: UUID,
    state_version: int,
    command_access: str,
) -> tuple[dict[str, Any], str]:
    run_id = uuid4()
    container_name = f"{runtime.compose_project}-worker-{run_id}"
    task = asyncio.create_task(
        asyncio.to_thread(
            _command_api_run,
            incident_id,
            run_id,
            state_version,
            command_access,
        )
    )
    worker_id = await _wait_for_container(
        driver,
        attempt=attempt,
        runtime=runtime,
        name=container_name,
    )
    gateway_id = _gateway_container_id(
        driver,
        attempt=attempt,
        runtime=runtime,
        service="vllm-gateway",
    )
    driver.run(("pause", gateway_id), operation="timeout_gateway_pause")
    try:
        status, record = await asyncio.wait_for(task, timeout=330)
    finally:
        driver.run(
            ("unpause", gateway_id),
            operation="timeout_gateway_unpause",
            accepted_returncodes=frozenset({0, 1}),
        )
    _require_terminal_record(
        status,
        record,
        success=False,
        expected_run_id=run_id,
        expected_incident_id=incident_id,
        expected_state_version=state_version,
    )
    if record.get("failure_code") != "model_timeout":
        raise ValueError("paused model route did not settle as model_timeout")
    _require_container_absent(driver, container_name)
    return record, worker_id


async def _wait_for_container(
    driver: DockerDriver,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    name: str,
) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            raw = driver.run(
                ("inspect", name),
                timeout_seconds=2,
                operation="worker_lookup",
            )
        except DockerCommandError:
            await asyncio.sleep(0.02)
        else:
            return _validated_project_container_id(
                raw,
                attempt=attempt,
                runtime=runtime,
                expected_name=name,
                expected_service="agent",
                expected_image_id=dict(runtime.image_ids)["agent"],
            )
    raise ValueError("live worker container was not observed")


def _gateway_container_id(
    driver: DockerDriver,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    service: str,
) -> str:
    raw = driver.run(
        (
            "ps",
            "--filter",
            f"label=com.docker.compose.project={runtime.compose_project}",
            "--filter",
            f"label=com.docker.compose.service={service}",
            "--format",
            "{{.ID}}",
        ),
        operation="gateway_container_lookup",
    )
    try:
        identifiers = tuple(item for item in raw.decode("ascii").splitlines() if item)
    except UnicodeError as exc:
        raise ValueError("gateway container identity is invalid") from exc
    if len(identifiers) != 1 or re.fullmatch(
        r"[0-9a-f]{12,64}", identifiers[0]
    ) is None:
        raise ValueError("exact gateway container is unavailable")
    raw_inspection = driver.run(
        ("inspect", identifiers[0]),
        operation="gateway_container_inspect",
    )
    target = {
        "vllm-gateway": "vllm_gateway",
        "tool-proxy-gateway": "tool_proxy_gateway",
    }.get(service)
    if target is None:
        raise ValueError("unreviewed gateway service")
    return _validated_project_container_id(
        raw_inspection,
        attempt=attempt,
        runtime=runtime,
        expected_name=None,
        expected_service=service,
        expected_image_id=dict(runtime.image_ids)[target],
    )


def _validated_project_container_id(
    value: bytes,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    expected_name: str | None,
    expected_service: str,
    expected_image_id: str,
) -> str:
    try:
        payload = json.loads(value)
        container = payload[0]
        container_id = str(container["Id"])
        created_at = datetime.fromisoformat(str(container["Created"]))
        labels = container["Config"].get("Labels") or {}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("project container inspection is invalid") from exc
    if (
        not isinstance(payload, list)
        or len(payload) != 1
        or re.fullmatch(r"[0-9a-f]{64}", container_id) is None
        or created_at.astimezone(UTC) < attempt.started_at
        or container.get("Image") != expected_image_id
        or labels.get("com.docker.compose.project") != runtime.compose_project
        or labels.get("com.docker.compose.service") != expected_service
        or (
            expected_name is not None
            and container.get("Name") != f"/{expected_name}"
        )
    ):
        raise ValueError("stale or unrelated project container cannot pass")
    return container_id


def _require_container_absent(driver: DockerDriver, name: str) -> None:
    raw = driver.run(
        (
            "ps",
            "--all",
            "--filter",
            f"name=^/{name}$",
            "--format",
            "{{.ID}}",
        ),
        timeout_seconds=3,
        operation="exact_container_absence",
    )
    if raw.strip():
        raise ValueError("exact container remained after custody cleanup")


def _require_exact_container_cleanup(
    *,
    driver: DockerDriver,
    name: str,
    project: str,
    operation: str,
) -> SandboxCleanupEvidence:
    custody = ExactContainerCleanupCustody(
        driver=driver,
        name=name,
        project=project,
        operation=operation,
    )
    try:
        return custody.retry_cleanup()
    except BaseException as cleanup_failure:
        raise DockerLiveEvidenceCleanupError(
            retry=custody.retry_cleanup,
            observe=lambda: custody.last_evidence,
            expected_project=project,
        ) from cleanup_failure


def _run_fault_probe(
    driver: DockerDriver,
    *,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    network: str,
    mode: Literal["crash", "timeout", "malformed"],
) -> tuple[str, str]:
    name = (
        f"{runtime.compose_project}-fault-{mode}-"
        f"{attempt.challenge[:12]}-{uuid4()}"
    )
    image_id = dict(runtime.image_ids)["agent"]
    expected_labels = _evidence_container_labels(
        attempt=attempt,
        project=runtime.compose_project,
        purpose=f"fault-{mode}",
    )
    script = _read_fixed_asset(FAULT_PROBE_PATH, maximum_bytes=64_000).decode("utf-8")
    try:
        driver.run(
            _probe_container_create_command(
                name=name,
                image_id=image_id,
                network=network,
                script=script,
                attempt=attempt,
                project=runtime.compose_project,
                purpose=f"fault-{mode}",
                arguments=(mode,),
            ),
            operation=f"fault_{mode}_create",
        )
        inspection = parse_container_inspection(
            driver.run(
                ("inspect", name),
                operation=f"fault_{mode}_containment_inspect",
            ),
            expected_image_id=image_id,
            expected_network=network,
            expected_name=name,
            expected_labels=expected_labels,
        )
        if inspection.created_at < attempt.started_at:
            raise ValueError("stale fault container cannot satisfy evidence")
        try:
            output = driver.run(
                ("start", "--attach", name),
                timeout_seconds=1 if mode == "timeout" else 10,
                operation=f"fault_{mode}_start",
                accepted_returncodes=frozenset({0, 23}),
            )
            timed_out = False
        except DockerCommandTimeout:
            if mode != "timeout":
                raise
            output = b""
            timed_out = True
        inspect_raw = driver.run(("inspect", name), operation=f"fault_{mode}_inspect")
        payload = json.loads(inspect_raw)
        returncode = payload[0]["State"].get("ExitCode") if not timed_out else None
        return (
            classify_fault_probe(
                mode=mode,
                returncode=returncode,
                output=output,
                timed_out=timed_out,
            ),
            inspection.container_id,
        )
    finally:
        _require_exact_container_cleanup(
            driver=driver,
            name=name,
            project=runtime.compose_project,
            operation=f"fault_{mode}_cleanup",
        )


def _require_docker_runtime_snapshot(
    startup: object,
) -> DockerRuntimeEvidenceSnapshot:
    if (
        not isinstance(startup, SandboxStartupEvidence)
        or startup.sandbox is not SandboxKind.DOCKER
        or startup.docker_runtime is None
    ):
        raise ValueError("application did not retain validated Docker startup evidence")
    runtime = startup.docker_runtime
    digests = (
        runtime.reviewed_snapshot_sha256,
        runtime.reviewed_worker_tree_sha256,
        runtime.expected_worker_tree_sha256,
        runtime.reviewed_worker_manifest_sha256,
        runtime.compose_config_sha256,
    )
    if (
        any(re.fullmatch(SHA256_PATTERN, digest) is None for digest in digests)
        or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,199}", runtime.compose_project)
        is None
        or tuple(target for target, _image_id in runtime.image_ids)
        != ("agent", "tool_proxy_gateway", "vllm_gateway")
        or any(
            DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None
            for _target, image_id in runtime.image_ids
        )
    ):
        raise ValueError("validated Docker runtime snapshot is malformed")
    return runtime


def _require_exact_cleanup(cleanup: object, expected_project: str) -> None:
    if (
        not isinstance(cleanup, SandboxCleanupEvidence)
        or cleanup.project_name != expected_project
        or cleanup.unresolved_checks
        or "cleanup_complete" not in cleanup.completed_checks
        or "docker_project_down" not in cleanup.completed_checks
    ):
        raise ValueError("application did not complete exact-project cleanup")


def _require_project_absent(driver: DockerDriver, project: str) -> None:
    """Prove exact-project containers and networks are absent from the host."""

    for resource, arguments in (
        (
            "containers",
            (
                "ps",
                "--all",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.ID}}",
            ),
        ),
        (
            "networks",
            (
                "network",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project}",
                "--format",
                "{{.ID}}",
            ),
        ),
    ):
        observed = driver.run(
            arguments,
            operation=f"exact_project_{resource}_absence",
        )
        if observed.strip():
            raise ValueError("exact Compose project cleanup is incomplete")


def _decode_signing_key(value: str) -> bytes:
    return _decode_base64url_key(value, label="agent signing configuration")


def _decode_base64url_key(value: str, *, label: str) -> bytes:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError, UnicodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not 32 <= len(decoded) <= 64:
        raise ValueError(f"{label} is invalid")
    return decoded


def _probe(
    probe_id: str,
    status: EvidenceStatus,
    *assertions: str,
    attributes: dict[str, JsonValue] | None = None,
) -> ProbeEvidence:
    return ProbeEvidence(
        probe_id=probe_id,
        status=status,
        assertions=tuple(sorted(assertions)),
        attributes=attributes or {},
    )


def _bundle_status(probes: Sequence[ProbeEvidence]) -> EvidenceStatus:
    if any(item.status is EvidenceStatus.FAILED for item in probes):
        return EvidenceStatus.FAILED
    if any(item.status is EvidenceStatus.BLOCKED for item in probes):
        return EvidenceStatus.BLOCKED
    return EvidenceStatus.PASSED


def _signature_input(
    *,
    content_sha256: str,
    content: EvidenceContent,
    issuer: str,
    key_id: str,
) -> bytes:
    return SIGNATURE_DOMAIN + canonical_json_bytes(
        {
            "content": content.model_dump(mode="json"),
            "content_sha256": content_sha256,
            "evidence_class": EvidenceClass.TRUSTED_LIVE.value,
            "issuer": issuer,
            "key_id": key_id,
        }
    )


def _validate_evidence_signing_key(signing_key: bytes) -> None:
    if not isinstance(signing_key, bytes) or not 32 <= len(signing_key) <= 64:
        raise ValueError("trusted-host evidence signing key is invalid")


def _evidence_signer_configuration(
    environment: Mapping[str, str],
) -> tuple[bytes, str, str]:
    issuer = environment.get(EVIDENCE_ISSUER_ENV, "")
    key_id = environment.get(EVIDENCE_KEY_ID_ENV, "")
    raw_key = environment.get(EVIDENCE_SIGNING_KEY_ENV, "")
    if not raw_key:
        raise ValueError("trusted-host evidence signer configuration is required")
    signing_key = _decode_base64url_key(
        raw_key,
        label="trusted-host evidence signing key",
    )
    _validate_evidence_signing_key(signing_key)
    _validate_signer_identity(issuer=issuer, key_id=key_id)
    return signing_key, issuer, key_id


def _validate_signer_identity(*, issuer: str, key_id: str) -> None:
    pattern = r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}"
    if re.fullmatch(pattern, issuer) is None or re.fullmatch(pattern, key_id) is None:
        raise ValueError("trusted-host evidence signer identity is invalid")


def _validate_core_live_invariants(
    *,
    environment: Mapping[str, str],
    output_directory: Path,
    attempt: EvidenceAttempt,
    signing_key: bytes,
    issuer: str,
    key_id: str,
) -> None:
    """Recheck non-production and custody invariants before any mutation."""

    if environment.get(EVIDENCE_ENVIRONMENT_ENV) != "nonproduction":
        raise ValueError("live evidence requires an explicit non-production marker")
    _validate_evidence_signing_key(signing_key)
    _validate_signer_identity(issuer=issuer, key_id=key_id)
    attempt_age = datetime.now(UTC) - attempt.started_at
    if attempt_age < timedelta(0) or attempt_age > timedelta(seconds=30):
        raise ValueError("live evidence attempt challenge is stale")
    if attempt.attempt_id.int == 0 or attempt.challenge == "0" * 64:
        raise ValueError("live evidence attempt challenge is not unique")
    if not output_directory.is_absolute():
        raise ValueError("live evidence output directory must be absolute")
    _validate_output_directory_path(output_directory)

    database_url = environment.get("VITAL_RELAY_DATABASE_URL", "")
    scope_id = environment.get("VITAL_RELAY_DEMO_SCOPE_ID", "")
    if database_url:
        from sqlalchemy.engine import make_url

        parsed = make_url(database_url)
        database_name = parsed.database or ""
        host = parsed.host
        if (
            re.search(r"(?:test|evidence|demo)", database_name, re.IGNORECASE)
            is None
            or host not in {None, "", "localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("live evidence requires a local non-production database")
    if scope_id:
        parsed_scope = UUID(scope_id)
        if parsed_scope.int == 0:
            raise ValueError("live evidence scope identity is invalid")


def _validate_output_directory_path(output_directory: Path) -> None:
    if not output_directory.is_absolute():
        raise ValueError("live evidence output directory must be absolute")
    normalized_output = output_directory.resolve(strict=False)
    if normalized_output != output_directory:
        raise ValueError("live evidence output directory must not traverse a symlink")
    if normalized_output == PROJECT_ROOT or normalized_output.is_relative_to(
        PROJECT_ROOT
    ):
        raise ValueError("live evidence output must remain outside the repository")
    parent = normalized_output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ValueError("live evidence output parent is unsafe")
    if normalized_output.exists():
        metadata = normalized_output.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError("live evidence output directory is unsafe")


def _source_bindings(manifest_sha256: str) -> dict[str, JsonValue]:
    manifest, observed_manifest_sha256 = load_probe_manifest()
    if observed_manifest_sha256 != manifest_sha256:
        raise ValueError("fixed probe manifest changed during the attempt")
    harness_paths = (
        "backend/src/vital_relay/agent/docker_live_evidence.py",
        "infrastructure/docker-agent/evidence/run_live_evidence.py",
    )
    product_paths = (
        "backend/src/vital_relay/main.py",
        "backend/src/vital_relay/adapters/postgres_agent_control.py",
        "backend/src/vital_relay/application/agent_control.py",
        "backend/src/vital_relay/application/agent_service.py",
        "backend/src/vital_relay/application/tool_proxy.py",
        "backend/src/vital_relay/agent/capabilities.py",
        "backend/src/vital_relay/agent/capability_runtime.py",
        "backend/src/vital_relay/agent/contracts.py",
        "backend/src/vital_relay/agent/policy.py",
        "backend/src/vital_relay/agent/sandbox.py",
        "backend/src/vital_relay/agent/sandbox_wire.py",
        "backend/src/vital_relay/agent/source_manifest.py",
        "backend/src/vital_relay/agent/tool_contracts.py",
        "backend/src/vital_relay/agent/tool_transport.py",
        "backend/src/vital_relay/agent/worker.py",
        "infrastructure/docker-agent/Dockerfile",
        "infrastructure/docker-agent/compose.yaml",
        "infrastructure/docker-agent/requirements.lock",
        "infrastructure/docker-agent/tool_proxy_gateway.py",
        "infrastructure/docker-agent/vllm_gateway.py",
    )
    return {
        "fixed_probes": {
            "assets": [
                {"name": name, "sha256": digest}
                for name, digest in sorted(manifest.assets.items())
            ],
            "manifest_sha256": manifest_sha256,
        },
        "harness": {
            "files": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(
                        _read_reviewed_docker_asset(PROJECT_ROOT / relative)
                    ).hexdigest(),
                }
                for relative in harness_paths
            ],
        },
        "product_sources": {
            "files": [
                {
                    "path": relative,
                    "sha256": hashlib.sha256(
                        _read_reviewed_docker_asset(PROJECT_ROOT / relative)
                    ).hexdigest(),
                }
                for relative in product_paths
            ]
        },
    }


def _complete_live_bindings(
    *,
    source_bindings: dict[str, JsonValue],
    active: Mapping[str, str],
    driver: DockerDriver,
    attempt: EvidenceAttempt,
    runtime: DockerRuntimeEvidenceSnapshot,
    image_evidence: tuple[ImageInspectionEvidence, ...],
    gateway_container_ids: Mapping[str, str],
    containment_container_id: str,
    success_worker_ids: tuple[str, ...],
    crash_worker_id: str,
    timeout_worker_id: str,
    malformed_container_id: str,
    audit_evidence: Mapping[str, JsonValue],
    success_run_id: UUID,
    crash_run_id: UUID,
    timeout_run_id: UUID,
) -> dict[str, JsonValue]:
    fixed_probes = source_bindings.get("fixed_probes")
    manifest_sha256 = (
        fixed_probes.get("manifest_sha256")
        if isinstance(fixed_probes, dict)
        else None
    )
    if (
        not isinstance(manifest_sha256, str)
        or _source_bindings(manifest_sha256) != source_bindings
    ):
        raise ValueError("evidence harness or product source changed during the attempt")
    try:
        worker_manifest = json.loads(runtime.reviewed_worker_manifest_json)
        compose_graph = json.loads(runtime.compose_graph_json)
    except json.JSONDecodeError as exc:
        raise ValueError("runtime snapshot contains invalid canonical JSON") from exc
    _validate_closed_worker_manifest(worker_manifest)
    if (
        runtime.reviewed_worker_manifest_json.encode("utf-8")
        != canonical_json_bytes(worker_manifest)
        or runtime.compose_graph_json.encode("utf-8")
        != canonical_json_bytes(compose_graph)
        or not hmac.compare_digest(
            runtime.reviewed_worker_tree_sha256,
            runtime.expected_worker_tree_sha256,
        )
        or worker_manifest["digest"] != runtime.reviewed_worker_tree_sha256
        or hashlib.sha256(canonical_json_bytes(worker_manifest)).hexdigest()
        != runtime.reviewed_worker_manifest_sha256
        or hashlib.sha256(canonical_json_bytes(compose_graph)).hexdigest()
        != runtime.compose_config_sha256
    ):
        raise ValueError("runtime manifest or Compose graph digest is invalid")
    policy_path = Path(
        active.get(
            "VITAL_RELAY_AGENT_POLICY_PATH",
            PROJECT_ROOT / "agents/policies/baseline/coordination_policy.yaml",
        )
    )
    policy_digest_path = Path(
        active.get(
            "VITAL_RELAY_AGENT_POLICY_DIGEST_PATH",
            PROJECT_ROOT / "agents/policies/baseline/coordination_policy.sha256",
        )
    )
    policy = load_pinned_policy_snapshot(policy_path, policy_digest_path)
    dependencies = _dependency_bindings(driver)
    all_container_ids = [
        *gateway_container_ids.values(),
        containment_container_id,
        *success_worker_ids,
        crash_worker_id,
        timeout_worker_id,
        malformed_container_id,
        str(audit_evidence["tool_probe_container_id"]),
    ]
    if len(all_container_ids) != len(set(all_container_ids)):
        raise ValueError("attempt container identities are not unique")
    bindings: dict[str, JsonValue] = {
        **source_bindings,
        "compose": {
            "graph": compose_graph,
            "graph_sha256": runtime.compose_config_sha256,
            "project": runtime.compose_project,
        },
        "containers": {
            "gateway_ids": dict(sorted(gateway_container_ids.items())),
            "probe_ids": [
                containment_container_id,
                malformed_container_id,
                str(audit_evidence["tool_probe_container_id"]),
            ],
            "worker_ids": [
                *success_worker_ids,
                crash_worker_id,
                timeout_worker_id,
            ],
        },
        "correlations": {
            "attempt_id": str(attempt.attempt_id),
            "audit_ids": audit_evidence["audit_ids"],
            "challenge": attempt.challenge,
            "invocation_ids": audit_evidence["invocation_ids"],
            "run_ids": [
                str(success_run_id),
                str(crash_run_id),
                str(timeout_run_id),
            ],
        },
        "dependencies": dependencies,
        "images": [item.model_dump(mode="json") for item in image_evidence],
        "model": {
            "artifact_sha256": active[
                "VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256"
            ],
            "id": active["VITAL_RELAY_VLLM_MODEL"],
            "revision": active["VITAL_RELAY_VLLM_MODEL_REVISION"],
        },
        "policy": {
            "id": policy.policy_id,
            "sha256": policy.sha256,
            "version": policy.version,
        },
        "runner": {
            "class": "ProcessSandboxAgentRunner",
            "reviewed_snapshot_sha256": runtime.reviewed_snapshot_sha256,
            "reviewed_worker_manifest": worker_manifest,
            "reviewed_worker_manifest_sha256": (
                runtime.reviewed_worker_manifest_sha256
            ),
            "reviewed_worker_tree_sha256": (
                runtime.reviewed_worker_tree_sha256
            ),
            "expected_worker_tree_sha256": (
                runtime.expected_worker_tree_sha256
            ),
            "sandbox": SandboxKind.DOCKER.value,
            "wire_schema_version": SANDBOX_WIRE_SCHEMA_VERSION,
        },
        "transport": {
            "api_bind": f"{API_BIND_HOST}:{API_PORT}",
            "inference_base_url": DOCKER_INFERENCE_BASE_URL,
            "tool_endpoint": DOCKER_TOOL_PROXY_ENDPOINT,
            "tool_upstream": (
                f"{DOCKER_TOOL_UPSTREAM_HOST}:{DOCKER_TOOL_UPSTREAM_PORT}"
            ),
            "vllm_upstream": (
                f"{DOCKER_VLLM_UPSTREAM_HOST}:{DOCKER_VLLM_UPSTREAM_PORT}"
            ),
        },
    }
    _reject_sensitive_evidence(bindings)
    return bindings


def _validate_closed_worker_manifest(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "digest",
        "entrypoint",
        "files",
        "name",
    }:
        raise ValueError("reviewed worker manifest is empty")
    digest = value.get("digest")
    entrypoint = value.get("entrypoint")
    name = value.get("name")
    files = value.get("files")
    if (
        re.fullmatch(SHA256_PATTERN, str(digest)) is None
        or not isinstance(entrypoint, str)
        or re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*", entrypoint)
        is None
        or not isinstance(name, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None
        or not isinstance(files, list)
        or not files
    ):
        raise ValueError("reviewed worker manifest is invalid")
    paths: list[str] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("reviewed worker manifest entry is invalid")
        path = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or path.startswith("/")
            or ".." in Path(path).parts
            or re.fullmatch(SHA256_PATTERN, str(digest)) is None
        ):
            raise ValueError("reviewed worker manifest entry is invalid")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("reviewed worker manifest is not exact and sorted")


def _dependency_bindings(driver: DockerDriver) -> list[JsonValue]:
    package_names = (
        "deepagents",
        "fastapi",
        "langchain-openai",
        "psycopg",
        "pydantic",
        "PyYAML",
        "SQLAlchemy",
        "uvicorn",
        "vital-relay",
    )
    packages: list[JsonValue] = []
    for name in package_names:
        try:
            version = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError as exc:
            raise ValueError("required live dependency is unavailable") from exc
        packages.append({"name": name, "version": version})
    docker_server = _single_safe_version(
        driver.run(
            ("version", "--format", "{{.Server.Version}}"),
            operation="docker_version_binding",
        )
    )
    compose = _single_safe_version(
        driver.run(
            ("compose", "version", "--short"),
            operation="compose_version_binding",
        )
    )
    return [
        *packages,
        {"name": "docker-server", "version": docker_server},
        {"name": "docker-compose", "version": compose},
        {"name": "python", "version": platform.python_version()},
    ]


def _single_safe_version(value: bytes) -> str:
    try:
        text = value.decode("ascii").strip()
    except UnicodeError as exc:
        raise ValueError("dependency version output is invalid") from exc
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}", text) is None:
        raise ValueError("dependency version output is invalid")
    return text


def _require_complete_live_bindings(content: EvidenceContent) -> None:
    required = {
        "compose",
        "containers",
        "correlations",
        "dependencies",
        "fixed_probes",
        "harness",
        "images",
        "model",
        "policy",
        "product_sources",
        "runner",
        "transport",
    }
    if set(content.bindings) != required:
        raise ValueError("passing live evidence has incomplete signed bindings")
    correlations = content.bindings.get("correlations")
    if (
        not isinstance(correlations, dict)
        or correlations.get("attempt_id") != str(content.attempt.attempt_id)
        or correlations.get("challenge") != content.attempt.challenge
        or len(correlations.get("run_ids", [])) != 3
        or len(correlations.get("audit_ids", [])) != 4
    ):
        raise ValueError("passing live evidence has invalid attempt correlation")


def _require_trusted_source_bindings(content: EvidenceContent) -> None:
    fixed_probes = content.bindings.get("fixed_probes")
    harness = content.bindings.get("harness")
    product_sources = content.bindings.get("product_sources")
    if (
        not isinstance(fixed_probes, dict)
        or fixed_probes.get("manifest_sha256")
        != content.probe_manifest_sha256
        or not isinstance(fixed_probes.get("assets"), list)
        or len(fixed_probes["assets"]) != 2
        or not isinstance(harness, dict)
        or not isinstance(harness.get("files"), list)
        or len(harness["files"]) != 2
        or not isinstance(product_sources, dict)
        or not isinstance(product_sources.get("files"), list)
        or not product_sources["files"]
    ):
        raise ValueError("trusted live evidence has incomplete source bindings")


def _reject_sensitive_evidence(value: object, *, key: str | None = None) -> None:
    if key is not None:
        words = set(re.split(r"[^a-z0-9]+", key.lower()))
        if words & _FORBIDDEN_EVIDENCE_KEYS:
            raise ValueError("evidence contains a forbidden sensitive field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            if not isinstance(child_key, str):
                raise ValueError("evidence object keys must be strings")
            _reject_sensitive_evidence(child, key=child_key)
    elif isinstance(value, list | tuple):
        for child in value:
            _reject_sensitive_evidence(child)
    elif isinstance(value, float) and not (
        value == value and abs(value) != float("inf")
    ):
        raise ValueError("evidence cannot contain non-finite numbers")


def _read_fixed_asset(path: Path, *, maximum_bytes: int) -> bytes:
    try:
        relative = path.relative_to(EVIDENCE_ROOT)
    except ValueError as exc:
        raise ValueError("fixed probe asset escaped its reviewed root") from exc
    if len(relative.parts) != 1 or relative.name.startswith("."):
        raise ValueError("fixed probe asset path is invalid")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError("fixed probe asset is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= maximum_bytes
        ):
            raise ValueError("fixed probe asset is unsafe")
        chunks = bytearray()
        while len(chunks) <= maximum_bytes:
            chunk = os.read(
                descriptor,
                min(65_536, maximum_bytes + 1 - len(chunks)),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) != metadata.st_size or len(chunks) > maximum_bytes:
            raise ValueError("fixed probe asset changed during its read")
        return bytes(chunks)
    except OSError as exc:
        raise ValueError("fixed probe asset is unavailable") from exc
    finally:
        os.close(descriptor)


def _docker_cli_environment() -> dict[str, str]:
    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
    }
    for name in ("HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"):
        value = os.environ.get(name)
        if value and "\x00" not in value and len(value) <= 4096:
            environment[name] = value
    return environment


def _model_availability(
    *,
    model: str,
    api_key: str,
) -> Literal["available", "unavailable", "mismatch"]:
    connection = http.client.HTTPConnection(VLLM_HOST, VLLM_PORT, timeout=2)
    try:
        connection.request(
            "GET",
            "/v1/models",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        raw = response.read(MAX_DOCUMENT_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_DOCUMENT_BYTES:
            return "unavailable"
        payload = json.loads(raw)
    except Exception:
        return "unavailable"
    finally:
        connection.close()
    if not isinstance(payload, dict):
        return "unavailable"
    listed = model in {
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict)
    }
    return "available" if listed else "mismatch"


def _postgres_is_available(database_url: str, scope_id: str) -> bool:
    try:
        parsed_scope = UUID(scope_id)
        from sqlalchemy import text

        from vital_relay.persistence.database import (
            create_postgres_engine,
            create_session_factory,
            require_active_scope,
        )

        engine = create_postgres_engine(database_url)
        try:
            with engine.connect() as connection:
                if connection.execute(text("SELECT 1")).scalar_one() != 1:
                    return False
                if (
                    connection.execute(
                        text("SELECT count(*) FROM demo_scopes")
                    ).scalar_one()
                    != 1
                ):
                    return False
            with create_session_factory(engine)() as session:
                require_active_scope(session, parsed_scope)
        finally:
            engine.dispose()
        return True
    except Exception:
        return False


def _enrolled_command_session_is_available(
    *,
    database_url: str,
    scope_id: str,
    command_access: str,
) -> bool:
    try:
        from vital_relay.adapters.postgres_persona_sessions import (
            PostgresPersonaSessionRepository,
        )
        from vital_relay.domain.persona_sessions import Persona
        from vital_relay.persistence.database import (
            create_postgres_engine,
            create_session_factory,
        )

        engine = create_postgres_engine(database_url)
        try:
            repository = PostgresPersonaSessionRepository(
                engine,
                create_session_factory(engine),
                UUID(scope_id),
            )
            principal = repository.authenticate_access(
                access_token=command_access,
                as_of=datetime.now(UTC),
            )
        finally:
            engine.dispose()
        return principal.persona is Persona.COMMAND
    except Exception:
        return False


def _incident_is_available(
    *,
    database_url: str,
    scope_id: str,
    incident_id: str,
    expected_state_version: str,
) -> bool:
    try:
        from sqlalchemy import text

        from vital_relay.persistence.database import create_postgres_engine

        parsed_scope = UUID(scope_id)
        parsed_incident = UUID(incident_id)
        parsed_version = int(expected_state_version)
        engine = create_postgres_engine(database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT state, state_version FROM incidents "
                        "WHERE scope_id = :scope_id "
                        "AND incident_id = :incident_id"
                    ),
                    {
                        "scope_id": parsed_scope,
                        "incident_id": parsed_incident,
                    },
                ).one_or_none()
        finally:
            engine.dispose()
        return row is not None and row[0] in {
            "escalating",
            "response_active",
        } and row[1] == parsed_version
    except Exception:
        return False


def _active_policy_matches(
    *,
    environment: Mapping[str, str],
    database_url: str,
    scope_id: str,
) -> bool:
    try:
        from sqlalchemy import text

        from vital_relay.persistence.database import create_postgres_engine

        policy_path = Path(
            environment.get(
                "VITAL_RELAY_AGENT_POLICY_PATH",
                PROJECT_ROOT
                / "agents/policies/baseline/coordination_policy.yaml",
            )
        )
        digest_path = Path(
            environment.get(
                "VITAL_RELAY_AGENT_POLICY_DIGEST_PATH",
                PROJECT_ROOT
                / "agents/policies/baseline/coordination_policy.sha256",
            )
        )
        policy = load_pinned_policy_snapshot(policy_path, digest_path)
        engine = create_postgres_engine(database_url)
        try:
            with engine.connect() as connection:
                row = connection.execute(
                    text(
                        "SELECT policy_id, policy_version, policy_sha256 "
                        "FROM agent_active_policies WHERE scope_id = :scope_id"
                    ),
                    {"scope_id": UUID(scope_id)},
                ).one_or_none()
        finally:
            engine.dispose()
        return row == (policy.policy_id, policy.version, policy.sha256)
    except Exception:
        return False


def _valid_incident_configuration(environment: Mapping[str, str]) -> bool:
    try:
        UUID(environment[EVIDENCE_INCIDENT_ID_ENV])
        return int(environment[EVIDENCE_INCIDENT_STATE_VERSION_ENV]) >= 1
    except (KeyError, TypeError, ValueError):
        return False


def _valid_agent_configuration(environment: Mapping[str, str]) -> bool:
    required = (
        "VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY",
        "VITAL_RELAY_VLLM_MODEL_REVISION",
        "VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256",
    )
    if environment.get("VITAL_RELAY_AGENT_SANDBOX") != "docker" or any(
        not environment.get(name) for name in required
    ):
        return False
    try:
        _decode_signing_key(environment["VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY"])
    except ValueError:
        return False
    artifact = environment["VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256"]
    try:
        timeout = float(environment.get("VITAL_RELAY_AGENT_TIMEOUT_SECONDS", "90"))
    except ValueError:
        return False
    return re.fullmatch(SHA256_PATTERN, artifact) is not None and 10 <= timeout <= 30


def _port_is_available(host: str, port: int) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        listener.close()


def _cleanup_attempt_record(
    *,
    retry_number: int,
    evidence: SandboxCleanupEvidence | None,
    project_sha256: str | None,
    host_absence_verified: bool,
) -> dict[str, JsonValue]:
    return {
        "already_closed": evidence.already_closed if evidence else False,
        "cleanup_attempt_count": evidence.attempt_count if evidence else None,
        "completed_checks": list(evidence.completed_checks) if evidence else [],
        "evidence_available": evidence is not None,
        "failed_checks": list(evidence.failed_checks) if evidence else [],
        "host_absence_verified": host_absence_verified,
        "project_sha256": project_sha256,
        "retry_number": retry_number,
        "unresolved_checks": list(evidence.unresolved_checks) if evidence else [],
    }


def _handle_cli_cleanup_failure(error: DockerLiveEvidenceCleanupError) -> int:
    """Retry exact-owner custody only, emit one closed failure, and exit 2."""

    attempts: list[dict[str, JsonValue]] = [
        _cleanup_attempt_record(
            retry_number=0,
            evidence=error.observed_cleanup(),
            project_sha256=error.project_sha256,
            host_absence_verified=False,
        )
    ]
    resolved = False
    for retry_number in range(1, MAX_EXACT_CLEANUP_RETRIES + 1):
        evidence: SandboxCleanupEvidence | None
        try:
            candidate = error.retry_cleanup()
            if not isinstance(candidate, SandboxCleanupEvidence):
                raise ValueError("exact cleanup retry returned invalid evidence")
            evidence = candidate
        except BaseException:
            evidence = error.observed_cleanup()
            host_absence_verified = False
        else:
            host_absence_verified = True
            resolved = True
        attempts.append(
            _cleanup_attempt_record(
                retry_number=retry_number,
                evidence=evidence,
                project_sha256=error.project_sha256,
                host_absence_verified=host_absence_verified,
            )
        )
        if resolved:
            break
    document: dict[str, JsonValue] = {
        "cleanup": {
            "attempts": attempts,
            "exact_owner_retries": len(attempts) - 1,
            "host_absence_verified": resolved,
            "resolved": resolved,
            "retry_limit": MAX_EXACT_CLEANUP_RETRIES,
        },
        "failure_code": (
            "live_attempt_failed_cleanup_resolved"
            if resolved
            else "docker_cleanup_unresolved"
        ),
        "lane": LANE,
        "schema_version": SCHEMA_VERSION,
        "status": EvidenceStatus.FAILED.value,
    }
    _reject_sensitive_evidence(document)
    print(canonical_json_bytes(document).decode("utf-8"))  # noqa: T201
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collect canonical Docker containment live evidence",
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="verify one trusted live bundle instead of collecting evidence",
    )
    args = parser.parse_args(argv)
    try:
        active_environment = dict(os.environ)
        signing_key, issuer, key_id = _evidence_signer_configuration(
            active_environment
        )
        if args.verify is not None:
            bundle = verify_live_bundle_file(
                args.verify.absolute(),
                signing_key=signing_key,
                issuer=issuer,
                key_id=key_id,
            )
            print(  # noqa: T201 - explicit verifier CLI
                json.dumps(
                    {
                        "content_sha256": bundle.content_sha256,
                        "status": bundle.content.status.value,
                        "verified": True,
                    },
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        attempt = EvidenceAttempt(
            attempt_id=uuid4(),
            challenge=secrets.token_hex(32),
            started_at=datetime.now(UTC),
        )
        output_directory = args.output_directory.resolve(strict=False)
        _manifest, manifest_sha256 = load_probe_manifest()
        bundle = asyncio.run(
            collect_live_bundle(
                manifest_sha256=manifest_sha256,
                attempt=attempt,
                signing_key=signing_key,
                issuer=issuer,
                key_id=key_id,
                output_directory=output_directory,
                environment=active_environment,
            )
        )
        published = publish_live_bundle(
            bundle,
            output_directory,
            signing_key=signing_key,
            issuer=issuer,
            key_id=key_id,
        )
    except DockerLiveEvidenceCleanupError as cleanup_error:
        # Keep the exact retained owner alive until its bounded retries finish.
        # A failed live attempt never reaches publication, even when cleanup
        # eventually resolves.
        return _handle_cli_cleanup_failure(cleanup_error)
    except Exception:
        # Internal harness or integrity failures are never converted into a
        # plausible evidence bundle, and raw exception text is not emitted.
        return 2
    print(  # noqa: T201 - explicit CLI, path and closed status only
        json.dumps(
            {
                "content_sha256": bundle.content_sha256,
                "evidence_file": str(published),
                "status": bundle.content.status.value,
                "blockers": [str(item) for item in bundle.content.blockers],
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0 if bundle.content.status is EvidenceStatus.PASSED else 1


if __name__ == "__main__":
    raise SystemExit(main())
