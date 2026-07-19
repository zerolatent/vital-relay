#!/usr/bin/env python3
"""Run the fixed Vital Relay NemoClaw live-attestation command graph."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SOURCE = PROJECT_ROOT / "backend/src"
SCHEMA_VERSION = 2
LANE_NAME = "nemoclaw-openshell-live-policy-attestation"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _emit_dependency_failure() -> int:
    body = {
        "blockers": ["host_harness_dependencies_unavailable"],
        "evidence_kind": "live_attempt",
        "lane": LANE_NAME,
        "outcome": "failed",
        "schema_version": SCHEMA_VERSION,
    }
    artifact = {
        **body,
        "evidence_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }
    sys.stdout.buffer.write(_canonical(artifact) + b"\n")
    return 1


def _load_attestor() -> Callable[[tuple[str, ...]], int] | None:
    try:
        from vital_relay.agent.nemoclaw_live_evidence import main as attest
    except Exception:
        return None
    return attest


def main() -> int:
    attest = _load_attestor()
    if attest is None:
        return _emit_dependency_failure()
    # The implementation emits a canonical closed failure receipt.  This
    # wrapper accepts no operator-controlled argv that could become a
    # subprocess path, sandbox name, command, route, or binary.
    return attest(tuple(sys.argv[1:]))


if __name__ == "__main__":
    if not BACKEND_SOURCE.is_dir():
        raise SystemExit(1)
    sys.path.insert(0, str(BACKEND_SOURCE))
    raise SystemExit(main())
