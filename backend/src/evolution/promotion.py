"""Operator-approved active-version pointers with atomic rollback."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Protocol
from uuid import uuid4

from vital_relay.evolution.archive import QualityDiversityArchive
from vital_relay.evolution.contracts import (
    ActiveVersionPointer,
    OperatorApproval,
    PromotionEvidence,
    PromotionAction,
    PromotionEvent,
    PromotionThresholds,
)


class PointerConflict(RuntimeError):
    pass


class ActivePointerStore(Protocol):
    def read(self) -> ActiveVersionPointer:
        """Return the current immutable pointer."""

    def compare_and_swap(
        self,
        expected_revision: int,
        replacement: ActiveVersionPointer,
    ) -> bool:
        """Atomically replace the pointer only at the expected revision."""


class InMemoryActivePointerStore:
    """Thread-safe adapter used by the offline lab; DB CAS is integrated later."""

    def __init__(self, initial_candidate_sha256: str) -> None:
        self._pointer = ActiveVersionPointer(
            active_candidate_sha256=initial_candidate_sha256,
            revision=0,
        )
        self._lock = Lock()

    def read(self) -> ActiveVersionPointer:
        with self._lock:
            return self._pointer

    def compare_and_swap(
        self,
        expected_revision: int,
        replacement: ActiveVersionPointer,
    ) -> bool:
        with self._lock:
            if self._pointer.revision != expected_revision:
                return False
            if replacement.revision != expected_revision + 1:
                raise ValueError("replacement must advance the pointer by one revision")
            self._pointer = replacement
            return True


class PromotionManager:
    def __init__(
        self,
        archive: QualityDiversityArchive,
        pointers: ActivePointerStore,
        thresholds: PromotionThresholds,
    ) -> None:
        self._archive = archive
        self._pointers = pointers
        self._thresholds = thresholds
        self._events: list[PromotionEvent] = []
        self._used_approvals: set = set()

    @property
    def events(self) -> tuple[PromotionEvent, ...]:
        return tuple(self._events)

    def promote(
        self,
        approval: OperatorApproval,
        evidence: PromotionEvidence,
        expected_revision: int,
        now: datetime | None = None,
    ) -> ActiveVersionPointer:
        if approval.action is not PromotionAction.PROMOTE:
            raise ValueError("promotion requires a promote approval")
        if approval.approval_id in self._used_approvals:
            raise ValueError("operator approval has already been consumed")
        if approval.expected_pointer_revision != expected_revision:
            raise ValueError("approval is bound to another pointer revision")
        if (
            evidence.candidate_sha256 != approval.candidate_sha256
            or evidence.evidence_sha256 != approval.evidence_sha256
        ):
            raise ValueError("approval does not bind the supplied selection evidence")
        if (
            evidence.minimum_development_gain
            != self._thresholds.minimum_development_gain
            or evidence.maximum_protected_regression
            != self._thresholds.maximum_protected_regression
        ):
            raise ValueError("selection evidence does not use host promotion thresholds")
        reports = (
            evidence.development,
            evidence.protected_validation,
            evidence.baseline_development,
            evidence.baseline_protected_validation,
        )
        for report in reports:
            entry = self._archive.resolve_verified_entry(report.report_sha256)
            if self._archive.evaluation_for(entry) != report:
                raise ValueError(
                    "promotion requires exact evaluator-issued archived reports"
                )
        current = self._pointers.read()
        if current.revision != expected_revision:
            raise PointerConflict("active pointer changed before promotion")
        if current.active_candidate_sha256 != evidence.baseline_candidate_sha256:
            raise ValueError("selection baseline is not the active candidate")
        if current.active_candidate_sha256 == approval.candidate_sha256:
            raise ValueError("candidate is already active")
        replacement = ActiveVersionPointer(
            active_candidate_sha256=approval.candidate_sha256,
            previous_candidate_sha256=current.active_candidate_sha256,
            revision=current.revision + 1,
        )
        self._swap_and_record(current, replacement, approval, now)
        return replacement

    def rollback(
        self,
        approval: OperatorApproval,
        expected_revision: int,
        now: datetime | None = None,
    ) -> ActiveVersionPointer:
        if approval.action is not PromotionAction.ROLLBACK:
            raise ValueError("rollback requires a rollback approval")
        if approval.approval_id in self._used_approvals:
            raise ValueError("operator approval has already been consumed")
        if approval.expected_pointer_revision != expected_revision:
            raise ValueError("approval is bound to another pointer revision")
        current = self._pointers.read()
        if current.revision != expected_revision:
            raise PointerConflict("active pointer changed before rollback")
        target = current.previous_candidate_sha256
        if target is None:
            raise ValueError("there is no previous candidate to restore")
        if approval.candidate_sha256 != target:
            raise ValueError("rollback approval must name the retained previous version")
        if not self._archive.has_verified_candidate(target):
            raise ValueError("rollback target is not a verified archived candidate")
        replacement = ActiveVersionPointer(
            active_candidate_sha256=target,
            previous_candidate_sha256=current.active_candidate_sha256,
            revision=current.revision + 1,
        )
        self._swap_and_record(current, replacement, approval, now)
        return replacement

    def _swap_and_record(
        self,
        current: ActiveVersionPointer,
        replacement: ActiveVersionPointer,
        approval: OperatorApproval,
        now: datetime | None,
    ) -> None:
        effective_now = now or datetime.now(UTC)
        if effective_now.tzinfo is None or effective_now.utcoffset() is None:
            raise ValueError("transition time must be timezone-aware")
        if effective_now < approval.approved_at:
            raise ValueError("transition cannot precede operator approval")
        event = PromotionEvent(
            event_id=uuid4(),
            approval_id=approval.approval_id,
            action=approval.action,
            from_candidate_sha256=current.active_candidate_sha256,
            to_candidate_sha256=replacement.active_candidate_sha256,
            evidence_sha256=approval.evidence_sha256,
            pointer_revision=replacement.revision,
            occurred_at=effective_now,
        )
        if not self._pointers.compare_and_swap(current.revision, replacement):
            raise PointerConflict("active pointer changed during atomic update")
        self._events.append(event)
        self._used_approvals.add(approval.approval_id)
