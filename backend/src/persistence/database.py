"""PostgreSQL engine and explicit demo-scope lifecycle helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.persistence.models import DemoScopeRow

POSTGRES_CONNECT_TIMEOUT_SECONDS = 5


class DemoScopeUnavailableError(RuntimeError):
    """The configured scope is missing or no longer accepts writes."""

    def __init__(self, *, scope_id: UUID, reason: str) -> None:
        self.scope_id = scope_id
        self.reason = reason
        super().__init__(f"demo scope {scope_id} is {reason}")


class DemoScopeConflictError(RuntimeError):
    """A stable demo scope ID was reused with different lifecycle data."""


def create_postgres_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create the supported synchronous PostgreSQL engine.

    Persistence never silently falls back to an in-memory or SQLite database.
    """

    try:
        url = make_url(database_url)
    except ArgumentError as exc:
        raise ValueError(
            "health persistence requires a PostgreSQL database URL"
        ) from exc
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif url.drivername != "postgresql+psycopg":
        raise ValueError("health persistence requires a PostgreSQL database URL")
    engine = create_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        pool_timeout=POSTGRES_CONNECT_TIMEOUT_SECONDS,
        connect_args={"connect_timeout": POSTGRES_CONNECT_TIMEOUT_SECONDS},
    )

    @event.listens_for(engine, "connect")
    def set_connection_timezone(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET TIME ZONE 'UTC'")
        finally:
            cursor.close()
        # psycopg starts a transaction for SET. End that setup transaction so
        # callers can safely apply their required isolation level on checkout,
        # including when the pool opens a second connection concurrently.
        dbapi_connection.commit()  # type: ignore[attr-defined]

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def create_demo_scope(
    engine: Engine,
    *,
    scope_id: UUID,
    created_at: datetime,
    expires_at: datetime,
) -> bool:
    """Create one explicit active scope; exact lifecycle retries are harmless."""

    normalized_created_at = _utc(created_at, field_name="created_at")
    normalized_expires_at = _utc(expires_at, field_name="expires_at")
    if normalized_expires_at <= normalized_created_at:
        raise ValueError("expires_at must be later than created_at")

    sessions = create_session_factory(engine)
    with sessions.begin() as session:
        session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": _scope_advisory_key(scope_id)},
        )
        existing = session.get(DemoScopeRow, scope_id)
        if existing is not None:
            if (
                existing.status == "active"
                and existing.created_at == normalized_created_at
                and existing.expires_at == normalized_expires_at
                and existing.closed_at is None
            ):
                return False
            raise DemoScopeConflictError(f"demo scope ID conflict: {scope_id}")
        session.add(
            DemoScopeRow(
                scope_id=scope_id,
                status="active",
                created_at=normalized_created_at,
                expires_at=normalized_expires_at,
                closed_at=None,
            )
        )
    return True


def _scope_advisory_key(scope_id: UUID) -> int:
    digest = sha256(f"demo_scope:{scope_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def require_active_scope(
    session: Session,
    scope_id: UUID,
    *,
    lock: bool = False,
) -> DemoScopeRow:
    statement = select(DemoScopeRow).where(DemoScopeRow.scope_id == scope_id)
    if lock:
        # Compatible share locks let normal writes proceed concurrently but
        # serialize against retention's exclusive scope-closing lock.
        statement = statement.with_for_update(read=True)
    row = session.scalar(statement)
    if row is None:
        raise DemoScopeUnavailableError(scope_id=scope_id, reason="missing")
    if row.status != "active":
        raise DemoScopeUnavailableError(scope_id=scope_id, reason="closed")
    if row.expires_at <= datetime.now(UTC):
        raise DemoScopeUnavailableError(scope_id=scope_id, reason="expired")
    return row


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
