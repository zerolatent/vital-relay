"""PostgreSQL read adapter for fixed first-aid protocol presentations."""

from __future__ import annotations

from hashlib import sha256
from secrets import compare_digest
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.application.protocol_service import ProtocolAuthenticationError
from vital_relay.domain.incidents import IncidentState
from vital_relay.domain.protocols import (
    FixedFirstAidProtocol,
    ProtocolPresentationView,
)
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    IncidentRow,
    ProtocolPresentationRow,
    ResponderAssignmentRevocationRow,
    ResponderAssignmentRow,
    ResponderRow,
)
from vital_relay.protocols.registry import (
    FixedProtocolRegistry,
    ProtocolIntegrityError,
)


class PostgresProtocolRepository:
    """Load the snapshot created atomically with responder acceptance."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        registry: FixedProtocolRegistry,
    ) -> None:
        self._sessions = sessions
        self.scope_id = scope_id
        self._registry = registry

    def get_for_command(self, incident_id: UUID) -> ProtocolPresentationView | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            row = self._presentation_row(session, incident_id=incident_id)
            return (
                self._presentation_view(session, row)
                if row is not None
                else None
            )

    def get_for_responder(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> ProtocolPresentationView | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            self._authenticate_responder(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            assignment = session.scalar(
                select(ResponderAssignmentRow).where(
                    ResponderAssignmentRow.scope_id == self.scope_id,
                    ResponderAssignmentRow.incident_id == incident_id,
                    ResponderAssignmentRow.responder_id == responder_id,
                )
            )
            if assignment is None:
                return None
            incident = session.get(IncidentRow, (self.scope_id, incident_id))
            if incident is None:
                raise ProtocolIntegrityError(
                    f"accepted assignment has no incident: {assignment.assignment_id}"
                )
            revocation_id = session.scalar(
                select(ResponderAssignmentRevocationRow.revocation_id).where(
                    ResponderAssignmentRevocationRow.scope_id == self.scope_id,
                    ResponderAssignmentRevocationRow.assignment_id
                    == assignment.assignment_id,
                )
            )
            if (
                incident.current_state != IncidentState.RESPONSE_ACTIVE.value
                or revocation_id is not None
            ):
                return None
            row = self._presentation_row(
                session,
                incident_id=incident_id,
                responder_id=responder_id,
            )
            return (
                self._presentation_view(session, row)
                if row is not None
                else None
            )

    def _presentation_row(
        self,
        session: Session,
        *,
        incident_id: UUID,
        responder_id: UUID | None = None,
    ) -> ProtocolPresentationRow | None:
        statement = select(ProtocolPresentationRow).where(
            ProtocolPresentationRow.scope_id == self.scope_id,
            ProtocolPresentationRow.incident_id == incident_id,
        )
        if responder_id is not None:
            statement = statement.where(
                ProtocolPresentationRow.responder_id == responder_id
            )
        row = session.scalar(statement)
        if row is not None:
            return row

        assignment_statement = select(ResponderAssignmentRow.assignment_id).where(
            ResponderAssignmentRow.scope_id == self.scope_id,
            ResponderAssignmentRow.incident_id == incident_id,
        )
        if responder_id is not None:
            assignment_statement = assignment_statement.where(
                ResponderAssignmentRow.responder_id == responder_id
            )
        if session.scalar(assignment_statement) is not None:
            raise ProtocolIntegrityError(
                f"accepted assignment has no protocol presentation: {incident_id}"
            )
        return None

    def _presentation_view(
        self,
        session: Session,
        row: ProtocolPresentationRow,
    ) -> ProtocolPresentationView:
        try:
            stored = FixedFirstAidProtocol.model_validate(row.protocol_snapshot)
            registered = self._registry.load_exact(
                protocol_id=row.protocol_id,
                version=row.protocol_version,
                content_sha256=row.content_sha256,
            )
        except ValidationError as exc:
            raise ProtocolIntegrityError(
                f"stored protocol snapshot is invalid: {row.presentation_id}"
            ) from exc

        incident = session.get(IncidentRow, (self.scope_id, row.incident_id))
        assignment = session.get(
            ResponderAssignmentRow,
            (self.scope_id, row.assignment_id),
        )

        if (
            stored != registered
            or row.schema_version != stored.schema_version
            or row.emergency_kind != stored.emergency_kind.value
            or row.protocol_id != stored.protocol_id
            or row.protocol_version != stored.version
            or row.content_sha256 != stored.content_sha256
            or incident is None
            or incident.kind != stored.emergency_kind.value
            or assignment is None
            or assignment.incident_id != row.incident_id
            or assignment.responder_id != row.responder_id
            or assignment.accepted_at != row.presented_at
        ):
            raise ProtocolIntegrityError(
                f"stored protocol metadata does not match protected content: "
                f"{row.presentation_id}"
            )

        try:
            return ProtocolPresentationView(
                schema_version=row.schema_version,
                presentation_id=row.presentation_id,
                assignment_id=row.assignment_id,
                incident_id=row.incident_id,
                responder_id=row.responder_id,
                presented_at=row.presented_at,
                protocol=stored,
                simulated=row.simulated,
            )
        except ValidationError as exc:
            raise ProtocolIntegrityError(
                f"stored protocol presentation is invalid: {row.presentation_id}"
            ) from exc

    def _authenticate_responder(
        self,
        session: Session,
        *,
        responder_id: UUID,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> None:
        responder = session.get(ResponderRow, (self.scope_id, responder_id))
        if responder is None or responder.status != "active":
            raise ProtocolAuthenticationError
        if authenticated_responder_id is not None:
            if (
                authenticated_responder_id != responder_id
                or responder_token is not None
            ):
                raise ProtocolAuthenticationError
            return
        if responder_token is None:
            raise ProtocolAuthenticationError
        supplied_hash = sha256(responder_token.encode("utf-8")).hexdigest()
        if not compare_digest(supplied_hash, responder.access_token_hash):
            raise ProtocolAuthenticationError
