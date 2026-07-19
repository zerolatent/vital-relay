#!/usr/bin/env python3
"""Validate real seed receipts and atomically publish demo enrollment outputs."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID


START_MARKER = "# BEGIN VITAL RELAY DEMO PERSONAS (managed)"
END_MARKER = "# END VITAL RELAY DEMO PERSONAS (managed)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network-receipt", type=Path, required=True)
    parser.add_argument("--community-receipt", type=Path, required=True)
    parser.add_argument("--command-receipt", type=Path, required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--credentials-file", type=Path, required=True)
    return parser


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{label} is not a readable JSON receipt") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"{label} must contain a JSON object")
    return value


def _required_string(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\n" in value
        or "\r" in value
    ):
        raise SystemExit(f"{label} must be a non-empty single-line string")
    return value


def _required_uuid(value: Any, label: str) -> str:
    text = _required_string(value, label)
    try:
        return str(UUID(text))
    except ValueError as exc:
        raise SystemExit(f"{label} must be a UUID") from exc


def _required_token(value: Any, label: str) -> str:
    token = _required_string(value, label)
    if len(token) < 43 or len(token) > 256:
        raise SystemExit(f"{label} has an invalid opaque-token length")
    if any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in token):
        raise SystemExit(f"{label} must be an unpadded base64url token")
    return token


def _persona_receipt(
    receipt: dict[str, Any],
    *,
    expected_persona: str,
    label: str,
) -> tuple[dict[str, Any], str]:
    account = receipt.get("account")
    if not isinstance(account, dict):
        raise SystemExit(f"{label}.account must be an object")
    if account.get("persona") != expected_persona:
        raise SystemExit(f"{label} is not a {expected_persona} account receipt")
    _required_uuid(account.get("account_id"), f"{label}.account.account_id")
    token = _required_token(receipt.get("enrollment_token"), f"{label}.enrollment_token")
    return account, token


def _without_managed_block(existing: str) -> str:
    lines = existing.splitlines()
    starts = [index for index, line in enumerate(lines) if line == START_MARKER]
    ends = [index for index, line in enumerate(lines) if line == END_MARKER]
    if not starts and not ends:
        return existing.rstrip("\n")
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise SystemExit("demo persona environment block is malformed")
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
    scope_id = _required_uuid(args.scope_id, "scope ID")
    api_base_url = _required_string(args.api_base_url, "API base URL")
    network = _load_object(args.network_receipt, "response-network receipt")
    community = _load_object(args.community_receipt, "community receipt")
    command = _load_object(args.command_receipt, "command receipt")

    if _required_uuid(network.get("scope_id"), "network.scope_id") != scope_id:
        raise SystemExit("response-network receipt belongs to a different scope")
    responders = network.get("responders")
    if not isinstance(responders, list) or len(responders) < 1:
        raise SystemExit("response-network receipt contains no responders")

    validated_responders: list[dict[str, str]] = []
    responder_ids: set[str] = set()
    for index, raw_responder in enumerate(responders, start=1):
        if not isinstance(raw_responder, dict):
            raise SystemExit(f"network.responders[{index - 1}] must be an object")
        responder_id = _required_uuid(
            raw_responder.get("responder_id"),
            f"network.responders[{index - 1}].responder_id",
        )
        if responder_id in responder_ids:
            raise SystemExit("response-network receipt contains duplicate responders")
        responder_ids.add(responder_id)
        validated_responders.append(
            {
                "responder_id": responder_id,
                "display_name": _required_string(
                    raw_responder.get("display_name"),
                    f"network.responders[{index - 1}].display_name",
                ),
                "enrollment_token": _required_token(
                    raw_responder.get("access_token"),
                    f"network.responders[{index - 1}].access_token",
                ),
            }
        )

    community_account, community_token = _persona_receipt(
        community,
        expected_persona="community",
        label="community",
    )
    command_account, command_token = _persona_receipt(
        command,
        expected_persona="command",
        label="command",
    )
    community_user_id = _required_string(
        community_account.get("user_id"),
        "community.account.user_id",
    )
    if command_account.get("user_id") is not None or command_account.get("responder_id") is not None:
        raise SystemExit("command account receipt unexpectedly contains a subject")

    environment_values = {
        "VITAL_RELAY_DEVICE_TOKEN": command_token,
        "VITAL_RELAY_DEMO_COMMAND_ACCOUNT_ID": _required_uuid(
            command_account.get("account_id"),
            "command.account.account_id",
        ),
        "VITAL_RELAY_DEMO_COMMAND_ENROLLMENT_TOKEN": command_token,
        "VITAL_RELAY_DEMO_COMMUNITY_ACCOUNT_ID": _required_uuid(
            community_account.get("account_id"),
            "community.account.account_id",
        ),
        "VITAL_RELAY_DEMO_COMMUNITY_ENROLLMENT_TOKEN": community_token,
        "VITAL_RELAY_DEMO_COMMUNITY_USER_ID": community_user_id,
        "VITAL_RELAY_DEMO_RESPONDER_ACCOUNT_ID": validated_responders[0]["responder_id"],
        "VITAL_RELAY_DEMO_RESPONDER_ENROLLMENT_TOKEN": validated_responders[0]["enrollment_token"],
        "VITAL_RELAY_DEMO_RESPONDER_ID": validated_responders[0]["responder_id"],
    }
    for index, responder in enumerate(validated_responders, start=1):
        environment_values[f"VITAL_RELAY_DEMO_RESPONDER_{index}_ID"] = responder["responder_id"]
        environment_values[f"VITAL_RELAY_DEMO_RESPONDER_{index}_ENROLLMENT_TOKEN"] = responder["enrollment_token"]

    env_path = args.env_file.expanduser()
    if env_path.is_symlink():
        raise SystemExit(f"refusing to read symlink: {env_path}")
    env_path = env_path.resolve(strict=False)
    if not env_path.exists() or not env_path.is_file():
        raise SystemExit(f"database environment file does not exist: {env_path}")
    existing = env_path.read_text(encoding="utf-8")
    retained = _without_managed_block(existing)
    env_lines = [START_MARKER]
    env_lines.extend(
        f"export {key}={shlex.quote(value)}"
        for key, value in sorted(environment_values.items())
    )
    env_lines.append(END_MARKER)
    env_block = "\n".join(env_lines)
    env_content = f"{retained}\n\n{env_block}\n" if retained else f"{env_block}\n"

    generated_at = datetime.now(UTC).isoformat()
    credential_lines = [
        "Vital Relay local demo enrollment credentials",
        f"Generated at: {generated_at}",
        f"API base URL: {api_base_url}",
        f"Scope ID: {scope_id}",
        "",
        "COMMUNITY SIMULATOR",
        f"  Account ID: {environment_values['VITAL_RELAY_DEMO_COMMUNITY_ACCOUNT_ID']}",
        f"  User ID: {community_user_id}",
        f"  Enrollment code: {community_token}",
        "",
        "RESPONDER SIMULATOR (primary)",
        f"  Account/Responder ID: {validated_responders[0]['responder_id']}",
        f"  Display name: {validated_responders[0]['display_name']}",
        f"  Enrollment code: {validated_responders[0]['enrollment_token']}",
        "",
        "COMMAND PERSONA",
        f"  Account ID: {environment_values['VITAL_RELAY_DEMO_COMMAND_ACCOUNT_ID']}",
        f"  Enrollment code: {command_token}",
    ]
    if len(validated_responders) > 1:
        credential_lines.extend(("", "BACKUP RESPONDERS"))
        for responder in validated_responders[1:]:
            credential_lines.extend(
                (
                    f"  {responder['display_name']}",
                    f"    Account/Responder ID: {responder['responder_id']}",
                    f"    Enrollment code: {responder['enrollment_token']}",
                )
            )

    _write_atomic(env_path, env_content)
    _write_atomic(args.credentials_file, "\n".join(credential_lines) + "\n")
    print(
        json.dumps(
            {
                "api_base_url": api_base_url,
                "command_account_id": environment_values[
                    "VITAL_RELAY_DEMO_COMMAND_ACCOUNT_ID"
                ],
                "community_account_id": environment_values[
                    "VITAL_RELAY_DEMO_COMMUNITY_ACCOUNT_ID"
                ],
                "credentials_file": str(
                    args.credentials_file.expanduser().resolve(strict=False)
                ),
                "env_file": str(env_path),
                "primary_responder_id": validated_responders[0]["responder_id"],
                "responder_count": len(validated_responders),
                "scope_id": scope_id,
                "status": "seeded",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
