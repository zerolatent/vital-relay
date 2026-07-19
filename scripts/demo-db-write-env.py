#!/usr/bin/env python3
"""Atomically maintain the database block in the local demo environment file."""

from __future__ import annotations

import argparse
import os
import shlex
import tempfile
from pathlib import Path


START_MARKER = "# BEGIN VITAL RELAY DEMO DATABASE (managed)"
END_MARKER = "# END VITAL RELAY DEMO DATABASE (managed)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--api-base-url", required=True)
    return parser


def _without_managed_block(existing: str) -> str:
    lines = existing.splitlines()
    starts = [index for index, line in enumerate(lines) if line == START_MARKER]
    ends = [index for index, line in enumerate(lines) if line == END_MARKER]
    if not starts and not ends:
        return existing.rstrip("\n")
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise SystemExit("demo database environment block is malformed")
    retained = lines[: starts[0]] + lines[ends[0] + 1 :]
    return "\n".join(retained).rstrip("\n")


def _write_atomic(path: Path, content: str) -> None:
    path = path.expanduser()
    if path.is_symlink():
        raise SystemExit(f"refusing to replace symlink: {path}")
    path = path.resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    args = _parser().parse_args()
    for label, value in (
        ("database URL", args.database_url),
        ("scope ID", args.scope_id),
        ("API base URL", args.api_base_url),
    ):
        if not value or value != value.strip() or "\n" in value or "\r" in value:
            raise SystemExit(f"{label} must be a non-empty single-line value")

    env_path = args.env_file.expanduser()
    if env_path.is_symlink():
        raise SystemExit(f"refusing to read symlink: {env_path}")
    env_path = env_path.resolve(strict=False)
    if env_path.exists():
        if not env_path.is_file():
            raise SystemExit(f"demo environment path is not a file: {env_path}")
        existing = env_path.read_text(encoding="utf-8")
    else:
        existing = ""
    retained = _without_managed_block(existing)
    block = "\n".join(
        (
            START_MARKER,
            f"export VITAL_RELAY_DATABASE_URL={shlex.quote(args.database_url)}",
            f"export VITAL_RELAY_DEMO_SCOPE_ID={shlex.quote(args.scope_id)}",
            f"export VITAL_RELAY_API_BASE_URL={shlex.quote(args.api_base_url)}",
            END_MARKER,
        )
    )
    content = f"{retained}\n\n{block}\n" if retained else f"{block}\n"
    _write_atomic(env_path, content)
    print(env_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
