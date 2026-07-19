"""Transactional PostGIS responder discovery and accepted dispatch persistence.

This adapter is the product path for dispatch. It ranks persisted, currently
available responders from their latest fresh geography point, creates one
durable invitation at a time, and releases exact wearer coordinates only after
the invited responder authenticates and accepts.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from secrets import compare_digest, token_urlsafe
from typing import Any
from uuid import UUID, uuid4, uuid5

from geoalchemy2.elements import WKTElement
from sqlalchemy import Engine, delete, or_, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.adapters.fingerprints import model_fingerprint
from vital_relay.adapters.postgres_health import PersistenceIntegrityError
from vital_relay.application.dispatch_service import (
    DispatchConflictError,
    DispatchNotFoundError,
    ResponderAuthenticationError,
)
from vital_relay.application.routing import RoutingProvider
from vital_relay.domain.dispatch import (
    AEDSiteView,
    AcceptedDispatchView,
    Coordinate,
    DispatchCoordinationView,
    InvitationStatus,
    ResponderCandidateView,
    ResponderDecision,
    ResponderDecisionRequest,
    ResponderDecisionResult,
    ResponderDecisionResultStatus,
    ResponderIncidentView,
    ResponderInvitationView,
    ResponderRole,
    ResponderSkill,
    RoutePlan,
    distance_band_for_meters,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import (
    GeoLocation,
    IncidentKind,
    IncidentState,
    IncidentTransition,
    IncidentTrigger,
    TimelineEventType,
    next_incident_state,
)
from vital_relay.protocols.registry import FixedProtocolRegistry
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    AEDSiteRow,
    IncidentRow,
    IncidentStateTransitionRow,
    IncidentTimelineEntryRow,
    PersonaAccountRow,
    ProtocolPresentationRow,
    ResponderAssignmentRevocationRow,
    ResponderAssignmentRow,
    ResponderAvailabilityRow,
    ResponderInvitationResponseRow,
    ResponderInvitationRow,
    ResponderLocationRow,
    ResponderRow,
    ResponderSkillRow,
)


_ID_NAMESPACE = UUID("fabd65cd-f0b6-48c0-8c70-3a854f1c9540")
_ELIGIBILITY_QUERY = text(
    """
    WITH latest_locations AS (
        SELECT DISTINCT ON (responder_id)
            location_id,
            responder_id,
            captured_at,
            location
        FROM responder_locations
        WHERE scope_id = :scope_id
        ORDER BY responder_id, captured_at DESC, location_id DESC
    ), incident_point AS (
        SELECT ST_SetSRID(
            ST_MakePoint(:incident_longitude, :incident_latitude),
            4326
        )::geography AS location
    )
    SELECT
        responder.responder_id,
        responder.display_name,
        responder.role,
        latest.location_id,
        latest.captured_at,
        ST_Y(latest.location::geometry) AS latitude,
        ST_X(latest.location::geometry) AS longitude,
        ST_Distance(latest.location, incident_point.location) AS distance_m
    FROM responders AS responder
    JOIN responder_availability AS availability
      ON availability.scope_id = responder.scope_id
     AND availability.responder_id = responder.responder_id
    JOIN latest_locations AS latest
      ON latest.responder_id = responder.responder_id
    CROSS JOIN incident_point
    WHERE responder.scope_id = :scope_id
      AND responder.status = 'active'
      AND availability.available = true
      AND latest.captured_at >= :fresh_after
      AND latest.captured_at <= :as_of
      AND EXISTS (
          SELECT 1
          FROM responder_skills AS skill
          WHERE skill.scope_id = responder.scope_id
            AND skill.responder_id = responder.responder_id
            AND skill.skill = 'first_aid'
            AND (
                skill.certified_until IS NULL
                OR skill.certified_until >= :as_of
            )
      )
      AND ST_DWithin(
          latest.location,
          incident_point.location,
          :radius_m
      )
    ORDER BY distance_m, responder.responder_id
    """
)
_NEAREST_AED_QUERY = text(
    """
    WITH incident_point AS (
        SELECT ST_SetSRID(
            ST_MakePoint(:incident_longitude, :incident_latitude),
            4326
        )::geography AS location
    ), nearest_candidates AS MATERIALIZED (
        SELECT aed.*, incident_point.location AS incident_location
        FROM aed_sites AS aed
        CROSS JOIN incident_point
        WHERE aed.scope_id = :scope_id
          AND aed.active = true
        ORDER BY aed.location <-> incident_point.location, aed.aed_id
        LIMIT 64
    )
    SELECT
        aed.aed_id,
        aed.name,
        aed.location_description,
        aed.access_instructions,
        aed.publicly_accessible,
        aed.updated_at,
        ST_Y(aed.location::geometry) AS latitude,
        ST_X(aed.location::geometry) AS longitude,
        ST_Distance(aed.location, aed.incident_location) AS distance_m
    FROM nearest_candidates AS aed
    ORDER BY distance_m, aed.aed_id
    LIMIT 1
    """
)
_ROUTE_DISTANCE_QUERY = text(
    """
    SELECT
        ST_Distance(responder_location.location, aed.location)
            AS responder_to_aed_distance_m,
        ST_Distance(
            aed.location,
            ST_SetSRID(
                ST_MakePoint(:incident_longitude, :incident_latitude),
                4326
            )::geography
        ) AS aed_to_wearer_distance_m
    FROM responder_locations AS responder_location
    JOIN aed_sites AS aed
      ON aed.scope_id = responder_location.scope_id
     AND aed.aed_id = :aed_id
    WHERE responder_location.scope_id = :scope_id
      AND responder_location.location_id = :location_id
    """
)


@dataclass(frozen=True)
class SeededResponderCredential:
    """A generated responder credential shown once by the explicit seed command."""

    responder_id: UUID
    display_name: str
    access_token: str


@dataclass(frozen=True)
class SeededResponseNetwork:
    """Summary returned after persisting the hackathon venue response network."""

    scope_id: UUID
    venue_name: str
    responders: tuple[SeededResponderCredential, ...]
    aed_site_ids: tuple[UUID, ...]
    seeded_at: datetime


@dataclass(frozen=True)
class _EligibleResponder:
    responder_id: UUID
    display_name: str
    role: ResponderRole
    skills: tuple[ResponderSkill, ...]
    captured_at: datetime
    latitude: float
    longitude: float
    distance_m: float
    location_id: UUID

    def public_view(self, rank: int) -> ResponderCandidateView:
        return ResponderCandidateView(
            schema_version=SCHEMA_VERSION,
            responder_id=self.responder_id,
            display_name=self.display_name,
            role=self.role,
            skills=self.skills,
            rank=rank,
            distance_band=distance_band_for_meters(self.distance_m),
            available=True,
            location_updated_at=self.captured_at,
        )


@dataclass(frozen=True)
class _AEDMatch:
    view: AEDSiteView
    distance_m: float


class PostgresDispatchRepository:
    """Scope-bound dispatch repository with owned atomic transaction boundaries."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        scope_id: UUID,
        routing_provider: RoutingProvider,
        protocol_registry: FixedProtocolRegistry,
        *,
        notification_enqueuer: Callable[
            [Session, ResponderInvitationRow, datetime],
            None,
        ]
        | None = None,
    ) -> None:
        self._engine = engine
        self._sessions = sessions
        self.scope_id = scope_id
        self._routing_provider = routing_provider
        self._protocol_registry = protocol_registry
        self._notification_enqueuer = notification_enqueuer

    def coordinate(
        self,
        incident_id: UUID,
        *,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
        expected_state_version: int | None = None,
    ) -> DispatchCoordinationView:
        occurred_at = _utc(as_of)
        fresh_cutoff = _utc(fresh_after)
        with self._transaction() as session:
            require_active_scope(session, self.scope_id, lock=True)
            incident = self._locked_dispatchable_incident(session, incident_id)
            if (
                expected_state_version is not None
                and incident.state_version != expected_state_version
            ):
                raise DispatchConflictError(
                    code="incident_state_version_mismatch",
                    identifier=str(incident_id),
                    current_state=IncidentState(incident.current_state),
                )
            eligible = self._eligible_responders(
                session,
                incident=incident,
                as_of=occurred_at,
                radius_m=radius_m,
                fresh_after=fresh_cutoff,
            )
            if incident.current_state == IncidentState.ESCALATING.value:
                if self._search_started_at(session, incident_id) is None:
                    self._append_timeline(
                        session,
                        incident=incident,
                        event_type=TimelineEventType.RESPONDER_SEARCH_STARTED,
                        occurred_at=occurred_at,
                        state=IncidentState.ESCALATING,
                        transition_id=None,
                        summary=(
                            "PostGIS responder and AED discovery started within "
                            f"{radius_m} metres."
                        ),
                    )
                self._invite_next_eligible(
                    session,
                    incident=incident,
                    eligible=eligible,
                    occurred_at=occurred_at,
                )
                incident.updated_at = max(incident.updated_at, occurred_at)
                session.flush()

            return self._coordination_view(
                session,
                incident=incident,
                as_of=occurred_at,
                radius_m=radius_m,
                fresh_after=fresh_cutoff,
                eligible=eligible,
            )

    def get_coordination(
        self,
        incident_id: UUID,
        *,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> DispatchCoordinationView:
        occurred_at = _utc(as_of)
        fresh_cutoff = _utc(fresh_after)
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            incident = self._dispatchable_incident(session, incident_id)
            eligible = self._eligible_responders(
                session,
                incident=incident,
                as_of=occurred_at,
                radius_m=radius_m,
                fresh_after=fresh_cutoff,
            )
            return self._coordination_view(
                session,
                incident=incident,
                as_of=occurred_at,
                radius_m=radius_m,
                fresh_after=fresh_cutoff,
                eligible=eligible,
            )

    def get_responder_incident(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> ResponderIncidentView | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            self._authenticate_responder(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            invitation = session.scalar(
                select(ResponderInvitationRow).where(
                    ResponderInvitationRow.scope_id == self.scope_id,
                    ResponderInvitationRow.incident_id == incident_id,
                    ResponderInvitationRow.responder_id == responder_id,
                )
            )
            if invitation is None:
                return None

            incident = session.get(IncidentRow, (self.scope_id, incident_id))
            if incident is None:
                raise PersistenceIntegrityError(
                    f"responder invitation has no incident: {invitation.invitation_id}"
                )
            response_id = session.scalar(
                select(ResponderInvitationResponseRow.response_id).where(
                    ResponderInvitationResponseRow.scope_id == self.scope_id,
                    ResponderInvitationResponseRow.invitation_id
                    == invitation.invitation_id,
                )
            )
            invitation_view = _invitation_view(
                invitation,
                response_id=response_id,
            )
            if invitation_view.responder.responder_id != responder_id:
                raise PersistenceIntegrityError(
                    "responder invitation candidate does not match its owner: "
                    f"{invitation.invitation_id}"
                )
            return ResponderIncidentView(
                schema_version=SCHEMA_VERSION,
                incident_id=incident.incident_id,
                kind=IncidentKind(incident.kind),
                state=IncidentState(incident.current_state),
                state_version=incident.state_version,
                updated_at=incident.updated_at,
                invitation=invitation_view,
            )

    def respond(
        self,
        incident_id: UUID,
        responder_id: UUID,
        decision: ResponderDecisionRequest,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
        server_received_at: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> ResponderDecisionResult:
        received_at = _utc(server_received_at)
        fresh_cutoff = _utc(fresh_after)
        fingerprint = model_fingerprint(decision)
        with self._transaction() as session:
            require_active_scope(session, self.scope_id, lock=True)
            self._authenticate_responder(
                session,
                responder_id=responder_id,
                responder_token=responder_token,
                authenticated_responder_id=authenticated_responder_id,
            )
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="responder-decision",
                identifier=str(decision.decision_id),
            )

            existing = session.get(
                ResponderInvitationResponseRow,
                (self.scope_id, decision.decision_id),
            )
            if existing is not None:
                if (
                    existing.request_fingerprint != fingerprint
                    or existing.incident_id != incident_id
                    or existing.responder_id != responder_id
                    or existing.invitation_id != decision.invitation_id
                ):
                    raise DispatchConflictError(
                        code="decision_id_reused",
                        identifier=str(decision.decision_id),
                    )
                return _stored_decision_result(existing)

            incident = self._locked_dispatchable_incident(session, incident_id)
            if incident.current_state != IncidentState.ESCALATING.value:
                raise DispatchConflictError(
                    code="incident_transition_not_allowed",
                    identifier=str(incident_id),
                    current_state=IncidentState(incident.current_state),
                )
            invitation = session.scalar(
                select(ResponderInvitationRow)
                .where(
                    ResponderInvitationRow.scope_id == self.scope_id,
                    ResponderInvitationRow.invitation_id
                    == decision.invitation_id,
                    ResponderInvitationRow.incident_id == incident_id,
                    ResponderInvitationRow.responder_id == responder_id,
                )
                .with_for_update()
            )
            if invitation is None:
                raise DispatchNotFoundError(
                    code="responder_invitation_not_found",
                    identifier=str(decision.invitation_id),
                )
            if invitation.status != InvitationStatus.PENDING.value:
                raise DispatchConflictError(
                    code="invitation_already_settled",
                    identifier=str(invitation.invitation_id),
                )

            if decision.decision is ResponderDecision.DECLINE:
                result = self._decline(
                    session,
                    incident=incident,
                    invitation=invitation,
                    decision=decision,
                    received_at=received_at,
                    radius_m=radius_m,
                    fresh_after=fresh_cutoff,
                )
            else:
                result = self._accept(
                    session,
                    incident=incident,
                    invitation=invitation,
                    decision=decision,
                    received_at=received_at,
                    radius_m=radius_m,
                    fresh_after=fresh_cutoff,
                )

            session.add(
                ResponderInvitationResponseRow(
                    scope_id=self.scope_id,
                    response_id=decision.decision_id,
                    invitation_id=invitation.invitation_id,
                    incident_id=incident.incident_id,
                    responder_id=responder_id,
                    request_fingerprint=fingerprint,
                    schema_version=decision.schema_version,
                    decision=decision.decision.value,
                    server_received_at=received_at,
                    payload={
                        "request": decision.model_dump(mode="json"),
                        "accepted_result": result.model_dump(mode="json"),
                    },
                    simulated=False,
                )
            )
            session.flush()

            if decision.decision is ResponderDecision.ACCEPT:
                accepted = result.accepted_dispatch
                if accepted is None:
                    raise PersistenceIntegrityError(
                        "accepted responder result has no dispatch"
                    )
                assignment_id = _stable_id(
                    self.scope_id,
                    "responder-assignment",
                    incident.incident_id,
                )
                session.add(
                    ResponderAssignmentRow(
                        scope_id=self.scope_id,
                        assignment_id=assignment_id,
                        incident_id=incident.incident_id,
                        responder_id=responder_id,
                        invitation_id=invitation.invitation_id,
                        aed_id=accepted.aed.aed_site_id,
                        response_id=decision.decision_id,
                        accepted_at=received_at,
                        static_route=accepted.route.model_dump(mode="json"),
                        simulated=False,
                    )
                )
                session.flush()
                protocol = self._protocol_registry.select(
                    IncidentKind(incident.kind)
                )
                session.add(
                    ProtocolPresentationRow(
                        scope_id=self.scope_id,
                        presentation_id=_stable_id(
                            self.scope_id,
                            "protocol-presentation",
                            incident.incident_id,
                        ),
                        incident_id=incident.incident_id,
                        assignment_id=assignment_id,
                        responder_id=responder_id,
                        schema_version=protocol.schema_version,
                        protocol_id=protocol.protocol_id,
                        protocol_version=protocol.version,
                        emergency_kind=protocol.emergency_kind.value,
                        content_sha256=protocol.content_sha256,
                        presented_at=received_at,
                        protocol_snapshot=protocol.model_dump(mode="json"),
                        simulated=False,
                    )
                )
                session.flush()
            return result

    def get_accepted_dispatch(
        self,
        incident_id: UUID,
        responder_id: UUID,
        *,
        responder_token: str | None,
        authenticated_responder_id: UUID | None,
    ) -> AcceptedDispatchView | None:
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
                raise PersistenceIntegrityError(
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
            return self._stored_assignment_dispatch(session, assignment)

    def _decline(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        invitation: ResponderInvitationRow,
        decision: ResponderDecisionRequest,
        received_at: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> ResponderDecisionResult:
        invitation.status = InvitationStatus.DECLINED.value
        invitation.responded_at = received_at
        self._append_timeline(
            session,
            incident=incident,
            event_type=TimelineEventType.RESPONDER_DECLINED,
            occurred_at=received_at,
            state=IncidentState.ESCALATING,
            transition_id=None,
            summary=f"Responder {invitation.responder_id} declined the invitation.",
        )
        eligible = self._eligible_responders(
            session,
            incident=incident,
            as_of=received_at,
            radius_m=radius_m,
            fresh_after=fresh_after,
        )
        self._invite_next_eligible(
            session,
            incident=incident,
            eligible=eligible,
            occurred_at=received_at,
        )
        incident.updated_at = max(incident.updated_at, received_at)
        session.flush()
        coordination = self._coordination_view(
            session,
            incident=incident,
            as_of=received_at,
            radius_m=radius_m,
            fresh_after=fresh_after,
            eligible=eligible,
            response_overrides={invitation.invitation_id: decision.decision_id},
        )
        invitation_view = next(
            item
            for item in coordination.invitations
            if item.invitation_id == invitation.invitation_id
        )
        return ResponderDecisionResult(
            schema_version=SCHEMA_VERSION,
            decision_id=decision.decision_id,
            status=ResponderDecisionResultStatus.ACCEPTED,
            decision=ResponderDecision.DECLINE,
            invitation=invitation_view,
            transition=None,
            coordination=coordination,
            accepted_dispatch=None,
            server_received_at=received_at,
        )

    def _accept(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        invitation: ResponderInvitationRow,
        decision: ResponderDecisionRequest,
        received_at: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> ResponderDecisionResult:
        eligible = self._eligible_responders(
            session,
            incident=incident,
            as_of=received_at,
            radius_m=radius_m,
            fresh_after=fresh_after,
        )
        responder = next(
            (
                item
                for item in eligible
                if item.responder_id == invitation.responder_id
            ),
            None,
        )
        if responder is None:
            raise DispatchConflictError(
                code="responder_not_eligible",
                identifier=str(invitation.responder_id),
            )
        aed = self._nearest_aed(session, incident=incident)
        distances = session.execute(
            _ROUTE_DISTANCE_QUERY,
            {
                "scope_id": self.scope_id,
                "location_id": responder.location_id,
                "aed_id": aed.view.aed_site_id,
                "incident_longitude": incident.longitude,
                "incident_latitude": incident.latitude,
            },
        ).mappings().one_or_none()
        if distances is None:
            raise PersistenceIntegrityError("PostGIS route points are incomplete")

        invitation.status = InvitationStatus.ACCEPTED.value
        invitation.responded_at = received_at
        from_state = IncidentState(incident.current_state)
        to_state = next_incident_state(
            from_state,
            IncidentTrigger.RESPONDER_ACCEPTED,
        )
        transition = IncidentStateTransitionRow(
            scope_id=self.scope_id,
            transition_id=_stable_id(
                self.scope_id,
                "responder-transition",
                decision.decision_id,
            ),
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            sequence=incident.state_version + 1,
            from_state=from_state.value,
            to_state=to_state.value,
            trigger=IncidentTrigger.RESPONDER_ACCEPTED.value,
            occurred_at=received_at,
            source_event_id=None,
            command_id=None,
            simulated=False,
        )
        session.add(transition)
        session.flush()
        incident.current_state = to_state.value
        incident.state_version += 1
        incident.updated_at = received_at
        route = self._routing_provider.route(
            route_id=_stable_id(
                self.scope_id,
                "static-route",
                decision.decision_id,
            ),
            responder_location=Coordinate(
                latitude=responder.latitude,
                longitude=responder.longitude,
            ),
            aed=aed.view,
            wearer_location=_wearer_location(incident),
            responder_to_aed_distance_m=float(
                distances["responder_to_aed_distance_m"]
            ),
            aed_to_wearer_distance_m=float(
                distances["aed_to_wearer_distance_m"]
            ),
            created_at=received_at,
        )
        invitation_view = _invitation_view(
            invitation,
            response_id=decision.decision_id,
        )
        accepted_dispatch = AcceptedDispatchView(
            schema_version=SCHEMA_VERSION,
            incident_id=incident.incident_id,
            state=IncidentState.RESPONSE_ACTIVE,
            invitation=invitation_view,
            wearer_location=_wearer_location(incident),
            aed=aed.view,
            route=route,
            activated_at=received_at,
            simulated=False,
        )
        transition_view = _transition_view(transition)
        self._append_timeline(
            session,
            incident=incident,
            event_type=TimelineEventType.RESPONDER_ACCEPTED,
            occurred_at=received_at,
            state=IncidentState.RESPONSE_ACTIVE,
            transition_id=transition.transition_id,
            summary=f"Responder {invitation.responder_id} accepted the invitation.",
        )
        self._append_timeline(
            session,
            incident=incident,
            event_type=TimelineEventType.DISPATCH_ACTIVATED,
            occurred_at=received_at,
            state=IncidentState.RESPONSE_ACTIVE,
            transition_id=transition.transition_id,
            summary=(
                "Accepted responder received an AED-first live walking route."
                if route.fallback_reason is None
                else "Accepted responder received an explicitly labelled "
                "AED-first static venue fallback route."
            ),
        )
        session.flush()
        return ResponderDecisionResult(
            schema_version=SCHEMA_VERSION,
            decision_id=decision.decision_id,
            status=ResponderDecisionResultStatus.ACCEPTED,
            decision=ResponderDecision.ACCEPT,
            invitation=invitation_view,
            transition=transition_view,
            coordination=None,
            accepted_dispatch=accepted_dispatch,
            server_received_at=received_at,
        )

    def _coordination_view(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
        eligible: Sequence[_EligibleResponder] | None = None,
        response_overrides: Mapping[UUID, UUID] | None = None,
    ) -> DispatchCoordinationView:
        if eligible is None:
            eligible = self._eligible_responders(
                session,
                incident=incident,
                as_of=as_of,
                radius_m=radius_m,
                fresh_after=fresh_after,
            )
        invitations = session.scalars(
            select(ResponderInvitationRow)
            .where(
                ResponderInvitationRow.scope_id == self.scope_id,
                ResponderInvitationRow.incident_id == incident.incident_id,
            )
            .order_by(ResponderInvitationRow.rank)
        ).all()
        responses = {
            row.invitation_id: row.response_id
            for row in session.scalars(
                select(ResponderInvitationResponseRow).where(
                    ResponderInvitationResponseRow.scope_id == self.scope_id,
                    ResponderInvitationResponseRow.incident_id
                    == incident.incident_id,
                )
            ).all()
        }
        if response_overrides:
            responses.update(response_overrides)

        candidates: list[ResponderCandidateView] = []
        invited_ids: set[UUID] = set()
        for invitation in invitations:
            try:
                candidate = ResponderCandidateView.model_validate(
                    invitation.candidate_snapshot
                )
            except (TypeError, ValueError) as exc:
                raise PersistenceIntegrityError(
                    f"invalid candidate snapshot: {invitation.invitation_id}"
                ) from exc
            if candidate.rank != invitation.rank:
                raise PersistenceIntegrityError(
                    f"candidate rank mismatch: {invitation.invitation_id}"
                )
            candidates.append(candidate)
            invited_ids.add(candidate.responder_id)

        next_rank = len(candidates) + 1
        for responder in eligible:
            if len(candidates) >= 50:
                break
            if responder.responder_id in invited_ids:
                continue
            candidates.append(responder.public_view(next_rank))
            next_rank += 1

        invitation_views = tuple(
            _invitation_view(
                invitation,
                response_id=responses.get(invitation.invitation_id),
            )
            for invitation in invitations
        )
        accepted = [
            item
            for item in invitation_views
            if item.status is InvitationStatus.ACCEPTED
        ]
        assignment = session.scalar(
            select(ResponderAssignmentRow).where(
                ResponderAssignmentRow.scope_id == self.scope_id,
                ResponderAssignmentRow.incident_id == incident.incident_id,
            )
        )
        if assignment is not None:
            accepted_dispatch = self._stored_assignment_dispatch(
                session,
                assignment,
            )
            nearest_aed = _AEDMatch(
                view=accepted_dispatch.aed,
                distance_m=0.0,
            )
        else:
            nearest_aed = self._nearest_aed(session, incident=incident)

        search_started_at = self._search_started_at(
            session,
            incident.incident_id,
        )
        if search_started_at is None:
            search_started_at = as_of
        activity_times = [incident.updated_at, search_started_at]
        activity_times.extend(invitation.created_at for invitation in invitations)
        activity_times.extend(
            invitation.responded_at
            for invitation in invitations
            if invitation.responded_at is not None
        )
        return DispatchCoordinationView(
            schema_version=SCHEMA_VERSION,
            incident_id=incident.incident_id,
            state=IncidentState(incident.current_state),
            search_radius_m=radius_m,
            search_started_at=search_started_at,
            candidates=tuple(candidates),
            invitations=invitation_views,
            nearest_aed=nearest_aed.view,
            accepted_responder_id=(
                accepted[0].responder.responder_id if accepted else None
            ),
            updated_at=max(activity_times),
            simulated=False,
        )

    def _stored_assignment_dispatch(
        self,
        session: Session,
        assignment: ResponderAssignmentRow,
    ) -> AcceptedDispatchView:
        response = session.get(
            ResponderInvitationResponseRow,
            (self.scope_id, assignment.response_id),
        )
        if response is None:
            raise PersistenceIntegrityError(
                f"accepted assignment has no response: {assignment.assignment_id}"
            )
        result = _stored_decision_result(response)
        dispatch = result.accepted_dispatch
        try:
            stored_route = RoutePlan.model_validate(assignment.static_route)
        except (TypeError, ValueError) as exc:
            raise PersistenceIntegrityError(
                f"accepted assignment route is invalid: {assignment.assignment_id}"
            ) from exc
        if (
            response.decision != ResponderDecision.ACCEPT.value
            or dispatch is None
            or dispatch.incident_id != assignment.incident_id
            or dispatch.invitation.invitation_id != assignment.invitation_id
            or dispatch.invitation.responder.responder_id
            != assignment.responder_id
            or dispatch.aed.aed_site_id != assignment.aed_id
            or dispatch.route != stored_route
            or dispatch.activated_at != assignment.accepted_at
        ):
            raise PersistenceIntegrityError(
                f"accepted assignment snapshot mismatch: {assignment.assignment_id}"
            )
        return dispatch

    def _eligible_responders(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        as_of: datetime,
        radius_m: int,
        fresh_after: datetime,
    ) -> tuple[_EligibleResponder, ...]:
        rows = session.execute(
            _ELIGIBILITY_QUERY,
            {
                "scope_id": self.scope_id,
                "incident_longitude": incident.longitude,
                "incident_latitude": incident.latitude,
                "fresh_after": fresh_after,
                "as_of": as_of,
                "radius_m": radius_m,
            },
        ).mappings().all()
        responder_ids = [row["responder_id"] for row in rows]
        if not responder_ids:
            return ()
        valid_skill_rows = session.scalars(
            select(ResponderSkillRow)
            .where(
                ResponderSkillRow.scope_id == self.scope_id,
                ResponderSkillRow.responder_id.in_(responder_ids),
                or_(
                    ResponderSkillRow.certified_until.is_(None),
                    ResponderSkillRow.certified_until >= as_of,
                ),
            )
            .order_by(ResponderSkillRow.responder_id, ResponderSkillRow.skill)
        ).all()
        skills_by_responder: dict[UUID, list[ResponderSkill]] = {}
        for skill_row in valid_skill_rows:
            skills_by_responder.setdefault(skill_row.responder_id, []).append(
                ResponderSkill(skill_row.skill)
            )
        return tuple(
            _EligibleResponder(
                responder_id=row["responder_id"],
                display_name=row["display_name"],
                role=ResponderRole(row["role"]),
                skills=tuple(skills_by_responder[row["responder_id"]]),
                captured_at=row["captured_at"],
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                distance_m=float(row["distance_m"]),
                location_id=row["location_id"],
            )
            for row in rows
        )

    def _invite_next_eligible(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        eligible: Sequence[_EligibleResponder],
        occurred_at: datetime,
    ) -> ResponderInvitationRow | None:
        existing = session.scalars(
            select(ResponderInvitationRow)
            .where(
                ResponderInvitationRow.scope_id == self.scope_id,
                ResponderInvitationRow.incident_id == incident.incident_id,
            )
            .order_by(ResponderInvitationRow.rank)
        ).all()
        if len(existing) >= 50:
            return None
        if any(
            row.status in {
                InvitationStatus.PENDING.value,
                InvitationStatus.ACCEPTED.value,
            }
            for row in existing
        ):
            return None
        invited_ids = {row.responder_id for row in existing}
        responder = next(
            (item for item in eligible if item.responder_id not in invited_ids),
            None,
        )
        if responder is None:
            return None
        rank = len(existing) + 1
        candidate = responder.public_view(rank)
        invitation = ResponderInvitationRow(
            scope_id=self.scope_id,
            invitation_id=_stable_id(
                self.scope_id,
                "responder-invitation",
                f"{incident.incident_id}:{rank}",
            ),
            incident_id=incident.incident_id,
            responder_id=responder.responder_id,
            rank=rank,
            status=InvitationStatus.PENDING.value,
            distance_m=responder.distance_m,
            candidate_snapshot=candidate.model_dump(mode="json"),
            created_at=occurred_at,
            responded_at=None,
            simulated=False,
        )
        session.add(invitation)
        if self._notification_enqueuer is not None:
            self._notification_enqueuer(session, invitation, occurred_at)
        self._append_timeline(
            session,
            incident=incident,
            event_type=TimelineEventType.RESPONDER_INVITED,
            occurred_at=occurred_at,
            state=IncidentState.ESCALATING,
            transition_id=None,
            summary=(
                f"Invited ranked responder {responder.responder_id} "
                f"({candidate.distance_band.value})."
            ),
        )
        session.flush()
        return invitation

    def _nearest_aed(
        self,
        session: Session,
        *,
        incident: IncidentRow,
    ) -> _AEDMatch:
        row = session.execute(
            _NEAREST_AED_QUERY,
            {
                "scope_id": self.scope_id,
                "incident_longitude": incident.longitude,
                "incident_latitude": incident.latitude,
            },
        ).mappings().one_or_none()
        if row is None:
            raise DispatchConflictError(
                code="no_available_aed",
                identifier=str(incident.incident_id),
            )
        return _aed_match(row)

    def _dispatchable_incident(
        self,
        session: Session,
        incident_id: UUID,
    ) -> IncidentRow:
        incident = session.get(IncidentRow, (self.scope_id, incident_id))
        if incident is None:
            raise DispatchNotFoundError(
                code="incident_not_found",
                identifier=str(incident_id),
            )
        if incident.current_state not in {
            IncidentState.ESCALATING.value,
            IncidentState.RESPONSE_ACTIVE.value,
        }:
            raise DispatchConflictError(
                code="incident_not_dispatchable",
                identifier=str(incident_id),
                current_state=IncidentState(incident.current_state),
            )
        return incident

    def _locked_dispatchable_incident(
        self,
        session: Session,
        incident_id: UUID,
    ) -> IncidentRow:
        incident = session.scalar(
            select(IncidentRow)
            .where(
                IncidentRow.scope_id == self.scope_id,
                IncidentRow.incident_id == incident_id,
            )
            .with_for_update()
        )
        if incident is None:
            raise DispatchNotFoundError(
                code="incident_not_found",
                identifier=str(incident_id),
            )
        if incident.current_state not in {
            IncidentState.ESCALATING.value,
            IncidentState.RESPONSE_ACTIVE.value,
        }:
            raise DispatchConflictError(
                code="incident_not_dispatchable",
                identifier=str(incident_id),
                current_state=IncidentState(incident.current_state),
            )
        return incident

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
            raise ResponderAuthenticationError
        if authenticated_responder_id is not None:
            if (
                authenticated_responder_id != responder_id
                or responder_token is not None
            ):
                raise ResponderAuthenticationError
            return
        if responder_token is None:
            raise ResponderAuthenticationError
        presented_hash = sha256(responder_token.encode("utf-8")).hexdigest()
        if not compare_digest(presented_hash, responder.access_token_hash):
            raise ResponderAuthenticationError

    def _search_started_at(
        self,
        session: Session,
        incident_id: UUID,
    ) -> datetime | None:
        return session.scalar(
            select(IncidentTimelineEntryRow.occurred_at)
            .where(
                IncidentTimelineEntryRow.scope_id == self.scope_id,
                IncidentTimelineEntryRow.incident_id == incident_id,
                IncidentTimelineEntryRow.event_type
                == TimelineEventType.RESPONDER_SEARCH_STARTED.value,
            )
            .order_by(IncidentTimelineEntryRow.sequence)
            .limit(1)
        )

    def _append_timeline(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        event_type: TimelineEventType,
        occurred_at: datetime,
        state: IncidentState,
        transition_id: UUID | None,
        summary: str,
    ) -> None:
        sequence = incident.next_timeline_sequence
        session.add(
            IncidentTimelineEntryRow(
                scope_id=self.scope_id,
                timeline_id=_stable_id(
                    self.scope_id,
                    "timeline",
                    f"{incident.incident_id}:{sequence}",
                ),
                schema_version=incident.schema_version,
                incident_id=incident.incident_id,
                sequence=sequence,
                event_type=event_type.value,
                occurred_at=occurred_at,
                state=state.value,
                transition_id=transition_id,
                source_event_id=None,
                command_id=None,
                summary=summary,
                simulated=False,
            )
        )
        incident.next_timeline_sequence += 1

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        connection = self._engine.connect().execution_options(
            isolation_level="READ COMMITTED"
        )
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                with session.begin():
                    yield session
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "40001":
                with self._sessions() as verification_session:
                    require_active_scope(verification_session, self.scope_id)
            raise
        finally:
            connection.close()


def seed_demo_response_network(
    sessions: sessionmaker[Session],
    *,
    scope_id: UUID,
    seeded_at: datetime,
) -> SeededResponseNetwork:
    """Persist the explicit Chicago Loop hackathon response network.

    This is an operator-triggered database seed, not an in-memory fallback.
    Tokens are rotated and returned in plaintext once; only SHA-256 hashes are
    persisted. Re-running also records a new current location for each responder.
    """

    occurred_at = _utc(seeded_at)
    responder_specs = (
        (
            "north-venue-staff",
            "Chicago Loop Venue Staff",
            ResponderRole.VENUE_STAFF,
            (ResponderSkill.FIRST_AID, ResponderSkill.CPR, ResponderSkill.AED),
            41.87830,
            -87.62965,
        ),
        (
            "west-trained-volunteer",
            "Chicago Loop Trained Volunteer",
            ResponderRole.TRAINED_VOLUNTEER,
            (ResponderSkill.FIRST_AID, ResponderSkill.CPR),
            41.87875,
            -87.63030,
        ),
    )
    aed_specs = (
        (
            "lobby-aed",
            "Chicago Loop Lobby AED",
            "Ground-floor lobby beside the security desk",
            "Enter through the main doors; the white AED cabinet is on the east wall.",
            True,
            41.87822,
            -87.62955,
        ),
        (
            "concourse-aed",
            "Chicago Loop Concourse AED",
            "Lower concourse near the north stairwell",
            "Use the north stairs and follow the red AED wall sign.",
            True,
            41.87765,
            -87.63045,
        ),
    )
    credentials: list[SeededResponderCredential] = []
    aed_ids: list[UUID] = []
    with sessions.begin() as session:
        require_active_scope(session, scope_id, lock=True)
        for (
            key,
            display_name,
            role,
            skills,
            latitude,
            longitude,
        ) in responder_specs:
            responder_id = _stable_id(scope_id, "seed-responder", key)
            access_token = token_urlsafe(32)
            token_hash = sha256(access_token.encode("utf-8")).hexdigest()
            responder = session.get(ResponderRow, (scope_id, responder_id))
            if responder is None:
                responder = ResponderRow(
                    scope_id=scope_id,
                    responder_id=responder_id,
                    display_name=display_name,
                    role=role.value,
                    access_token_hash=token_hash,
                    status="active",
                    created_at=occurred_at,
                    updated_at=occurred_at,
                    simulated=False,
                )
                session.add(responder)
            else:
                responder.display_name = display_name
                responder.role = role.value
                responder.access_token_hash = token_hash
                responder.status = "active"
                responder.updated_at = max(responder.updated_at, occurred_at)
            session.flush()
            persona_account = session.get(
                PersonaAccountRow,
                (scope_id, responder_id),
            )
            if persona_account is None:
                session.add(
                    PersonaAccountRow(
                        scope_id=scope_id,
                        account_id=responder_id,
                        display_name=display_name,
                        persona="responder",
                        user_id=None,
                        responder_id=responder_id,
                        enrollment_token_hash=token_hash,
                        status="active",
                        created_at=occurred_at,
                        updated_at=occurred_at,
                    )
                )
            else:
                persona_account.display_name = display_name
                persona_account.persona = "responder"
                persona_account.user_id = None
                persona_account.responder_id = responder_id
                persona_account.enrollment_token_hash = token_hash
                persona_account.status = "active"
                persona_account.updated_at = max(
                    persona_account.updated_at,
                    occurred_at,
                )
            session.flush()
            session.execute(
                delete(ResponderSkillRow).where(
                    ResponderSkillRow.scope_id == scope_id,
                    ResponderSkillRow.responder_id == responder_id,
                )
            )
            session.add_all(
                ResponderSkillRow(
                    scope_id=scope_id,
                    responder_id=responder_id,
                    skill=skill.value,
                    certified_until=None,
                )
                for skill in skills
            )
            availability = session.get(
                ResponderAvailabilityRow,
                (scope_id, responder_id),
            )
            if availability is None:
                session.add(
                    ResponderAvailabilityRow(
                        scope_id=scope_id,
                        responder_id=responder_id,
                        available=True,
                        updated_at=occurred_at,
                    )
                )
            else:
                availability.available = True
                availability.updated_at = occurred_at
            session.add(
                ResponderLocationRow(
                    scope_id=scope_id,
                    location_id=uuid4(),
                    responder_id=responder_id,
                    captured_at=occurred_at,
                    horizontal_accuracy_m=5.0,
                    location=WKTElement(
                        f"POINT({longitude} {latitude})",
                        srid=4326,
                    ),
                    simulated=False,
                )
            )
            credentials.append(
                SeededResponderCredential(
                    responder_id=responder_id,
                    display_name=display_name,
                    access_token=access_token,
                )
            )

        for (
            key,
            name,
            location_description,
            access_instructions,
            publicly_accessible,
            latitude,
            longitude,
        ) in aed_specs:
            aed_id = _stable_id(scope_id, "seed-aed", key)
            aed = session.get(AEDSiteRow, (scope_id, aed_id))
            point = WKTElement(f"POINT({longitude} {latitude})", srid=4326)
            if aed is None:
                session.add(
                    AEDSiteRow(
                        scope_id=scope_id,
                        aed_id=aed_id,
                        name=name,
                        location_description=location_description,
                        access_instructions=access_instructions,
                        publicly_accessible=publicly_accessible,
                        location=point,
                        active=True,
                        created_at=occurred_at,
                        updated_at=occurred_at,
                        simulated=False,
                    )
                )
            else:
                aed.name = name
                aed.location_description = location_description
                aed.access_instructions = access_instructions
                aed.publicly_accessible = publicly_accessible
                aed.location = point
                aed.active = True
                aed.updated_at = max(aed.updated_at, occurred_at)
            aed_ids.append(aed_id)

    return SeededResponseNetwork(
        scope_id=scope_id,
        venue_name="Chicago Loop demo venue",
        responders=tuple(credentials),
        aed_site_ids=tuple(aed_ids),
        seeded_at=occurred_at,
    )


def _invitation_view(
    invitation: ResponderInvitationRow,
    *,
    response_id: UUID | None,
) -> ResponderInvitationView:
    candidate = ResponderCandidateView.model_validate(invitation.candidate_snapshot)
    return ResponderInvitationView(
        schema_version=SCHEMA_VERSION,
        invitation_id=invitation.invitation_id,
        incident_id=invitation.incident_id,
        sequence=invitation.rank,
        responder=candidate,
        status=InvitationStatus(invitation.status),
        invited_at=invitation.created_at,
        responded_at=invitation.responded_at,
        decision_id=response_id,
    )


def _transition_view(row: IncidentStateTransitionRow) -> IncidentTransition:
    return IncidentTransition(
        schema_version=row.schema_version,
        transition_id=row.transition_id,
        incident_id=row.incident_id,
        sequence=row.sequence,
        from_state=row.from_state,
        to_state=row.to_state,
        trigger=row.trigger,
        occurred_at=row.occurred_at,
        source_event_id=row.source_event_id,
        check_in_id=row.command_id,
        simulated=False,
    )


def _stored_decision_result(
    row: ResponderInvitationResponseRow,
) -> ResponderDecisionResult:
    payload = row.payload.get("accepted_result")
    if not isinstance(payload, dict):
        raise PersistenceIntegrityError(
            f"stored responder decision has no result: {row.response_id}"
        )
    try:
        accepted = ResponderDecisionResult.model_validate(payload)
        replay_payload = accepted.model_dump(mode="json")
        replay_payload["status"] = ResponderDecisionResultStatus.ALREADY_PROCESSED.value
        result = ResponderDecisionResult.model_validate(replay_payload)
    except (TypeError, ValueError) as exc:
        raise PersistenceIntegrityError(
            f"stored responder decision result is invalid: {row.response_id}"
        ) from exc
    if (
        result.decision_id != row.response_id
        or result.invitation.invitation_id != row.invitation_id
        or result.invitation.incident_id != row.incident_id
        or result.invitation.responder.responder_id != row.responder_id
        or result.server_received_at != row.server_received_at
    ):
        raise PersistenceIntegrityError(
            f"stored responder result does not match receipt: {row.response_id}"
        )
    return result


def _aed_match(row: Mapping[str, Any]) -> _AEDMatch:
    return _AEDMatch(
        view=AEDSiteView(
            schema_version=SCHEMA_VERSION,
            aed_site_id=row["aed_id"],
            name=row["name"],
            coordinate=Coordinate(
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
            ),
            location_description=row["location_description"],
            access_instructions=row["access_instructions"],
            publicly_accessible=row["publicly_accessible"],
            available=True,
            availability_confirmed_at=row["updated_at"],
        ),
        distance_m=float(row["distance_m"]),
    )


def _wearer_location(incident: IncidentRow) -> GeoLocation:
    return GeoLocation(
        latitude=incident.latitude,
        longitude=incident.longitude,
        horizontal_accuracy_m=incident.horizontal_accuracy_m,
        captured_at=incident.location_captured_at,
    )


def _stable_id(scope_id: UUID, namespace: str, identifier: object) -> UUID:
    return uuid5(_ID_NAMESPACE, f"{scope_id}:{namespace}:{identifier}")


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
        raise ValueError("dispatch timestamps must be timezone-aware")
    return value.astimezone(UTC)
