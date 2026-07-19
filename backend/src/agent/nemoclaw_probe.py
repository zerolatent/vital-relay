"""Minimal sandbox-side probes for the live NemoClaw acceptance harness.

This module is deliberately standard-library only.  It is staged as a reviewed
single-file bundle; it must never import the Vital Relay application, database,
capability, model-runner, persistence, or evidence-orchestrator modules.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import http.client
import json
import os
import re
import signal
import ssl
import stat
import subprocess
import sys
import time
from typing import Final


SCHEMA_VERSION: Final = 1
STAGED_RUNTIME: Final = "/sandbox/vital-relay-runtime"
STAGED_PYTHON: Final = "/sandbox/vital-relay-runtime/bin/python3.14"
STAGED_WORKER: Final = (
    "/sandbox/vital-relay-runtime/bin/vital-relay-agent-worker"
)
STAGED_PROBE: Final = "/sandbox/vital-relay-runtime/nemoclaw_probe.py"
PROTECTED_FILE: Final = "/proc/1/environ"
MANAGED_MODELS_HOST: Final = "inference.local"
MANAGED_MODELS_PATH: Final = "/v1/models"
TOOL_PROXY_HOST: Final = "vital-relay.internal"
TOOL_PROXY_PORT: Final = 8443
WRONG_TOOL_PATH_PREFIX: Final = "/health/vital-relay-evidence/"
PROXY_HOST_FILE: Final = "/usr/local/share/nemoclaw/dcode-proxy-host"
PROXY_PORT_FILE: Final = "/usr/local/share/nemoclaw/dcode-proxy-port"
UNLISTED_BINARY: Final = "/usr/bin/curl"
MAX_INPUT_BYTES: Final = 16_384
MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024
MAX_CA_BYTES: Final = 4 * 1024 * 1024
MAX_RUNTIME_FILES: Final = 100_000
MAX_RUNTIME_BYTES: Final = 4 * 1024 * 1024 * 1024
OCSF_EXPORT_DIRECTORY: Final = "/var/log"
MAX_OCSF_LOG_FILES: Final = 32
MAX_OCSF_DELTA_BYTES: Final = 8 * 1024 * 1024
MAX_OCSF_EVENT_BYTES: Final = 1024 * 1024
_OCSF_LOG_NAME_RE: Final = re.compile(
    r"^openshell-ocsf\.[0-9]{4}-[0-9]{2}-[0-9]{2}\.log$"
)
_VERSION_RE: Final = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_HOST_RE: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_CHALLENGE_RE: Final = re.compile(r"^[0-9a-f]{32}$")
_EXPECTED_WORKER_ENV_KEYS: Final = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


class ProbeError(RuntimeError):
    """Closed failure used only to select a nonzero exit status."""


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, child in pairs:
        if key in value:
            raise ProbeError
        value[key] = child
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _emit(receipt: dict[str, object]) -> int:
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


def _request(keys: frozenset[str]) -> dict[str, object]:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > MAX_INPUT_BYTES:
        raise ProbeError
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError from exc
    if not isinstance(value, dict) or frozenset(value) != keys:
        raise ProbeError
    return value


def _require_string(value: object, *, maximum: int = 2_048) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or len(value) > maximum
    ):
        raise ProbeError
    return value


def _require_sha256(value: object) -> str:
    text = _require_string(value, maximum=64)
    if _SHA256_RE.fullmatch(text) is None:
        raise ProbeError
    return text


def _challenge(value: object) -> str:
    text = _require_string(value, maximum=32)
    if _CHALLENGE_RE.fullmatch(text) is None:
        raise ProbeError
    return text


def _stable_stat(metadata: os.stat_result) -> tuple[int, ...]:
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


def _hash_descriptor(descriptor: int, maximum: int | None = None) -> str:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise ProbeError
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            break
        total += len(chunk)
        if maximum is not None and total > maximum:
            raise ProbeError
        digest.update(chunk)
    if _stable_stat(os.fstat(descriptor)) != _stable_stat(before):
        raise ProbeError
    return digest.hexdigest()


def _read_owned_file(
    path: str,
    *,
    maximum: int,
    exact_mode: int | None = None,
) -> tuple[bytes, str]:
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or (metadata.st_mode & 0o022) != 0
            or (
                exact_mode is not None
                and stat.S_IMODE(metadata.st_mode) != exact_mode
            )
        ):
            raise ProbeError
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, 16_384)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > maximum:
                raise ProbeError
        after = os.fstat(descriptor)
        if _stable_stat(after) != _stable_stat(metadata):
            raise ProbeError
        return bytes(raw), hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)


def _transport_from_environment() -> tuple[str, int, ssl.SSLContext, str, str]:
    raw_host, _ = _read_owned_file(PROXY_HOST_FILE, maximum=256, exact_mode=0o444)
    raw_port, _ = _read_owned_file(PROXY_PORT_FILE, maximum=16, exact_mode=0o444)
    try:
        host = raw_host.decode("ascii").strip()
        port_text = raw_port.decode("ascii").strip()
    except UnicodeError as exc:
        raise ProbeError from exc
    if _HOST_RE.fullmatch(host) is None or not port_text.isdecimal():
        raise ProbeError
    port = int(port_text)
    if not 1 <= port <= 65_535:
        raise ProbeError
    proxy_url = f"http://{host}:{port}"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if os.environ.get(name) != proxy_url:
            raise ProbeError
    no_proxy = f"localhost,127.0.0.1,::1,{host}"
    for name in ("NO_PROXY", "no_proxy"):
        if os.environ.get(name) != no_proxy:
            raise ProbeError
    if any(os.environ.get(name) for name in ("ALL_PROXY", "all_proxy", "OPENAI_PROXY")):
        raise ProbeError
    ca_path = os.environ.get("SSL_CERT_FILE", "")
    if not ca_path.startswith("/") or "\x00" in ca_path:
        raise ProbeError
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if os.environ.get(name) not in {None, ca_path}:
            raise ProbeError
    ca_bytes, ca_sha256 = _read_owned_file(ca_path, maximum=MAX_CA_BYTES)
    try:
        context = ssl.create_default_context(cadata=ca_bytes.decode("ascii"))
    except (UnicodeError, ValueError, ssl.SSLError) as exc:
        raise ProbeError from exc
    identity = _sha256(
        {
            "ca_sha256": ca_sha256,
            "no_proxy": no_proxy,
            "proxy_url": proxy_url,
        }
    )
    return host, port, context, ca_sha256, identity


def _https_request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    proxy_host, proxy_port, context, _, _ = _transport_from_environment()
    connection = http.client.HTTPSConnection(
        proxy_host,
        proxy_port,
        timeout=10.0,
        context=context,
    )
    connection.set_tunnel(host, port)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        raw_length = response.getheader("content-length")
        if raw_length is not None and int(raw_length) > MAX_RESPONSE_BYTES:
            raise ProbeError
        body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ProbeError
        return response.status, body
    finally:
        connection.close()


def _mount_identity(root: str) -> tuple[str, bool, bool]:
    descriptor = os.open(
        "/proc/self/mountinfo",
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 4 * 1024 * 1024 + 1)
        if (
            len(raw) > 4 * 1024 * 1024
            or _stable_stat(os.fstat(descriptor)) != _stable_stat(before)
        ):
            raise ProbeError
    finally:
        os.close(descriptor)
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ProbeError from exc

    def unescape(value: str) -> str:
        return re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            value,
        )

    candidates: list[tuple[int, dict[str, object]]] = []
    for line in lines:
        fields = line.split()
        if "-" not in fields or len(fields) < 10:
            raise ProbeError
        separator = fields.index("-")
        if separator < 6 or len(fields) <= separator + 3:
            raise ProbeError
        mount_point = unescape(fields[4])
        normalized = mount_point.rstrip("/") or "/"
        if root != normalized and not root.startswith(normalized.rstrip("/") + "/"):
            continue
        mount_options = tuple(sorted(fields[5].split(",")))
        super_options = tuple(sorted(fields[separator + 3].split(",")))
        entry = {
            "device": fields[2],
            "filesystem": fields[separator + 1],
            "mount_id": fields[0],
            "mount_options": mount_options,
            "mount_point": mount_point,
            "root": unescape(fields[3]),
            "super_options": super_options,
        }
        candidates.append((len(normalized), entry))
    if not candidates:
        raise ProbeError
    _, authoritative = max(candidates, key=lambda item: item[0])
    mount_read_only = "ro" in authoritative["mount_options"]
    statvfs_read_only = bool(os.statvfs(root).f_flag & os.ST_RDONLY)
    return _sha256(authoritative), mount_read_only, statvfs_read_only


def _assert_directory_not_writable(descriptor: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    try:
        created = os.open(
            ".vital-relay-immutability-probe",
            flags,
            0o600,
            dir_fd=descriptor,
        )
    except OSError as exc:
        if exc.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
            raise ProbeError from exc
        return
    os.close(created)
    try:
        os.unlink(".vital-relay-immutability-probe", dir_fd=descriptor)
    except OSError:
        pass
    raise ProbeError


def _assert_file_not_writable(name: str, directory_fd: int) -> None:
    flags = os.O_WRONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
            raise ProbeError from exc
        return
    os.close(descriptor)
    raise ProbeError


def runtime_manifest(root: str = STAGED_RUNTIME) -> tuple[str, int, int, int]:
    """Descriptor-walk and hash immutable sorted metadata without path traversal."""

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    root_fd = os.open(root, flags)
    entries: list[dict[str, object]] = []
    file_count = 0
    directory_count = 0
    total_bytes = 0

    def walk(directory_fd: int, relative: str) -> None:
        nonlocal file_count, directory_count, total_bytes
        before = os.fstat(directory_fd)
        if not stat.S_ISDIR(before.st_mode):
            raise ProbeError
        names = sorted(os.listdir(directory_fd))
        if any(
            not name
            or name in {".", ".."}
            or "/" in name
            or "\x00" in name
            for name in names
        ):
            raise ProbeError
        directory_count += 1
        if file_count + directory_count > MAX_RUNTIME_FILES:
            raise ProbeError
        _assert_directory_not_writable(directory_fd)
        entries.append(
            {
                "children": names,
                "ctime_ns": before.st_ctime_ns,
                "device": before.st_dev,
                "gid": before.st_gid,
                "inode": before.st_ino,
                "kind": "directory",
                "mode": stat.S_IMODE(before.st_mode),
                "mtime_ns": before.st_mtime_ns,
                "nlink": before.st_nlink,
                "path": relative,
                "uid": before.st_uid,
            }
        )
        for name in names:
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            path = f"{relative}/{name}" if relative else name
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(name, flags, dir_fd=directory_fd)
                try:
                    if _stable_stat(os.fstat(child_fd)) != _stable_stat(metadata):
                        raise ProbeError
                    walk(child_fd, path)
                    if _stable_stat(os.fstat(child_fd)) != _stable_stat(metadata):
                        raise ProbeError
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(metadata.st_mode):
                _assert_file_not_writable(name, directory_fd)
                file_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if _stable_stat(opened) != _stable_stat(metadata):
                        raise ProbeError
                    file_hash = _hash_descriptor(file_fd)
                    if _stable_stat(os.fstat(file_fd)) != _stable_stat(metadata):
                        raise ProbeError
                finally:
                    os.close(file_fd)
                file_count += 1
                total_bytes += metadata.st_size
                if (
                    file_count + directory_count > MAX_RUNTIME_FILES
                    or total_bytes > MAX_RUNTIME_BYTES
                ):
                    raise ProbeError
                entries.append(
                    {
                        "ctime_ns": metadata.st_ctime_ns,
                        "device": metadata.st_dev,
                        "gid": metadata.st_gid,
                        "inode": metadata.st_ino,
                        "kind": "file",
                        "mode": stat.S_IMODE(metadata.st_mode),
                        "mtime_ns": metadata.st_mtime_ns,
                        "nlink": metadata.st_nlink,
                        "path": path,
                        "sha256": file_hash,
                        "size": metadata.st_size,
                        "uid": metadata.st_uid,
                    }
                )
            else:
                raise ProbeError
        if _stable_stat(os.fstat(directory_fd)) != _stable_stat(before):
            raise ProbeError

    try:
        walk(root_fd, "")
    finally:
        os.close(root_fd)
    if file_count == 0:
        raise ProbeError
    return _sha256(entries), file_count, directory_count, total_bytes


def _runtime_inference() -> int:
    request = _request(
        frozenset(
            {
                "expected_ca_sha256",
                "expected_model",
                "expected_probe_source_sha256",
                "expected_runtime_sha256",
                "expected_transport_identity_sha256",
            }
        )
    )
    expected_model = _require_string(request["expected_model"], maximum=200)
    expected_probe_source = _require_sha256(
        request["expected_probe_source_sha256"]
    )
    expected_runtime = _require_sha256(request["expected_runtime_sha256"])
    expected_ca = _require_sha256(request["expected_ca_sha256"])
    expected_transport = _require_sha256(
        request["expected_transport_identity_sha256"]
    )
    if os.readlink("/proc/self/exe") != STAGED_PYTHON:
        raise ProbeError
    if os.path.realpath(__file__) != STAGED_PROBE:
        raise ProbeError
    probe_fd = os.open(
        STAGED_PROBE,
        os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        probe_source_sha256 = _hash_descriptor(probe_fd, 2 * 1024 * 1024)
    finally:
        os.close(probe_fd)
    if probe_source_sha256 != expected_probe_source:
        raise ProbeError
    before = runtime_manifest()
    if before[0] != expected_runtime:
        raise ProbeError
    _, _, _, ca_sha256, transport_identity = _transport_from_environment()
    if ca_sha256 != expected_ca or transport_identity != expected_transport:
        raise ProbeError
    status_code, body = _https_request(
        MANAGED_MODELS_HOST,
        443,
        "GET",
        MANAGED_MODELS_PATH,
        headers={"Authorization": "Bearer nemoclaw-managed-inference"},
    )
    if status_code != 200:
        raise ProbeError
    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError from exc
    if (
        not isinstance(payload, dict)
        or set(payload) < {"data"}
        or not isinstance(payload["data"], list)
    ):
        raise ProbeError
    model_ids = {
        item.get("id")
        for item in payload["data"]
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if model_ids != {expected_model}:
        raise ProbeError
    after = runtime_manifest()
    if after != before:
        raise ProbeError
    mount_identity, mount_read_only, statvfs_read_only = _mount_identity(STAGED_RUNTIME)
    if not mount_read_only or not statvfs_read_only:
        raise ProbeError
    return _emit(
        {
            "actual_ca_sha256": ca_sha256,
            "model_identity_sha256": _sha256(expected_model),
            "mount_read_only": True,
            "passed": True,
            "probe": "runtime_inference",
            "proc_self_exe": STAGED_PYTHON,
            "runtime_bytes": before[3],
            "runtime_directory_count": before[2],
            "runtime_file_count": before[1],
            "runtime_manifest_sha256": before[0],
            "sandbox_probe_source_sha256": probe_source_sha256,
            "runtime_mount_identity_sha256": mount_identity,
            "runtime_write_denied": True,
            "schema_version": SCHEMA_VERSION,
            "statvfs_read_only": True,
            "transport_identity_sha256": transport_identity,
        }
    )


def _network_attempt(probe: str) -> int:
    request = _request(frozenset({"challenge"}))
    challenge = _challenge(request["challenge"])
    if probe == "unlisted_host":
        host = f"{challenge}.github.com"
        port = 443
        path = "/"
    elif probe == "wrong_tool_route":
        host = TOOL_PROXY_HOST
        port = TOOL_PROXY_PORT
        path = WRONG_TOOL_PATH_PREFIX + challenge
    else:
        raise ProbeError
    outcome = "transport_error"
    try:
        _https_request(host, port, "GET", path)
        outcome = "http_response"
    except (OSError, ssl.SSLError, http.client.HTTPException):
        pass
    return _emit(
        {
            "attempted": True,
            "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
            "client_outcome": outcome,
            "probe": probe,
            "process_pid": os.getpid(),
            "schema_version": SCHEMA_VERSION,
        }
    )


def _unlisted_binary() -> int:
    request = _request(frozenset({"challenge"}))
    challenge = _challenge(request["challenge"])
    proxy_host, proxy_port, _, _, _ = _transport_from_environment()
    ca_path = _require_string(os.environ.get("SSL_CERT_FILE"), maximum=2_048)
    url = f"https://inference.local/v1/models?evidence_challenge={challenge}"
    argv = (
        UNLISTED_BINARY,
        "--silent",
        "--show-error",
        "--fail-with-body",
        "--max-time",
        "10",
        "--proxy",
        f"http://{proxy_host}:{proxy_port}",
        "--cacert",
        ca_path,
        url,
    )
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    process_pid = process.pid
    try:
        returncode = process.wait(timeout=15.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5.0)
        returncode = process.returncode
    return _emit(
        {
            "attempted": True,
            "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
            "client_outcome": (
                "http_response" if returncode == 0 else "process_nonzero"
            ),
            "probe": "unlisted_binary",
            "process_pid": process_pid,
            "schema_version": SCHEMA_VERSION,
        }
    )


def _protected_file() -> int:
    challenge = "0" * 32
    try:
        descriptor = os.open(PROTECTED_FILE, os.O_RDONLY | os.O_CLOEXEC)
    except PermissionError:
        return _emit(
            {
                "attempted": True,
                "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
                "client_outcome": "permission_denied",
                "probe": "protected_file",
                "process_pid": os.getpid(),
                "schema_version": SCHEMA_VERSION,
            }
        )
    except OSError as exc:
        if exc.errno in {errno.EACCES, errno.EPERM}:
            return _emit(
                {
                    "attempted": True,
                    "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
                    "client_outcome": "permission_denied",
                    "probe": "protected_file",
                    "process_pid": os.getpid(),
                    "schema_version": SCHEMA_VERSION,
                }
            )
        raise ProbeError from exc
    else:
        os.close(descriptor)
        raise ProbeError


def _ocsf_provenance(
    event: object,
    *,
    expected_schema_version: str,
    expected_vendor: str,
    expected_openshell_version: str,
) -> None:
    if not isinstance(event, dict):
        raise ProbeError
    metadata = event.get("metadata")
    product = metadata.get("product") if isinstance(metadata, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("version") != expected_schema_version
        or not isinstance(product, dict)
        or product.get("name") != "OpenShell Sandbox Supervisor"
        or product.get("vendor_name") != expected_vendor
        or product.get("version") != expected_openshell_version
    ):
        raise ProbeError


def capture_ocsf_cursor(
    log_directory: str = OCSF_EXPORT_DIRECTORY,
    *,
    expected_schema_version: str,
    expected_vendor: str,
    expected_openshell_version: str,
) -> dict[str, object]:
    """Capture authoritative in-sandbox JSONL offsets and current provenance."""

    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(log_directory, flags)
    files: list[dict[str, object]] = []
    latest_event: object | None = None
    try:
        names = sorted(
            name
            for name in os.listdir(directory_fd)
            if _OCSF_LOG_NAME_RE.fullmatch(name) is not None
        )
        if not names or len(names) > MAX_OCSF_LOG_FILES:
            raise ProbeError
        file_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        for name in names:
            descriptor = os.open(name, file_flags, dir_fd=directory_fd)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ProbeError
                if metadata.st_size:
                    os.lseek(descriptor, -1, os.SEEK_END)
                    if os.read(descriptor, 1) != b"\n":
                        raise ProbeError
                os.lseek(descriptor, 0, os.SEEK_SET)
                prefix_digest = hashlib.sha256()
                remaining = metadata.st_size
                while remaining:
                    chunk = os.read(descriptor, min(65_536, remaining))
                    if not chunk:
                        raise ProbeError
                    prefix_digest.update(chunk)
                    remaining -= len(chunk)
                if metadata.st_size:
                    tail_size = min(metadata.st_size, MAX_OCSF_EVENT_BYTES + 1)
                    os.lseek(descriptor, -tail_size, os.SEEK_END)
                    tail = os.read(descriptor, tail_size)
                    lines = tail.splitlines()
                    if tail_size < metadata.st_size:
                        lines = lines[1:]
                    if not lines:
                        raise ProbeError
                    try:
                        latest_event = json.loads(
                            lines[-1], object_pairs_hook=_unique_json_object
                        )
                    except (UnicodeError, json.JSONDecodeError) as exc:
                        raise ProbeError from exc
                if _stable_stat(os.fstat(descriptor)) != _stable_stat(metadata):
                    raise ProbeError
                files.append(
                    {
                        "device": metadata.st_dev,
                        "inode": metadata.st_ino,
                        "mtime_ns": metadata.st_mtime_ns,
                        "name": name,
                        "prefix_sha256": prefix_digest.hexdigest(),
                        "size": metadata.st_size,
                    }
                )
            finally:
                os.close(descriptor)
    finally:
        os.close(directory_fd)
    _ocsf_provenance(
        latest_event,
        expected_schema_version=expected_schema_version,
        expected_vendor=expected_vendor,
        expected_openshell_version=expected_openshell_version,
    )
    return {"files": files, "probe": "ocsf_cursor", "schema_version": 1}


def _validated_ocsf_cursor(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "files",
        "probe",
        "schema_version",
    }:
        raise ProbeError
    files = value.get("files")
    if (
        value.get("probe") != "ocsf_cursor"
        or value.get("schema_version") != 1
        or not isinstance(files, list)
        or not 1 <= len(files) <= MAX_OCSF_LOG_FILES
    ):
        raise ProbeError
    names: list[str] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {
            "device",
            "inode",
            "mtime_ns",
            "name",
            "prefix_sha256",
            "size",
        }:
            raise ProbeError
        name = item.get("name")
        if (
            not isinstance(name, str)
            or _OCSF_LOG_NAME_RE.fullmatch(name) is None
            or not all(
                isinstance(item.get(field), int) and item[field] >= minimum
                for field, minimum in (
                    ("device", 0),
                    ("inode", 1),
                    ("mtime_ns", 0),
                    ("size", 0),
                )
            )
            or not isinstance(item.get("prefix_sha256"), str)
            or _SHA256_RE.fullmatch(item["prefix_sha256"]) is None
        ):
            raise ProbeError
        names.append(name)
    if names != sorted(set(names)):
        raise ProbeError
    return value


def read_ocsf_delta(
    cursor_value: object,
    log_directory: str = OCSF_EXPORT_DIRECTORY,
    *,
    expected_schema_version: str,
    expected_vendor: str,
    expected_openshell_version: str,
) -> tuple[dict[str, object], ...]:
    """Read only stable post-cursor records from the fixed sandbox export."""

    cursor = _validated_ocsf_cursor(cursor_value)
    cursor_files = cursor["files"]
    assert isinstance(cursor_files, list)
    cursor_by_name = {item["name"]: item for item in cursor_files}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(log_directory, flags)
    raw_delta = bytearray()
    try:
        active_names = sorted(
            name
            for name in os.listdir(directory_fd)
            if _OCSF_LOG_NAME_RE.fullmatch(name) is not None
        )
        if (
            not 1 <= len(active_names) <= MAX_OCSF_LOG_FILES
            or not set(cursor_by_name).issubset(active_names)
        ):
            raise ProbeError
        file_flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        for name in active_names:
            descriptor = os.open(name, file_flags, dir_fd=directory_fd)
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise ProbeError
                prior = cursor_by_name.get(name)
                offset = 0
                if prior is not None:
                    if (
                        before.st_dev != prior["device"]
                        or before.st_ino != prior["inode"]
                        or before.st_size < prior["size"]
                    ):
                        raise ProbeError
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    prefix_digest = hashlib.sha256()
                    remaining = prior["size"]
                    while remaining:
                        chunk = os.read(descriptor, min(65_536, remaining))
                        if not chunk:
                            raise ProbeError
                        prefix_digest.update(chunk)
                        remaining -= len(chunk)
                    if not hmac.compare_digest(
                        prefix_digest.hexdigest(), prior["prefix_sha256"]
                    ):
                        raise ProbeError
                    offset = prior["size"]
                os.lseek(descriptor, offset, os.SEEK_SET)
                while True:
                    chunk = os.read(descriptor, 65_536)
                    if not chunk:
                        break
                    raw_delta.extend(chunk)
                    if len(raw_delta) > MAX_OCSF_DELTA_BYTES:
                        raise ProbeError
                if _stable_stat(os.fstat(descriptor)) != _stable_stat(before):
                    raise ProbeError
            finally:
                os.close(descriptor)
    finally:
        os.close(directory_fd)
    if raw_delta and not raw_delta.endswith(b"\n"):
        raise ProbeError
    events: list[dict[str, object]] = []
    for line in raw_delta.splitlines():
        if not line or len(line) > MAX_OCSF_EVENT_BYTES:
            raise ProbeError
        try:
            event = json.loads(line, object_pairs_hook=_unique_json_object)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProbeError from exc
        _ocsf_provenance(
            event,
            expected_schema_version=expected_schema_version,
            expected_vendor=expected_vendor,
            expected_openshell_version=expected_openshell_version,
        )
        assert isinstance(event, dict)
        events.append(event)
    return tuple(events)


def _ocsf_export() -> int:
    request = _request(
        frozenset(
            {
                "action",
                "cursor",
                "expected_openshell_version",
                "expected_schema_version",
                "expected_vendor",
                "export_path",
            }
        )
    )
    action = request["action"]
    export_path = _require_string(request["export_path"], maximum=256)
    schema_version = _require_string(
        request["expected_schema_version"], maximum=32
    )
    vendor = _require_string(request["expected_vendor"], maximum=100)
    openshell_version = _require_string(
        request["expected_openshell_version"], maximum=32
    )
    if (
        action not in {"capture", "delta"}
        or export_path != OCSF_EXPORT_DIRECTORY
        or _VERSION_RE.fullmatch(schema_version) is None
        or _VERSION_RE.fullmatch(openshell_version) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}", vendor) is None
    ):
        raise ProbeError
    if action == "capture":
        if request["cursor"] is not None:
            raise ProbeError
        cursor = capture_ocsf_cursor(
            expected_schema_version=schema_version,
            expected_vendor=vendor,
            expected_openshell_version=openshell_version,
        )
        events: tuple[dict[str, object], ...] = ()
    else:
        cursor = _validated_ocsf_cursor(request["cursor"])
        events = read_ocsf_delta(
            cursor,
            expected_schema_version=schema_version,
            expected_vendor=vendor,
            expected_openshell_version=openshell_version,
        )
    return _emit(
        {
            "action": action,
            "cursor": cursor,
            "events": events,
            "expected_openshell_version": openshell_version,
            "expected_schema_version": schema_version,
            "expected_vendor": vendor,
            "export_path": export_path,
            "probe": "ocsf_export",
            "schema_version": SCHEMA_VERSION,
        }
    )


def _proc_stat(pid: int) -> int:
    raw, _ = _read_proc_file(pid, "stat", maximum=16_384)
    try:
        text = raw.decode("ascii")
        close = text.rindex(")")
        fields = text[close + 2 :].split()
        return int(fields[19])
    except (UnicodeError, ValueError, IndexError) as exc:
        raise ProbeError from exc


def _read_proc_file(pid: int, name: str, *, maximum: int) -> tuple[bytes, str]:
    if pid < 1 or name not in {"cmdline", "environ", "stat"}:
        raise ProbeError
    path = f"/proc/{pid}/{name}"
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, maximum + 1)
        if (
            len(raw) > maximum
            or _stable_stat(os.fstat(descriptor)) != _stable_stat(before)
        ):
            raise ProbeError
        return raw, hashlib.sha256(raw).hexdigest()
    finally:
        os.close(descriptor)


def _worker_transport(pid: int) -> tuple[str, str]:
    raw, _ = _read_proc_file(pid, "environ", maximum=256 * 1024)
    environment: dict[str, str] = {}
    for item in raw.rstrip(b"\0").split(b"\0"):
        try:
            key, value = item.decode("utf-8").split("=", 1)
        except (UnicodeError, ValueError) as exc:
            raise ProbeError from exc
        if key in environment:
            raise ProbeError
        environment[key] = value
    selected = {key: environment.get(key) for key in _EXPECTED_WORKER_ENV_KEYS}
    if any(
        selected[key] is None
        for key in (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        )
    ):
        raise ProbeError
    if any(environment.get(key) for key in ("ALL_PROXY", "all_proxy", "OPENAI_PROXY")):
        raise ProbeError
    raw_ca = selected["SSL_CERT_FILE"]
    if not isinstance(raw_ca, str) or not raw_ca.startswith("/"):
        raise ProbeError
    if any(
        selected[name] not in {None, raw_ca}
        for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
    ):
        raise ProbeError
    root_fd = os.open(
        f"/proc/{pid}/root", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    )
    try:
        def read_root_owned(
            relative: str,
            maximum: int,
            mode: int | None = None,
        ) -> bytes:
            descriptor = os.open(
                relative,
                os.O_RDONLY
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=root_fd,
            )
            try:
                before = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != 0
                    or before.st_mode & 0o022
                    or (
                        mode is not None
                        and stat.S_IMODE(before.st_mode) != mode
                    )
                ):
                    raise ProbeError
                raw = os.read(descriptor, maximum + 1)
                if (
                    len(raw) > maximum
                    or _stable_stat(os.fstat(descriptor))
                    != _stable_stat(before)
                ):
                    raise ProbeError
                return raw
            finally:
                os.close(descriptor)

        try:
            proxy_host = read_root_owned(
                PROXY_HOST_FILE.lstrip("/"), 256, 0o444
            ).decode("ascii").strip()
            proxy_port_text = read_root_owned(
                PROXY_PORT_FILE.lstrip("/"), 16, 0o444
            ).decode("ascii").strip()
        except UnicodeError as exc:
            raise ProbeError from exc
        if (
            _HOST_RE.fullmatch(proxy_host) is None
            or not proxy_port_text.isdecimal()
            or not 1 <= int(proxy_port_text) <= 65_535
        ):
            raise ProbeError
        proxy_url = f"http://{proxy_host}:{int(proxy_port_text)}"
        expected_no_proxy = f"localhost,127.0.0.1,::1,{proxy_host}"
        if any(
            selected[name] != proxy_url
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
        ) or any(
            selected[name] != expected_no_proxy
            for name in ("NO_PROXY", "no_proxy")
        ):
            raise ProbeError
        ca_fd = os.open(
            raw_ca.lstrip("/"),
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            ca_metadata = os.fstat(ca_fd)
            if (
                not stat.S_ISREG(ca_metadata.st_mode)
                or ca_metadata.st_uid != 0
                or ca_metadata.st_mode & 0o022
            ):
                raise ProbeError
            ca_sha256 = _hash_descriptor(ca_fd, MAX_CA_BYTES)
        finally:
            os.close(ca_fd)
    finally:
        os.close(root_fd)
    identity = _sha256({"environment": selected, "actual_ca_sha256": ca_sha256})
    return ca_sha256, identity


def _exact_process() -> int:
    request = _request(
        frozenset(
            {
                "action",
                "expected_ca_sha256",
                "expected_command_sha256",
                "expected_start_time_ticks",
                "expected_transport_identity_sha256",
                "process_pid",
            }
        )
    )
    action = request["action"]
    pid = request["process_pid"]
    start = request["expected_start_time_ticks"]
    if (
        action not in {"inspect", "terminate"}
        or not isinstance(pid, int)
        or pid < 1
        or not isinstance(start, int)
        or start < 0
        or (action == "terminate" and start < 1)
    ):
        raise ProbeError
    expected_command = _require_sha256(request["expected_command_sha256"])
    expected_ca = _require_sha256(request["expected_ca_sha256"])
    expected_transport = _require_sha256(
        request["expected_transport_identity_sha256"]
    )
    expected_handle = _sha256(
        {
            "command_sha256": expected_command,
            "executable": STAGED_PYTHON,
            "pid": pid,
            "start_time_ticks": start,
            "transport_identity_sha256": expected_transport,
        }
    )
    try:
        actual_start = _proc_stat(pid)
    except (FileNotFoundError, ProcessLookupError):
        if action != "terminate":
            raise
        return _emit(
            {
                "absent_after_termination": True,
                "action": action,
                "actual_ca_sha256": expected_ca,
                "exact_command_sha256": expected_command,
                "exact_executable": STAGED_PYTHON,
                "probe": "exact_process",
                "process_handle_sha256": expected_handle,
                "process_pid": pid,
                "schema_version": SCHEMA_VERSION,
                "start_time_ticks": start,
                "worker_transport_identity_sha256": expected_transport,
            }
        )
    if action == "terminate" and actual_start != start:
        return _emit(
            {
                "absent_after_termination": True,
                "action": action,
                "actual_ca_sha256": expected_ca,
                "exact_command_sha256": expected_command,
                "exact_executable": STAGED_PYTHON,
                "probe": "exact_process",
                "process_handle_sha256": expected_handle,
                "process_pid": pid,
                "schema_version": SCHEMA_VERSION,
                "start_time_ticks": start,
                "worker_transport_identity_sha256": expected_transport,
            }
        )
    if (start and actual_start != start) or os.readlink(
        f"/proc/{pid}/exe"
    ) != STAGED_PYTHON:
        raise ProbeError
    start = actual_start
    cmdline, _ = _read_proc_file(pid, "cmdline", maximum=16_384)
    command_sha256 = hashlib.sha256(cmdline).hexdigest()
    if (
        command_sha256 != expected_command
        or not cmdline
        or STAGED_WORKER.encode() not in cmdline.split(b"\0")
    ):
        raise ProbeError
    actual_ca, actual_transport = _worker_transport(pid)
    if actual_ca != expected_ca or actual_transport != expected_transport:
        raise ProbeError
    handle = _sha256(
        {
            "command_sha256": command_sha256,
            "executable": STAGED_PYTHON,
            "pid": pid,
            "start_time_ticks": start,
            "transport_identity_sha256": actual_transport,
        }
    )
    absent = False
    if action == "terminate":
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                observed_start = _proc_stat(pid)
            except (FileNotFoundError, ProcessLookupError):
                absent = True
                break
            if observed_start != start:
                absent = True
                break
            time.sleep(0.05)
        if not absent:
            raise ProbeError
    return _emit(
        {
            "absent_after_termination": absent,
            "action": action,
            "actual_ca_sha256": actual_ca,
            "exact_command_sha256": command_sha256,
            "exact_executable": STAGED_PYTHON,
            "probe": "exact_process",
            "process_handle_sha256": handle,
            "process_pid": pid,
            "schema_version": SCHEMA_VERSION,
            "start_time_ticks": start,
            "worker_transport_identity_sha256": actual_transport,
        }
    )


def main(argv: tuple[str, ...] | None = None) -> int:
    active = tuple(sys.argv[1:] if argv is None else argv)
    if len(active) != 1:
        return 2
    try:
        if active[0] == "runtime-inference":
            return _runtime_inference()
        if active[0] == "unlisted-host":
            return _network_attempt("unlisted_host")
        if active[0] == "wrong-tool-route":
            return _network_attempt("wrong_tool_route")
        if active[0] == "unlisted-binary":
            return _unlisted_binary()
        if active[0] == "protected-file":
            return _protected_file()
        if active[0] == "ocsf-export":
            return _ocsf_export()
        if active[0] == "exact-process":
            return _exact_process()
    except Exception:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
