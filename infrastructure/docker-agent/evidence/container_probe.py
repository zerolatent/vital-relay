"""Fixed in-container containment and gateway probe.

The host passes any ephemeral authority over stdin.  This process emits only
closed booleans and HTTP status/error codes; it never echoes request material.
"""

from __future__ import annotations

import errno
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any


MAX_RESPONSE_BYTES = 131_072


def _request(
    host: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, str | None, dict[str, Any] | None]:
    connection = http.client.HTTPConnection(host, 8080, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    except Exception:
        return 0, "transport_failed", None
    finally:
        connection.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        return 0, "response_too_large", None
    try:
        payload = json.loads(raw)
    except (UnicodeError, json.JSONDecodeError):
        return response.status, "invalid_json", None
    code: str | None = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("code"), str):
            code = detail["code"]
        elif isinstance(payload.get("error"), str):
            code = payload["error"]
    return response.status, code, payload if isinstance(payload, dict) else None


def _denied_read(path: str) -> bool:
    try:
        Path(path).read_bytes()
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    return False


def _denied_write(path: str) -> bool:
    try:
        Path(path).write_bytes(b"evidence-probe")
    except OSError as exc:
        return exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    try:
        Path(path).unlink()
    except OSError:
        pass
    return False


def _tmpfs_checks() -> tuple[bool, bool]:
    target = Path("/tmp/vital-relay-evidence-probe")
    target.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    target.chmod(0o700)
    write_ok = target.read_text(encoding="ascii").startswith("#!")
    try:
        completed = subprocess.run(
            (str(target),),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        execute_denied = True
    else:
        execute_denied = completed.returncode != 0
    target.unlink(missing_ok=True)
    return write_ok, execute_denied


def _unlisted_egress_denied() -> bool:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(1.0)
    try:
        return connection.connect_ex(("1.1.1.1", 443)) != 0
    finally:
        connection.close()


def _containment_probe(model: str) -> dict[str, object]:
    tmp_write, tmp_execute_denied = _tmpfs_checks()
    model_status, model_code, models = _request(
        "vllm-gateway",
        "GET",
        "/v1/models",
        headers={"Accept": "application/json"},
    )
    listed_models = {
        item.get("id")
        for item in (models or {}).get("data", [])
        if isinstance(item, dict)
    }
    vllm_denied_status, vllm_denied_code, _ = _request(
        "vllm-gateway",
        "GET",
        "/v1/unreviewed",
    )
    tool_denied_status, tool_denied_code, _ = _request(
        "tool-proxy-gateway",
        "GET",
        "/v1/unreviewed",
    )
    missing_status, missing_code, _ = _request(
        "tool-proxy-gateway",
        "POST",
        "/internal/v1/agent/tools/invoke",
        headers={"Content-Type": "application/json"},
        body=b"{}",
    )
    return {
        "schema_version": 1,
        "mode": "containment",
        "checks": {
            "effective_uid": os.getuid() == 65532,
            "protected_paths_absent": all(
                not Path(path).exists()
                for path in (
                    "/run/secrets",
                    "/root/.ssh",
                    "/var/run/docker.sock",
                )
            ),
            "protected_read_denied": _denied_read("/etc/shadow"),
            "worker_tree_write_denied": _denied_write(
                "/opt/vital-relay/src/vital_relay/evidence-write"
            ),
            "root_write_denied": _denied_write("/evidence-write"),
            "tmpfs_write_allowed": tmp_write,
            "tmpfs_execute_denied": tmp_execute_denied,
            "unlisted_egress_denied": _unlisted_egress_denied(),
            "vllm_route_allowed": model_status == 200 and model in listed_models,
            "vllm_wrong_path_denied": (
                vllm_denied_status == 403 and vllm_denied_code == "path_denied"
            ),
            "tool_wrong_path_denied": (
                tool_denied_status == 403
                and tool_denied_code == "tool_not_allowed"
            ),
            "tool_missing_authority_denied": (
                missing_status == 401 and missing_code == "invalid_capability"
            ),
            "no_transport_failure": model_code != "transport_failed",
        },
    }


def _tool_probe(requests: list[object]) -> dict[str, object]:
    results: dict[str, dict[str, object]] = {}
    for item in requests:
        if not isinstance(item, dict) or set(item) != {
            "probe_id",
            "authority",
            "invocation",
        }:
            raise ValueError("invalid tool probe input")
        probe_id = item["probe_id"]
        authority = item["authority"]
        invocation = item["invocation"]
        if (
            not isinstance(probe_id, str)
            or not isinstance(authority, str)
            or not authority
            or not isinstance(invocation, dict)
        ):
            raise ValueError("invalid tool probe input")
        body = json.dumps(
            invocation,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        status, code, payload = _request(
            "tool-proxy-gateway",
            "POST",
            "/internal/v1/agent/tools/invoke",
            headers={
                "Content-Type": "application/json",
                "X-Vital-Relay-Agent-Capability": authority,
            },
            body=body,
        )
        results[probe_id] = {
            "status": status,
            "code": code or ("success" if payload and "result" in payload else None),
        }
    return {
        "schema_version": 1,
        "mode": "tool",
        "results": results,
    }


def main() -> int:
    try:
        document = json.load(sys.stdin)
        if not isinstance(document, dict):
            raise ValueError("probe input must be a mapping")
        mode = document.get("mode")
        if mode == "containment" and set(document) == {"mode", "model"}:
            model = document["model"]
            if not isinstance(model, str) or not model:
                raise ValueError("model is required")
            result = _containment_probe(model)
        elif mode == "tool" and set(document) == {"mode", "requests"}:
            requests = document["requests"]
            if not isinstance(requests, list) or not requests:
                raise ValueError("tool requests are required")
            result = _tool_probe(requests)
        else:
            raise ValueError("unreviewed probe mode")
    except Exception:
        return 2
    json.dump(
        result,
        sys.stdout,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
