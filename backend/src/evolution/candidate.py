"""Candidate construction around schema-owning mutation adapters."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from vital_relay.evolution.contracts import (
    ArtifactKind,
    ArtifactReference,
    CandidateManifest,
    InvalidAttemptReason,
    InvalidAttemptRecord,
    MutationManifest,
    MutationTarget,
)
from vital_relay.evolution.policy import PolicyArtifactAdapter


_PROTECTED_POINTER_PREFIXES = (
    "/protected",
    "/evaluator",
    "/expected_outcomes",
    "/promotion",
    "/audit",
    "/protocols/content",
)


class MutationRejected(ValueError):
    def __init__(self, reason: InvalidAttemptReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


class ImproverArtifactAdapter(Protocol):
    """Schema owner for reproducible mutations of the improver artifact."""

    def canonical_payload(self, reference: ArtifactReference) -> bytes:
        """Resolve canonical content and reject unknown references."""

    def apply_mutation(
        self,
        parent: ArtifactReference,
        mutation: MutationManifest,
    ) -> ArtifactReference:
        """Apply a typed mutation and return its content-addressed reference."""


class CandidateFactory:
    """Build immutable descendants; schema/range checks stay in the A2 adapter."""

    def build_policy_candidate(
        self,
        parent: CandidateManifest,
        mutation: MutationManifest,
        adapter: PolicyArtifactAdapter,
        *,
        candidate_id: str,
        created_at: datetime,
    ) -> CandidateManifest:
        self._validate_common(parent, mutation, MutationTarget.COORDINATION_POLICY)
        try:
            child_policy = adapter.apply_mutation(parent.policy, mutation)
            payload = adapter.canonical_payload(child_policy)
        except MutationRejected:
            raise
        except Exception as exc:
            raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED) from exc
        if sha256(payload).hexdigest() != child_policy.sha256:
            raise MutationRejected(InvalidAttemptReason.HASH_MISMATCH)
        if child_policy == parent.policy:
            raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
        return CandidateManifest.create(
            candidate_id=candidate_id,
            parent_candidate_id=parent.candidate_id,
            generation=parent.generation + 1,
            created_at=created_at,
            policy=child_policy,
            improver=parent.improver,
            mutation_sha256=mutation.mutation_sha256,
        )

    def build_improver_candidate(
        self,
        parent: CandidateManifest,
        mutation: MutationManifest,
        adapter: ImproverArtifactAdapter,
        *,
        candidate_id: str,
        created_at: datetime,
    ) -> CandidateManifest:
        self._validate_common(parent, mutation, MutationTarget.IMPROVER)
        try:
            child_improver = adapter.apply_mutation(parent.improver, mutation)
            payload = adapter.canonical_payload(child_improver)
        except MutationRejected:
            raise
        except Exception as exc:
            raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED) from exc
        if child_improver.kind is not ArtifactKind.IMPROVER:
            raise MutationRejected(InvalidAttemptReason.UNSUPPORTED_TARGET)
        if sha256(payload).hexdigest() != child_improver.sha256:
            raise MutationRejected(InvalidAttemptReason.HASH_MISMATCH)
        if child_improver.sha256 == parent.improver.sha256:
            raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
        return CandidateManifest.create(
            candidate_id=candidate_id,
            parent_candidate_id=parent.candidate_id,
            generation=parent.generation + 1,
            created_at=created_at,
            policy=parent.policy,
            improver=child_improver,
            mutation_sha256=mutation.mutation_sha256,
        )

    def invalid_attempt(
        self,
        parent: CandidateManifest,
        mutation: MutationManifest | None,
        *,
        attempt_id: UUID,
        attempted_at: datetime,
        reason: InvalidAttemptReason,
    ) -> InvalidAttemptRecord:
        return InvalidAttemptRecord(
            attempt_id=attempt_id,
            parent_candidate_id=parent.candidate_id,
            attempted_at=attempted_at,
            reason=reason,
            proposed_sha256=(mutation.mutation_sha256 if mutation else None),
            operation_paths=(
                tuple(operation.path for operation in mutation.operations)
                if mutation
                else ()
            ),
        )

    def _validate_common(
        self,
        parent: CandidateManifest,
        mutation: MutationManifest,
        expected_target: MutationTarget,
    ) -> None:
        if mutation.parent_policy != parent.policy:
            raise MutationRejected(InvalidAttemptReason.HASH_MISMATCH)
        if mutation.target is not expected_target:
            raise MutationRejected(InvalidAttemptReason.UNSUPPORTED_TARGET)
        if mutation.generated_by.kind is not ArtifactKind.IMPROVER:
            raise MutationRejected(InvalidAttemptReason.UNSUPPORTED_TARGET)
        if mutation.generated_by.sha256 != parent.improver.sha256:
            raise MutationRejected(InvalidAttemptReason.HASH_MISMATCH)
        if any(
            operation.path == prefix or operation.path.startswith(f"{prefix}/")
            for operation in mutation.operations
            for prefix in _PROTECTED_POINTER_PREFIXES
        ):
            raise MutationRejected(InvalidAttemptReason.PROTECTED_PATH)
