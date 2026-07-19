"""Read-only prerequisite inventory; readiness is never live-run evidence."""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

import httpx

from vital_relay.agent.contracts import VLLMSettings


MINIMUM_NEMOCLAW_NODE = (22, 16, 0)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    ready: bool
    detail: str


def inspect_agent_readiness(
    settings: VLLMSettings,
    *,
    vllm_python: Path | None = None,
) -> tuple[ReadinessCheck, ...]:
    """Inspect prerequisites without installing software or changing state."""

    python_path = vllm_python or Path(
        os.environ.get(
            "VITAL_RELAY_VLLM_PYTHON",
            str(Path.home() / ".venv-vllm-metal/bin/python"),
        )
    )
    return (
        _node_check(),
        _docker_check(),
        _command_check("nemo-deepagents", ("nemo-deepagents", "--version")),
        _vllm_python_check(python_path),
        _vllm_endpoint_check(settings),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect Vital Relay agent-runtime prerequisites",
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--vllm-python", type=Path)
    args = parser.parse_args(argv)
    settings = VLLMSettings(
        base_url=args.base_url,
        model=args.model,
        api_key=os.environ.get("VITAL_RELAY_VLLM_API_KEY", "local-vllm"),
    )
    checks = inspect_agent_readiness(
        settings,
        vllm_python=args.vllm_python,
    )
    print(  # noqa: T201 - this module is an explicit CLI
        json.dumps(
            {
                "ready": all(check.ready for check in checks),
                "checks": [asdict(check) for check in checks],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if all(check.ready for check in checks) else 1


def _node_check() -> ReadinessCheck:
    completed = _run(("node", "--version"))
    if completed is None:
        return ReadinessCheck("node", False, "node command not found")
    raw = completed.removeprefix("v").strip()
    try:
        version = tuple(int(part) for part in raw.split(".")[:3])
    except ValueError:
        return ReadinessCheck("node", False, "node version was not parseable")
    ready = version >= MINIMUM_NEMOCLAW_NODE
    detail = raw if ready else f"{raw}; NemoClaw requires >=22.16.0"
    return ReadinessCheck("node", ready, detail)


def _docker_check() -> ReadinessCheck:
    completed = _run(("docker", "info", "--format", "{{.ServerVersion}}"))
    if completed is None:
        return ReadinessCheck(
            "container_runtime",
            False,
            "docker CLI or running Docker/Colima daemon unavailable",
        )
    return ReadinessCheck("container_runtime", True, completed)


def _command_check(name: str, command: tuple[str, ...]) -> ReadinessCheck:
    completed = _run(command)
    if completed is None:
        return ReadinessCheck(name, False, f"{name} command unavailable")
    return ReadinessCheck(name, True, completed.splitlines()[0][:200])


def _vllm_python_check(python_path: Path) -> ReadinessCheck:
    completed = _run(
        (
            str(python_path),
            "-c",
            (
                "import platform,sys;"
                "print(f'{sys.version_info.major}.{sys.version_info.minor} "
                "{platform.machine()}')"
            ),
        )
    )
    if completed is None:
        return ReadinessCheck(
            "vllm_metal_python",
            False,
            f"native Python 3.12 not found at {python_path}",
        )
    ready = completed.strip() == "3.12 arm64"
    detail = completed if ready else f"{completed}; expected 3.12 arm64"
    return ReadinessCheck("vllm_metal_python", ready, detail)


def _vllm_endpoint_check(settings: VLLMSettings) -> ReadinessCheck:
    try:
        response = httpx.get(
            f"{settings.base_url}/models",
            headers={
                "Authorization": (
                    "Bearer " + settings.api_key.get_secret_value()
                )
            },
            timeout=2.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return ReadinessCheck(
            "vllm_endpoint",
            False,
            f"{settings.base_url}/models unavailable",
        )
    model_ids = {
        entry.get("id")
        for entry in payload.get("data", [])
        if isinstance(entry, dict)
    } if isinstance(payload, dict) else set()
    ready = settings.model in model_ids
    detail = (
        f"catalog lists {settings.model}; typed-tool round not run"
        if ready
        else f"configured model {settings.model} not listed"
    )
    return ReadinessCheck("vllm_endpoint", ready, detail)


def _run(command: tuple[str, ...]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or completed.stderr.strip() or "available"


if __name__ == "__main__":
    raise SystemExit(main())
