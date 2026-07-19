"""Trusted-host builders for exact sandbox worker source snapshots.

The manifests in this module are packaging authority and are never included in
worker runtimes.  They capture only explicitly reviewed source paths, validate
the complete internal import closure, and reject symlinks and host-only module
classes before bytes can enter a Docker or NemoClaw build context.
"""

from __future__ import annotations

import ast
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import stat
import subprocess
import sys
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


WORKER_SOURCE_PREFIX = "backend/src/"
MAX_REVIEWED_SOURCE_BYTES = 4 * 1024 * 1024
NEMOCLAW_DEPENDENCY_LOCK_PATH = "infrastructure/docker-agent/requirements.lock"
NEMOCLAW_DEPENDENCY_LOCK_SHA256 = (
    "362c2f7aa6a3ef0eb27d609783d71f623d7c081c3d51b1fa3cbf3b1747825bee"
)
NEMOCLAW_ALLOWED_BOOTSTRAP_DISTRIBUTIONS = frozenset(
    {"pip", "setuptools", "wheel"}
)
NEMOCLAW_WORKER_DISTRIBUTION = "vital-relay-agent-worker-runtime"
NEMOCLAW_WORKER_VERSION = "0.9.0"
NEMOCLAW_WORKER_DIST_INFO = (
    "vital_relay_agent_worker_runtime-0.9.0.dist-info"
)
NEMOCLAW_WORKER_LAUNCHER = "vital-relay-agent-worker"
NEMOCLAW_WORKER_ENTRYPOINT = "vital_relay.agent.worker:main"
NEMOCLAW_ENTRY_POINTS_BYTES = (
    "[console_scripts]\n"
    f"{NEMOCLAW_WORKER_LAUNCHER} = {NEMOCLAW_WORKER_ENTRYPOINT}\n"
).encode("ascii")
NEMOCLAW_METADATA_BYTES = (
    "Metadata-Version: 2.4\n"
    f"Name: {NEMOCLAW_WORKER_DISTRIBUTION}\n"
    f"Version: {NEMOCLAW_WORKER_VERSION}\n"
    "Requires-Python: <3.15,>=3.14\n"
).encode("ascii")
NEMOCLAW_TOP_LEVEL_BYTES = b"vital_relay\n"
RUNTIME_CUSTOMIZATION_MODULES = ("sitecustomize", "usercustomize")

AGENT_WORKER_SOURCE_PATHS = (
    "vital_relay/__init__.py",
    "vital_relay/agent/__init__.py",
    "vital_relay/agent/capability_runtime.py",
    "vital_relay/agent/contracts.py",
    "vital_relay/agent/deep_agent.py",
    "vital_relay/agent/http_tools.py",
    "vital_relay/agent/policy.py",
    "vital_relay/agent/runner.py",
    "vital_relay/agent/sandbox_wire.py",
    "vital_relay/agent/tool_contracts.py",
    "vital_relay/agent/tool_identity.py",
    "vital_relay/agent/tool_transport.py",
    "vital_relay/agent/tools.py",
    "vital_relay/agent/worker.py",
    "vital_relay/domain/__init__.py",
    "vital_relay/domain/dispatch.py",
    "vital_relay/domain/health.py",
    "vital_relay/domain/incidents.py",
    "vital_relay/domain/protocols.py",
    "vital_relay/evolution/__init__.py",
    "vital_relay/evolution/ace/__init__.py",
    "vital_relay/evolution/ace/contracts.py",
    "vital_relay/evolution/ace/selection.py",
    "vital_relay/evolution/hashing.py",
)

# These prefixes cover every trusted-host source class called out by the
# architecture boundary, including future cadence/mutation integration names.
TRUSTED_HOST_MODULE_PREFIXES = (
    "vital_relay.adapters",
    "vital_relay.agent.capabilities",
    "vital_relay.agent.readiness",
    "vital_relay.agent.sandbox",
    "vital_relay.agent.smoke",
    "vital_relay.agent.source_manifest",
    "vital_relay.api",
    "vital_relay.application",
    "vital_relay.config",
    "vital_relay.evolution.ace.curation",
    "vital_relay.evolution.ace.control",
    "vital_relay.evolution.ace.evidence",
    "vital_relay.evolution.ace.merge",
    "vital_relay.evolution.ace.redaction",
    "vital_relay.evolution.ace.reflection",
    "vital_relay.evolution.ace.release",
    "vital_relay.evolution.ace.round",
    "vital_relay.evolution.ace.store",
    "vital_relay.evolution.archive",
    "vital_relay.evolution.bundle_store",
    "vital_relay.evolution.bundles",
    "vital_relay.evolution.cadence",
    "vital_relay.evolution.candidate",
    "vital_relay.evolution.contracts",
    "vital_relay.evolution.control",
    "vital_relay.evolution.evidence",
    "vital_relay.evolution.evaluator",
    "vital_relay.evolution.improver",
    "vital_relay.evolution.lineage",
    "vital_relay.evolution.mutation",
    "vital_relay.evolution.policy",
    "vital_relay.evolution.promotion",
    "vital_relay.evolution.recorded",
    "vital_relay.evolution.release",
    "vital_relay.evolution.round",
    "vital_relay.evolution.scenario",
    "vital_relay.evolution.signing",
    "vital_relay.main",
    "vital_relay.persistence",
    "vital_relay.protocols",
)

MUTATION_WORKER_ENTRYPOINT = "vital_relay.evolution.mutation_worker:main"
MUTATION_WORKER_IMPLEMENTATION_PATHS = (
    "vital_relay/evolution/mutation_contracts.py",
    "vital_relay/evolution/mutation_worker.py",
)
MUTATION_WORKER_SOURCE_PATHS = (
    "vital_relay/__init__.py",
    "vital_relay/agent/__init__.py",
    "vital_relay/agent/contracts.py",
    "vital_relay/agent/tool_contracts.py",
    "vital_relay/domain/__init__.py",
    "vital_relay/domain/dispatch.py",
    "vital_relay/domain/health.py",
    "vital_relay/domain/incidents.py",
    "vital_relay/evolution/__init__.py",
    "vital_relay/evolution/ace/__init__.py",
    "vital_relay/evolution/ace/contracts.py",
    "vital_relay/evolution/ace/model_client.py",
    "vital_relay/evolution/ace/selection.py",
    "vital_relay/evolution/hashing.py",
    *MUTATION_WORKER_IMPLEMENTATION_PATHS,
)


@dataclass(frozen=True, slots=True)
class ReviewedSourceManifest:
    """Exact source identity and executable module for one worker class."""

    name: str
    entrypoint: str
    source_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("-", "").isalnum():
            raise ValueError("reviewed source manifest name is invalid")
        _validate_entrypoint(self.entrypoint)
        if not self.source_paths:
            raise ValueError("reviewed source manifest is empty")
        if tuple(sorted(set(self.source_paths))) != self.source_paths:
            raise ValueError("reviewed source paths must be unique and sorted")
        for source_path in self.source_paths:
            _validate_source_path(source_path)
            module = _module_name_for_path(source_path)
            if _is_trusted_host_module(module):
                raise ValueError(f"trusted-host source is forbidden: {source_path}")
        entrypoint_module = self.entrypoint.partition(":")[0]
        if _source_path_for_module(entrypoint_module) not in self.source_paths:
            raise ValueError("worker entrypoint source is not in the manifest")


@dataclass(frozen=True, slots=True)
class ReviewedSourceSnapshot:
    """Immutable source bytes captured from one reviewed checkout."""

    manifest: ReviewedSourceManifest
    digest: str
    files: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class ReviewedWorkerWheel:
    """Built worker-only wheel bound to source and dependency identities."""

    source_snapshot: ReviewedSourceSnapshot
    wheel_path: Path
    wheel_sha256: str
    archive_member_sha256: tuple[tuple[str, str], ...]
    dependency_lock_sha256: str


@dataclass(frozen=True, slots=True)
class MutationWorkerEntrypointContract:
    """Integration seam for a worker that can return only unsigned results."""

    entrypoint: str = MUTATION_WORKER_ENTRYPOINT
    result_authority: str = "unsigned"
    host_attestation_required: bool = True

    def __post_init__(self) -> None:
        _validate_entrypoint(self.entrypoint)
        if self.result_authority != "unsigned" or not self.host_attestation_required:
            raise ValueError("mutation results must remain unsigned and host-attested")


def build_mutation_worker_source_manifest(
    *,
    additional_source_paths: Sequence[str] = (),
    contract: MutationWorkerEntrypointContract | None = None,
) -> ReviewedSourceManifest:
    """Return the one reviewed future-lane manifest without inventing code.

    ``additional_source_paths`` remains only as a compatibility assertion for
    the incoming lane: it may be empty or name the two known implementation
    files exactly. No caller can extend the reviewed source universe.
    """

    bound_contract = MUTATION_WORKER_CONTRACT if contract is None else contract
    if bound_contract is not MUTATION_WORKER_CONTRACT:
        raise ValueError("mutation worker entrypoint contract is fixed")
    requested = tuple(sorted(set(additional_source_paths)))
    if requested not in {(), MUTATION_WORKER_IMPLEMENTATION_PATHS}:
        raise ValueError("mutation worker source paths are not the exact allowlist")
    if MUTATION_WORKER_SOURCE_MANIFEST.entrypoint != bound_contract.entrypoint:
        raise ValueError("mutation worker manifest does not match its fixed contract")
    return MUTATION_WORKER_SOURCE_MANIFEST


def capture_reviewed_source_snapshot(
    project_root: Path,
    manifest: ReviewedSourceManifest,
) -> ReviewedSourceSnapshot:
    """Capture exactly one manifest and verify its complete internal imports."""

    source_root = project_root / WORKER_SOURCE_PREFIX
    files: dict[str, bytes] = {}
    for source_path in manifest.source_paths:
        files[source_path] = _read_regular_nofollow(source_root, source_path)
    validate_source_import_closure(manifest, files)
    digest = source_content_digest(files)
    return ReviewedSourceSnapshot(
        manifest=manifest,
        digest=digest,
        files=tuple(sorted(files.items())),
    )


def validate_source_import_closure(
    manifest: ReviewedSourceManifest,
    files: Mapping[str, bytes],
) -> None:
    """Fail closed for missing, extra, malformed, or host-only source imports."""

    expected = set(manifest.source_paths)
    if set(files) != expected:
        missing = sorted(expected - set(files))
        extra = sorted(set(files) - expected)
        raise ValueError(f"source manifest mismatch; missing={missing}, extra={extra}")
    for source_path, raw in files.items():
        try:
            tree = ast.parse(raw, filename=source_path)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"invalid reviewed Python source: {source_path}") from exc
        for module in _internal_imports(tree, expected):
            if _is_trusted_host_module(module):
                raise ValueError(
                    f"sandbox source imports trusted-host module: {module}"
                )
            dependency = _source_path_for_import(module, expected)
            if dependency not in expected:
                raise ValueError(
                    f"sandbox import dependency is not reviewed: {module}"
                )
        for package_init in _required_package_inits(source_path):
            if package_init not in expected:
                raise ValueError(
                    f"sandbox package initializer is not reviewed: {package_init}"
                )


def validate_staged_source_tree(
    source_root: Path,
    snapshot: ReviewedSourceSnapshot,
) -> None:
    """Inspect actual staged bytes and reject every non-manifest filesystem entry."""

    actual = _read_exact_tree(source_root)
    expected = dict(snapshot.files)
    if actual != expected:
        raise ValueError("staged worker source differs from the reviewed snapshot")
    validate_source_import_closure(snapshot.manifest, actual)
    if source_content_digest(actual) != snapshot.digest:
        raise ValueError("staged worker source digest is not deterministic")


def build_nemoclaw_worker_source_bundle(
    project_root: Path,
    destination: Path,
) -> ReviewedSourceSnapshot:
    """Build a new exact NemoClaw source project, never a full backend wheel."""

    snapshot = capture_reviewed_source_snapshot(
        project_root,
        NEMOCLAW_AGENT_SOURCE_MANIFEST,
    )
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    dependency_lock = _validated_dependency_lock(project_root)
    source_root = destination / "src"
    source_root.mkdir(mode=0o700)
    for relative_name, raw in snapshot.files:
        _write_new_file(source_root / relative_name, raw)
    pyproject = _nemoclaw_worker_pyproject().encode("utf-8")
    manifest_bytes = _bundle_manifest_bytes(snapshot, pyproject)
    _write_new_file(destination / "pyproject.toml", pyproject)
    _write_new_file(destination / "worker-requirements.lock", dependency_lock)
    _write_new_file(destination / "worker-source-manifest.json", manifest_bytes)
    validate_staged_source_tree(source_root, snapshot)
    _validate_nemoclaw_bundle(
        destination,
        snapshot,
        pyproject,
        dependency_lock,
        manifest_bytes,
    )
    return snapshot


def build_nemoclaw_worker_wheel(
    project_root: Path,
    destination: Path,
    *,
    python_executable: Path | None = None,
) -> ReviewedWorkerWheel:
    """Build and inspect a worker-only wheel in a new output directory."""

    interpreter = (
        Path(sys.executable) if python_executable is None else python_executable
    )
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    source_project = destination / "source-project"
    snapshot = build_nemoclaw_worker_source_bundle(project_root, source_project)
    wheel_directory = destination / "wheel"
    wheel_directory.mkdir(mode=0o700)
    completed = subprocess.run(
        (
            str(interpreter),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(wheel_directory),
            str(source_project),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SOURCE_DATE_EPOCH": "315532800",
        },
        timeout=120,
    )
    wheels = tuple(wheel_directory.glob("*.whl"))
    if completed.returncode != 0 or len(wheels) != 1:
        raise ValueError("reviewed NemoClaw worker wheel build failed")
    wheel_path = wheels[0]
    archive_member_sha256 = inspect_nemoclaw_worker_wheel(wheel_path, snapshot)
    wheel_sha256 = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    return ReviewedWorkerWheel(
        source_snapshot=snapshot,
        wheel_path=wheel_path,
        wheel_sha256=wheel_sha256,
        archive_member_sha256=archive_member_sha256,
        dependency_lock_sha256=NEMOCLAW_DEPENDENCY_LOCK_SHA256,
    )


def inspect_nemoclaw_worker_wheel(
    wheel_path: Path,
    snapshot: ReviewedSourceSnapshot,
    *,
    reviewed_wheel_sha256: str | None = None,
) -> tuple[tuple[str, str], ...]:
    """Validate the complete wheel and return its deterministic member hashes."""

    expected = {
        *(path for path, _raw in snapshot.files),
        f"{NEMOCLAW_WORKER_DIST_INFO}/METADATA",
        f"{NEMOCLAW_WORKER_DIST_INFO}/RECORD",
        f"{NEMOCLAW_WORKER_DIST_INFO}/WHEEL",
        f"{NEMOCLAW_WORKER_DIST_INFO}/entry_points.txt",
        f"{NEMOCLAW_WORKER_DIST_INFO}/top_level.txt",
    }
    try:
        wheel_bytes = wheel_path.read_bytes()
    except OSError as exc:
        raise ValueError("NemoClaw worker wheel is unreadable") from exc
    if reviewed_wheel_sha256 is not None:
        _assert_sha256(reviewed_wheel_sha256, label="reviewed wheel")
        actual_sha256 = hashlib.sha256(wheel_bytes).hexdigest()
        if not hmac.compare_digest(actual_sha256, reviewed_wheel_sha256):
            raise ValueError("NemoClaw worker wheel SHA-256 is not reviewed")
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
            infos = archive.infolist()
            members = [item.filename for item in infos]
            if len(members) != len(set(members)) or set(members) != expected:
                raise ValueError("NemoClaw worker wheel members are not exact")
            for item in infos:
                path = PurePosixPath(item.filename)
                unix_mode = item.external_attr >> 16
                if (
                    path.is_absolute()
                    or any(part in {"", ".", ".."} for part in path.parts)
                    or stat.S_ISLNK(unix_mode)
                ):
                    raise ValueError("NemoClaw worker wheel contains an unsafe member")
            for source_path, raw in snapshot.files:
                if archive.read(source_path) != raw:
                    raise ValueError("NemoClaw worker wheel source bytes changed")
            members = {item.filename: archive.read(item.filename) for item in infos}
            _validate_nemoclaw_wheel_metadata(members)
            _validate_record(
                members[f"{NEMOCLAW_WORKER_DIST_INFO}/RECORD"],
                members,
                record_path=f"{NEMOCLAW_WORKER_DIST_INFO}/RECORD",
            )
            return tuple(
                sorted(
                    (name, hashlib.sha256(raw).hexdigest())
                    for name, raw in members.items()
                )
            )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("NemoClaw worker wheel is unreadable") from exc


def _validate_nemoclaw_wheel_metadata(members: Mapping[str, bytes]) -> None:
    prefix = NEMOCLAW_WORKER_DIST_INFO
    if members[f"{prefix}/METADATA"] != NEMOCLAW_METADATA_BYTES:
        raise ValueError("NemoClaw worker wheel metadata changed")
    if members[f"{prefix}/entry_points.txt"] != NEMOCLAW_ENTRY_POINTS_BYTES:
        raise ValueError("NemoClaw worker wheel entrypoint metadata changed")
    if members[f"{prefix}/top_level.txt"] != NEMOCLAW_TOP_LEVEL_BYTES:
        raise ValueError("NemoClaw worker wheel top-level metadata changed")
    _validate_wheel_metadata(members[f"{prefix}/WHEEL"])


def _validate_wheel_metadata(raw: bytes) -> None:
    try:
        lines = raw.decode("ascii").splitlines()
        if lines and not lines[-1]:
            lines.pop()
        fields = dict(line.split(": ", 1) for line in lines)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("NemoClaw worker WHEEL metadata is invalid") from exc
    if len(fields) != len(lines) or set(fields) != {
        "Wheel-Version",
        "Generator",
        "Root-Is-Purelib",
        "Tag",
    }:
        raise ValueError("NemoClaw worker WHEEL metadata changed")
    if (
        fields["Wheel-Version"] != "1.0"
        or not fields["Generator"].startswith("setuptools (")
        or fields["Root-Is-Purelib"] != "true"
        or fields["Tag"] != "py3-none-any"
    ):
        raise ValueError("NemoClaw worker WHEEL metadata changed")


def _parse_record(raw: bytes) -> dict[str, tuple[str, str]]:
    try:
        rows = tuple(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    except (UnicodeError, csv.Error) as exc:
        raise ValueError("installed distribution RECORD is invalid") from exc
    parsed: dict[str, tuple[str, str]] = {}
    for row in rows:
        if len(row) != 3 or not row[0] or row[0] in parsed:
            raise ValueError("installed distribution RECORD is invalid")
        parsed[row[0]] = (row[1], row[2])
    if not parsed:
        raise ValueError("installed distribution RECORD is empty")
    return parsed


def _record_digest(raw: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return f"sha256={encoded.rstrip(b'=').decode('ascii')}"


def _validate_record(
    record_raw: bytes,
    members: Mapping[str, bytes],
    *,
    record_path: str,
) -> None:
    rows = _parse_record(record_raw)
    if set(rows) != set(members):
        raise ValueError("NemoClaw worker wheel RECORD members are not exact")
    for name, raw in members.items():
        digest, size = rows[name]
        if name == record_path:
            if digest or size:
                raise ValueError("NemoClaw worker wheel RECORD is invalid")
        elif digest != _record_digest(raw) or size != str(len(raw)):
            raise ValueError("NemoClaw worker wheel RECORD digest changed")


def install_nemoclaw_worker_runtime(
    *,
    project_root: Path,
    python_executable: Path,
    wheel: ReviewedWorkerWheel,
    reviewed_wheel_sha256: str,
    wheelhouse: Path,
    target: Path,
) -> None:
    """Install dependencies and the worker wheel into a provably fresh target."""

    _require_runtime_site_packages(python_executable, target)
    if not target.is_dir() or (target / "vital_relay").exists():
        raise ValueError("NemoClaw worker runtime is not a fresh target")
    _assert_sha256(reviewed_wheel_sha256, label="reviewed wheel")
    if not hmac.compare_digest(wheel.wheel_sha256, reviewed_wheel_sha256):
        raise ValueError("NemoClaw worker wheel SHA-256 is not reviewed")
    archive_member_sha256 = inspect_nemoclaw_worker_wheel(
        wheel.wheel_path,
        wheel.source_snapshot,
        reviewed_wheel_sha256=reviewed_wheel_sha256,
    )
    if archive_member_sha256 != wheel.archive_member_sha256:
        raise ValueError("NemoClaw worker wheel member digests changed")
    before = _installed_distributions(python_executable, target)
    if set(before) - NEMOCLAW_ALLOWED_BOOTSTRAP_DISTRIBUTIONS:
        raise ValueError("NemoClaw worker runtime contains pre-existing packages")
    _validate_installed_file_inventory(target)
    _assert_no_runtime_customization_specs(python_executable, target)
    dependency_lock = _validated_dependency_lock(project_root)
    dependency_install = subprocess.run(
        (
            str(python_executable),
            "-B",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--disable-pip-version-check",
            "--require-hashes",
            "--no-compile",
            "--find-links",
            str(wheelhouse),
            "--requirement",
            str(project_root / NEMOCLAW_DEPENDENCY_LOCK_PATH),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
    )
    if dependency_install.returncode != 0:
        raise ValueError("NemoClaw worker dependency install failed")
    # A locked dependency must not introduce a startup hook before the next
    # interpreter process (the worker-only wheel install) is allowed to run.
    _validate_installed_file_inventory(target)
    _assert_no_runtime_customization_specs(python_executable, target)
    worker_install = subprocess.run(
        (
            str(python_executable),
            "-B",
            "-m",
            "pip",
            "install",
            "--no-index",
            "--disable-pip-version-check",
            "--no-deps",
            "--no-compile",
            str(wheel.wheel_path),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if worker_install.returncode != 0:
        raise ValueError("NemoClaw worker wheel install failed")
    validate_installed_worker_runtime(
        python_executable=python_executable,
        target=target,
        snapshot=wheel.source_snapshot,
        reviewed_wheel_sha256=reviewed_wheel_sha256,
        dependency_lock=dependency_lock,
        require_dependency_inventory=True,
    )


def validate_installed_worker_runtime(
    *,
    python_executable: Path,
    target: Path,
    snapshot: ReviewedSourceSnapshot,
    reviewed_wheel_sha256: str,
    dependency_lock: bytes | None = None,
    require_dependency_inventory: bool = False,
) -> None:
    """Inspect actual installed source and run isolated negative spec checks."""

    _assert_sha256(reviewed_wheel_sha256, label="reviewed wheel")
    installed_files = _validate_installed_file_inventory(target)
    _assert_no_runtime_customization_specs(python_executable, target)
    installed_package = _read_exact_tree(target / "vital_relay")
    installed_sources = {
        f"vital_relay/{path}": raw for path, raw in installed_package.items()
    }
    if installed_sources != dict(snapshot.files):
        raise ValueError("installed worker source members are not exact")
    validate_source_import_closure(snapshot.manifest, installed_sources)
    if source_content_digest(installed_sources) != snapshot.digest:
        raise ValueError("installed worker source digest changed")
    distributions = _installed_distributions(python_executable, target)
    if "vital-relay" in distributions:
        raise ValueError("ordinary full Vital Relay distribution is installed")
    if distributions.get(NEMOCLAW_WORKER_DISTRIBUTION) != NEMOCLAW_WORKER_VERSION:
        raise ValueError("reviewed worker distribution metadata is missing")
    _validate_installed_worker_metadata(
        target,
        python_executable,
        installed_files,
        reviewed_wheel_sha256,
    )
    if require_dependency_inventory:
        if dependency_lock is None:
            raise ValueError("dependency inventory is required")
        expected = _locked_distribution_versions(dependency_lock)
        actual_dependencies = {
            name: version
            for name, version in distributions.items()
            if name != NEMOCLAW_WORKER_DISTRIBUTION
            and name not in NEMOCLAW_ALLOWED_BOOTSTRAP_DISTRIBUTIONS
        }
        if actual_dependencies != expected:
            raise ValueError("installed worker dependencies differ from the lock")
    _assert_negative_runtime_specs(python_executable, target)


def source_content_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for relative_name, raw in sorted(files.items()):
        encoded_name = relative_name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(4, "big"))
        digest.update(encoded_name)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    if not files:
        raise ValueError("reviewed source content set is empty")
    return digest.hexdigest()


def _assert_sha256(value: str, *, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError(f"{label} SHA-256 is invalid")


def _internal_imports(
    tree: ast.AST,
    reviewed_paths: set[str],
) -> tuple[str, ...]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(
                alias.name for alias in node.names if alias.name == "vital_relay"
                or alias.name.startswith("vital_relay.")
            )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError("relative imports are forbidden in sandbox source")
            if node.module and (
                node.module == "vital_relay"
                or node.module.startswith("vital_relay.")
            ):
                modules.add(node.module)
                package_path = f"{node.module.replace('.', '/')}/__init__.py"
                if package_path in reviewed_paths:
                    modules.update(
                        f"{node.module}.{alias.name}"
                        for alias in node.names
                        if alias.name != "*"
                    )
    return tuple(sorted(modules))


def _source_path_for_module(module: str) -> str:
    relative = module.replace(".", "/")
    if module in {
        "vital_relay",
        "vital_relay.agent",
        "vital_relay.domain",
        "vital_relay.evolution",
        "vital_relay.evolution.ace",
    }:
        return f"{relative}/__init__.py"
    return f"{relative}.py"


def _source_path_for_import(module: str, reviewed_paths: set[str]) -> str:
    relative = module.replace(".", "/")
    module_path = f"{relative}.py"
    package_path = f"{relative}/__init__.py"
    if module_path in reviewed_paths:
        return module_path
    if package_path in reviewed_paths:
        return package_path
    return module_path


def _module_name_for_path(source_path: str) -> str:
    if source_path.endswith("/__init__.py"):
        source_path = source_path.removesuffix("/__init__.py")
    else:
        source_path = source_path.removesuffix(".py")
    return source_path.replace("/", ".")


def _required_package_inits(source_path: str) -> tuple[str, ...]:
    parent = PurePosixPath(source_path).parent
    required: list[str] = []
    while str(parent) not in {".", ""}:
        required.append(f"{parent.as_posix()}/__init__.py")
        parent = parent.parent
    return tuple(required)


def _is_trusted_host_module(module: str) -> bool:
    return any(
        module == prefix or module.startswith(f"{prefix}.")
        for prefix in TRUSTED_HOST_MODULE_PREFIXES
    )


def _validate_entrypoint(value: str) -> None:
    module, separator, function = value.partition(":")
    if (
        separator != ":"
        or not module.startswith("vital_relay.")
        or not all(part.isidentifier() for part in module.split("."))
        or not function.isidentifier()
        or _is_trusted_host_module(module)
    ):
        raise ValueError("reviewed worker entrypoint is invalid")


def _validate_source_path(value: str) -> None:
    path = PurePosixPath(value)
    if (
        not value.startswith("vital_relay/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix != ".py"
    ):
        raise ValueError(f"reviewed source path is invalid: {value}")


def _read_regular_nofollow(root: Path, relative_name: str) -> bytes:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ValueError("reviewed source root is missing") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ValueError("reviewed source root is unsafe")
    common_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory_flags = common_flags | nofollow | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, directory_flags)
        descriptors.append(current)
        parts = PurePosixPath(relative_name).parts
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(
            parts[-1],
            common_flags | nofollow,
            dir_fd=current,
        )
        descriptors.append(descriptor)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_REVIEWED_SOURCE_BYTES
        ):
            raise ValueError(f"reviewed source is unsafe: {relative_name}")
        raw = os.read(descriptor, MAX_REVIEWED_SOURCE_BYTES + 1)
    except OSError as exc:
        raise ValueError(f"reviewed source is unreadable: {relative_name}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    if not raw or len(raw) > MAX_REVIEWED_SOURCE_BYTES:
        raise ValueError(f"reviewed source is unsafe: {relative_name}")
    return raw


def _read_exact_tree(root: Path) -> dict[str, bytes]:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ValueError("staged source root is missing") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise ValueError("staged source root is unsafe")
    files: dict[str, bytes] = {}
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        active = Path(directory)
        if stat.S_ISLNK(active.lstat().st_mode):
            raise ValueError("staged source contains a symlink")
        for name in directory_names:
            child = active / name
            metadata = child.lstat()
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError("staged source directory is unsafe")
        for name in file_names:
            child = active / name
            metadata = child.lstat()
            if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise ValueError("staged source file is unsafe")
            relative = child.relative_to(root).as_posix()
            files[relative] = _read_regular_nofollow(root, relative)
    return files


def _write_new_file(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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


def _nemoclaw_worker_pyproject() -> str:
    return f"""[build-system]
requires = [\"setuptools>=75\"]
build-backend = \"setuptools.build_meta\"

[project]
name = \"{NEMOCLAW_WORKER_DISTRIBUTION}\"
version = \"{NEMOCLAW_WORKER_VERSION}\"
requires-python = \">=3.14,<3.15\"
dependencies = []

[project.scripts]
{NEMOCLAW_WORKER_LAUNCHER} = \"{NEMOCLAW_WORKER_ENTRYPOINT}\"

[tool.setuptools]
package-dir = {{\"\" = \"src\"}}

[tool.setuptools.packages.find]
where = [\"src\"]
"""


def _bundle_manifest_bytes(
    snapshot: ReviewedSourceSnapshot,
    pyproject: bytes,
) -> bytes:
    payload = {
        "entrypoint": snapshot.manifest.entrypoint,
        "dependency_lock_sha256": NEMOCLAW_DEPENDENCY_LOCK_SHA256,
        "manifest": snapshot.manifest.name,
        "pyproject_sha256": hashlib.sha256(pyproject).hexdigest(),
        "source_digest": snapshot.digest,
        "sources": {
            path: hashlib.sha256(raw).hexdigest()
            for path, raw in snapshot.files
        },
    }
    return (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _validate_nemoclaw_bundle(
    destination: Path,
    snapshot: ReviewedSourceSnapshot,
    pyproject: bytes,
    dependency_lock: bytes,
    manifest_bytes: bytes,
) -> None:
    actual = _read_exact_tree(destination)
    expected = {
        "pyproject.toml": pyproject,
        "worker-requirements.lock": dependency_lock,
        "worker-source-manifest.json": manifest_bytes,
        **{f"src/{path}": raw for path, raw in snapshot.files},
    }
    if actual != expected:
        raise ValueError("NemoClaw worker bundle contains unexpected bytes")


def _validated_dependency_lock(project_root: Path) -> bytes:
    path = project_root / NEMOCLAW_DEPENDENCY_LOCK_PATH
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != NEMOCLAW_DEPENDENCY_LOCK_SHA256:
        raise ValueError("NemoClaw worker dependency lock changed")
    if not _locked_distribution_versions(raw):
        raise ValueError("NemoClaw worker dependency lock is empty")
    return raw


def _locked_distribution_versions(raw: bytes) -> dict[str, str]:
    import re

    dependencies: dict[str, str] = {}
    for line in raw.decode("utf-8").splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^ ]+) \\", line)
        if match is None:
            continue
        name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        if name in dependencies:
            raise ValueError("dependency lock contains a duplicate distribution")
        dependencies[name] = match.group(2)
    return dependencies


def _validate_installed_file_inventory(target: Path) -> dict[str, bytes]:
    """Bind every target file to one installed distribution RECORD."""

    actual = _read_installed_tree(target)
    for relative_name in actual:
        path = PurePosixPath(relative_name)
        if _is_runtime_customization_path(relative_name) or path.suffix == ".pth":
            raise ValueError("installed runtime contains an executable path hook")
    record_paths = tuple(
        sorted(
            name
            for name in actual
            if name.endswith(".dist-info/RECORD")
        )
    )
    if actual and not record_paths:
        raise ValueError("installed runtime files have no distribution RECORD")
    reviewed_local_paths: set[str] = set()
    for record_path in record_paths:
        rows = _parse_record(actual[record_path])
        for member_name, (digest, size) in rows.items():
            member_path = _installed_record_member_path(target, member_name)
            if member_name == record_path:
                raw = _read_installed_regular_file(member_path)
                if digest or size:
                    raise ValueError("installed distribution RECORD is invalid")
            elif not digest and not size:
                if member_path.exists():
                    raise ValueError(
                        "installed runtime contains an unhashed executable file"
                    )
                continue
            else:
                raw = _read_installed_regular_file(member_path)
            if member_name != record_path and (
                digest != _record_digest(raw) or size != str(len(raw))
            ):
                raise ValueError("installed distribution RECORD digest changed")
            try:
                local_name = member_path.relative_to(target.resolve()).as_posix()
            except ValueError:
                continue
            if local_name in reviewed_local_paths:
                raise ValueError("installed runtime file has multiple owners")
            reviewed_local_paths.add(local_name)
    if set(actual) != reviewed_local_paths:
        raise ValueError("installed runtime contains unexpected files")
    return actual


def _is_runtime_customization_path(relative_name: str) -> bool:
    parts = PurePosixPath(relative_name).parts
    if not parts:
        return False
    top_level_name = parts[0].casefold()
    return any(
        top_level_name == module or top_level_name.startswith(f"{module}.")
        for module in RUNTIME_CUSTOMIZATION_MODULES
    )


def _read_installed_tree(root: Path) -> dict[str, bytes]:
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ValueError("installed runtime root is missing") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("installed runtime root is unsafe")
    files: dict[str, bytes] = {}
    for directory, directory_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
    ):
        active = Path(directory)
        if stat.S_ISLNK(active.lstat().st_mode):
            raise ValueError("installed runtime contains a symlink")
        for name in directory_names:
            child = active / name
            relative_name = child.relative_to(root).as_posix()
            if _is_runtime_customization_path(relative_name):
                raise ValueError(
                    "installed runtime contains an executable path hook"
                )
            child_metadata = child.lstat()
            if not stat.S_ISDIR(child_metadata.st_mode) or stat.S_ISLNK(
                child_metadata.st_mode
            ):
                raise ValueError("installed runtime directory is unsafe")
        for name in file_names:
            child = active / name
            relative_name = child.relative_to(root).as_posix()
            if _is_runtime_customization_path(relative_name):
                raise ValueError(
                    "installed runtime contains an executable path hook"
                )
            files[relative_name] = _read_installed_regular_file(child)
    return files


def _installed_record_member_path(target: Path, member_name: str) -> Path:
    path = PurePosixPath(member_name)
    if path.is_absolute() or any(part in {"", "."} for part in path.parts):
        raise ValueError("installed distribution RECORD path is unsafe")
    parts = path.parts
    leading_parents = 0
    for part in parts:
        if part != "..":
            break
        leading_parents += 1
    if ".." in parts[leading_parents:]:
        raise ValueError("installed distribution RECORD path is unsafe")
    candidate = target.joinpath(*parts).resolve()
    if not candidate.exists() and len(parts) >= 2 and parts[-2] == "bin":
        target_install = target / "bin" / parts[-1]
        if target_install.exists():
            candidate = target_install.resolve()
    return candidate


def _read_installed_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ValueError("installed distribution member is unsafe")
        return path.read_bytes()
    except OSError as exc:
        raise ValueError("installed distribution member is missing") from exc


def _validate_installed_worker_metadata(
    target: Path,
    python_executable: Path,
    installed_files: Mapping[str, bytes],
    reviewed_wheel_sha256: str,
) -> None:
    prefix = NEMOCLAW_WORKER_DIST_INFO
    expected_dist_info = {
        f"{prefix}/INSTALLER",
        f"{prefix}/METADATA",
        f"{prefix}/RECORD",
        f"{prefix}/REQUESTED",
        f"{prefix}/WHEEL",
        f"{prefix}/direct_url.json",
        f"{prefix}/entry_points.txt",
        f"{prefix}/top_level.txt",
    }
    actual_dist_info = {
        name for name in installed_files if name.startswith(f"{prefix}/")
    }
    if actual_dist_info != expected_dist_info:
        raise ValueError("installed worker metadata members are not exact")
    if installed_files[f"{prefix}/METADATA"] != NEMOCLAW_METADATA_BYTES:
        raise ValueError("installed worker metadata changed")
    if (
        installed_files[f"{prefix}/entry_points.txt"]
        != NEMOCLAW_ENTRY_POINTS_BYTES
    ):
        raise ValueError("installed worker entrypoint metadata changed")
    if installed_files[f"{prefix}/top_level.txt"] != NEMOCLAW_TOP_LEVEL_BYTES:
        raise ValueError("installed worker top-level metadata changed")
    if installed_files[f"{prefix}/INSTALLER"] != b"pip\n":
        raise ValueError("installed worker installer metadata changed")
    if installed_files[f"{prefix}/REQUESTED"] != b"":
        raise ValueError("installed worker request metadata changed")
    _validate_wheel_metadata(installed_files[f"{prefix}/WHEEL"])
    try:
        direct_url = json.loads(installed_files[f"{prefix}/direct_url.json"])
        archive_info = direct_url["archive_info"]
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("installed worker wheel origin is invalid") from exc
    if (
        archive_info.get("hash") != f"sha256={reviewed_wheel_sha256}"
        or archive_info.get("hashes") != {"sha256": reviewed_wheel_sha256}
    ):
        raise ValueError("installed worker wheel origin is not reviewed")
    record_path = f"{prefix}/RECORD"
    rows = _parse_record(installed_files[record_path])
    launcher_rows = tuple(
        name
        for name in rows
        if PurePosixPath(name).name == NEMOCLAW_WORKER_LAUNCHER
        and len(PurePosixPath(name).parts) >= 2
        and PurePosixPath(name).parts[-2] == "bin"
    )
    if len(launcher_rows) != 1:
        raise ValueError("installed worker launcher metadata is not exact")
    launcher = _read_installed_regular_file(
        _installed_record_member_path(target, launcher_rows[0])
    )
    expected_launcher = (
        f"#!{python_executable}\n"
        "import sys\n"
        "from vital_relay.agent.worker import main\n"
        "if __name__ == '__main__':\n"
        "    sys.argv[0] = sys.argv[0].removesuffix('.exe')\n"
        "    sys.exit(main())\n"
    ).encode("utf-8")
    if launcher != expected_launcher:
        raise ValueError("installed worker launcher changed")


def _installed_distributions(
    python_executable: Path,
    target: Path,
) -> dict[str, str]:
    script = """
import importlib.metadata as metadata
import json
import re
import sys

target = sys.argv[1]
result = {}
for distribution in metadata.distributions(path=[target]):
    name = re.sub(r\"[-_.]+\", \"-\", distribution.metadata[\"Name\"]).lower()
    if name in result:
        raise SystemExit(2)
    result[name] = distribution.version
print(json.dumps(result, sort_keys=True, separators=(\",\", \":\")))
"""
    completed = subprocess.run(
        (str(python_executable), "-I", "-S", "-B", "-c", script, str(target)),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("installed dependency inventory is unreadable") from exc
    if completed.returncode != 0 or not isinstance(payload, dict):
        raise ValueError("installed dependency inventory is invalid")
    return {str(name): str(version) for name, version in payload.items()}


def _require_runtime_site_packages(
    python_executable: Path,
    target: Path,
) -> None:
    script = """
import json
import site
print(json.dumps(site.getsitepackages(), sort_keys=True, separators=(\",\", \":\")))
"""
    completed = subprocess.run(
        (str(python_executable), "-I", "-S", "-B", "-c", script),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    try:
        candidates = {
            Path(value).resolve()
            for value in json.loads(completed.stdout)
        }
    except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("NemoClaw runtime site-packages probe failed") from exc
    if completed.returncode != 0 or target.resolve() not in candidates:
        raise ValueError("target is not owned by the supplied runtime interpreter")


def _assert_no_runtime_customization_specs(
    python_executable: Path,
    target: Path,
) -> None:
    script = """
import importlib.machinery
import json
import sys

search = [sys.argv[1]]
for name in json.loads(sys.argv[2]):
    if importlib.machinery.PathFinder.find_spec(name, search) is not None:
        raise SystemExit(2)
"""
    completed = subprocess.run(
        (
            str(python_executable),
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(target),
            json.dumps(RUNTIME_CUSTOMIZATION_MODULES),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("installed runtime exposes a Python customization hook")


def _assert_negative_runtime_specs(
    python_executable: Path,
    target: Path,
    *,
    forbidden_modules: Sequence[str] = TRUSTED_HOST_MODULE_PREFIXES,
) -> None:
    script = """
import importlib.machinery
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])

def find_spec(fullname):
    search = [str(root)]
    qualified = []
    spec = None
    for part in fullname.split(\".\"):
        qualified.append(part)
        spec = importlib.machinery.PathFinder.find_spec(\".\".join(qualified), search)
        if spec is None:
            return None
        search = list(spec.submodule_search_locations or ())
    return spec

for name in json.loads(sys.argv[2]):
    if find_spec(name) is not None:
        raise SystemExit(2)
if find_spec(\"vital_relay.agent.worker\") is None:
    raise SystemExit(3)
"""
    completed = subprocess.run(
        (
            str(python_executable),
            "-I",
            "-S",
            "-B",
            "-c",
            script,
            str(target),
            json.dumps(tuple(forbidden_modules)),
        ),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if completed.returncode != 0:
        raise ValueError("installed runtime exposes trusted-host modules")


DOCKER_AGENT_SOURCE_MANIFEST = ReviewedSourceManifest(
    name="docker-agent-worker",
    entrypoint="vital_relay.agent.worker:main",
    source_paths=AGENT_WORKER_SOURCE_PATHS,
)
NEMOCLAW_AGENT_SOURCE_MANIFEST = ReviewedSourceManifest(
    name="nemoclaw-agent-worker",
    entrypoint=NEMOCLAW_WORKER_ENTRYPOINT,
    source_paths=AGENT_WORKER_SOURCE_PATHS,
)
MUTATION_WORKER_CONTRACT = MutationWorkerEntrypointContract()
MUTATION_WORKER_SOURCE_MANIFEST = ReviewedSourceManifest(
    name="mutation-worker",
    entrypoint=MUTATION_WORKER_CONTRACT.entrypoint,
    source_paths=MUTATION_WORKER_SOURCE_PATHS,
)
