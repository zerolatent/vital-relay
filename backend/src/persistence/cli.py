"""Fail-closed database migration and demo-scope lifecycle CLI."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from vital_relay.adapters.postgres_retention import (
    PostgresHealthRetentionRepository,
)
from vital_relay.adapters.postgres_dispatch import seed_demo_response_network
from vital_relay.adapters.postgres_persona_sessions import (
    provision_persona_account,
)
from vital_relay.application.health_retention import HealthRetentionService
from vital_relay.persistence.database import (
    create_demo_scope,
    create_postgres_engine,
    create_session_factory,
)
from vital_relay.persistence.migrations import upgrade_database
from vital_relay.domain.persona_sessions import Persona


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vital-relay-db")
    parser.add_argument(
        "--database-url",
        help="Explicit PostgreSQL URL; defaults to VITAL_RELAY_DATABASE_URL.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("upgrade", help="Apply all Alembic migrations.")

    create = commands.add_parser("create-scope", help="Create one demo scope.")
    create.add_argument("--scope", type=UUID, required=True)
    create.add_argument("--retention-hours", type=int, default=24)

    seed = commands.add_parser(
        "seed-response-network",
        help=(
            "Persist the Chicago Loop responder/AED network and emit newly "
            "rotated responder tokens once."
        ),
    )
    seed.add_argument("--scope", type=UUID, required=True)
    seed.add_argument("--confirm", type=UUID, required=True)

    persona_account = commands.add_parser(
        "create-persona-account",
        help="Create or rotate one passwordless persona enrollment account.",
    )
    persona_account.add_argument("--scope", type=UUID, required=True)
    persona_account.add_argument("--confirm", type=UUID, required=True)
    persona_account.add_argument(
        "--persona",
        choices=[item.value for item in Persona],
        required=True,
    )
    persona_account.add_argument("--display-name", required=True)
    persona_account.add_argument("--user-id")
    persona_account.add_argument("--responder-id", type=UUID)

    for name in ("preview-reset", "reset-scope", "purge-expired"):
        operation = commands.add_parser(name)
        operation.add_argument("--scope", type=UUID, required=True)
        operation.add_argument("--confirm", type=UUID, required=True)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    database_url = args.database_url or os.environ.get(
        "VITAL_RELAY_DATABASE_URL"
    )
    if not database_url:
        raise SystemExit("VITAL_RELAY_DATABASE_URL or --database-url is required")

    if args.command == "upgrade":
        upgrade_database(database_url)
        _print_json({"status": "upgraded", "revision": "head"})
        return

    engine = create_postgres_engine(database_url)
    try:
        if args.command == "create-scope":
            if args.retention_hours <= 0:
                raise SystemExit("--retention-hours must be positive")
            created_at = datetime.now(UTC)
            expires_at = created_at + timedelta(hours=args.retention_hours)
            create_demo_scope(
                engine,
                scope_id=args.scope,
                created_at=created_at,
                expires_at=expires_at,
            )
            _print_json(
                {
                    "status": "created",
                    "scope_id": args.scope,
                    "created_at": created_at,
                    "expires_at": expires_at,
                    "retention_hours": args.retention_hours,
                }
            )
            return

        if args.command == "seed-response-network":
            if args.confirm != args.scope:
                raise SystemExit("--confirm must exactly match --scope")
            result = seed_demo_response_network(
                create_session_factory(engine),
                scope_id=args.scope,
                seeded_at=datetime.now(UTC),
            )
            _print_json(asdict(result))
            return

        if args.command == "create-persona-account":
            if args.confirm != args.scope:
                raise SystemExit("--confirm must exactly match --scope")
            persona = Persona(args.persona)
            if persona is Persona.COMMUNITY and not args.user_id:
                raise SystemExit("community persona requires --user-id")
            if persona is Persona.RESPONDER and args.responder_id is None:
                raise SystemExit("responder persona requires --responder-id")
            if persona is Persona.COMMAND and (
                args.user_id is not None or args.responder_id is not None
            ):
                raise SystemExit(
                    "command persona cannot include --user-id or --responder-id"
                )
            if persona is Persona.COMMUNITY and args.responder_id is not None:
                raise SystemExit("community persona cannot include --responder-id")
            if persona is Persona.RESPONDER and args.user_id is not None:
                raise SystemExit("responder persona cannot include --user-id")
            result = provision_persona_account(
                create_session_factory(engine),
                scope_id=args.scope,
                persona=persona,
                display_name=args.display_name,
                user_id=args.user_id,
                responder_id=args.responder_id,
                provisioned_at=datetime.now(UTC),
            )
            _print_json(
                {
                    "account": result.account.model_dump(mode="json"),
                    "enrollment_token": result.enrollment_token,
                }
            )
            return

        repository = PostgresHealthRetentionRepository(
            create_session_factory(engine),
            args.scope,
        )
        service = HealthRetentionService(repository)
        now = datetime.now(UTC)
        if args.command == "preview-reset":
            result: Any = service.preview(now, args.confirm)
        elif args.command == "reset-scope":
            result = service.reset(now, args.confirm)
        else:
            result = service.purge_if_expired(now, args.confirm)
        _print_json(asdict(result))
    finally:
        engine.dispose()


def _print_json(value: object) -> None:
    print(json.dumps(value, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
