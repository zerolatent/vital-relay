"""PostgreSQL adapter for immutable evolution releases and atomic activation."""

from __future__ import annotations

from datetime import UTC, datetime
from secrets import compare_digest
from typing import Callable
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, sessionmaker

from vital_relay.application.evolution_promotion import (
    ActiveEvolutionVersion,
    ArchivedCandidateVersion,
    CandidateVersionDetail,
    CandidateVersionSummary,
    CanonicalArtifactBytes,
    CanonicalPairedACERelease,
    CommandApprovalRecord,
    EvolutionConflict,
    EvolutionIntegrityError,
    EvolutionPointerNotFound,
    EvolutionTransitionCommand,
    EvolutionTransitionResult,
    EvolutionVersionNotFound,
    PromotionTarget,
)
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.evolution.contracts import PromotionAction, PromotionEvidence
from vital_relay.persistence.database import require_active_scope
from vital_relay.persistence.models import (
    AgentActivePolicyRow,
    EvolutionActiveVersionRow,
    EvolutionCandidateVersionRow,
    EvolutionCommandApprovalRow,
    EvolutionPromotionEventRow,
    PersonaAccountRow,
    PersonaSessionRow,
)


class PostgresEvolutionPromotionRepository:
    """Durable scope adapter; each transition is one database transaction."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        scope_id: UUID,
    ) -> None:
        self._sessions = sessions
        self.scope_id = scope_id

    def archive(self, release: ArchivedCandidateVersion) -> CandidateVersionDetail:
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {
                    "key": (
                        f"evolution-release:{self.scope_id}:"
                        f"{release.target.target_sha256}"
                    )
                },
            )
            existing = session.get(
                EvolutionCandidateVersionRow,
                (self.scope_id, release.target.target_sha256),
            )
            if existing is not None:
                persisted = _release(existing)
                if not _same_release_material(persisted, release):
                    raise EvolutionIntegrityError("evolution_version_digest_conflict")
                return persisted.detail
            row = _release_row(self.scope_id, release)
            session.add(row)
            session.flush()
            return release.detail

    def list_versions(
        self,
        *,
        limit: int,
    ) -> tuple[CandidateVersionSummary, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            rows = session.scalars(
                select(EvolutionCandidateVersionRow)
                .where(EvolutionCandidateVersionRow.scope_id == self.scope_id)
                .order_by(
                    EvolutionCandidateVersionRow.archived_at.desc(),
                    EvolutionCandidateVersionRow.target_sha256.desc(),
                )
                .limit(limit)
            ).all()
            return tuple(_release(row).summary for row in rows)

    def get_version(self, version_sha256: str) -> CandidateVersionDetail:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            return _release(self._version_row(session, version_sha256)).detail

    def load_release(self, version_sha256: str) -> ArchivedCandidateVersion:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            return _release(self._version_row(session, version_sha256))

    def get_active(self) -> ActiveEvolutionVersion:
        with self._sessions() as session:
            require_active_scope(session, self.scope_id)
            row = session.get(EvolutionActiveVersionRow, self.scope_id)
            if row is None:
                raise EvolutionPointerNotFound()
            return _active(row)

    def initialize_active(
        self,
        version_sha256: str,
        *,
        principal: PersonaPrincipal,
        activated_at: datetime,
        verify_release: Callable[[ArchivedCandidateVersion], None],
    ) -> ActiveEvolutionVersion:
        now = _utc(activated_at, "activated_at")
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            _require_active_command_principal(
                session,
                scope_id=self.scope_id,
                principal=principal,
                as_of=now,
            )
            release = _release(
                self._version_row(session, version_sha256, lock=True)
            )
            verify_release(release)
            version = release.target
            existing = session.get(EvolutionActiveVersionRow, self.scope_id)
            if existing is not None:
                if existing.active_version_sha256 == version_sha256:
                    return _active(existing)
                raise EvolutionConflict("active_evolution_version_already_initialized")
            policy = session.get(AgentActivePolicyRow, self.scope_id)
            if policy is None:
                raise EvolutionConflict("active_agent_policy_missing")
            if not compare_digest(policy.policy_sha256, version.policy.sha256):
                raise EvolutionConflict("active_agent_policy_mismatch")
            row = EvolutionActiveVersionRow(
                scope_id=self.scope_id,
                active_version_sha256=version.target_sha256,
                active_candidate_sha256=version.candidate_sha256,
                previous_version_sha256=None,
                previous_candidate_sha256=None,
                revision=0,
                activated_at=now,
                activated_by_account_id=principal.account_id,
                activated_by_session_id=principal.session_id,
            )
            session.add(row)
            session.flush()
            return _active(row)

    def transition(
        self,
        action: PromotionAction,
        command: EvolutionTransitionCommand,
        *,
        principal: PersonaPrincipal,
        occurred_at: datetime,
        verify_release: Callable[[ArchivedCandidateVersion], None],
    ) -> EvolutionTransitionResult:
        now = _utc(occurred_at, "occurred_at")
        with self._sessions.begin() as session:
            require_active_scope(session, self.scope_id, lock=True)
            _require_active_command_principal(
                session,
                scope_id=self.scope_id,
                principal=principal,
                as_of=now,
            )
            existing = session.get(
                EvolutionCommandApprovalRow,
                (self.scope_id, command.approval_id),
            )
            if existing is not None:
                result = _transition_result(session, existing, replayed=True)
                if not result.approval.matches(
                    action=action,
                    command=command,
                    principal=principal,
                ):
                    raise EvolutionConflict("command_approval_already_consumed")
                return result

            current = session.get(EvolutionActiveVersionRow, self.scope_id)
            if current is None:
                raise EvolutionPointerNotFound()
            if current.revision != command.expected_pointer_revision:
                raise EvolutionConflict("stale_pointer_revision")

            target_release = _release(
                self._version_row(
                    session,
                    command.target_version_sha256,
                    lock=True,
                )
            )
            source_release = _release(
                self._version_row(
                    session,
                    current.active_version_sha256,
                    lock=True,
                )
            )
            verify_release(target_release)
            target = target_release.target
            source = source_release.target
            if action is PromotionAction.PROMOTE:
                if (
                    target.active_baseline_version_sha256
                    != current.active_version_sha256
                    or target.active_baseline_candidate_sha256
                    != current.active_candidate_sha256
                    or target.active_baseline_policy != source.policy
                    or target.active_baseline_playbook_sha256
                    != source.playbook_sha256
                ):
                    raise EvolutionConflict("promotion_baseline_mismatch")
                evidence_sha256: str | None = target.promotion_evidence_sha256
            else:
                if current.previous_version_sha256 is None:
                    raise EvolutionConflict("rollback_version_unavailable")
                if target.target_sha256 != current.previous_version_sha256:
                    raise EvolutionConflict("rollback_target_mismatch")
                evidence_sha256 = None

            if target.target_sha256 == current.active_version_sha256:
                raise EvolutionConflict("evolution_version_already_active")

            policy = session.scalar(
                select(AgentActivePolicyRow)
                .where(AgentActivePolicyRow.scope_id == self.scope_id)
                .with_for_update()
            )
            if policy is None:
                raise EvolutionConflict("active_agent_policy_missing")
            replay = session.get(
                EvolutionCommandApprovalRow,
                (self.scope_id, command.approval_id),
            )
            if replay is not None:
                result = _transition_result(session, replay, replayed=True)
                if result.approval.matches(
                    action=action,
                    command=command,
                    principal=principal,
                ):
                    return result
                raise EvolutionConflict("command_approval_already_consumed")
            latest_revision = session.scalar(
                select(EvolutionActiveVersionRow.revision).where(
                    EvolutionActiveVersionRow.scope_id == self.scope_id
                )
            )
            if latest_revision != command.expected_pointer_revision:
                raise EvolutionConflict("stale_pointer_revision")
            if not compare_digest(policy.policy_sha256, source.policy.sha256):
                raise EvolutionConflict("active_agent_policy_mismatch")

            cas = session.execute(
                update(EvolutionActiveVersionRow)
                .where(
                    EvolutionActiveVersionRow.scope_id == self.scope_id,
                    EvolutionActiveVersionRow.revision
                    == command.expected_pointer_revision,
                    EvolutionActiveVersionRow.active_version_sha256
                    == current.active_version_sha256,
                )
                .values(
                    active_version_sha256=target.target_sha256,
                    active_candidate_sha256=target.candidate_sha256,
                    previous_version_sha256=current.active_version_sha256,
                    previous_candidate_sha256=current.active_candidate_sha256,
                    revision=command.expected_pointer_revision + 1,
                    activated_at=now,
                    activated_by_account_id=principal.account_id,
                    activated_by_session_id=principal.session_id,
                )
                .execution_options(synchronize_session=False)
            )
            if cas.rowcount != 1:
                replay = session.get(
                    EvolutionCommandApprovalRow,
                    (self.scope_id, command.approval_id),
                )
                if replay is not None:
                    result = _transition_result(session, replay, replayed=True)
                    if result.approval.matches(
                        action=action,
                        command=command,
                        principal=principal,
                    ):
                        return result
                raise EvolutionConflict("stale_pointer_revision")

            if not compare_digest(policy.policy_sha256, target.policy.sha256):
                if now < policy.activated_at:
                    raise EvolutionConflict("activation_time_regressed")
                policy.policy_id = target.policy.policy_id
                policy.policy_version = target.policy.version
                policy.policy_sha256 = target.policy.sha256
                policy.revision += 1
                policy.activated_at = now
                policy.activated_by_account_id = principal.account_id

            approval = EvolutionCommandApprovalRow(
                scope_id=self.scope_id,
                approval_id=command.approval_id,
                action=action.value,
                target_version_sha256=target.target_sha256,
                evidence_sha256=evidence_sha256,
                expected_pointer_revision=command.expected_pointer_revision,
                account_id=principal.account_id,
                session_id=principal.session_id,
                approved_at=now,
                consumed_at=now,
                resulting_pointer_revision=command.expected_pointer_revision + 1,
            )
            session.add(approval)
            session.flush()
            event = EvolutionPromotionEventRow(
                scope_id=self.scope_id,
                event_id=uuid4(),
                approval_id=command.approval_id,
                action=action.value,
                from_version_sha256=current.active_version_sha256,
                to_version_sha256=target.target_sha256,
                from_candidate_sha256=current.active_candidate_sha256,
                to_candidate_sha256=target.candidate_sha256,
                evidence_sha256=evidence_sha256,
                pointer_revision=command.expected_pointer_revision + 1,
                account_id=principal.account_id,
                session_id=principal.session_id,
                occurred_at=now,
            )
            session.add(event)
            session.flush()
            return _transition_result(session, approval, replayed=False)

    def _version_row(
        self,
        session: Session,
        version_sha256: str,
        *,
        lock: bool = False,
    ) -> EvolutionCandidateVersionRow:
        statement = select(EvolutionCandidateVersionRow).where(
            EvolutionCandidateVersionRow.scope_id == self.scope_id,
            EvolutionCandidateVersionRow.target_sha256 == version_sha256,
        )
        if lock:
            statement = statement.with_for_update(read=True)
        row = session.scalar(statement)
        if row is None:
            raise EvolutionVersionNotFound()
        return row


def _release_row(
    scope_id: UUID,
    release: ArchivedCandidateVersion,
) -> EvolutionCandidateVersionRow:
    target = release.target
    return EvolutionCandidateVersionRow(
        scope_id=scope_id,
        target_sha256=target.target_sha256,
        release_kind=target.release_kind.value,
        candidate_bundle_sha256=target.candidate_bundle_sha256,
        candidate_sha256=target.candidate_sha256,
        policy_id=target.policy.policy_id,
        policy_version=target.policy.version,
        policy_sha256=target.policy.sha256,
        playbook_sha256=target.playbook_sha256,
        delta_log_sha256=target.delta_log_sha256,
        improver_sha256=target.improver.sha256,
        generator_role_sha256=target.generator_role.role_sha256,
        generator_model_sha256=target.generator_model.model_sha256,
        promotion_evidence_sha256=target.promotion_evidence_sha256,
        development_report_sha256=target.development_report_sha256,
        development_report_signature_sha256=(
            target.development_report_signature_sha256
        ),
        protected_validation_report_sha256=(
            target.protected_validation_report_sha256
        ),
        protected_validation_report_signature_sha256=(
            target.protected_validation_report_signature_sha256
        ),
        final_report_sha256=target.final_report_sha256,
        final_cadence_receipt_sha256=target.final_cadence_receipt_sha256,
        final_cadence_artifact_sha256=target.final_cadence_artifact_sha256,
        paired_report_sha256=target.paired_report_sha256,
        paired_report_artifact_sha256=target.paired_report_artifact_sha256,
        ace_release_sha256=target.ace_release_sha256,
        ace_release_artifact_sha256=target.ace_release_artifact_sha256,
        active_baseline_version_sha256=target.active_baseline_version_sha256,
        active_baseline_candidate_sha256=(
            target.active_baseline_candidate_sha256
        ),
        active_baseline_policy_id=target.active_baseline_policy.policy_id,
        active_baseline_policy_version=target.active_baseline_policy.version,
        active_baseline_policy_sha256=target.active_baseline_policy.sha256,
        active_baseline_playbook_sha256=(
            target.active_baseline_playbook_sha256
        ),
        target_canonical_bytes=release.target_canonical_bytes,
        evidence_canonical_bytes=release.evidence_canonical_bytes,
        ace_release_canonical_bytes=(
            release.paired_ace_release.release_artifact.canonical_bytes
        ),
        paired_report_canonical_bytes=(
            release.paired_ace_release.paired_report.canonical_bytes
        ),
        final_cadence_canonical_bytes=(
            release.final_cadence_evidence.canonical_bytes
        ),
        archived_at=release.archived_at,
    )


def _same_release_material(
    first: ArchivedCandidateVersion,
    second: ArchivedCandidateVersion,
) -> bool:
    return (
        first.target_canonical_bytes == second.target_canonical_bytes
        and first.evidence_canonical_bytes == second.evidence_canonical_bytes
        and first.paired_ace_release == second.paired_ace_release
        and first.final_cadence_evidence == second.final_cadence_evidence
    )


def _release(row: EvolutionCandidateVersionRow) -> ArchivedCandidateVersion:
    try:
        target = PromotionTarget.model_validate_json(row.target_canonical_bytes)
        evidence = (
            PromotionEvidence.model_validate_json(row.evidence_canonical_bytes)
            if row.evidence_canonical_bytes is not None
            else None
        )
        paired_ace_release = CanonicalPairedACERelease(
            release_artifact=CanonicalArtifactBytes(
                artifact_sha256=row.ace_release_artifact_sha256,
                canonical_bytes=row.ace_release_canonical_bytes,
            ),
            paired_report=CanonicalArtifactBytes(
                artifact_sha256=row.paired_report_artifact_sha256,
                canonical_bytes=row.paired_report_canonical_bytes,
            ),
        )
        final_cadence_evidence = CanonicalArtifactBytes(
            artifact_sha256=row.final_cadence_artifact_sha256,
            canonical_bytes=row.final_cadence_canonical_bytes,
        )
        release = ArchivedCandidateVersion(
            target=target,
            target_canonical_bytes=row.target_canonical_bytes,
            evidence=evidence,
            evidence_canonical_bytes=row.evidence_canonical_bytes,
            paired_ace_release=paired_ace_release,
            final_cadence_evidence=final_cadence_evidence,
            archived_at=row.archived_at,
        )
    except (ValidationError, ValueError) as exc:
        raise EvolutionIntegrityError("archived_release_integrity_failed") from exc
    expected = (
        row.target_sha256,
        row.release_kind,
        row.candidate_bundle_sha256,
        row.candidate_sha256,
        row.policy_id,
        row.policy_version,
        row.policy_sha256,
        row.playbook_sha256,
        row.delta_log_sha256,
        row.improver_sha256,
        row.generator_role_sha256,
        row.generator_model_sha256,
        row.promotion_evidence_sha256,
        row.development_report_sha256,
        row.development_report_signature_sha256,
        row.protected_validation_report_sha256,
        row.protected_validation_report_signature_sha256,
        row.final_report_sha256,
        row.final_cadence_receipt_sha256,
        row.final_cadence_artifact_sha256,
        row.paired_report_sha256,
        row.paired_report_artifact_sha256,
        row.ace_release_sha256,
        row.ace_release_artifact_sha256,
        row.active_baseline_version_sha256,
        row.active_baseline_candidate_sha256,
        row.active_baseline_policy_id,
        row.active_baseline_policy_version,
        row.active_baseline_policy_sha256,
        row.active_baseline_playbook_sha256,
    )
    actual = (
        target.target_sha256,
        target.release_kind.value,
        target.candidate_bundle_sha256,
        target.candidate_sha256,
        target.policy.policy_id,
        target.policy.version,
        target.policy.sha256,
        target.playbook_sha256,
        target.delta_log_sha256,
        target.improver.sha256,
        target.generator_role.role_sha256,
        target.generator_model.model_sha256,
        target.promotion_evidence_sha256,
        target.development_report_sha256,
        target.development_report_signature_sha256,
        target.protected_validation_report_sha256,
        target.protected_validation_report_signature_sha256,
        target.final_report_sha256,
        target.final_cadence_receipt_sha256,
        target.final_cadence_artifact_sha256,
        target.paired_report_sha256,
        target.paired_report_artifact_sha256,
        target.ace_release_sha256,
        target.ace_release_artifact_sha256,
        target.active_baseline_version_sha256,
        target.active_baseline_candidate_sha256,
        target.active_baseline_policy.policy_id,
        target.active_baseline_policy.version,
        target.active_baseline_policy.sha256,
        target.active_baseline_playbook_sha256,
    )
    if expected != actual:
        raise EvolutionIntegrityError("archived_release_metadata_mismatch")
    return release


def _active(row: EvolutionActiveVersionRow) -> ActiveEvolutionVersion:
    return ActiveEvolutionVersion(
        active_version_sha256=row.active_version_sha256,
        active_candidate_sha256=row.active_candidate_sha256,
        previous_version_sha256=row.previous_version_sha256,
        previous_candidate_sha256=row.previous_candidate_sha256,
        revision=row.revision,
        activated_at=row.activated_at,
        activated_by_account_id=row.activated_by_account_id,
        activated_by_session_id=row.activated_by_session_id,
    )


def _approval(row: EvolutionCommandApprovalRow) -> CommandApprovalRecord:
    return CommandApprovalRecord(
        approval_id=row.approval_id,
        action=PromotionAction(row.action),
        target_version_sha256=row.target_version_sha256,
        evidence_sha256=row.evidence_sha256,
        expected_pointer_revision=row.expected_pointer_revision,
        account_id=row.account_id,
        session_id=row.session_id,
        approved_at=row.approved_at,
        consumed_at=row.consumed_at,
        resulting_pointer_revision=row.resulting_pointer_revision,
    )


def _transition_result(
    session: Session,
    approval: EvolutionCommandApprovalRow,
    *,
    replayed: bool,
) -> EvolutionTransitionResult:
    event = session.scalar(
        select(EvolutionPromotionEventRow).where(
            EvolutionPromotionEventRow.scope_id == approval.scope_id,
            EvolutionPromotionEventRow.approval_id == approval.approval_id,
        )
    )
    if event is None:
        raise EvolutionIntegrityError("consumed_approval_event_missing")
    active = ActiveEvolutionVersion(
        active_version_sha256=event.to_version_sha256,
        active_candidate_sha256=event.to_candidate_sha256,
        previous_version_sha256=event.from_version_sha256,
        previous_candidate_sha256=event.from_candidate_sha256,
        revision=event.pointer_revision,
        activated_at=event.occurred_at,
        activated_by_account_id=event.account_id,
        activated_by_session_id=event.session_id,
    )
    return EvolutionTransitionResult(
        active=active,
        approval=_approval(approval),
        replayed=replayed,
    )


def _require_active_command_principal(
    session: Session,
    *,
    scope_id: UUID,
    principal: PersonaPrincipal,
    as_of: datetime,
) -> None:
    row = session.execute(
        select(PersonaSessionRow, PersonaAccountRow)
        .join(
            PersonaAccountRow,
            (PersonaAccountRow.scope_id == PersonaSessionRow.scope_id)
            & (PersonaAccountRow.account_id == PersonaSessionRow.account_id),
        )
        .where(
            PersonaSessionRow.scope_id == scope_id,
            PersonaSessionRow.session_id == principal.session_id,
            PersonaSessionRow.account_id == principal.account_id,
        )
        .with_for_update(read=True)
    ).one_or_none()
    if row is None:
        raise EvolutionConflict("command_principal_not_active")
    persona_session, account = row
    if (
        principal.scope_id != scope_id
        or principal.persona is not Persona.COMMAND
        or persona_session.installation_id != principal.installation_id
        or persona_session.status != "active"
        or persona_session.rotated_at > as_of
        or persona_session.access_expires_at <= as_of
        or account.status != "active"
        or account.persona != Persona.COMMAND.value
    ):
        raise EvolutionConflict("command_principal_not_active")


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
