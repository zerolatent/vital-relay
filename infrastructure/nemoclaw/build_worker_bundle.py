"""Build or install the exact reviewed NemoClaw agent worker runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "backend/src"))

from vital_relay.agent.source_manifest import (  # noqa: E402
    NEMOCLAW_AGENT_SOURCE_MANIFEST,
    NEMOCLAW_DEPENDENCY_LOCK_PATH,
    ReviewedWorkerWheel,
    build_nemoclaw_worker_wheel,
    capture_reviewed_source_snapshot,
    inspect_nemoclaw_worker_wheel,
    install_nemoclaw_worker_runtime,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    build = subcommands.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--python", type=Path, default=Path(sys.executable))
    install = subcommands.add_parser("install")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--wheelhouse", type=Path, required=True)
    install.add_argument("--target", type=Path, required=True)
    install.add_argument("--python", type=Path, required=True)
    install.add_argument("--wheel-sha256", required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "build":
        wheel = build_nemoclaw_worker_wheel(
            PROJECT_ROOT,
            arguments.output,
            python_executable=arguments.python,
        )
        print(
            json.dumps(
                {
                    "dependency_lock_sha256": wheel.dependency_lock_sha256,
                    "source_digest": wheel.source_snapshot.digest,
                    "wheel": wheel.wheel_path.name,
                    "wheel_sha256": wheel.wheel_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 0

    wheel_paths = tuple((arguments.bundle / "wheel").glob("*.whl"))
    if len(wheel_paths) != 1:
        raise ValueError("bundle must contain exactly one worker wheel")
    snapshot = capture_reviewed_source_snapshot(
        PROJECT_ROOT,
        NEMOCLAW_AGENT_SOURCE_MANIFEST,
    )
    wheel_path = wheel_paths[0]
    archive_member_sha256 = inspect_nemoclaw_worker_wheel(
        wheel_path,
        snapshot,
        reviewed_wheel_sha256=arguments.wheel_sha256,
    )
    lock = PROJECT_ROOT / NEMOCLAW_DEPENDENCY_LOCK_PATH
    wheel = ReviewedWorkerWheel(
        source_snapshot=snapshot,
        wheel_path=wheel_path,
        wheel_sha256=arguments.wheel_sha256,
        archive_member_sha256=archive_member_sha256,
        dependency_lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
    )
    install_nemoclaw_worker_runtime(
        project_root=PROJECT_ROOT,
        python_executable=arguments.python,
        wheel=wheel,
        reviewed_wheel_sha256=arguments.wheel_sha256,
        wheelhouse=arguments.wheelhouse,
        target=arguments.target,
    )
    print(
        json.dumps(
            {
                "dependency_lock_sha256": wheel.dependency_lock_sha256,
                "source_digest": snapshot.digest,
                "target": str(arguments.target),
                "wheel_sha256": wheel.wheel_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
