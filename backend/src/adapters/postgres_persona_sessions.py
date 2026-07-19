"""PostgreSQL-backed persona accounts, sessions, and active discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, and_, desc, exists, select, text
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.application.persona_session_service import (
    PersonaAuthenticationError,
)
from vital_relay.domain.dispatch import InvitationStatus
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentKind, IncidentState
from vital_relay.domain.persona_sessions import (
    ActiveIncidentList,
    ActiveIncidentSummary,
    Persona,
    PersonaAccountView,
    PersonaPrincipal,
    PersonaSessionCreateRequest,
    PersonaSessionReceipt,
    PersonaSessionRevocationReceipt,
    PersonaSessionRevocationStatus,
    PersonaSessionRotateRequest,
    PersonaSessionRotationReceipt,
    PersonaSessionView,
)
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    IncidentRow,
    PersonaAccountRow,
    PersonaSessionRow,
    ResponderAssignmentRevocationRow,
    ResponderAssignmentRow,
    ResponderInvitationRow,
    ResponderRow,
)


_ACTIVE_INCIDENT_STATES = (
    IncidentState.VERIFYING.value,
    IncidentState.ESCALATING.value,
    IncidentState.RESPONSE_ACTIVE.value,
)
_TOKEN_BYTES = 32
_DUMMY_HASH = "0" * 64


@dataclass(frozen=True)
class ProvisionedPersonaAccount:
    """Operator receipt that reveals an enrollment token exactly once."""

    account: PersonaAccountView
    enrollment_token: str = field(repr=False)


class PostgresPersonaSessionRepository:
    """Own transactional session issuance, rotation, and discovery."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._engine = engine
        self._sessions = sessions
        self.scope_id = scope_id

    def create(
        self,
        request: PersonaSessionCreateRequest,
        *,
        enrollment_token: str,
        issued_at: datetime,
        access_ttl: timedelta,
        refresh_ttl: timedelta,
    ) -> PersonaSessionReceipt:
        now = _utc(issued_at)
        supplied_hash = _token_hash(enrollment_token)
        with self._sessions.begin() as session:
            scope = require_active_scope(session, self.scope_id, lock=True)
            account = session.scalar(
                select(PersonaAccountRow)
                .where(
                    PersonaAccountRow.scope_id == self.scope_id,
                    PersonaAccountRow.enrollment_token_hash == supplied_hash,
                )
                .with_for_update()
            )
            expected_hash = (
                account.enrollment_token_hash if account is not None else _DUMMY_HASH
            )
            if (
                account is None
                or not compare_digest(supplied_hash, expected_hash)
                or not self._account_is_active(session, account)
            ):
                raise PersonaAuthenticationError(
                    code="invalid_enrollment_token"
                )

            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="persona-session-create",
                identifier=f"{account.account_id}:{request.installation_id}",
            )
            prior_sessions = session.scalars(
                select(PersonaSessionRow)
                .where(
                    PersonaSessionRow.scope_id == self.scope_id,
                    PersonaSessionRow.account_id == account.account_id,
                    PersonaSessionRow.installation_id == request.installation_id,
                    PersonaSessionRow.status == "active",
                )
                .with_for_update()
            ).all()
            for prior in prior_sessions:
                prior.status = "revoked"
                prior.revoked_at = max(now, prior.rotated_at)

            access_expires_at, refresh_expires_at = _bounded_lifetimes(
                issued_at=now,
                scope_expires_at=scope.expires_at,
                access_ttl=access_ttl,
                refresh_ttl=refresh_ttl,
            )
            access_token = _new_token()
            refresh_token = _new_token()
            row = PersonaSessionRow(
                scope_id=self.scope_id,
                session_id=uuid4(),
                account_id=account.account_id,
                installation_id=request.installation_id,
                access_token_hash=_token_hash(access_token),
                refresh_token_hash=_token_hash(refresh_token),
                status="active",
                issued_at=now,
                rotated_at=now,
                access_expires_at=access_expires_at,
                refresh_expires_at=refresh_expires_at,
                revoked_at=None,
            )
            session.add(row)
            session.flush()
            return PersonaSessionReceipt(
                schema_version=SCHEMA_VERSION,
                session_id=row.session_id,
                account=_account_view(account),
                installation_id=row.installation_id,
                access_token=access_token,
                refresh_token=refresh_token,
                issued_at=row.issued_at,
                rotated_at=row.rotated_at,
                access_expires_at=row.access_expires_at,
                refresh_expires_at=row.refresh_expires_at,
            )

    def current(
        self,
        *,
        access_token: str,
        as_of: datetime,
    ) -> PersonaSessionView:
        with self._sessions() as session:
            row, account = self._active_access_session(
                session,
                access_token=access_token,
                as_of=_utc(as_of),
            )
            return PersonaSessionView(
                schema_version=SCHEMA_VERSION,
                session_id=row.session_id,
                account=_account_view(account),
                installation_id=row.installation_id,
                issued_at=row.issued_at,
                rotated_at=row.rotated_at,
                access_expires_at=row.access_expires_at,
                refresh_expires_at=row.refresh_expires_at,
            )

    def authenticate_access(
        self,
        *,
        access_token: str,
        as_of: datetime,
    ) -> PersonaPrincipal:
        with self._sessions() as session:
            row, account = self._active_access_session(
                session,
                access_token=access_token,
                as_of=_utc(as_of),
            )
            return PersonaPrincipal(
                scope_id=self.scope_id,
                session_id=row.session_id,
                account_id=account.account_id,
                installation_id=row.installation_id,
                persona=Persona(account.persona),
                user_id=account.user_id,
                responder_id=account.responder_id,
            )

    def rotate(
        self,
        session_id: UUID,
        request: PersonaSessionRotateRequest,
        *,
        refresh_token: str,
        rotated_at: datetime,
        access_ttl: timedelta,
    ) -> PersonaSessionRotationReceipt:
        now = _utc(rotated_at)
        supplied_hash = _token_hash(refresh_token)
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            row = session.scalar(
                select(PersonaSessionRow)
                .where(
                    PersonaSessionRow.scope_id == self.scope_id,
                    PersonaSessionRow.session_id == session_id,
                )
                .with_for_update()
            )
            expected_hash = row.refresh_token_hash if row is not None else _DUMMY_HASH
            if (
                row is None
                or not compare_digest(supplied_hash, expected_hash)
                or row.status != "active"
                or row.installation_id != request.installation_id
                or row.refresh_expires_at <= now
            ):
                raise PersonaAuthenticationError(code="invalid_refresh_token")
            account = session.get(
                PersonaAccountRow,
                (self.scope_id, row.account_id),
            )
            if account is None or not self._account_is_active(session, account):
                raise PersonaAuthenticationError(code="invalid_refresh_token")

            access_expires_at = min(
                now + access_ttl,
                row.refresh_expires_at - timedelta(microseconds=1),
            )
            if access_expires_at <= now:
                raise PersonaAuthenticationError(code="invalid_refresh_token")
            access_token = _new_token()
            row.access_token_hash = _token_hash(access_token)
            row.rotated_at = now
            row.access_expires_at = access_expires_at
            session.flush()
            return PersonaSessionRotationReceipt(
                schema_version=SCHEMA_VERSION,
                session_id=row.session_id,
                access_token=access_token,
                rotated_at=row.rotated_at,
                access_expires_at=row.access_expires_at,
            )

    def revoke(
        self,
        session_id: UUID,
        *,
        refresh_token: str,
        revoked_at: datetime,
    ) -> PersonaSessionRevocationReceipt:
        now = _utc(revoked_at)
        supplied_hash = _token_hash(refresh_token)
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            row = session.scalar(
                select(PersonaSessionRow)
                .where(
                    PersonaSessionRow.scope_id == self.scope_id,
                    PersonaSessionRow.session_id == session_id,
                )
                .with_for_update()
            )
            expected_hash = row.refresh_token_hash if row is not None else _DUMMY_HASH
            if (
                row is None
                or not compare_digest(supplied_hash, expected_hash)
                or row.refresh_expires_at <= now
            ):
                raise PersonaAuthenticationError(code="invalid_refresh_token")
            if row.status == "revoked":
                if row.revoked_at is None:
                    raise RuntimeError("revoked persona session has no revoked_at")
                return PersonaSessionRevocationReceipt(
                    schema_version=SCHEMA_VERSION,
                    session_id=row.session_id,
                    status=PersonaSessionRevocationStatus.ALREADY_REVOKED,
                    revoked_at=row.revoked_at,
                )
            row.status = "revoked"
            row.revoked_at = max(now, row.rotated_at)
            session.flush()
            return PersonaSessionRevocationReceipt(
                schema_version=SCHEMA_VERSION,
                session_id=row.session_id,
                status=PersonaSessionRevocationStatus.REVOKED,
                revoked_at=row.revoked_at,
            )

    def active_incidents(
        self,
        principal: PersonaPrincipal,
        *,
        as_of: datetime,
    ) -> ActiveIncidentList:
        received_at = _utc(as_of)
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            if principal.scope_id != self.scope_id:
                raise PersonaAuthenticationError(code="invalid_session_token")

            if principal.persona is Persona.RESPONDER:
                rows = session.execute(
                    select(IncidentRow, ResponderInvitationRow)
                    .join(
                        ResponderInvitationRow,
                        and_(
                            ResponderInvitationRow.scope_id
                            == IncidentRow.scope_id,
                            ResponderInvitationRow.incident_id
                            == IncidentRow.incident_id,
                        ),
                    )
                    .where(
                        IncidentRow.scope_id == self.scope_id,
                        IncidentRow.current_state.in_(_ACTIVE_INCIDENT_STATES),
                        ResponderInvitationRow.responder_id
                        == principal.responder_id,
                        ResponderInvitationRow.status.in_(
                            (
                                InvitationStatus.PENDING.value,
                                InvitationStatus.ACCEPTED.value,
                            )
                        ),
                        ~exists(
                            select(ResponderAssignmentRevocationRow.revocation_id)
                            .join(
                                ResponderAssignmentRow,
                                and_(
                                    ResponderAssignmentRow.scope_id
                                    == ResponderAssignmentRevocationRow.scope_id,
                                    ResponderAssignmentRow.assignment_id
                                    == ResponderAssignmentRevocationRow.assignment_id,
                                ),
                            )
                            .where(
                                ResponderAssignmentRow.scope_id == self.scope_id,
                                ResponderAssignmentRow.incident_id
                                == IncidentRow.incident_id,
                                ResponderAssignmentRow.responder_id
                                == principal.responder_id,
                            )
                        ),
                    )
                    .order_by(
                        desc(IncidentRow.updated_at),
                        desc(IncidentRow.incident_id),
                    )
                    .limit(100)
                ).all()
                incidents = tuple(
                    _active_summary(incident, invitation=invitation)
                    for incident, invitation in rows
                )
            else:
                statement = select(IncidentRow).where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.current_state.in_(_ACTIVE_INCIDENT_STATES),
                )
                if principal.persona is Persona.COMMUNITY:
                    statement = statement.where(
                        IncidentRow.user_id == principal.user_id
                    )
                incident_rows = session.scalars(
                    statement.order_by(
                        desc(IncidentRow.updated_at),
                        desc(IncidentRow.incident_id),
                    ).limit(100)
                ).all()
                incidents = tuple(
                    _active_summary(incident, invitation=None)
                    for incident in incident_rows
                )
            return ActiveIncidentList(
                schema_version=SCHEMA_VERSION,
                persona=principal.persona,
                incidents=incidents,
                server_received_at=received_at,
            )

    def _active_access_session(
        self,
        session: Session,
        *,
        access_token: str,
        as_of: datetime,
    ) -> tuple[PersonaSessionRow, PersonaAccountRow]:
        require_active_scope(session, self.scope_id)
        supplied_hash = _token_hash(access_token)
        row = session.scalar(
            select(PersonaSessionRow).where(
                PersonaSessionRow.scope_id == self.scope_id,
                PersonaSessionRow.access_token_hash == supplied_hash,
            )
        )
        expected_hash = row.access_token_hash if row is not None else _DUMMY_HASH
        if (
            row is None
            or not compare_digest(supplied_hash, expected_hash)
            or row.status != "active"
            or row.access_expires_at <= as_of
            or row.refresh_expires_at <= as_of
        ):
            raise PersonaAuthenticationError(code="invalid_session_token")
        account = session.get(PersonaAccountRow, (self.scope_id, row.account_id))
        if account is None or not self._account_is_active(session, account):
            raise PersonaAuthenticationError(code="invalid_session_token")
        return row, account

    def _account_is_active(
        self,
        session: Session,
        account: PersonaAccountRow,
    ) -> bool:
        if account.status != "active":
            return False
        if account.persona != Persona.RESPONDER.value:
            return True
        responder = session.get(
            ResponderRow,
            (self.scope_id, account.responder_id),
        )
        return responder is not None and responder.status == "active"


def provision_persona_account(
    sessions: sessionmaker[Session],
    *,
    scope_id: UUID,
    persona: Persona,
    display_name: str,
    provisioned_at: datetime,
    user_id: str | None = None,
    responder_id: UUID | None = None,
    enrollment_token: str | None = None,
) -> ProvisionedPersonaAccount:
    """Create or rotate one stable persona account and reveal its token once."""

    now = _utc(provisioned_at)
    token = enrollment_token or _new_token()
    token_hash = _token_hash(token)
    account_id = _account_id(
        scope_id,
        persona=persona,
        user_id=user_id,
        responder_id=responder_id,
    )
    # Validate the subject contract before opening a database transaction.
    view = PersonaAccountView(
        schema_version=SCHEMA_VERSION,
        account_id=account_id,
        display_name=display_name,
        persona=persona,
        user_id=user_id,
        responder_id=responder_id,
    )
    with sessions.begin() as session:
        require_active_scope(session, scope_id, lock=True)
        if persona is Persona.RESPONDER:
            responder = session.get(ResponderRow, (scope_id, responder_id))
            if responder is None or responder.status != "active":
                raise ValueError("responder persona requires an active responder")
        row = session.get(PersonaAccountRow, (scope_id, account_id))
        if row is None:
            row = PersonaAccountRow(
                scope_id=scope_id,
                account_id=account_id,
                display_name=view.display_name,
                persona=persona.value,
                user_id=view.user_id,
                responder_id=view.responder_id,
                enrollment_token_hash=token_hash,
                status="active",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.display_name = view.display_name
            row.persona = persona.value
            row.user_id = view.user_id
            row.responder_id = view.responder_id
            row.enrollment_token_hash = token_hash
            row.status = "active"
            row.updated_at = max(now, row.updated_at)
        session.flush()
    return ProvisionedPersonaAccount(account=view, enrollment_token=token)


def _active_summary(
    incident: IncidentRow,
    *,
    invitation: ResponderInvitationRow | None,
) -> ActiveIncidentSummary:
    return ActiveIncidentSummary(
        schema_version=SCHEMA_VERSION,
        incident_id=incident.incident_id,
        kind=IncidentKind(incident.kind),
        state=IncidentState(incident.current_state),
        state_version=incident.state_version,
        updated_at=incident.updated_at,
        invitation_id=(invitation.invitation_id if invitation is not None else None),
        invitation_status=(
            InvitationStatus(invitation.status) if invitation is not None else None
        ),
    )


def _account_view(row: PersonaAccountRow) -> PersonaAccountView:
    return PersonaAccountView(
        schema_version=SCHEMA_VERSION,
        account_id=row.account_id,
        display_name=row.display_name,
        persona=Persona(row.persona),
        user_id=row.user_id,
        responder_id=row.responder_id,
    )


def _account_id(
    scope_id: UUID,
    *,
    persona: Persona,
    user_id: str | None,
    responder_id: UUID | None,
) -> UUID:
    if persona is Persona.RESPONDER:
        if responder_id is None:
            raise ValueError("responder_id is required")
        return responder_id
    if persona is Persona.COMMUNITY:
        if user_id is None:
            raise ValueError("user_id is required")
        return uuid5(scope_id, f"persona-account:community:{user_id}")
    return uuid5(scope_id, "persona-account:command")


def _bounded_lifetimes(
    *,
    issued_at: datetime,
    scope_expires_at: datetime,
    access_ttl: timedelta,
    refresh_ttl: timedelta,
) -> tuple[datetime, datetime]:
    refresh_expires_at = min(issued_at + refresh_ttl, scope_expires_at)
    access_expires_at = min(
        issued_at + access_ttl,
        refresh_expires_at - timedelta(microseconds=1),
    )
    if access_expires_at <= issued_at:
        raise PersonaAuthenticationError(code="invalid_enrollment_token")
    return access_expires_at, refresh_expires_at


def _new_token() -> str:
    return token_urlsafe(_TOKEN_BYTES)


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _transaction_lock(
    session: Session,
    *,
    scope_id: UUID,
    namespace: str,
    identifier: str,
) -> None:
    digest = sha256(f"{scope_id}:{namespace}:{identifier}".encode()).digest()
    key = int.from_bytes(digest[:8], byteorder="big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("persona-session timestamps must be timezone-aware")
    return value.astimezone(UTC)
