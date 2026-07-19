"""Transactional PostgreSQL incident persistence.

Wearable events, incident state, health-context snapshots, holds, deadlines, and
audit entries are committed together.  There is intentionally no in-memory
incident implementation: product incident endpoints are available only when the
configured PostgreSQL scope is active.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from uuid import UUID, uuid5

from sqlalchemy import Engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.adapters.fingerprints import model_fingerprint
from vital_relay.adapters.postgres_health import (
    PersistenceIntegrityError,
    PostgresHealthCapabilityRepository,
    PostgresHealthMetricRepository,
    PostgresHealthSnapshotRepository,
)
from vital_relay.application.health_context import HealthSnapshotService
from vital_relay.application.incident_service import (
    IncidentConflictError,
    IncidentNotFoundError,
)
from vital_relay.domain.health_context import (
    HealthSnapshotCreateRequest,
    SnapshotCaptureReason,
)
from vital_relay.domain.incidents import (
    CheckInRequest,
    CheckInResult,
    CheckInStatus,
    EventIngestionStatus,
    GeoLocation,
    IncidentKind,
    IncidentResolutionReceipt,
    IncidentResolutionRequest,
    IncidentResolutionStatus,
    IncidentState,
    IncidentTimelineEntry,
    IncidentTransition,
    IncidentTrigger,
    IncidentView,
    ResolutionAction,
    TimelineEventType,
    WearableEventRequest,
    WearableEventResult,
    WearableEventSource,
    WearableEventType,
    next_incident_state,
    trigger_for_check_in,
)
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    HealthSnapshotHoldRow,
    IncidentCommandRow,
    IncidentDeadlineRow,
    IncidentResolutionReceiptRow,
    IncidentRow,
    IncidentStateTransitionRow,
    IncidentTimelineEntryRow,
    ResponderAssignmentRevocationRow,
    ResponderAssignmentRow,
    WearableEventRow,
)


_ID_NAMESPACE = UUID("fabd65cd-f0b6-48c0-8c70-3a854f1c9540")
_ACTIVE_STATES = (
    IncidentState.VERIFYING.value,
    IncidentState.ESCALATING.value,
    IncidentState.RESPONSE_ACTIVE.value,
)
_VERIFICATION_DEADLINE_KIND = "verification_timeout"
_PENDING = "pending"
_FIRED = "fired"
_CANCELLED = "cancelled"

T = TypeVar("T")


class _FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class PostgresIncidentRepository:
    """Scope-bound real incident repository with owned transaction boundaries."""

    def __init__(
        self,
        engine: Engine,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._engine = engine
        self._sessions = sessions
        self.scope_id = scope_id

    def ingest(
        self,
        event: WearableEventRequest,
        *,
        server_received_at: datetime,
        verification_deadline_at: datetime | None,
    ) -> WearableEventResult:
        received_at = _utc(server_received_at)
        deadline_at = (
            _utc(verification_deadline_at)
            if verification_deadline_at is not None
            else None
        )
        if event.event_type is WearableEventType.FALL_DETECTED:
            if deadline_at is None or deadline_at <= received_at:
                raise ValueError("fall events require a future verification deadline")
        elif deadline_at is not None:
            raise ValueError("manual SOS events cannot include a verification deadline")

        fingerprint = model_fingerprint(event)
        with self._transaction() as session:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="wearable_event",
                identifier=str(event.event_id),
            )
            prior = session.get(
                WearableEventRow,
                (self.scope_id, event.event_id),
            )
            if prior is not None:
                if prior.request_fingerprint != fingerprint:
                    raise IncidentConflictError(
                        code="event_id_conflict",
                        identifier=str(event.event_id),
                    )
                return self._event_result(
                    session,
                    submitted_event_id=event.event_id,
                    canonical=prior,
                    status=EventIngestionStatus.ALREADY_PROCESSED,
                )

            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="incident_user",
                identifier=event.user_id,
            )
            natural_duplicate = self._apple_natural_duplicate(session, event)
            if natural_duplicate is not None:
                return self._event_result(
                    session,
                    submitted_event_id=event.event_id,
                    canonical=natural_duplicate,
                    status=EventIngestionStatus.ALREADY_PROCESSED,
                )

            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="device_sequence",
                identifier=f"{event.device_id}:{event.sequence}",
            )
            sequence_owner = session.scalar(
                select(WearableEventRow).where(
                    WearableEventRow.scope_id == self.scope_id,
                    WearableEventRow.device_id == event.device_id,
                    WearableEventRow.sequence == event.sequence,
                )
            )
            if sequence_owner is not None:
                raise IncidentConflictError(
                    code="device_sequence_conflict",
                    identifier=f"{event.device_id}:{event.sequence}",
                )

            active_incident = session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.user_id == event.user_id,
                    IncidentRow.current_state.in_(_ACTIVE_STATES),
                )
                .with_for_update()
            )
            if active_incident is None:
                incident = self._open_incident(
                    session,
                    event=event,
                    fingerprint=fingerprint,
                    received_at=received_at,
                    verification_deadline_at=deadline_at,
                )
            else:
                incident = self._attach_to_active_incident(
                    session,
                    incident=active_incident,
                    event=event,
                    fingerprint=fingerprint,
                    received_at=received_at,
                )

            session.flush()
            canonical = session.get(
                WearableEventRow,
                (self.scope_id, event.event_id),
            )
            if canonical is None:
                raise PersistenceIntegrityError(
                    f"accepted wearable event was not persisted: {event.event_id}"
                )
            return WearableEventResult(
                schema_version=event.schema_version,
                event_id=event.event_id,
                canonical_event_id=event.event_id,
                status=EventIngestionStatus.ACCEPTED,
                incident=self._incident_view(session, incident),
                server_received_at=received_at,
            )

    def check_in(
        self,
        incident_id: UUID,
        request: CheckInRequest,
        *,
        server_received_at: datetime,
    ) -> CheckInResult:
        received_at = _utc(server_received_at)
        fingerprint = model_fingerprint(request)
        result: CheckInResult | None = None
        post_commit_conflict: IncidentConflictError | None = None

        with self._transaction(isolation_level="READ COMMITTED") as session:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="incident_check_in",
                identifier=str(request.response_id),
            )
            prior = session.get(
                IncidentCommandRow,
                (self.scope_id, request.response_id),
            )
            if prior is not None:
                if (
                    prior.incident_id != incident_id
                    or prior.request_fingerprint != fingerprint
                ):
                    raise IncidentConflictError(
                        code="check_in_id_conflict",
                        identifier=str(request.response_id),
                    )
                transition = session.scalar(
                    select(IncidentStateTransitionRow).where(
                        IncidentStateTransitionRow.scope_id == self.scope_id,
                        IncidentStateTransitionRow.command_id == request.response_id,
                    )
                )
                if transition is None:
                    raise PersistenceIntegrityError(
                        "stored check-in is missing its incident transition"
                    )
                stored_result = _stored_check_in_result(prior)
                if (
                    stored_result.transition.transition_id
                    != transition.transition_id
                ):
                    raise PersistenceIntegrityError(
                        "stored check-in result does not match its transition"
                    )
                return CheckInResult(
                    schema_version=stored_result.schema_version,
                    response_id=stored_result.response_id,
                    status=CheckInStatus.ALREADY_PROCESSED,
                    incident=stored_result.incident,
                    transition=stored_result.transition,
                    server_received_at=stored_result.server_received_at,
                )

            incident = session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.incident_id == incident_id,
                )
                .with_for_update()
            )
            if incident is None:
                raise IncidentNotFoundError(incident_id)

            deadline = self._deadline_for_incident(
                session,
                incident_id,
                lock=True,
            )
            if (
                incident.current_state == IncidentState.VERIFYING.value
                and deadline is not None
                and deadline.status == _PENDING
                and deadline.due_at <= received_at
            ):
                self._fire_locked_deadline(
                    session,
                    incident=incident,
                    deadline=deadline,
                    occurred_at=received_at,
                )
                post_commit_conflict = IncidentConflictError(
                    code="incident_transition_not_allowed",
                    identifier=str(incident_id),
                    current_state=IncidentState.ESCALATING,
                )
            elif incident.current_state != IncidentState.VERIFYING.value:
                raise IncidentConflictError(
                    code="incident_transition_not_allowed",
                    identifier=str(incident_id),
                    current_state=IncidentState(incident.current_state),
                )
            else:
                result = self._record_check_in(
                    session,
                    incident=incident,
                    deadline=deadline,
                    request=request,
                    fingerprint=fingerprint,
                    received_at=received_at,
                )

        if post_commit_conflict is not None:
            raise post_commit_conflict
        if result is None:
            raise PersistenceIntegrityError("check-in transaction produced no result")
        return result

    def resolve(
        self,
        incident_id: UUID,
        request: IncidentResolutionRequest,
        *,
        server_received_at: datetime,
    ) -> IncidentResolutionReceipt:
        received_at = _utc(server_received_at)
        fingerprint = model_fingerprint(request)

        with self._transaction(isolation_level="READ COMMITTED") as session:
            require_active_scope(session, self.scope_id, lock=True)
            _transaction_lock(
                session,
                scope_id=self.scope_id,
                namespace="incident_resolution",
                identifier=str(request.resolution_id),
            )
            prior = session.get(
                IncidentResolutionReceiptRow,
                (self.scope_id, request.resolution_id),
            )
            if prior is not None:
                if (
                    prior.incident_id != incident_id
                    or prior.request_fingerprint != fingerprint
                ):
                    raise IncidentConflictError(
                        code="resolution_id_conflict",
                        identifier=str(request.resolution_id),
                    )
                transition = session.scalar(
                    select(IncidentStateTransitionRow).where(
                        IncidentStateTransitionRow.scope_id == self.scope_id,
                        IncidentStateTransitionRow.resolution_id
                        == request.resolution_id,
                    )
                )
                if transition is None:
                    raise PersistenceIntegrityError(
                        "stored resolution is missing its incident transition"
                    )
                stored = _stored_resolution_receipt(prior)
                if (
                    _transition_from_row(transition) != stored.transition
                    or transition.incident_id != incident_id
                ):
                    raise PersistenceIntegrityError(
                        "stored resolution receipt does not match its transition"
                    )
                return stored.model_copy(
                    update={
                        "status": IncidentResolutionStatus.ALREADY_PROCESSED,
                    }
                )

            incident = session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.incident_id == incident_id,
                )
                .with_for_update()
            )
            if incident is None:
                raise IncidentNotFoundError(incident_id)
            if incident.current_state != IncidentState.RESPONSE_ACTIVE.value:
                raise IncidentConflictError(
                    code="incident_transition_not_allowed",
                    identifier=str(incident_id),
                    current_state=IncidentState(incident.current_state),
                )

            assignment = session.scalar(
                select(ResponderAssignmentRow)
                .where(
                    ResponderAssignmentRow.scope_id == self.scope_id,
                    ResponderAssignmentRow.incident_id == incident_id,
                )
                .with_for_update()
            )
            if assignment is None:
                raise PersistenceIntegrityError(
                    f"response-active incident has no assignment: {incident_id}"
                )
            if received_at < max(assignment.accepted_at, incident.updated_at):
                raise PersistenceIntegrityError(
                    "resolution server time precedes active incident history"
                )
            existing_revocation = session.scalar(
                select(ResponderAssignmentRevocationRow.revocation_id).where(
                    ResponderAssignmentRevocationRow.scope_id == self.scope_id,
                    ResponderAssignmentRevocationRow.assignment_id
                    == assignment.assignment_id,
                )
            )
            if existing_revocation is not None:
                raise PersistenceIntegrityError(
                    f"active assignment is already revoked: {assignment.assignment_id}"
                )

            from_state = IncidentState(incident.current_state)
            trigger = IncidentTrigger(request.action.value)
            to_state = next_incident_state(from_state, trigger)
            transition = IncidentStateTransitionRow(
                scope_id=self.scope_id,
                transition_id=_stable_id(
                    self.scope_id,
                    "resolution-transition",
                    request.resolution_id,
                ),
                schema_version=request.schema_version,
                incident_id=incident.incident_id,
                sequence=incident.state_version + 1,
                from_state=from_state.value,
                to_state=to_state.value,
                trigger=trigger.value,
                occurred_at=received_at,
                source_event_id=None,
                command_id=None,
                resolution_id=request.resolution_id,
                simulated=False,
            )

            incident.current_state = to_state.value
            incident.state_version += 1
            incident.updated_at = received_at
            incident.resolved_at = received_at
            accepted_receipt = IncidentResolutionReceipt(
                schema_version=request.schema_version,
                resolution_id=request.resolution_id,
                status=IncidentResolutionStatus.ACCEPTED,
                action=request.action,
                incident=self._incident_view(session, incident),
                transition=_transition_from_row(transition),
                server_received_at=received_at,
            )
            session.add(
                IncidentResolutionReceiptRow(
                    scope_id=self.scope_id,
                    resolution_id=request.resolution_id,
                    incident_id=incident.incident_id,
                    request_fingerprint=fingerprint,
                    schema_version=request.schema_version,
                    action=request.action.value,
                    server_received_at=received_at,
                    payload={
                        "request": request.model_dump(mode="json"),
                        "accepted_receipt": accepted_receipt.model_dump(mode="json"),
                    },
                    simulated=False,
                )
            )
            session.flush()
            session.add(transition)
            session.flush()
            self._append_timeline(
                session,
                incident=incident,
                sequence=incident.next_timeline_sequence,
                event_type=TimelineEventType.STATE_TRANSITIONED,
                occurred_at=received_at,
                state=to_state,
                transition_id=transition.transition_id,
                source_event_id=None,
                command_id=None,
                summary=_resolution_summary(request.action),
            )
            incident.next_timeline_sequence += 1
            session.add(
                ResponderAssignmentRevocationRow(
                    scope_id=self.scope_id,
                    revocation_id=_stable_id(
                        self.scope_id,
                        "assignment-revocation",
                        assignment.assignment_id,
                    ),
                    assignment_id=assignment.assignment_id,
                    incident_id=incident.incident_id,
                    responder_id=assignment.responder_id,
                    resolution_id=request.resolution_id,
                    transition_id=transition.transition_id,
                    reason=request.action.value,
                    revoked_at=received_at,
                    simulated=False,
                )
            )
            session.flush()
            return accepted_receipt

    def get(self, incident_id: UUID) -> IncidentView | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            incident = session.get(IncidentRow, (self.scope_id, incident_id))
            return (
                self._incident_view(session, incident)
                if incident is not None
                else None
            )

    def timeline(
        self,
        incident_id: UUID,
    ) -> tuple[IncidentTimelineEntry, ...] | None:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            incident = session.get(IncidentRow, (self.scope_id, incident_id))
            if incident is None:
                return None
            rows = session.scalars(
                select(IncidentTimelineEntryRow)
                .where(
                    IncidentTimelineEntryRow.scope_id == self.scope_id,
                    IncidentTimelineEntryRow.incident_id == incident_id,
                )
                .order_by(IncidentTimelineEntryRow.sequence)
            ).all()
            return tuple(_timeline_from_row(row) for row in rows)

    def process_due(
        self,
        *,
        as_of: datetime,
        limit: int = 100,
    ) -> int:
        normalized_as_of = _utc(as_of)
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            candidate_ids = session.scalars(
                select(IncidentDeadlineRow.deadline_id)
                .where(
                    IncidentDeadlineRow.scope_id == self.scope_id,
                    IncidentDeadlineRow.status == _PENDING,
                    IncidentDeadlineRow.due_at <= normalized_as_of,
                )
                .order_by(
                    IncidentDeadlineRow.due_at,
                    IncidentDeadlineRow.deadline_id,
                )
                .limit(limit)
            ).all()

        processed = 0
        for deadline_id in candidate_ids:
            if self._process_one_deadline(deadline_id, normalized_as_of):
                processed += 1
        return processed

    def _open_incident(
        self,
        session: Session,
        *,
        event: WearableEventRequest,
        fingerprint: str,
        received_at: datetime,
        verification_deadline_at: datetime | None,
    ) -> IncidentRow:
        incident_id = _stable_id(self.scope_id, "incident", event.event_id)
        snapshot_id = _stable_id(self.scope_id, "snapshot", incident_id)
        hold_id = _stable_id(self.scope_id, "snapshot-hold", incident_id)
        transition_id = _stable_id(
            self.scope_id,
            "event-transition",
            event.event_id,
        )
        initial_state = (
            IncidentState.VERIFYING
            if event.event_type is WearableEventType.FALL_DETECTED
            else IncidentState.ESCALATING
        )
        trigger = (
            IncidentTrigger.FALL_DETECTED
            if event.event_type is WearableEventType.FALL_DETECTED
            else IncidentTrigger.MANUAL_SOS
        )
        kind = (
            IncidentKind.FALL
            if event.event_type is WearableEventType.FALL_DETECTED
            else IncidentKind.MANUAL_SOS
        )

        snapshot_service = HealthSnapshotService(
            metric_repository=PostgresHealthMetricRepository(
                self._sessions,
                self.scope_id,
                session=session,
            ),
            capability_repository=PostgresHealthCapabilityRepository(
                self._sessions,
                self.scope_id,
                session=session,
            ),
            snapshot_repository=PostgresHealthSnapshotRepository(
                self._sessions,
                self.scope_id,
                session=session,
            ),
            clock=_FixedClock(received_at),
        )
        snapshot_service.create(
            HealthSnapshotCreateRequest(
                schema_version=event.schema_version,
                snapshot_id=snapshot_id,
                user_id=event.user_id,
                capture_reason=SnapshotCaptureReason.INCIDENT_CREATED,
            )
        )
        session.add(
            HealthSnapshotHoldRow(
                scope_id=self.scope_id,
                hold_id=hold_id,
                snapshot_id=snapshot_id,
                reason="incident",
                reference_id=str(incident_id),
                created_at=received_at,
                notes="Retain health context while the incident audit is retained.",
            )
        )
        session.flush()

        event_row = _event_row(
            scope_id=self.scope_id,
            incident_id=incident_id,
            event=event,
            fingerprint=fingerprint,
            received_at=received_at,
        )
        timeline_count = 3 if initial_state is IncidentState.VERIFYING else 2
        incident = IncidentRow(
            scope_id=self.scope_id,
            incident_id=incident_id,
            schema_version=event.schema_version,
            user_id=event.user_id,
            kind=kind.value,
            current_state=initial_state.value,
            trigger_event_id=event.event_id,
            health_snapshot_id=snapshot_id,
            health_snapshot_hold_id=hold_id,
            simulated=False,
            state_version=1,
            next_timeline_sequence=timeline_count + 1,
            latitude=event.location.latitude,
            longitude=event.location.longitude,
            horizontal_accuracy_m=event.location.horizontal_accuracy_m,
            location_captured_at=event.location.captured_at,
            opened_at=received_at,
            updated_at=received_at,
            resolved_at=None,
        )
        session.add_all((event_row, incident))
        # Both sides of the event/incident cycle are deferred, so this flush
        # creates the durable owners before their immediate audit references.
        session.flush()

        transition = IncidentStateTransitionRow(
            scope_id=self.scope_id,
            transition_id=transition_id,
            schema_version=event.schema_version,
            incident_id=incident_id,
            sequence=1,
            from_state=IncidentState.MONITORING.value,
            to_state=initial_state.value,
            trigger=trigger.value,
            occurred_at=received_at,
            source_event_id=event.event_id,
            command_id=None,
            simulated=False,
        )
        session.add(transition)
        session.flush()

        if verification_deadline_at is not None:
            session.add(
                IncidentDeadlineRow(
                    scope_id=self.scope_id,
                    deadline_id=_stable_id(
                        self.scope_id,
                        "verification-deadline",
                        incident_id,
                    ),
                    incident_id=incident_id,
                    kind=_VERIFICATION_DEADLINE_KIND,
                    due_at=verification_deadline_at,
                    status=_PENDING,
                    created_at=received_at,
                    settled_at=None,
                    settled_transition_id=None,
                )
            )

        self._append_timeline(
            session,
            incident=incident,
            sequence=1,
            event_type=TimelineEventType.WEARABLE_EVENT_RECEIVED,
            occurred_at=received_at,
            state=initial_state,
            transition_id=None,
            source_event_id=event.event_id,
            command_id=None,
            summary=(
                "Authenticated Apple fall event received."
                if event.source is WearableEventSource.APPLE_FALL
                else "Authenticated manual SOS event received."
            ),
        )
        self._append_timeline(
            session,
            incident=incident,
            sequence=2,
            event_type=TimelineEventType.INCIDENT_OPENED,
            occurred_at=received_at,
            state=initial_state,
            transition_id=transition_id,
            source_event_id=event.event_id,
            command_id=None,
            summary=(
                "Fall incident opened for wearer verification."
                if initial_state is IncidentState.VERIFYING
                else "Manual SOS incident opened for escalation."
            ),
        )
        if initial_state is IncidentState.VERIFYING:
            self._append_timeline(
                session,
                incident=incident,
                sequence=3,
                event_type=TimelineEventType.VERIFICATION_STARTED,
                occurred_at=received_at,
                state=initial_state,
                transition_id=transition_id,
                source_event_id=event.event_id,
                command_id=None,
                summary="Wearer check-in window started.",
            )
        return incident

    def _attach_to_active_incident(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        event: WearableEventRequest,
        fingerprint: str,
        received_at: datetime,
    ) -> IncidentRow:
        session.add(
            _event_row(
                scope_id=self.scope_id,
                incident_id=incident.incident_id,
                event=event,
                fingerprint=fingerprint,
                received_at=received_at,
            )
        )
        session.flush()

        incident.latitude = event.location.latitude
        incident.longitude = event.location.longitude
        incident.horizontal_accuracy_m = event.location.horizontal_accuracy_m
        incident.location_captured_at = event.location.captured_at
        incident.updated_at = received_at

        transition: IncidentStateTransitionRow | None = None
        if (
            event.event_type is WearableEventType.MANUAL_SOS
            and incident.current_state == IncidentState.VERIFYING.value
        ):
            from_state = IncidentState(incident.current_state)
            to_state = next_incident_state(from_state, IncidentTrigger.MANUAL_SOS)
            transition = IncidentStateTransitionRow(
                scope_id=self.scope_id,
                transition_id=_stable_id(
                    self.scope_id,
                    "event-transition",
                    event.event_id,
                ),
                schema_version=event.schema_version,
                incident_id=incident.incident_id,
                sequence=incident.state_version + 1,
                from_state=from_state.value,
                to_state=to_state.value,
                trigger=IncidentTrigger.MANUAL_SOS.value,
                occurred_at=received_at,
                source_event_id=event.event_id,
                command_id=None,
                simulated=False,
            )
            session.add(transition)
            session.flush()
            incident.current_state = to_state.value
            incident.state_version += 1
            deadline = self._deadline_for_incident(
                session,
                incident.incident_id,
                lock=True,
            )
            if deadline is None or deadline.status != _PENDING:
                raise PersistenceIntegrityError(
                    "verifying incident is missing its pending deadline"
                )
            deadline.status = _CANCELLED
            deadline.settled_at = received_at
            deadline.settled_transition_id = transition.transition_id

        start_sequence = incident.next_timeline_sequence
        current_state = IncidentState(incident.current_state)
        self._append_timeline(
            session,
            incident=incident,
            sequence=start_sequence,
            event_type=TimelineEventType.WEARABLE_EVENT_RECEIVED,
            occurred_at=received_at,
            state=current_state,
            transition_id=None,
            source_event_id=event.event_id,
            command_id=None,
            summary=(
                "Authenticated Apple fall event attached to the active incident."
                if event.source is WearableEventSource.APPLE_FALL
                else "Authenticated manual SOS event attached to the active incident."
            ),
        )
        incident.next_timeline_sequence += 1
        if transition is not None:
            self._append_timeline(
                session,
                incident=incident,
                sequence=incident.next_timeline_sequence,
                event_type=TimelineEventType.STATE_TRANSITIONED,
                occurred_at=received_at,
                state=current_state,
                transition_id=transition.transition_id,
                source_event_id=event.event_id,
                command_id=None,
                summary="Manual SOS advanced fall verification to escalation.",
            )
            incident.next_timeline_sequence += 1
        return incident

    def _record_check_in(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        deadline: IncidentDeadlineRow | None,
        request: CheckInRequest,
        fingerprint: str,
        received_at: datetime,
    ) -> CheckInResult:
        if deadline is None or deadline.status != _PENDING:
            raise PersistenceIntegrityError(
                "verifying incident is missing its pending deadline"
            )
        from_state = IncidentState(incident.current_state)
        trigger = trigger_for_check_in(request.response)
        to_state = next_incident_state(from_state, trigger)
        transition = IncidentStateTransitionRow(
            scope_id=self.scope_id,
            transition_id=_stable_id(
                self.scope_id,
                "check-in-transition",
                request.response_id,
            ),
            schema_version=request.schema_version,
            incident_id=incident.incident_id,
            sequence=incident.state_version + 1,
            from_state=from_state.value,
            to_state=to_state.value,
            trigger=trigger.value,
            occurred_at=received_at,
            source_event_id=None,
            command_id=request.response_id,
            simulated=False,
        )
        accepted_result = CheckInResult(
            schema_version=request.schema_version,
            response_id=request.response_id,
            status=CheckInStatus.ACCEPTED,
            incident=IncidentView(
                schema_version=incident.schema_version,
                incident_id=incident.incident_id,
                user_id=incident.user_id,
                kind=incident.kind,
                state=to_state,
                trigger_event_id=incident.trigger_event_id,
                location=GeoLocation(
                    latitude=incident.latitude,
                    longitude=incident.longitude,
                    horizontal_accuracy_m=incident.horizontal_accuracy_m,
                    captured_at=incident.location_captured_at,
                ),
                simulated=False,
                health_snapshot_id=incident.health_snapshot_id,
                opened_at=incident.opened_at,
                updated_at=received_at,
                verification_expires_at=deadline.due_at,
                resolved_at=(
                    received_at if to_state is IncidentState.RESOLVED else None
                ),
                state_version=incident.state_version + 1,
            ),
            transition=_transition_from_row(transition),
            server_received_at=received_at,
        )
        command = IncidentCommandRow(
            scope_id=self.scope_id,
            command_id=request.response_id,
            incident_id=incident.incident_id,
            request_fingerprint=fingerprint,
            schema_version=request.schema_version,
            command_type=request.response.value,
            device_id=request.device_id,
            responded_at=request.responded_at,
            server_received_at=received_at,
            payload={
                "request": request.model_dump(mode="json"),
                "accepted_result": accepted_result.model_dump(mode="json"),
            },
        )
        session.add(command)
        session.flush()
        session.add(transition)
        session.flush()

        incident.current_state = to_state.value
        incident.state_version += 1
        incident.updated_at = received_at
        incident.resolved_at = (
            received_at if to_state is IncidentState.RESOLVED else None
        )
        deadline.status = _CANCELLED
        deadline.settled_at = received_at
        deadline.settled_transition_id = transition.transition_id
        self._append_timeline(
            session,
            incident=incident,
            sequence=incident.next_timeline_sequence,
            event_type=TimelineEventType.CHECK_IN_RECORDED,
            occurred_at=received_at,
            state=to_state,
            transition_id=transition.transition_id,
            source_event_id=None,
            command_id=request.response_id,
            summary=(
                "Wearer reported they are okay; incident resolved."
                if to_state is IncidentState.RESOLVED
                else "Wearer requested help; incident advanced to escalation."
            ),
        )
        incident.next_timeline_sequence += 1
        session.flush()
        return accepted_result

    def _process_one_deadline(self, deadline_id: UUID, as_of: datetime) -> bool:
        with self._transaction(isolation_level="READ COMMITTED") as session:
            require_active_scope(session, self.scope_id, lock=True)
            incident_id = session.scalar(
                select(IncidentDeadlineRow.incident_id).where(
                    IncidentDeadlineRow.scope_id == self.scope_id,
                    IncidentDeadlineRow.deadline_id == deadline_id,
                )
            )
            if incident_id is None:
                return False
            incident = session.scalar(
                select(IncidentRow)
                .where(
                    IncidentRow.scope_id == self.scope_id,
                    IncidentRow.incident_id == incident_id,
                )
                .with_for_update()
            )
            if incident is None:
                raise PersistenceIntegrityError(
                    f"deadline has no incident: {deadline_id}"
                )
            deadline = session.scalar(
                select(IncidentDeadlineRow)
                .where(
                    IncidentDeadlineRow.scope_id == self.scope_id,
                    IncidentDeadlineRow.deadline_id == deadline_id,
                )
                .with_for_update()
            )
            if (
                deadline is None
                or deadline.status != _PENDING
                or deadline.due_at > as_of
            ):
                return False
            if incident.current_state != IncidentState.VERIFYING.value:
                raise PersistenceIntegrityError(
                    "pending verification deadline belongs to a non-verifying incident"
                )
            self._fire_locked_deadline(
                session,
                incident=incident,
                deadline=deadline,
                occurred_at=as_of,
            )
            return True

    def _fire_locked_deadline(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        deadline: IncidentDeadlineRow,
        occurred_at: datetime,
    ) -> IncidentStateTransitionRow:
        from_state = IncidentState(incident.current_state)
        to_state = next_incident_state(
            from_state,
            IncidentTrigger.VERIFICATION_TIMEOUT,
        )
        transition = IncidentStateTransitionRow(
            scope_id=self.scope_id,
            transition_id=_stable_id(
                self.scope_id,
                "deadline-transition",
                deadline.deadline_id,
            ),
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            sequence=incident.state_version + 1,
            from_state=from_state.value,
            to_state=to_state.value,
            trigger=IncidentTrigger.VERIFICATION_TIMEOUT.value,
            occurred_at=occurred_at,
            source_event_id=None,
            command_id=None,
            simulated=False,
        )
        session.add(transition)
        session.flush()
        incident.current_state = to_state.value
        incident.state_version += 1
        incident.updated_at = occurred_at
        deadline.status = _FIRED
        deadline.settled_at = occurred_at
        deadline.settled_transition_id = transition.transition_id
        self._append_timeline(
            session,
            incident=incident,
            sequence=incident.next_timeline_sequence,
            event_type=TimelineEventType.VERIFICATION_TIMED_OUT,
            occurred_at=occurred_at,
            state=to_state,
            transition_id=transition.transition_id,
            source_event_id=None,
            command_id=None,
            summary="Wearer check-in timed out; incident advanced to escalation.",
        )
        incident.next_timeline_sequence += 1
        session.flush()
        return transition

    def _apple_natural_duplicate(
        self,
        session: Session,
        event: WearableEventRequest,
    ) -> WearableEventRow | None:
        if event.source is not WearableEventSource.APPLE_FALL:
            return None
        return session.scalar(
            select(WearableEventRow).where(
                WearableEventRow.scope_id == self.scope_id,
                WearableEventRow.user_id == event.user_id,
                WearableEventRow.source == WearableEventSource.APPLE_FALL.value,
                WearableEventRow.device_id == event.device_id,
                WearableEventRow.observed_at == event.observed_at,
            )
        )

    def _event_result(
        self,
        session: Session,
        *,
        submitted_event_id: UUID,
        canonical: WearableEventRow,
        status: EventIngestionStatus,
    ) -> WearableEventResult:
        incident = session.get(
            IncidentRow,
            (self.scope_id, canonical.incident_id),
        )
        if incident is None:
            raise PersistenceIntegrityError(
                f"wearable event has no incident: {canonical.event_id}"
            )
        return WearableEventResult(
            schema_version=canonical.schema_version,
            event_id=submitted_event_id,
            canonical_event_id=canonical.event_id,
            status=status,
            incident=self._incident_view(session, incident),
            server_received_at=canonical.server_received_at,
        )

    def _incident_view(
        self,
        session: Session,
        incident: IncidentRow,
    ) -> IncidentView:
        deadline = self._deadline_for_incident(
            session,
            incident.incident_id,
            lock=False,
        )
        if incident.kind == IncidentKind.FALL.value and deadline is None:
            raise PersistenceIntegrityError(
                f"fall incident has no verification deadline: {incident.incident_id}"
            )
        return IncidentView(
            schema_version=incident.schema_version,
            incident_id=incident.incident_id,
            user_id=incident.user_id,
            kind=incident.kind,
            state=incident.current_state,
            trigger_event_id=incident.trigger_event_id,
            location=GeoLocation(
                latitude=incident.latitude,
                longitude=incident.longitude,
                horizontal_accuracy_m=incident.horizontal_accuracy_m,
                captured_at=incident.location_captured_at,
            ),
            simulated=False,
            health_snapshot_id=incident.health_snapshot_id,
            opened_at=incident.opened_at,
            updated_at=incident.updated_at,
            verification_expires_at=deadline.due_at if deadline else None,
            resolved_at=incident.resolved_at,
            state_version=incident.state_version,
        )

    def _deadline_for_incident(
        self,
        session: Session,
        incident_id: UUID,
        *,
        lock: bool,
    ) -> IncidentDeadlineRow | None:
        statement = select(IncidentDeadlineRow).where(
            IncidentDeadlineRow.scope_id == self.scope_id,
            IncidentDeadlineRow.incident_id == incident_id,
            IncidentDeadlineRow.kind == _VERIFICATION_DEADLINE_KIND,
        )
        if lock:
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _append_timeline(
        self,
        session: Session,
        *,
        incident: IncidentRow,
        sequence: int,
        event_type: TimelineEventType,
        occurred_at: datetime,
        state: IncidentState,
        transition_id: UUID | None,
        source_event_id: UUID | None,
        command_id: UUID | None,
        summary: str,
    ) -> None:
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
                source_event_id=source_event_id,
                command_id=command_id,
                summary=summary,
                simulated=False,
            )
        )

    @contextmanager
    def _transaction(
        self,
        *,
        isolation_level: str = "REPEATABLE READ",
    ) -> Iterator[Session]:
        connection = self._engine.connect().execution_options(
            isolation_level=isolation_level
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


def _event_row(
    *,
    scope_id: UUID,
    incident_id: UUID,
    event: WearableEventRequest,
    fingerprint: str,
    received_at: datetime,
) -> WearableEventRow:
    return WearableEventRow(
        scope_id=scope_id,
        event_id=event.event_id,
        request_fingerprint=fingerprint,
        schema_version=event.schema_version,
        user_id=event.user_id,
        event_type=event.event_type.value,
        source=event.source.value,
        simulated=False,
        observed_at=event.observed_at,
        server_received_at=received_at,
        device_id=event.device_id,
        sequence=event.sequence,
        payload=event.payload.model_dump(mode="json"),
        latitude=event.location.latitude,
        longitude=event.location.longitude,
        horizontal_accuracy_m=event.location.horizontal_accuracy_m,
        location_captured_at=event.location.captured_at,
        incident_id=incident_id,
    )


def _transition_from_row(row: IncidentStateTransitionRow) -> IncidentTransition:
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


def _timeline_from_row(row: IncidentTimelineEntryRow) -> IncidentTimelineEntry:
    return IncidentTimelineEntry(
        schema_version=row.schema_version,
        timeline_id=row.timeline_id,
        incident_id=row.incident_id,
        sequence=row.sequence,
        event_type=row.event_type,
        occurred_at=row.occurred_at,
        state=row.state,
        transition_id=row.transition_id,
        source_event_id=row.source_event_id,
        check_in_id=row.command_id,
        summary=row.summary,
        simulated=False,
    )


def _stored_check_in_result(row: IncidentCommandRow) -> CheckInResult:
    payload = row.payload.get("accepted_result")
    if not isinstance(payload, dict):
        raise PersistenceIntegrityError(
            f"stored check-in has no accepted result: {row.command_id}"
        )
    try:
        result = CheckInResult.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise PersistenceIntegrityError(
            f"stored check-in result is invalid: {row.command_id}"
        ) from exc
    if (
        result.response_id != row.command_id
        or result.incident.incident_id != row.incident_id
        or result.server_received_at != row.server_received_at
    ):
        raise PersistenceIntegrityError(
            f"stored check-in result does not match its receipt: {row.command_id}"
        )
    return result


def _stored_resolution_receipt(
    row: IncidentResolutionReceiptRow,
) -> IncidentResolutionReceipt:
    payload = row.payload.get("accepted_receipt")
    if not isinstance(payload, dict):
        raise PersistenceIntegrityError(
            f"stored resolution has no accepted receipt: {row.resolution_id}"
        )
    try:
        receipt = IncidentResolutionReceipt.model_validate(payload)
    except (TypeError, ValueError) as exc:
        raise PersistenceIntegrityError(
            f"stored resolution receipt is invalid: {row.resolution_id}"
        ) from exc
    if (
        receipt.resolution_id != row.resolution_id
        or receipt.incident.incident_id != row.incident_id
        or receipt.action.value != row.action
        or receipt.server_received_at != row.server_received_at
        or receipt.status is not IncidentResolutionStatus.ACCEPTED
    ):
        raise PersistenceIntegrityError(
            f"stored resolution receipt does not match its row: {row.resolution_id}"
        )
    return receipt


def _resolution_summary(action: ResolutionAction) -> str:
    if action is ResolutionAction.CLOSE:
        return "Command closed the active response; responder access revoked."
    return "Command completed handoff; responder access revoked."


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


def _utc(value: datetime | None) -> datetime:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("incident timestamps must be timezone-aware")
    return value.astimezone(UTC)
