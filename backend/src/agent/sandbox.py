"""Process-isolated AgentRunner adapters for NemoClaw and Docker.

The host owns capability issuance, policy activation, and persistence.  A
sandbox receives one bounded request over stdin and returns one normalized
``AgentRunResult`` over stdout.  No shell is involved and stderr is never
copied into an operational result because provider/transport failures can
contain credentials or sensitive diagnostics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

import yaml

from vital_relay.agent.capability_runtime import ToolInvocationContext
from vital_relay.agent.contracts import (
    AgentFailureCode,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    SandboxKind,
    VLLMSettings,
)
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.agent.runner import SystemClock
from vital_relay.agent.sandbox_wire import (
    DOCKER_INFERENCE_BASE_URL,
    DOCKER_TOOL_PROXY_ENDPOINT,
    MAX_SANDBOX_REQUEST_BYTES,
    MAX_SANDBOX_RESULT_BYTES,
    NEMOCLAW_INFERENCE_BASE_URL,
    NEMOCLAW_MANAGED_INFERENCE_API_KEY,
    SANDBOX_WIRE_SCHEMA_VERSION,
    SandboxInvocationEnvelope,
    SandboxVLLMEnvelope,
    SandboxWorkerEnvelope,
)
from vital_relay.agent.source_manifest import (
    DOCKER_AGENT_SOURCE_MANIFEST,
    ReviewedSourceSnapshot,
    capture_reviewed_source_snapshot,
    source_content_digest,
    validate_source_import_closure,
    validate_staged_source_tree,
)
from vital_relay.agent.tools import BoundedToolGateway, Clock
from vital_relay.evolution.ace.contracts import SelectedContext
from vital_relay.evolution.ace.selection import verify_selected_context


DEFAULT_SANDBOX_TIMEOUT_SECONDS = 90.0
NEMOCLAW_WORKER_EXECUTABLE = (
    "/sandbox/vital-relay-runtime/bin/vital-relay-agent-worker"
)
NEMOCLAW_HOST_CLI_EXECUTABLE = "/usr/local/bin/nemo-deepagents"
NEMOCLAW_MANAGED_EXEC_LAUNCHER = (
    "/usr/local/lib/nemoclaw/dcode-managed-exec"
)
NEMOCLAW_SANDBOX_NAME_PATTERN = re.compile(
    r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
DOCKER_COMPOSE_PROJECT_NAME = "vital-relay-agent"
DOCKER_REQUIRED_GATEWAY_SERVICES = frozenset(
    {"tool-proxy-gateway", "vllm-gateway"}
)
DOCKER_BUILD_TARGETS = ("agent", "vllm_gateway", "tool_proxy_gateway")
DOCKER_IMAGE_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DOCKER_VLLM_UPSTREAM_HOST = "host.docker.internal"
DOCKER_VLLM_UPSTREAM_PORT = "8001"
DOCKER_TOOL_UPSTREAM_SCHEME = "http"
DOCKER_TOOL_UPSTREAM_HOST = "host.docker.internal"
DOCKER_TOOL_UPSTREAM_PORT = "8000"
MAX_DOCKER_COMPOSE_BYTES = 256 * 1024
MAX_DOCKER_REVIEWED_ASSET_BYTES = 32 * 1024 * 1024
DEFAULT_SANDBOX_READINESS_TIMEOUT_SECONDS = 300.0
DOCKER_PROJECT_ROOT = Path(__file__).resolve().parents[4]
DOCKER_REVIEWED_COMPOSE_FILE = (
    DOCKER_PROJECT_ROOT / "infrastructure/docker-agent/compose.yaml"
)
DOCKER_REVIEWED_ASSET_SHA256 = {
    "infrastructure/docker-agent/Dockerfile": (
        "4af64734705eb8ebf38cb48139018acea45ec33767b389b0bb49df7fced6d628"
    ),
    "infrastructure/docker-agent/Dockerfile.dockerignore": (
        "13b7d544fa127e3e14b4c48786c27f1d891d4d48548a7e689142cb5d09b27ccd"
    ),
    "infrastructure/docker-agent/compose.yaml": (
        "4f1577ea4899349d5489b4afa88f48b7048b15cf9fcb9a99b48e641cef917ae3"
    ),
    "infrastructure/docker-agent/empty.env": (
        "847c1b04e7b2c9831d63581d17be566d3e79202563933a0329f5cde8f79cdea7"
    ),
    "infrastructure/docker-agent/requirements.lock": (
        "362c2f7aa6a3ef0eb27d609783d71f623d7c081c3d51b1fa3cbf3b1747825bee"
    ),
    "infrastructure/docker-agent/tool_proxy_gateway.py": (
        "3499f67a3a4e89bd7317851ed90ccae85eb182dd3eae5276d1be074c3e025880"
    ),
    "infrastructure/docker-agent/vllm_gateway.py": (
        "809b27f1544de818da70831299fcbf413e42bebc176ec0b800c46dc73869341d"
    ),
}
DOCKER_REVIEWED_WORKER_TREE_SHA256 = (
    "77a3fba84a6b710e0651f226d82afd276b4255c5f3cd0515ca5884d51aa60462"
)
# Final cross-lane image pin retained for the integration owner.  This lane
# validates an exact manifest-derived digest but deliberately does not update
# the final pin before the remaining Wave 3 sources merge.


@dataclass(frozen=True, slots=True)
class CompletedSandboxCommand:
    returncode: int
    stdout: bytes


@dataclass(frozen=True, slots=True)
class SandboxStartupCheck:
    """One fixed, host-executed readiness assertion."""

    name: str
    command: tuple[str, ...]
    expected_healthy_services: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class SandboxStartupEvidence:
    """Privacy-bounded evidence authored by the host launcher."""

    sandbox: SandboxKind
    checked_at: datetime
    completed_checks: tuple[str, ...]
    docker_runtime: DockerRuntimeEvidenceSnapshot | None = None


@dataclass(frozen=True, slots=True)
class DockerRuntimeEvidenceSnapshot:
    """Immutable, path-free identity of one validated Docker runtime.

    The snapshot deliberately omits launcher temp paths, tags, environment,
    subprocess output, and container logs.  It is safe to retain after exact
    project cleanup and gives a live-evidence collector the content identities
    needed to inspect the already selected product runtime.
    """

    reviewed_snapshot_sha256: str
    reviewed_worker_tree_sha256: str
    expected_worker_tree_sha256: str
    reviewed_worker_manifest_sha256: str
    reviewed_worker_manifest_json: str
    compose_project: str
    compose_config_sha256: str
    compose_graph_json: str
    image_ids: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class SandboxCleanupEvidence:
    """Host-authored evidence for exact-project and owned-root cleanup."""

    sandbox: SandboxKind
    checked_at: datetime
    completed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]
    unresolved_checks: tuple[str, ...] = ()
    project_name: str | None = None
    owned_temp_root_count: int = 0
    attempt_count: int = 1
    already_closed: bool = False


@dataclass(frozen=True, slots=True)
class ReviewedDockerSnapshot:
    """No-follow capture of every byte visible to the Docker build."""

    digest: str
    files: tuple[tuple[str, bytes], ...]
    reviewed_worker_tree_sha256: str = ""
    reviewed_worker_manifest_sha256: str = ""
    reviewed_worker_manifest_json: str = ""


@dataclass(frozen=True, slots=True)
class DockerRuntimeProfile:
    """Host-constructed immutable runtime graph bound to built image IDs."""

    project_name: str
    context_path: Path
    compose_path: Path
    env_file: Path
    compose_prefix: tuple[str, ...]
    image_tags: tuple[tuple[str, str], ...]
    image_ids: tuple[tuple[str, str], ...]
    expected_graph: dict[str, object]


@dataclass(slots=True)
class DockerStartupAttempt:
    """Provisional resources that are not runner-owned until verification."""

    owned_temp_roots: list[Path] = field(default_factory=list)
    runtime: DockerRuntimeProfile | None = None
    project_may_exist: bool = False


@dataclass(frozen=True, slots=True)
class DockerOwnedProject:
    """Exact project identity and executor retained until `down` succeeds."""

    runtime: DockerRuntimeProfile
    executor: SandboxCommandExecutor


@dataclass(frozen=True, slots=True)
class DockerCleanupOutcome:
    """One cleanup attempt plus the exact resources still unresolved."""

    evidence: SandboxCleanupEvidence
    unresolved_projects: tuple[DockerOwnedProject, ...]
    unresolved_roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ProcessSandboxSelection:
    """Exactly one operator-selected process sandbox configuration.

    This value has no automatic choice and cannot describe both runtimes. The
    application may default its operator setting to NemoClaw, but must parse a
    concrete selection before calling :meth:`ProcessSandboxAgentRunner.selected`.
    """

    sandbox: SandboxKind
    nemoclaw_sandbox_name: str | None = None
    docker_compose_file: Path | None = None

    def __post_init__(self) -> None:
        if self.sandbox is SandboxKind.NEMOCLAW:
            if self.nemoclaw_sandbox_name is None:
                raise ValueError("NemoClaw selection requires a sandbox name")
            if self.docker_compose_file is not None:
                raise ValueError("NemoClaw selection cannot include Docker config")
            return
        if self.sandbox is SandboxKind.DOCKER:
            if self.docker_compose_file is None:
                raise ValueError("Docker selection requires a compose file")
            if self.nemoclaw_sandbox_name is not None:
                raise ValueError("Docker selection cannot include NemoClaw config")
            return
        raise ValueError("operator selection requires NemoClaw or Docker")


class SandboxOutputLimitExceeded(RuntimeError):
    """The untrusted process crossed the stdout evidence limit."""


class SandboxRuntimeUnavailable(RuntimeError):
    """A fixed startup check failed without exposing subprocess diagnostics."""

    def __init__(self, sandbox: SandboxKind, check: str) -> None:
        self.sandbox = sandbox
        self.check = check
        super().__init__(f"{sandbox.value} sandbox failed {check} readiness")


class SandboxCommandExecutor(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
    ) -> CompletedSandboxCommand: ...


class SubprocessSandboxCommandExecutor:
    """Run fixed argv with bounded stdout and no captured diagnostics."""

    def __init__(
        self,
        *,
        launcher_environment: Mapping[str, str] | None = None,
    ) -> None:
        additions = dict(launcher_environment or {})
        if set(additions) - {"COMPOSE_DISABLE_ENV_FILE"}:
            raise ValueError("sandbox launcher environment is outside the allowlist")
        if additions.get("COMPOSE_DISABLE_ENV_FILE") not in {None, "1"}:
            raise ValueError("Compose environment-file discovery must be disabled")
        if any(
            not value or "\x00" in value or len(value) > 200
            for value in additions.values()
        ):
            raise ValueError("sandbox launcher environment is invalid")
        self._launcher_environment = additions

    def run(
        self,
        command: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
    ) -> CompletedSandboxCommand:
        process = subprocess.Popen(
            tuple(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            start_new_session=True,
            bufsize=0,
            env=_sandbox_launcher_environment(
                command,
                additions=self._launcher_environment,
            ),
        )
        try:
            stdout = _communicate_bounded(
                process,
                input_bytes=input_bytes,
                timeout_seconds=timeout_seconds,
                maximum_stdout_bytes=MAX_SANDBOX_RESULT_BYTES,
            )
        except BaseException:
            _kill_process_group(process)
            raise
        return CompletedSandboxCommand(returncode=process.returncode, stdout=stdout)


class DockerSandboxCommandExecutor(SubprocessSandboxCommandExecutor):
    """Ensure a timed-out Docker CLI cannot leave its named worker running."""

    def run(
        self,
        command: Sequence[str],
        *,
        input_bytes: bytes,
        timeout_seconds: float,
    ) -> CompletedSandboxCommand:
        try:
            return super().run(
                command,
                input_bytes=input_bytes,
                timeout_seconds=timeout_seconds,
            )
        except BaseException:
            container_name = _docker_run_container_name(command)
            if container_name is not None:
                try:
                    super().run(
                        (command[0], "rm", "--force", container_name),
                        input_bytes=b"",
                        timeout_seconds=10.0,
                    )
                except BaseException:
                    # The original outcome remains authoritative. The durable
                    # run is still failed closed and its capability is drained.
                    pass
            raise


def _docker_run_container_name(command: Sequence[str]) -> str | None:
    try:
        position = command.index("--name")
        value = command[position + 1]
    except (IndexError, ValueError):
        return None
    if not value or re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,199}", value) is None:
        return None
    return value


def _sandbox_launcher_environment(
    command: Sequence[str],
    *,
    additions: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the small reviewed host environment exposed to a sandbox CLI."""

    environment = {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    command_path = Path(command[0]) if command else None
    path_entries = []
    if command_path is not None and command_path.is_absolute():
        path_entries.append(str(command_path.parent))
    path_entries.extend(
        ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin")
    )
    environment["PATH"] = os.pathsep.join(dict.fromkeys(path_entries))

    # NemoClaw needs its host-local configuration identity, but never backend
    # configuration. Keep this allowlist deliberately small and bounded.
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
    environment.update(additions or {})
    return environment


def _communicate_bounded(
    process: subprocess.Popen[bytes],
    *,
    input_bytes: bytes,
    timeout_seconds: float,
    maximum_stdout_bytes: int,
) -> bytes:
    if process.stdin is None or process.stdout is None:
        raise RuntimeError("sandbox process pipes are unavailable")
    selector = selectors.DefaultSelector()
    stdin = process.stdin
    stdout = process.stdout
    os.set_blocking(stdin.fileno(), False)
    os.set_blocking(stdout.fileno(), False)
    output = bytearray()
    sent = 0
    stdin_open = True
    stdout_open = True
    selector.register(stdout, selectors.EVENT_READ, "stdout")
    if input_bytes:
        selector.register(stdin, selectors.EVENT_WRITE, "stdin")
    else:
        stdin.close()
        stdin_open = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while stdout_open or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            events = selector.select(min(remaining, 0.25))
            if not events and process.poll() is not None and stdin_open:
                selector.unregister(stdin)
                stdin.close()
                stdin_open = False
            for key, _mask in events:
                if key.data == "stdin":
                    try:
                        written = os.write(
                            stdin.fileno(),
                            input_bytes[sent : sent + 65_536],
                        )
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        written = 0
                        sent = len(input_bytes)
                    else:
                        sent += written
                    if sent >= len(input_bytes):
                        selector.unregister(stdin)
                        stdin.close()
                        stdin_open = False
                    continue
                try:
                    chunk = os.read(stdout.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stdout)
                    stdout.close()
                    stdout_open = False
                    continue
                output.extend(chunk)
                if len(output) > maximum_stdout_bytes:
                    raise SandboxOutputLimitExceeded(
                        "sandbox stdout exceeds the evidence limit"
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds)
        process.wait(timeout=remaining)
        return bytes(output)
    finally:
        selector.close()
        if stdin_open:
            stdin.close()
        if stdout_open:
            stdout.close()


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (AttributeError, OSError):
        if process.poll() is None:
            process.kill()
    try:
        if process.poll() is None:
            process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()


def _validated_docker_compose_path(value: str | Path) -> Path:
    compose_path = Path(value)
    if not compose_path.is_absolute():
        raise ValueError("Docker compose path must be absolute")
    if compose_path != DOCKER_REVIEWED_COMPOSE_FILE:
        raise ValueError("Docker compose path is not the reviewed profile")
    try:
        metadata = compose_path.lstat()
    except OSError as exc:
        raise ValueError("Docker compose file is missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_size <= 0
        or metadata.st_size > MAX_DOCKER_COMPOSE_BYTES
    ):
        raise ValueError("Docker compose file is unsafe")
    try:
        payload = yaml.safe_load(_read_reviewed_docker_asset(compose_path))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("Docker compose file is invalid") from exc
    _validate_docker_compose_profile(payload)
    _validate_reviewed_docker_assets()
    return compose_path


def _validate_docker_compose_profile(payload: object) -> None:
    """Accept only the complete reviewed containment profile."""

    profile = _reviewed_mapping(payload, label="Docker compose profile")
    _require_exact_keys(profile, {"services", "networks"}, label="compose")
    services = _reviewed_mapping(profile["services"], label="Docker services")
    _require_exact_keys(
        services,
        {"agent", *DOCKER_REQUIRED_GATEWAY_SERVICES},
        label="services",
    )
    networks = _reviewed_mapping(profile["networks"], label="Docker networks")
    if networks != {
        "agent-internal": {"internal": True, "attachable": False},
        "host-egress": {},
    }:
        raise ValueError("Docker networks differ from the reviewed profile")

    agent = _reviewed_mapping(services["agent"], label="Docker agent")
    _require_exact_keys(
        agent,
        {
            "build",
            "entrypoint",
            "user",
            "depends_on",
            "networks",
            "read_only",
            "tmpfs",
            "cap_drop",
            "security_opt",
            "pids_limit",
            "mem_limit",
            "cpus",
            "ulimits",
            "init",
            "stop_grace_period",
        },
        label="agent service",
    )
    _validate_reviewed_build(agent["build"], target="agent")
    if {
        "entrypoint": agent["entrypoint"],
        "user": agent["user"],
        "depends_on": agent["depends_on"],
        "networks": agent["networks"],
        "read_only": agent["read_only"],
        "tmpfs": agent["tmpfs"],
        "cap_drop": agent["cap_drop"],
        "security_opt": agent["security_opt"],
        "pids_limit": agent["pids_limit"],
        "mem_limit": agent["mem_limit"],
        "cpus": agent["cpus"],
        "ulimits": agent["ulimits"],
        "init": agent["init"],
        "stop_grace_period": agent["stop_grace_period"],
    } != {
        "entrypoint": ["python", "-m", "vital_relay.agent.worker"],
        "user": "65532:65532",
        "depends_on": {
            "vllm-gateway": {"condition": "service_healthy"},
            "tool-proxy-gateway": {"condition": "service_healthy"},
        },
        "networks": ["agent-internal"],
        "read_only": True,
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 96,
        "mem_limit": "1g",
        "cpus": 1.0,
        "ulimits": {"nofile": {"soft": 256, "hard": 256}},
        "init": True,
        "stop_grace_period": "2s",
    }:
        raise ValueError("Docker agent differs from the reviewed profile")

    _validate_reviewed_gateway(
        services["vllm-gateway"],
        service_name="vllm-gateway",
        target="vllm_gateway",
        environment={
            "VLLM_UPSTREAM_HOST": DOCKER_VLLM_UPSTREAM_HOST,
            "VLLM_UPSTREAM_PORT": DOCKER_VLLM_UPSTREAM_PORT,
            "VLLM_EXPECTED_MODEL": "__VITAL_RELAY_MODEL__",
        },
        healthcheck_command=(
            "python",
            "/gateway/vllm_gateway.py",
            "--check-readiness",
        ),
    )
    _validate_reviewed_gateway(
        services["tool-proxy-gateway"],
        service_name="tool-proxy-gateway",
        target="tool_proxy_gateway",
        environment={
            "TOOL_PROXY_UPSTREAM_SCHEME": DOCKER_TOOL_UPSTREAM_SCHEME,
            "TOOL_PROXY_UPSTREAM_HOST": DOCKER_TOOL_UPSTREAM_HOST,
            "TOOL_PROXY_UPSTREAM_PORT": DOCKER_TOOL_UPSTREAM_PORT,
        },
        healthcheck_command=(
            "python",
            "/gateway/tool_proxy_gateway.py",
            "--check-readiness",
        ),
    )


def _validate_reviewed_gateway(
    value: object,
    *,
    service_name: str,
    target: str,
    environment: dict[str, str],
    healthcheck_command: tuple[str, ...],
) -> None:
    service = _reviewed_mapping(value, label=f"Docker {service_name}")
    _require_exact_keys(
        service,
        {
            "build",
            "environment",
            "extra_hosts",
            "networks",
            "read_only",
            "tmpfs",
            "cap_drop",
            "security_opt",
            "pids_limit",
            "mem_limit",
            "cpus",
            "init",
            "healthcheck",
        },
        label=f"{service_name} service",
    )
    _validate_reviewed_build(service["build"], target=target)
    if {
        "environment": service["environment"],
        "extra_hosts": service["extra_hosts"],
        "networks": service["networks"],
        "read_only": service["read_only"],
        "tmpfs": service["tmpfs"],
        "cap_drop": service["cap_drop"],
        "security_opt": service["security_opt"],
        "pids_limit": service["pids_limit"],
        "mem_limit": service["mem_limit"],
        "cpus": service["cpus"],
        "init": service["init"],
        "healthcheck": service["healthcheck"],
    } != {
        "environment": environment,
        "extra_hosts": ["host.docker.internal:host-gateway"],
        "networks": ["agent-internal", "host-egress"],
        "read_only": True,
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=16m"],
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "pids_limit": 64,
        "mem_limit": "128m",
        "cpus": 0.5,
        "init": True,
        "healthcheck": {
            "test": ["CMD", *healthcheck_command],
            "interval": "5s",
            "timeout": "3s",
            "retries": 6,
            "start_period": "5s",
        },
    }:
        raise ValueError(f"Docker {service_name} differs from the reviewed profile")


def _validate_reviewed_build(value: object, *, target: str) -> None:
    build = _reviewed_mapping(value, label="Docker build")
    if build != {
        "context": "../..",
        "dockerfile": "infrastructure/docker-agent/Dockerfile",
        "target": target,
    }:
        raise ValueError("Docker build provenance differs from the reviewed profile")


def _reviewed_mapping(value: object, *, label: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value


def _require_exact_keys(
    value: dict[object, object],
    expected: set[str],
    *,
    label: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"Docker {label} contains unreviewed keys")


def _validate_reviewed_docker_assets() -> ReviewedDockerSnapshot:
    """Capture and content-validate the complete reviewed runtime input set."""

    snapshot = _capture_reviewed_docker_assets()
    if snapshot.reviewed_worker_tree_sha256 != DOCKER_REVIEWED_WORKER_TREE_SHA256:
        raise ValueError("reviewed Docker worker source changed")
    return snapshot


def _capture_reviewed_docker_assets() -> ReviewedDockerSnapshot:
    """Capture the exact boundary snapshot before the final cross-lane pin gate."""

    files: dict[str, bytes] = {}
    for relative_name in DOCKER_REVIEWED_ASSET_SHA256:
        files[relative_name] = _read_reviewed_docker_asset(
            DOCKER_PROJECT_ROOT / relative_name
        )
    worker_snapshot = capture_reviewed_source_snapshot(
        DOCKER_PROJECT_ROOT,
        DOCKER_AGENT_SOURCE_MANIFEST,
    )
    for path, raw in worker_snapshot.files:
        files[f"backend/src/{path}"] = raw
    reviewed_worker_manifest_json = json.dumps(
        {
            "digest": worker_snapshot.digest,
            "entrypoint": worker_snapshot.manifest.entrypoint,
            "files": [
                {
                    "path": path,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                for path, raw in worker_snapshot.files
            ],
            "name": worker_snapshot.manifest.name,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    _validate_reviewed_snapshot_content(files)
    digest = _docker_content_digest(files)
    return ReviewedDockerSnapshot(
        digest=digest,
        files=tuple(sorted(files.items())),
        reviewed_worker_tree_sha256=worker_snapshot.digest,
        reviewed_worker_manifest_sha256=hashlib.sha256(
            reviewed_worker_manifest_json.encode("utf-8")
        ).hexdigest(),
        reviewed_worker_manifest_json=reviewed_worker_manifest_json,
    )


def _validate_reviewed_snapshot_content(files: Mapping[str, bytes]) -> None:
    for relative_name, expected_sha256 in DOCKER_REVIEWED_ASSET_SHA256.items():
        raw = files.get(relative_name)
        if raw is None or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError(f"reviewed Docker asset changed: {relative_name}")
    worker_prefix = "backend/src/"
    worker_files = {
        name.removeprefix(worker_prefix): raw
        for name, raw in files.items()
        if name.startswith(worker_prefix)
    }
    validate_source_import_closure(DOCKER_AGENT_SOURCE_MANIFEST, worker_files)
    expected_paths = {
        f"backend/src/{path}" for path in DOCKER_AGENT_SOURCE_MANIFEST.source_paths
    } | set(DOCKER_REVIEWED_ASSET_SHA256)
    if set(files) != expected_paths:
        raise ValueError("reviewed Docker snapshot contains unexpected paths")


def _read_reviewed_docker_asset(path: Path) -> bytes:
    descriptor = _open_reviewed_docker_asset(path)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_DOCKER_REVIEWED_ASSET_BYTES
        ):
            raise ValueError("reviewed Docker asset is unsafe")
        chunks = bytearray()
        while len(chunks) <= MAX_DOCKER_REVIEWED_ASSET_BYTES:
            chunk = os.read(
                descriptor,
                min(
                    65_536,
                    MAX_DOCKER_REVIEWED_ASSET_BYTES + 1 - len(chunks),
                ),
            )
            if not chunk:
                break
            chunks.extend(chunk)
        if not chunks or len(chunks) > MAX_DOCKER_REVIEWED_ASSET_BYTES:
            raise ValueError("reviewed Docker asset is unsafe")
        return bytes(chunks)
    except OSError as exc:
        raise ValueError("reviewed Docker asset is unreadable") from exc
    finally:
        os.close(descriptor)


def _open_reviewed_docker_asset(path: Path) -> int:
    """Traverse every component through no-follow directory descriptors."""

    try:
        relative = path.relative_to(DOCKER_PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("reviewed Docker path escaped the project") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("reviewed Docker path is invalid")
    common_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = common_flags | nofollow | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(DOCKER_PROJECT_ROOT, directory_flags)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(
                component,
                directory_flags,
                dir_fd=current,
            )
            descriptors.append(current)
        return os.open(
            relative.parts[-1],
            common_flags | nofollow,
            dir_fd=current,
        )
    except OSError as exc:
        raise ValueError("reviewed Docker asset is missing or unsafe") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _reviewed_worker_files(root: Path) -> dict[Path, bytes]:
    if root != DOCKER_PROJECT_ROOT / "backend/src/vital_relay":
        raise ValueError("Docker worker source is not the active reviewed checkout")
    snapshot = capture_reviewed_source_snapshot(
        DOCKER_PROJECT_ROOT,
        DOCKER_AGENT_SOURCE_MANIFEST,
    )
    return {
        DOCKER_PROJECT_ROOT / "backend/src" / path: raw
        for path, raw in snapshot.files
    }


def _reviewed_worker_tree_sha256(root: Path) -> str:
    if root != DOCKER_PROJECT_ROOT / "backend/src/vital_relay":
        raise ValueError("Docker worker source is not the active reviewed checkout")
    return capture_reviewed_source_snapshot(
        DOCKER_PROJECT_ROOT,
        DOCKER_AGENT_SOURCE_MANIFEST,
    ).digest


def _docker_content_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative_name, raw in sorted(files.items()):
        encoded_name = relative_name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    if not files:
        raise ValueError("reviewed Docker content set is empty")
    return digest.hexdigest()


def _stage_reviewed_docker_snapshot(
    snapshot: ReviewedDockerSnapshot,
    *,
    owned_roots: list[Path],
) -> Path:
    """Write captured bytes once into a digest-addressed read-only context."""

    if _docker_content_digest(dict(snapshot.files)) != snapshot.digest:
        raise ValueError("reviewed Docker snapshot digest is invalid")
    staging_root = Path(
        tempfile.mkdtemp(prefix="vital-relay-agent-build-")
    ).resolve()
    owned_roots.append(staging_root)
    context = staging_root / snapshot.digest
    context.mkdir(mode=0o700)
    directories = {context}
    for relative_name, raw in snapshot.files:
        target = context / relative_name
        if target.parent not in directories:
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            directories.update(target.parents)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(target, flags, 0o400)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    for directory in sorted(
        directories,
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if directory == staging_root or staging_root not in directory.parents:
            continue
        directory.chmod(0o500)
    _validate_staged_docker_snapshot(context, snapshot)
    return context


def _validate_staged_docker_snapshot(
    context: Path,
    snapshot: ReviewedDockerSnapshot,
) -> None:
    """Inspect the actual build context after staging, including all paths."""

    expected = dict(snapshot.files)
    actual: dict[str, bytes] = {}
    for directory, directory_names, file_names in os.walk(
        context,
        topdown=True,
        followlinks=False,
    ):
        active = Path(directory)
        if stat.S_ISLNK(active.lstat().st_mode):
            raise ValueError("staged Docker context contains a symlink")
        for name in directory_names:
            child = active / name
            metadata = child.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError("staged Docker context directory is unsafe")
        for name in file_names:
            child = active / name
            metadata = child.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError("staged Docker context file is unsafe")
            relative = child.relative_to(context).as_posix()
            descriptor = os.open(
                child,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                raw = os.read(descriptor, MAX_DOCKER_REVIEWED_ASSET_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > MAX_DOCKER_REVIEWED_ASSET_BYTES:
                raise ValueError("staged Docker context file is oversized")
            actual[relative] = raw
    if actual != expected:
        raise ValueError("staged Docker context differs from the exact snapshot")
    if _docker_content_digest(actual) != snapshot.digest:
        raise ValueError("staged Docker context digest is not deterministic")
    worker_files = {
        path.removeprefix("backend/src/"): raw
        for path, raw in expected.items()
        if path.startswith("backend/src/")
    }
    worker_snapshot = ReviewedSourceSnapshot(
        manifest=DOCKER_AGENT_SOURCE_MANIFEST,
        digest=source_content_digest(worker_files),
        files=tuple(sorted(worker_files.items())),
    )
    validate_staged_source_tree(context / "backend/src", worker_snapshot)


def _docker_image_tags(snapshot: ReviewedDockerSnapshot) -> dict[str, str]:
    prefix = f"vital-relay-reviewed:{snapshot.digest[:32]}"
    return {
        target: f"{prefix}-{target.replace('_', '-')}"
        for target in DOCKER_BUILD_TARGETS
    }


def _docker_runtime_graph(
    snapshot: ReviewedDockerSnapshot,
    *,
    image_ids: Mapping[str, str],
    model: str,
) -> dict[str, object]:
    """Construct the only runtime graph from the content-locked build profile."""

    try:
        compose_raw = dict(snapshot.files)[
            "infrastructure/docker-agent/compose.yaml"
        ]
        graph = yaml.safe_load(compose_raw)
    except (KeyError, yaml.YAMLError) as exc:
        raise ValueError("reviewed Docker compose capture is invalid") from exc
    _validate_docker_compose_profile(graph)
    services = graph["services"]
    target_by_service = {
        "agent": "agent",
        "vllm-gateway": "vllm_gateway",
        "tool-proxy-gateway": "tool_proxy_gateway",
    }
    for service_name, target in target_by_service.items():
        image_id = image_ids.get(target)
        if image_id is None or DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None:
            raise ValueError("Docker runtime image ID is invalid")
        service = services[service_name]
        del service["build"]
        service["image"] = image_id
    services["vllm-gateway"]["environment"]["VLLM_EXPECTED_MODEL"] = model
    return graph


def _validate_rendered_docker_graph(
    rendered: bytes,
    *,
    expected: Mapping[str, object],
    project_name: str,
) -> None:
    try:
        payload = json.loads(rendered)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("rendered Docker config is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("rendered Docker config is not a mapping")
    actual = dict(payload)
    rendered_name = actual.pop("name", project_name)
    if rendered_name != project_name:
        raise ValueError("rendered Docker project identity drifted")
    if actual != expected:
        raise ValueError("rendered Docker graph differs from the reviewed graph")
    forbidden = {
        "build",
        "cap_add",
        "configs",
        "devices",
        "external",
        "ipc",
        "pid",
        "privileged",
        "secrets",
        "userns_mode",
        "uts",
        "volumes",
        "volumes_from",
    }

    def reject_forbidden(value: object) -> None:
        if isinstance(value, dict):
            if set(value) & forbidden:
                raise ValueError("rendered Docker graph contains an escape")
            for child in value.values():
                reject_forbidden(child)
        elif isinstance(value, list):
            for child in value:
                reject_forbidden(child)

    reject_forbidden(actual)


def _stage_docker_runtime_profile(
    *,
    graph: Mapping[str, object],
    digest: str,
    owned_roots: list[Path],
) -> Path:
    root = Path(
        tempfile.mkdtemp(prefix="vital-relay-agent-profile-")
    ).resolve()
    owned_roots.append(root)
    profile_root = root / digest
    profile_root.mkdir(mode=0o700)
    compose_path = profile_root / "compose.json"
    raw = json.dumps(
        graph,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    _write_new_readonly_file(compose_path, raw)
    profile_root.chmod(0o500)
    return compose_path


def _write_new_readonly_file(path: Path, raw: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_owned_docker_temp_root(root: Path) -> bool:
    """Remove only one canonical launcher-created temp root."""

    allowed_prefixes = (
        "vital-relay-agent-build-",
        "vital-relay-agent-profile-",
    )
    temp_root = Path(tempfile.gettempdir()).resolve()
    if (
        not root.is_absolute()
        or root != root.resolve(strict=False)
        or root.parent != temp_root
        or not root.name.startswith(allowed_prefixes)
    ):
        raise ValueError("Docker cleanup target is not an owned temp root")
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("Docker cleanup target is unreadable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("Docker cleanup target is unsafe")
    for directory, directory_names, _file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        active_directory = Path(directory)
        active_metadata = active_directory.lstat()
        if stat.S_ISLNK(active_metadata.st_mode):
            raise ValueError("Docker cleanup tree contains a directory symlink")
        active_directory.chmod(0o700, follow_symlinks=False)
        for name in directory_names:
            child = active_directory / name
            child_metadata = child.lstat()
            if stat.S_ISDIR(child_metadata.st_mode) and not stat.S_ISLNK(
                child_metadata.st_mode
            ):
                child.chmod(0o700, follow_symlinks=False)
    shutil.rmtree(root)
    return True


def _docker_image_id(value: bytes) -> str:
    try:
        text = value.decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Docker image ID is invalid") from exc
    lines = text.splitlines()
    if len(lines) != 1 or text not in {lines[0], f"{lines[0]}\n", f"{lines[0]}\r\n"}:
        raise ValueError("Docker image ID is invalid")
    image_id = lines[0]
    if DOCKER_IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise ValueError("Docker image ID is invalid")
    return image_id


def _healthy_docker_service_containers(value: bytes) -> dict[str, str]:
    try:
        text = value.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Docker readiness output is not JSON") from exc
    try:
        decoded = json.loads(text)
        entries = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        try:
            entries = [json.loads(line) for line in text.splitlines() if line]
        except json.JSONDecodeError as exc:
            raise ValueError("Docker readiness output is not JSON") from exc
    containers: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Docker readiness entry is invalid")
        service = entry.get("Service") or entry.get("service")
        state = entry.get("State") or entry.get("state")
        health = entry.get("Health") or entry.get("health")
        container_id = entry.get("ID") or entry.get("Id") or entry.get("id")
        if (
            not isinstance(service, str)
            or not isinstance(container_id, str)
            or re.fullmatch(r"[0-9a-f]{12,64}", container_id) is None
            or str(state).lower() != "running"
            or str(health).lower() != "healthy"
            or service in containers
        ):
            raise ValueError("Docker gateway readiness entry is invalid")
        containers[service] = container_id
    if set(containers) != DOCKER_REQUIRED_GATEWAY_SERVICES:
        raise ValueError("Docker gateways are incomplete")
    return containers


def _healthy_docker_services(value: bytes) -> frozenset[str]:
    try:
        text = value.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("Docker readiness output is not UTF-8") from exc
    try:
        decoded = json.loads(text)
        entries = decoded if isinstance(decoded, list) else [decoded]
    except json.JSONDecodeError:
        try:
            entries = [json.loads(line) for line in text.splitlines() if line]
        except json.JSONDecodeError as exc:
            raise ValueError("Docker readiness output is not JSON") from exc
    healthy: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Docker readiness entry is invalid")
        service = entry.get("Service") or entry.get("service")
        state = entry.get("State") or entry.get("state")
        health = entry.get("Health") or entry.get("health")
        if (
            isinstance(service, str)
            and str(state).lower() == "running"
            and str(health).lower() == "healthy"
        ):
            healthy.add(service)
    return frozenset(healthy)


class ProcessSandboxAgentRunner:
    """Execute the real Deep Agent worker through a reviewed sandbox command."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        startup_checks: Sequence[SandboxStartupCheck],
        docker_container_name_prefix: str | None = None,
        docker_snapshot: ReviewedDockerSnapshot | None = None,
        docker_executable: str | None = None,
        settings: VLLMSettings,
        tool_proxy_endpoint: str,
        sandbox: SandboxKind,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        executor: SandboxCommandExecutor | None = None,
        clock: Clock | None = None,
    ) -> None:
        if sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("process runner cannot claim an in-process sandbox")
        if not command or any(
            not part or "\x00" in part or len(part) > 4_096 for part in command
        ):
            raise ValueError("sandbox command must be a bounded argv vector")
        if not 1.0 <= timeout_seconds <= 300.0:
            raise ValueError("sandbox timeout must be between 1 and 300 seconds")
        # Validate the endpoint at construction rather than after a run is stored.
        SandboxWorkerEnvelope.validate_tool_proxy_endpoint(tool_proxy_endpoint)
        self._command = tuple(command)
        self._startup_checks = tuple(startup_checks)
        self._docker_container_name_prefix = docker_container_name_prefix
        self._docker_snapshot = docker_snapshot
        self._docker_executable = docker_executable
        self._docker_runtime: DockerRuntimeProfile | None = None
        self._docker_runtime_executor: SandboxCommandExecutor | None = None
        self._docker_owned_temp_roots: tuple[Path, ...] = ()
        self._docker_unresolved_projects: tuple[DockerOwnedProject, ...] = ()
        self._docker_unresolved_temp_roots: tuple[Path, ...] = ()
        self._cleanup_history: tuple[SandboxCleanupEvidence, ...] = ()
        self._last_cleanup_evidence: SandboxCleanupEvidence | None = None
        self._cleanup_in_progress = False
        self._closed = False
        self._lifecycle_lock = threading.Lock()
        self._settings = settings
        self._tool_proxy_endpoint = tool_proxy_endpoint
        self._sandbox = sandbox
        self._timeout_seconds = timeout_seconds
        self._executor = executor or SubprocessSandboxCommandExecutor()
        self._clock = clock or SystemClock()

    @classmethod
    def selected(
        cls,
        selection: ProcessSandboxSelection,
        *,
        settings: VLLMSettings,
        tool_proxy_endpoint: str,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        nemoclaw_executable: str = NEMOCLAW_HOST_CLI_EXECUTABLE,
        docker_executable: str = "docker",
        executor: SandboxCommandExecutor | None = None,
        clock: Clock | None = None,
    ) -> ProcessSandboxAgentRunner:
        """Construct only the explicitly selected runner.

        This method never probes another runtime and never catches one
        substrate's construction failure to choose the other.
        """

        if selection.sandbox is SandboxKind.NEMOCLAW:
            assert selection.nemoclaw_sandbox_name is not None
            return cls.nemoclaw(
                sandbox_name=selection.nemoclaw_sandbox_name,
                settings=settings,
                tool_proxy_endpoint=tool_proxy_endpoint,
                timeout_seconds=timeout_seconds,
                executable=nemoclaw_executable,
                executor=executor,
                clock=clock,
            )
        if selection.sandbox is SandboxKind.DOCKER:
            assert selection.docker_compose_file is not None
            return cls.docker(
                compose_file=selection.docker_compose_file,
                settings=settings,
                tool_proxy_endpoint=tool_proxy_endpoint,
                timeout_seconds=timeout_seconds,
                executable=docker_executable,
                executor=executor,
                clock=clock,
            )
        raise ValueError("operator selection requires NemoClaw or Docker")

    @classmethod
    def nemoclaw(
        cls,
        *,
        sandbox_name: str,
        settings: VLLMSettings,
        tool_proxy_endpoint: str,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        executable: str = NEMOCLAW_HOST_CLI_EXECUTABLE,
        executor: SandboxCommandExecutor | None = None,
        clock: Clock | None = None,
    ) -> ProcessSandboxAgentRunner:
        if not Path(executable).is_absolute():
            raise ValueError("NemoClaw host CLI executable must be absolute")
        if NEMOCLAW_SANDBOX_NAME_PATTERN.fullmatch(sandbox_name) is None:
            raise ValueError("invalid NemoClaw sandbox name")
        if settings.base_url != NEMOCLAW_INFERENCE_BASE_URL:
            raise ValueError("NemoClaw inference must use its managed route")
        if (
            settings.api_key.get_secret_value()
            != NEMOCLAW_MANAGED_INFERENCE_API_KEY
        ):
            raise ValueError(
                "NemoClaw inference must use its non-secret managed placeholder"
            )
        if settings.max_retries != 0 or settings.temperature != 0.0:
            raise ValueError("NemoClaw runs require zero retries and temperature")
        inner_timeout = str(max(1, int(timeout_seconds)))
        return cls(
            command=(
                executable,
                sandbox_name,
                "exec",
                "--no-tty",
                "--timeout",
                inner_timeout,
                "--stdin",
                "--",
                NEMOCLAW_MANAGED_EXEC_LAUNCHER,
                NEMOCLAW_WORKER_EXECUTABLE,
            ),
            startup_checks=(
                SandboxStartupCheck(
                    "nemoclaw_status",
                    (executable, sandbox_name, "status"),
                ),
                SandboxStartupCheck(
                    "nemoclaw_doctor",
                    (executable, sandbox_name, "doctor"),
                ),
            ),
            docker_container_name_prefix=None,
            settings=settings,
            tool_proxy_endpoint=tool_proxy_endpoint,
            sandbox=SandboxKind.NEMOCLAW,
            timeout_seconds=timeout_seconds,
            executor=executor,
            clock=clock,
        )

    @classmethod
    def docker(
        cls,
        *,
        compose_file: str | Path,
        settings: VLLMSettings,
        tool_proxy_endpoint: str,
        timeout_seconds: float = DEFAULT_SANDBOX_TIMEOUT_SECONDS,
        executable: str = "docker",
        executor: SandboxCommandExecutor | None = None,
        clock: Clock | None = None,
    ) -> ProcessSandboxAgentRunner:
        _validated_docker_compose_path(compose_file)
        snapshot = _validate_reviewed_docker_assets()
        if executable != "docker":
            raise ValueError("Docker executable must use the reviewed PATH lookup")
        if settings.base_url != DOCKER_INFERENCE_BASE_URL:
            raise ValueError("Docker inference must use the fixed vLLM gateway")
        if settings.max_retries != 0 or settings.temperature != 0.0:
            raise ValueError("Docker runs require zero retries and temperature")
        if tool_proxy_endpoint != DOCKER_TOOL_PROXY_ENDPOINT:
            raise ValueError("Docker tools must use the fixed tool proxy gateway")
        return cls(
            command=(executable,),
            startup_checks=(),
            docker_container_name_prefix=None,
            docker_snapshot=snapshot,
            docker_executable=executable,
            settings=settings,
            tool_proxy_endpoint=tool_proxy_endpoint,
            sandbox=SandboxKind.DOCKER,
            timeout_seconds=timeout_seconds,
            executor=executor
            or DockerSandboxCommandExecutor(
                launcher_environment={"COMPOSE_DISABLE_ENV_FILE": "1"}
            ),
            clock=clock,
        )

    @property
    def model_id(self) -> str:
        return self._settings.model

    @property
    def sandbox(self) -> SandboxKind:
        return self._sandbox

    @property
    def last_cleanup_evidence(self) -> SandboxCleanupEvidence | None:
        return self._last_cleanup_evidence

    @property
    def cleanup_history(self) -> tuple[SandboxCleanupEvidence, ...]:
        """Return append-only per-attempt cleanup evidence."""

        return self._cleanup_history

    def close(
        self,
        *,
        timeout_seconds: float = DEFAULT_SANDBOX_READINESS_TIMEOUT_SECONDS,
    ) -> SandboxCleanupEvidence:
        """Fence future use and release only this runner's exact resources."""

        if not 1.0 <= timeout_seconds <= 600.0:
            raise ValueError("cleanup timeout must be between 1 and 600 seconds")
        with self._lifecycle_lock:
            first_close = not self._closed
            self._closed = True
            if self._sandbox is not SandboxKind.DOCKER:
                if self._last_cleanup_evidence is not None:
                    evidence = replace(
                        self._last_cleanup_evidence,
                        already_closed=not first_close,
                    )
                else:
                    evidence = SandboxCleanupEvidence(
                        sandbox=self._sandbox,
                        checked_at=self._aware_now(),
                        completed_checks=("closed",),
                        failed_checks=(),
                        already_closed=not first_close,
                    )
                self._last_cleanup_evidence = evidence
                return evidence

            if first_close:
                if self._docker_runtime is not None:
                    self._docker_unresolved_projects += (
                        DockerOwnedProject(
                            runtime=self._docker_runtime,
                            executor=(
                                self._docker_runtime_executor or self._executor
                            ),
                        ),
                    )
                self._docker_unresolved_temp_roots = tuple(
                    dict.fromkeys(
                        (
                            *self._docker_unresolved_temp_roots,
                            *self._docker_owned_temp_roots,
                        )
                    )
                )
                self._docker_runtime = None
                self._docker_runtime_executor = None
                self._docker_owned_temp_roots = ()

            if not (
                self._docker_unresolved_projects
                or self._docker_unresolved_temp_roots
            ) and not first_close:
                if self._last_cleanup_evidence is None:
                    raise RuntimeError("closed runner is missing cleanup evidence")
                evidence = replace(
                    self._last_cleanup_evidence,
                    already_closed=True,
                )
                self._last_cleanup_evidence = evidence
                return evidence

            self._cleanup_in_progress = True
            try:
                outcome = self._cleanup_docker_resources(
                    owned_projects=self._docker_unresolved_projects,
                    owned_roots=self._docker_unresolved_temp_roots,
                    timeout_seconds=timeout_seconds,
                    runner_closed=True,
                )
                return self._record_docker_cleanup_outcome(
                    outcome,
                    already_closed=not first_close,
                )
            finally:
                self._cleanup_in_progress = False

    def validate_startup(
        self,
        *,
        executor: SandboxCommandExecutor | None = None,
        timeout_seconds: float = DEFAULT_SANDBOX_READINESS_TIMEOUT_SECONDS,
    ) -> SandboxStartupEvidence:
        """Fail startup unless the one selected runtime passes fixed checks."""

        with self._lifecycle_lock:
            return self._validate_startup_locked(
                executor=executor,
                timeout_seconds=timeout_seconds,
            )

    def _validate_startup_locked(
        self,
        *,
        executor: SandboxCommandExecutor | None,
        timeout_seconds: float,
    ) -> SandboxStartupEvidence:
        if self._closed:
            raise SandboxRuntimeUnavailable(self._sandbox, "sandbox_closed")
        if self._cleanup_in_progress or (
            self._docker_unresolved_projects
            or self._docker_unresolved_temp_roots
        ):
            raise SandboxRuntimeUnavailable(
                self._sandbox, "docker_cleanup_incomplete"
            )
        if self._docker_runtime is not None:
            raise SandboxRuntimeUnavailable(
                self._sandbox, "docker_already_started"
            )

        if not 1.0 <= timeout_seconds <= 600.0:
            raise ValueError("readiness timeout must be between 1 and 600 seconds")
        active_executor = executor or self._executor
        if self._sandbox is SandboxKind.DOCKER:
            return self._validate_docker_startup(
                executor=active_executor,
                timeout_seconds=timeout_seconds,
            )
        completed_checks: list[str] = []
        for check in self._startup_checks:
            try:
                completed = active_executor.run(
                    check.command,
                    input_bytes=b"",
                    timeout_seconds=timeout_seconds,
                )
                valid = completed.returncode == 0
                if valid and check.expected_healthy_services:
                    valid = (
                        _healthy_docker_services(completed.stdout)
                        == check.expected_healthy_services
                    )
            except Exception:
                valid = False
            if not valid:
                raise SandboxRuntimeUnavailable(self._sandbox, check.name)
            completed_checks.append(check.name)
        return SandboxStartupEvidence(
            sandbox=self._sandbox,
            checked_at=self._aware_now(),
            completed_checks=tuple(completed_checks),
        )

    def run(
        self,
        request: AgentRunRequest,
        tools: BoundedToolGateway,
        *,
        policy_snapshot: CoordinationPolicySnapshot,
        invocation_context: ToolInvocationContext,
        selected_context: SelectedContext,
    ) -> AgentRunResult:
        with self._lifecycle_lock:
            return self._run_locked(
                request,
                tools,
                policy_snapshot=policy_snapshot,
                invocation_context=invocation_context,
                selected_context=selected_context,
            )

    def _run_locked(
        self,
        request: AgentRunRequest,
        tools: BoundedToolGateway,
        *,
        policy_snapshot: CoordinationPolicySnapshot,
        invocation_context: ToolInvocationContext,
        selected_context: SelectedContext,
    ) -> AgentRunResult:
        started_at = self._aware_now()
        if self._closed or self._cleanup_in_progress or (
            self._docker_unresolved_projects
            or self._docker_unresolved_temp_roots
        ):
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        if not self._valid_host_inputs(
            request,
            tools,
            policy_snapshot=policy_snapshot,
            invocation_context=invocation_context,
            selected_context=selected_context,
        ):
            return self._manual(request, started_at, AgentFailureCode.POLICY_INVALID)
        envelope = SandboxWorkerEnvelope(
            schema_version=SANDBOX_WIRE_SCHEMA_VERSION,
            request=request,
            selected_context=selected_context,
            policy_snapshot=policy_snapshot,
            invocation=SandboxInvocationEnvelope.from_context(invocation_context),
            vllm=SandboxVLLMEnvelope.from_settings(self._settings),
            tool_proxy_endpoint=self._tool_proxy_endpoint,
            sandbox=self._sandbox,
        )
        try:
            command = self._command_for_request(request)
        except Exception:
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        try:
            completed = self._executor.run(
                command,
                input_bytes=envelope.to_wire_bytes(),
                timeout_seconds=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._manual(request, started_at, AgentFailureCode.MODEL_TIMEOUT)
        except Exception:
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        if completed.returncode != 0:
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        if not completed.stdout or len(completed.stdout) > MAX_SANDBOX_RESULT_BYTES:
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        try:
            result = AgentRunResult.model_validate_json(completed.stdout)
        except Exception:
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        if (
            result.run_id != request.run_id
            or result.incident_id != request.incident.incident_id
            or result.policy != request.policy
            or result.model_id != self._settings.model
            or result.sandbox is not self._sandbox
        ):
            return self._manual(request, started_at, AgentFailureCode.RUNNER_ERROR)
        return result

    def _command_for_request(self, request: AgentRunRequest) -> tuple[str, ...]:
        if self._sandbox is SandboxKind.DOCKER:
            runtime = self._verify_docker_runtime()
            return (
                *runtime.compose_prefix,
                "run",
                "--rm",
                "--no-deps",
                "--pull",
                "never",
                "--no-TTY",
                "--name",
                f"{runtime.project_name}-worker-{request.run_id}",
                "agent",
            )
        if self._docker_container_name_prefix is None:
            return self._command
        return (
            *self._command,
            "--name",
            f"{self._docker_container_name_prefix}-{request.run_id}",
            "agent",
        )

    def _validate_docker_startup(
        self,
        *,
        executor: SandboxCommandExecutor,
        timeout_seconds: float,
    ) -> SandboxStartupEvidence:
        snapshot = self._docker_snapshot
        executable = self._docker_executable
        if snapshot is None or executable is None:
            raise SandboxRuntimeUnavailable(SandboxKind.DOCKER, "docker_profile")
        try:
            active_snapshot = _validate_reviewed_docker_assets()
            if (
                active_snapshot.digest != snapshot.digest
                or snapshot.reviewed_worker_tree_sha256
                != DOCKER_REVIEWED_WORKER_TREE_SHA256
                or active_snapshot.reviewed_worker_tree_sha256
                != snapshot.reviewed_worker_tree_sha256
                or active_snapshot.reviewed_worker_manifest_sha256
                != snapshot.reviewed_worker_manifest_sha256
                or active_snapshot.reviewed_worker_manifest_json
                != snapshot.reviewed_worker_manifest_json
            ):
                raise ValueError("reviewed Docker source changed after construction")
        except Exception as exc:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, "docker_source_snapshot"
            ) from exc
        attempt = DockerStartupAttempt()
        try:
            completed_checks = self._start_docker_runtime_attempt(
                attempt=attempt,
                snapshot=snapshot,
                executable=executable,
                executor=executor,
                timeout_seconds=timeout_seconds,
            )
            checked_at = self._aware_now()
        except BaseException:
            owned_projects = (
                (
                    DockerOwnedProject(
                        runtime=attempt.runtime,
                        executor=executor,
                    ),
                )
                if attempt.project_may_exist and attempt.runtime is not None
                else ()
            )
            self._cleanup_in_progress = True
            try:
                try:
                    outcome = self._cleanup_docker_resources(
                        owned_projects=owned_projects,
                        owned_roots=tuple(attempt.owned_temp_roots),
                        timeout_seconds=timeout_seconds,
                        runner_closed=False,
                    )
                except BaseException:
                    unresolved_checks = (
                        (("docker_project_down",) if owned_projects else ())
                        + (
                            ("docker_temp_roots",)
                            if attempt.owned_temp_roots
                            else ()
                        )
                    )
                    outcome = DockerCleanupOutcome(
                        evidence=SandboxCleanupEvidence(
                            sandbox=SandboxKind.DOCKER,
                            checked_at=datetime.now(UTC),
                            completed_checks=(),
                            failed_checks=("docker_cleanup_internal",),
                            unresolved_checks=unresolved_checks,
                            project_name=(
                                owned_projects[0].runtime.project_name
                                if owned_projects
                                else None
                            ),
                            owned_temp_root_count=len(
                                attempt.owned_temp_roots
                            ),
                        ),
                        unresolved_projects=owned_projects,
                        unresolved_roots=tuple(attempt.owned_temp_roots),
                    )
                self._record_docker_cleanup_outcome(outcome)
            finally:
                self._cleanup_in_progress = False
            raise
        runtime = attempt.runtime
        if runtime is None:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, "docker_profile"
            )
        self._docker_runtime = runtime
        self._docker_runtime_executor = executor
        self._docker_owned_temp_roots = tuple(attempt.owned_temp_roots)
        compose_graph_json = json.dumps(
            runtime.expected_graph,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return SandboxStartupEvidence(
            sandbox=SandboxKind.DOCKER,
            checked_at=checked_at,
            completed_checks=completed_checks,
            docker_runtime=DockerRuntimeEvidenceSnapshot(
                reviewed_snapshot_sha256=snapshot.digest,
                reviewed_worker_tree_sha256=(
                    snapshot.reviewed_worker_tree_sha256
                ),
                expected_worker_tree_sha256=DOCKER_REVIEWED_WORKER_TREE_SHA256,
                reviewed_worker_manifest_sha256=(
                    snapshot.reviewed_worker_manifest_sha256
                ),
                reviewed_worker_manifest_json=(
                    snapshot.reviewed_worker_manifest_json
                ),
                compose_project=runtime.project_name,
                compose_config_sha256=hashlib.sha256(
                    compose_graph_json.encode("utf-8")
                ).hexdigest(),
                compose_graph_json=compose_graph_json,
                image_ids=runtime.image_ids,
            ),
        )

    def _start_docker_runtime_attempt(
        self,
        *,
        attempt: DockerStartupAttempt,
        snapshot: ReviewedDockerSnapshot,
        executable: str,
        executor: SandboxCommandExecutor,
        timeout_seconds: float,
    ) -> tuple[str, ...]:
        completed_checks: list[str] = []

        self._checked_docker_command(
            executor,
            (executable, "version", "--format", "{{.Server.Version}}"),
            timeout_seconds=timeout_seconds,
            check="docker_daemon",
        )
        completed_checks.append("docker_daemon")
        try:
            context_path = _stage_reviewed_docker_snapshot(
                snapshot,
                owned_roots=attempt.owned_temp_roots,
            )
        except Exception as exc:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, "docker_stage_context"
            ) from exc
        image_tags = _docker_image_tags(snapshot)
        dockerfile = context_path / "infrastructure/docker-agent/Dockerfile"
        image_ids: dict[str, str] = {}
        for target in DOCKER_BUILD_TARGETS:
            built = self._checked_docker_command(
                executor,
                (
                    executable,
                    "build",
                    "--pull=false",
                    "--no-cache",
                    "--quiet",
                    "--file",
                    str(dockerfile),
                    "--target",
                    target,
                    "--tag",
                    image_tags[target],
                    str(context_path),
                ),
                timeout_seconds=timeout_seconds,
                check=f"docker_build_{target}",
            )
            try:
                image_ids[target] = _docker_image_id(built.stdout)
            except ValueError as exc:
                raise SandboxRuntimeUnavailable(
                    SandboxKind.DOCKER, f"docker_build_{target}"
                ) from exc
            completed_checks.append(f"docker_build_{target}")
            self._require_docker_tag_image_id(
                executor=executor,
                executable=executable,
                target=target,
                tag=image_tags[target],
                expected=image_ids[target],
                timeout_seconds=timeout_seconds,
            )
            completed_checks.append(f"docker_image_{target}")
            for prior_target, prior_id in image_ids.items():
                if prior_target == target:
                    continue
                self._require_docker_tag_image_id(
                    executor=executor,
                    executable=executable,
                    target=prior_target,
                    tag=image_tags[prior_target],
                    expected=prior_id,
                    timeout_seconds=timeout_seconds,
                )
        for target, image_id in image_ids.items():
            self._require_docker_tag_image_id(
                executor=executor,
                executable=executable,
                target=target,
                tag=image_tags[target],
                expected=image_id,
                timeout_seconds=timeout_seconds,
            )

        graph = _docker_runtime_graph(
            snapshot,
            image_ids=image_ids,
            model=self._settings.model,
        )
        profile_digest = hashlib.sha256(
            snapshot.digest.encode("ascii")
            + b"\x00"
            + json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        project_name = f"{DOCKER_COMPOSE_PROJECT_NAME}-{profile_digest[:16]}"
        compose_path = _stage_docker_runtime_profile(
            graph=graph,
            digest=profile_digest,
            owned_roots=attempt.owned_temp_roots,
        )
        env_file = context_path / "infrastructure/docker-agent/empty.env"
        compose_prefix = (
            executable,
            "compose",
            "--env-file",
            str(env_file),
            "--project-name",
            project_name,
            "--file",
            str(compose_path),
        )
        rendered = self._checked_docker_command(
            executor,
            (
                *compose_prefix,
                "config",
                "--format",
                "json",
                "--no-interpolate",
                "--no-normalize",
                "--no-path-resolution",
            ),
            timeout_seconds=timeout_seconds,
            check="docker_compose_config",
        )
        try:
            _validate_rendered_docker_graph(
                rendered.stdout,
                expected=graph,
                project_name=project_name,
            )
        except ValueError as exc:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, "docker_compose_config"
            ) from exc
        completed_checks.append("docker_compose_config")
        runtime = DockerRuntimeProfile(
            project_name=project_name,
            context_path=context_path,
            compose_path=compose_path,
            env_file=env_file,
            compose_prefix=compose_prefix,
            image_tags=tuple(sorted(image_tags.items())),
            image_ids=tuple(sorted(image_ids.items())),
            expected_graph=graph,
        )
        attempt.runtime = runtime
        attempt.project_may_exist = True
        self._checked_docker_command(
            executor,
            (
                *compose_prefix,
                "up",
                "--detach",
                "--wait",
                "--no-build",
                "--pull",
                "never",
                *sorted(DOCKER_REQUIRED_GATEWAY_SERVICES),
            ),
            timeout_seconds=timeout_seconds,
            check="docker_gateway_start",
        )
        completed_checks.append("docker_gateway_start")
        try:
            self._verify_docker_runtime_profile(
                runtime,
                snapshot=snapshot,
                executable=executable,
                executor=executor,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, "docker_gateways"
            ) from exc
        completed_checks.append("docker_gateways")
        return tuple(completed_checks)

    def _verify_docker_runtime(
        self,
        *,
        executor: SandboxCommandExecutor | None = None,
        timeout_seconds: float = DEFAULT_SANDBOX_READINESS_TIMEOUT_SECONDS,
    ) -> DockerRuntimeProfile:
        runtime = self._docker_runtime
        snapshot = self._docker_snapshot
        executable = self._docker_executable
        if runtime is None or snapshot is None or executable is None:
            raise ValueError("Docker runtime has not passed startup validation")
        return self._verify_docker_runtime_profile(
            runtime,
            snapshot=snapshot,
            executable=executable,
            executor=executor or self._executor,
            timeout_seconds=timeout_seconds,
        )

    def _verify_docker_runtime_profile(
        self,
        runtime: DockerRuntimeProfile,
        *,
        snapshot: ReviewedDockerSnapshot,
        executable: str,
        executor: SandboxCommandExecutor,
        timeout_seconds: float,
    ) -> DockerRuntimeProfile:
        if _validate_reviewed_docker_assets().digest != snapshot.digest:
            raise ValueError("reviewed Docker source changed")
        image_ids = dict(runtime.image_ids)
        for target, tag in runtime.image_tags:
            completed = self._checked_docker_command(
                executor,
                (
                    executable,
                    "image",
                    "inspect",
                    "--format",
                    "{{.Id}}",
                    tag,
                ),
                timeout_seconds=timeout_seconds,
                check=f"docker_image_{target}",
            )
            if _docker_image_id(completed.stdout) != image_ids[target]:
                raise ValueError("Docker image tag was substituted")
        rendered = self._checked_docker_command(
            executor,
            (
                *runtime.compose_prefix,
                "config",
                "--format",
                "json",
                "--no-interpolate",
                "--no-normalize",
                "--no-path-resolution",
            ),
            timeout_seconds=timeout_seconds,
            check="docker_compose_config",
        )
        _validate_rendered_docker_graph(
            rendered.stdout,
            expected=runtime.expected_graph,
            project_name=runtime.project_name,
        )
        gateways = self._checked_docker_command(
            executor,
            (
                *runtime.compose_prefix,
                "ps",
                "--format",
                "json",
                *sorted(DOCKER_REQUIRED_GATEWAY_SERVICES),
            ),
            timeout_seconds=timeout_seconds,
            check="docker_gateways",
        )
        containers = _healthy_docker_service_containers(gateways.stdout)
        for service, container_id in containers.items():
            inspected = self._checked_docker_command(
                executor,
                (
                    executable,
                    "inspect",
                    "--format",
                    "{{.Image}}",
                    container_id,
                ),
                timeout_seconds=timeout_seconds,
                check="docker_gateways",
            )
            target = {
                "vllm-gateway": "vllm_gateway",
                "tool-proxy-gateway": "tool_proxy_gateway",
            }[service]
            if _docker_image_id(inspected.stdout) != image_ids[target]:
                raise ValueError("Docker gateway image ID drifted")
        return runtime

    def _cleanup_docker_resources(
        self,
        *,
        owned_projects: tuple[DockerOwnedProject, ...],
        owned_roots: tuple[Path, ...],
        timeout_seconds: float,
        runner_closed: bool,
    ) -> DockerCleanupOutcome:
        completed_checks: list[str] = []
        failed_checks: list[str] = []
        unresolved_projects: list[DockerOwnedProject] = []
        for project in owned_projects:
            try:
                completed = project.executor.run(
                    (
                        *project.runtime.compose_prefix,
                        "down",
                        "--remove-orphans",
                        "--timeout",
                        "10",
                    ),
                    input_bytes=b"",
                    timeout_seconds=timeout_seconds,
                )
                if completed.returncode != 0:
                    raise RuntimeError("Docker project cleanup failed")
            except BaseException:
                failed_checks.append("docker_project_down")
                unresolved_projects.append(project)
            else:
                completed_checks.append("docker_project_down")

        protected_roots: set[Path] = set()
        for project in unresolved_projects:
            required_paths = (
                project.runtime.context_path,
                project.runtime.compose_path,
                project.runtime.env_file,
            )
            for root in owned_roots:
                if any(
                    path == root or path.is_relative_to(root)
                    for path in required_paths
                ):
                    protected_roots.add(root)

        unresolved_roots: list[Path] = []
        for root in owned_roots:
            if root in protected_roots:
                unresolved_roots.append(root)
                continue
            try:
                _remove_owned_docker_temp_root(root)
            except BaseException:
                failed_checks.append("docker_temp_root_removal")
                unresolved_roots.append(root)
        if owned_roots and not unresolved_roots:
            completed_checks.append("docker_temp_roots_removed")
        elif not owned_roots:
            completed_checks.append("no_owned_temp_roots")
        unresolved_checks: list[str] = []
        if unresolved_projects:
            unresolved_checks.append("docker_project_down")
        if unresolved_roots:
            unresolved_checks.append("docker_temp_roots")
        if not unresolved_checks:
            completed_checks.append("cleanup_complete")
        if runner_closed:
            completed_checks.append("closed")
        project_name = (
            owned_projects[0].runtime.project_name if owned_projects else None
        )
        return DockerCleanupOutcome(
            evidence=SandboxCleanupEvidence(
                sandbox=SandboxKind.DOCKER,
                checked_at=self._aware_now(),
                completed_checks=tuple(dict.fromkeys(completed_checks)),
                failed_checks=tuple(dict.fromkeys(failed_checks)),
                unresolved_checks=tuple(unresolved_checks),
                project_name=project_name,
                owned_temp_root_count=len(unresolved_roots),
            ),
            unresolved_projects=tuple(unresolved_projects),
            unresolved_roots=tuple(unresolved_roots),
        )

    def _record_docker_cleanup_outcome(
        self,
        outcome: DockerCleanupOutcome,
        *,
        already_closed: bool = False,
    ) -> SandboxCleanupEvidence:
        """Retain unresolved custody and merge cleanup evidence monotonically."""

        self._docker_unresolved_projects = outcome.unresolved_projects
        self._docker_unresolved_temp_roots = outcome.unresolved_roots
        attempt_evidence = replace(
            outcome.evidence,
            already_closed=already_closed,
        )
        self._cleanup_history += (attempt_evidence,)

        completed_checks: list[str] = []
        failed_checks: list[str] = []
        project_name: str | None = None
        for attempt in self._cleanup_history:
            for check in attempt.completed_checks:
                if check != "cleanup_complete" and check not in completed_checks:
                    completed_checks.append(check)
            for check in attempt.failed_checks:
                if check not in failed_checks:
                    failed_checks.append(check)
            if attempt.project_name is not None:
                project_name = attempt.project_name
        unresolved_checks = attempt_evidence.unresolved_checks
        if not unresolved_checks:
            completed_checks.append("cleanup_complete")
        aggregate = SandboxCleanupEvidence(
            sandbox=SandboxKind.DOCKER,
            checked_at=attempt_evidence.checked_at,
            completed_checks=tuple(completed_checks),
            failed_checks=tuple(failed_checks),
            unresolved_checks=unresolved_checks,
            project_name=project_name,
            owned_temp_root_count=len(outcome.unresolved_roots),
            attempt_count=len(self._cleanup_history),
            already_closed=already_closed,
        )
        self._last_cleanup_evidence = aggregate
        return aggregate

    def _checked_docker_command(
        self,
        executor: SandboxCommandExecutor,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        check: str,
    ) -> CompletedSandboxCommand:
        try:
            completed = executor.run(
                command,
                input_bytes=b"",
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            raise SandboxRuntimeUnavailable(SandboxKind.DOCKER, check) from exc
        if completed.returncode != 0:
            raise SandboxRuntimeUnavailable(SandboxKind.DOCKER, check)
        return completed

    def _require_docker_tag_image_id(
        self,
        *,
        executor: SandboxCommandExecutor,
        executable: str,
        target: str,
        tag: str,
        expected: str,
        timeout_seconds: float,
    ) -> None:
        completed = self._checked_docker_command(
            executor,
            (
                executable,
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                tag,
            ),
            timeout_seconds=timeout_seconds,
            check=f"docker_image_{target}",
        )
        try:
            inspected = _docker_image_id(completed.stdout)
        except ValueError as exc:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, f"docker_image_{target}"
            ) from exc
        if inspected != expected:
            raise SandboxRuntimeUnavailable(
                SandboxKind.DOCKER, f"docker_image_{target}"
            )

    def _valid_host_inputs(
        self,
        request: AgentRunRequest,
        tools: BoundedToolGateway,
        *,
        policy_snapshot: CoordinationPolicySnapshot,
        invocation_context: ToolInvocationContext,
        selected_context: SelectedContext,
    ) -> bool:
        try:
            policy_snapshot.verify_reference(request.policy)
        except Exception:
            return False
        tool_names = {definition.name for definition in tools.definitions()}
        if set(policy_snapshot.allowed_tools) != tool_names:
            return False
        try:
            verify_selected_context(
                selected_context,
                request,
                available_tools=invocation_context.allowed_tools,
                model_id=self._settings.model,
            )
        except Exception:
            return False
        return (
            invocation_context.run_id == request.run_id
            and invocation_context.incident_id == request.incident.incident_id
            and invocation_context.state_version == request.incident.state_version
            and invocation_context.policy_sha256 == request.policy.sha256
            and set(invocation_context.allowed_tools).issubset(tool_names)
        )

    def _manual(
        self,
        request: AgentRunRequest,
        started_at: datetime,
        failure: AgentFailureCode,
    ) -> AgentRunResult:
        return AgentRunResult(
            schema_version=request.schema_version,
            run_id=request.run_id,
            incident_id=request.incident.incident_id,
            policy=request.policy,
            model_id=self._settings.model,
            sandbox=self._sandbox,
            status=AgentRunStatus.MANUAL_REQUIRED,
            started_at=started_at,
            finished_at=self._aware_now(),
            tool_trace=(),
            failure_code=failure,
        )

    def _aware_now(self) -> datetime:
        value = self._clock.now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("sandbox runner clock must be timezone-aware")
        return value.astimezone(UTC)
