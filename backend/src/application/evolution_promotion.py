"""Verified release archival and authenticated transactional activation.

The paired-round release contract is intentionally opaque here.  Its owning
lane supplies a verifier that validates exact canonical bytes on the trusted
offline host; persistence only retains those bytes and the digests bound by a
``PromotionTarget``.  Protected case material is never part of an API view.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from hashlib import sha256
from typing import Callable, Literal, Protocol, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vital_relay.agent.contracts import AgentPolicyReference
from vital_relay.domain.persona_sessions import Persona, PersonaPrincipal
from vital_relay.evolution.ace.contracts import ACERole, ModelIdentity, RoleIdentity
from vital_relay.evolution.contracts import (
    ArtifactKind,
    ArtifactReference,
    PromotionAction,
    PromotionEvidence,
)
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256


SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_RELEASE_ARTIFACT_BYTES = 16_000_000


class EvolutionPromotionModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class PromotionReleaseKind(str, Enum):
    """The two unambiguous evidence modes accepted by activation."""

    PLAYBOOK_ONLY = "playbook_only"
    CANDIDATE_OR_POLICY_CHANGE = "candidate_or_policy_change"


class PromotionTarget(EvolutionPromotionModel):
    """Canonical identity of the complete release selected for activation.

    A candidate bundle is only one member of this identity.  The target also
    binds operational context, generation identities, signed selection
    evidence, the cadence-issued final report, and the baseline against which
    the release was selected.
    """

    schema_version: Literal[1] = 1
    release_kind: PromotionReleaseKind
    candidate_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    policy: AgentPolicyReference
    playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    delta_log_sha256: str = Field(pattern=SHA256_PATTERN)
    improver: ArtifactReference
    generator_role: RoleIdentity
    generator_model: ModelIdentity
    promotion_evidence_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    development_report_sha256: str = Field(pattern=SHA256_PATTERN)
    development_report_signature_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_signature_sha256: str = Field(
        pattern=SHA256_PATTERN
    )
    final_report_sha256: str = Field(pattern=SHA256_PATTERN)
    final_cadence_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    final_cadence_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    paired_report_sha256: str = Field(pattern=SHA256_PATTERN)
    paired_report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_release_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_release_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_version_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_policy: AgentPolicyReference
    active_baseline_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    target_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_bindings(self, info: ValidationInfo) -> Self:
        if self.improver.kind is not ArtifactKind.IMPROVER:
            raise ValueError("promotion target improver has the wrong artifact kind")
        if self.generator_role.role is not ACERole.GENERATOR:
            raise ValueError("promotion target requires the Generator role identity")
        if self.release_kind is PromotionReleaseKind.PLAYBOOK_ONLY:
            if self.promotion_evidence_sha256 is not None:
                raise ValueError("playbook-only target cannot bind legacy evidence")
            if self.candidate_sha256 != self.active_baseline_candidate_sha256:
                raise ValueError(
                    "playbook-only target must retain the active candidate"
                )
            if self.policy != self.active_baseline_policy:
                raise ValueError("playbook-only target must retain the active policy")
            if self.playbook_sha256 == self.active_baseline_playbook_sha256:
                raise ValueError("playbook-only target must select a new playbook")
        else:
            if self.promotion_evidence_sha256 is None:
                raise ValueError("candidate or policy change requires legacy evidence")
            if self.candidate_sha256 == self.active_baseline_candidate_sha256:
                raise ValueError(
                    "legacy promotion evidence requires a changed candidate"
                )
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"target_sha256"})
        )
        if self.target_sha256 != actual:
            raise ValueError("target_sha256 does not match canonical promotion target")
        return self

    @classmethod
    def create(cls, **values: object) -> PromotionTarget:
        material = cls.model_validate(
            {**values, "target_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"target_sha256"})
        )
        return cls.model_validate({**values, "target_sha256": digest})


class CanonicalArtifactBytes(EvolutionPromotionModel):
    """Exact canonical bytes with a transport digest, without redefining its type."""

    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    canonical_bytes: bytes = Field(
        min_length=1,
        max_length=MAX_RELEASE_ARTIFACT_BYTES,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_digest(self) -> Self:
        if sha256(self.canonical_bytes).hexdigest() != self.artifact_sha256:
            raise ValueError("canonical artifact bytes do not match their digest")
        return self


class CanonicalPairedACERelease(EvolutionPromotionModel):
    """Exact frozen ACE release and paired report, retained independently."""

    release_artifact: CanonicalArtifactBytes = Field(repr=False)
    paired_report: CanonicalArtifactBytes = Field(repr=False)


class VerifiedReleaseClaims(EvolutionPromotionModel):
    """Protected-data-free bindings derived by the trusted concrete verifier.

    ``authenticated_target_sha256`` is a value-object seam for the complete
    verified release identity, not an echo of client-supplied target bytes.
    """

    authenticated_target_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    paired_report_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_release_sha256: str = Field(pattern=SHA256_PATTERN)
    development_report_sha256: str = Field(pattern=SHA256_PATTERN)
    development_report_signature_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_signature_sha256: str = Field(
        pattern=SHA256_PATTERN
    )
    final_report_sha256: str = Field(pattern=SHA256_PATTERN)
    final_cadence_receipt_sha256: str = Field(pattern=SHA256_PATTERN)


class PairedRoundReleaseVerifier(Protocol):
    """Trusted-host seam for frozen paired ACE plus final cadence evidence.

    The concrete root adapter must parse the exact canonical bytes as the frozen
    ``ACEReleaseArtifact`` and ``PairedACEReport`` types, use ``ACEReleaseVerifier``
    to validate their selected playbook and all signed development/protected
    evaluations, validate final cadence issuance, and return only derived claims.
    ``authenticated_target_sha256`` must be recomputed from a newly constructed
    canonical target only after every field has been re-derived: schema version
    and release mode; candidate bundle, candidate, and policy; selected playbook
    and delta log; improver and Generator/model identities; optional legacy
    evidence; signed
    development/protected report and signature digests; final report, cadence
    receipt, and exact cadence artifact digest; paired-report and ACE-release
    semantic and exact-artifact digests; and the complete active-baseline
    identity.  It must never be copied from an unverified target.  When legacy
    evidence is present, the verifier must also authenticate both its candidate
    and prior-baseline report pairs with the trusted integrity authority.
    """

    def verify_release(
        self,
        *,
        target: PromotionTarget,
        evidence: PromotionEvidence | None,
        paired_ace_release: CanonicalPairedACERelease,
        final_cadence_evidence: CanonicalArtifactBytes,
    ) -> VerifiedReleaseClaims:
        """Reject unsigned, tampered, unsafe, mismatched, or stale material."""


class EvolutionReleaseSource(Protocol):
    """Trusted-host resolver for an already materialized release digest."""

    def load_release(
        self,
        target_sha256: str,
    ) -> ArchivedCandidateVersion:
        """Resolve exact host-owned material without accepting a client path."""


class CandidateVersionSummary(EvolutionPromotionModel):
    """Protected-data-free metadata safe for the command API."""

    version_sha256: str = Field(pattern=SHA256_PATTERN)
    release_kind: PromotionReleaseKind
    candidate_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    policy: AgentPolicyReference
    playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    delta_log_sha256: str = Field(pattern=SHA256_PATTERN)
    improver_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_role_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_model_sha256: str = Field(pattern=SHA256_PATTERN)
    development_report_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    final_report_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_version_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    active_baseline_policy: AgentPolicyReference
    active_baseline_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    archived_at: AwareDatetime

    @field_validator("archived_at")
    @classmethod
    def normalize_archived_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class CandidateVersionDetail(CandidateVersionSummary):
    promotion_evidence_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    development_report_signature_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_validation_report_signature_sha256: str = Field(
        pattern=SHA256_PATTERN
    )
    final_cadence_receipt_sha256: str = Field(pattern=SHA256_PATTERN)
    final_cadence_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    paired_report_sha256: str = Field(pattern=SHA256_PATTERN)
    paired_report_artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_release_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_release_artifact_sha256: str = Field(pattern=SHA256_PATTERN)


class ArchivedCandidateVersion(EvolutionPromotionModel):
    """Internal exact material; never use this model as an API response."""

    target: PromotionTarget
    target_canonical_bytes: bytes = Field(
        min_length=1,
        max_length=MAX_RELEASE_ARTIFACT_BYTES,
        repr=False,
    )
    evidence: PromotionEvidence | None = None
    evidence_canonical_bytes: bytes | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_RELEASE_ARTIFACT_BYTES,
        repr=False,
    )
    paired_ace_release: CanonicalPairedACERelease = Field(repr=False)
    final_cadence_evidence: CanonicalArtifactBytes = Field(repr=False)
    archived_at: AwareDatetime

    @field_validator("archived_at")
    @classmethod
    def normalize_archive_time(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_exact_bytes(self) -> Self:
        if canonical_json_bytes(self.target) != self.target_canonical_bytes:
            raise ValueError("promotion target bytes are not exact canonical bytes")
        if (self.evidence is None) != (self.evidence_canonical_bytes is None):
            raise ValueError("promotion evidence and exact bytes must form a pair")
        if self.evidence is not None and (
            canonical_json_bytes(self.evidence) != self.evidence_canonical_bytes
        ):
            raise ValueError("promotion evidence bytes are not exact canonical bytes")
        if self.target.release_kind is PromotionReleaseKind.PLAYBOOK_ONLY:
            if self.evidence is not None:
                raise ValueError("playbook-only release cannot retain legacy evidence")
        elif self.evidence is None:
            raise ValueError("candidate or policy change requires legacy evidence")
        if (
            self.target.ace_release_artifact_sha256
            != self.paired_ace_release.release_artifact.artifact_sha256
        ):
            raise ValueError("promotion target does not bind the paired release bytes")
        if (
            self.target.paired_report_artifact_sha256
            != self.paired_ace_release.paired_report.artifact_sha256
        ):
            raise ValueError("promotion target does not bind the paired report bytes")
        if (
            self.target.final_cadence_artifact_sha256
            != self.final_cadence_evidence.artifact_sha256
        ):
            raise ValueError("promotion target does not bind final cadence bytes")
        return self

    @property
    def summary(self) -> CandidateVersionSummary:
        target = self.target
        return CandidateVersionSummary(
            version_sha256=target.target_sha256,
            release_kind=target.release_kind,
            candidate_bundle_sha256=target.candidate_bundle_sha256,
            candidate_sha256=target.candidate_sha256,
            policy=target.policy,
            playbook_sha256=target.playbook_sha256,
            delta_log_sha256=target.delta_log_sha256,
            improver_sha256=target.improver.sha256,
            generator_role_sha256=target.generator_role.role_sha256,
            generator_model_sha256=target.generator_model.model_sha256,
            development_report_sha256=target.development_report_sha256,
            protected_validation_report_sha256=(
                target.protected_validation_report_sha256
            ),
            final_report_sha256=target.final_report_sha256,
            active_baseline_version_sha256=(
                target.active_baseline_version_sha256
            ),
            active_baseline_candidate_sha256=(
                target.active_baseline_candidate_sha256
            ),
            active_baseline_policy=target.active_baseline_policy,
            active_baseline_playbook_sha256=(
                target.active_baseline_playbook_sha256
            ),
            archived_at=self.archived_at,
        )

    @property
    def detail(self) -> CandidateVersionDetail:
        target = self.target
        return CandidateVersionDetail(
            **self.summary.model_dump(),
            promotion_evidence_sha256=target.promotion_evidence_sha256,
            development_report_signature_sha256=(
                target.development_report_signature_sha256
            ),
            protected_validation_report_signature_sha256=(
                target.protected_validation_report_signature_sha256
            ),
            final_cadence_receipt_sha256=target.final_cadence_receipt_sha256,
            final_cadence_artifact_sha256=(
                target.final_cadence_artifact_sha256
            ),
            paired_report_sha256=target.paired_report_sha256,
            paired_report_artifact_sha256=(
                target.paired_report_artifact_sha256
            ),
            ace_release_sha256=target.ace_release_sha256,
            ace_release_artifact_sha256=(
                target.ace_release_artifact_sha256
            ),
        )


class ActiveEvolutionVersion(EvolutionPromotionModel):
    active_version_sha256: str = Field(pattern=SHA256_PATTERN)
    active_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    previous_version_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    previous_candidate_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    revision: int = Field(ge=0)
    activated_at: AwareDatetime
    activated_by_account_id: UUID
    activated_by_session_id: UUID

    @field_validator("activated_at")
    @classmethod
    def normalize_activated_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_previous_pair(self) -> Self:
        if (self.previous_version_sha256 is None) != (
            self.previous_candidate_sha256 is None
        ):
            raise ValueError("previous version and candidate identities form a pair")
        return self


class EvolutionTransitionCommand(EvolutionPromotionModel):
    approval_id: UUID
    target_version_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_pointer_revision: int = Field(ge=0)


class ArchiveEvolutionReleaseCommand(EvolutionPromotionModel):
    target_sha256: str = Field(pattern=SHA256_PATTERN)


class CommandApprovalRecord(EvolutionPromotionModel):
    approval_id: UUID
    action: PromotionAction
    target_version_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    expected_pointer_revision: int = Field(ge=0)
    account_id: UUID
    session_id: UUID
    approved_at: AwareDatetime
    consumed_at: AwareDatetime
    resulting_pointer_revision: int = Field(ge=1)

    @field_validator("approved_at", "consumed_at")
    @classmethod
    def normalize_approval_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_consumption(self) -> Self:
        if (
            self.action is PromotionAction.ROLLBACK
            and self.evidence_sha256 is not None
        ):
            raise ValueError("rollback approval cannot bind promotion evidence")
        if self.consumed_at < self.approved_at:
            raise ValueError("approval consumption cannot precede approval")
        if self.resulting_pointer_revision != self.expected_pointer_revision + 1:
            raise ValueError("approval result must advance the pointer once")
        return self

    def matches(
        self,
        *,
        action: PromotionAction,
        command: EvolutionTransitionCommand,
        principal: PersonaPrincipal,
    ) -> bool:
        return (
            self.action is action
            and self.target_version_sha256 == command.target_version_sha256
            and self.expected_pointer_revision == command.expected_pointer_revision
            and self.account_id == principal.account_id
            and self.session_id == principal.session_id
        )


class EvolutionTransitionResult(EvolutionPromotionModel):
    active: ActiveEvolutionVersion
    approval: CommandApprovalRecord
    replayed: bool


class EvolutionPromotionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code}


class EvolutionVersionNotFound(EvolutionPromotionError):
    def __init__(self, code: str = "evolution_version_not_found") -> None:
        super().__init__(code)


class EvolutionPointerNotFound(EvolutionPromotionError):
    def __init__(self) -> None:
        super().__init__("active_evolution_version_not_found")


class EvolutionConflict(EvolutionPromotionError):
    pass


class EvolutionIntegrityError(EvolutionPromotionError):
    pass


class EvolutionPromotionRepository(Protocol):
    def archive(self, release: ArchivedCandidateVersion) -> CandidateVersionDetail:
        """Persist immutable exact bytes; exact retries are idempotent."""

    def list_versions(self, *, limit: int) -> tuple[CandidateVersionSummary, ...]: ...

    def get_version(self, version_sha256: str) -> CandidateVersionDetail: ...

    def load_release(self, version_sha256: str) -> ArchivedCandidateVersion: ...

    def get_active(self) -> ActiveEvolutionVersion: ...

    def initialize_active(
        self,
        version_sha256: str,
        *,
        principal: PersonaPrincipal,
        activated_at: datetime,
        verify_release: Callable[[ArchivedCandidateVersion], None],
    ) -> ActiveEvolutionVersion: ...

    def transition(
        self,
        action: PromotionAction,
        command: EvolutionTransitionCommand,
        *,
        principal: PersonaPrincipal,
        occurred_at: datetime,
        verify_release: Callable[[ArchivedCandidateVersion], None],
    ) -> EvolutionTransitionResult:
        """Verify exact bytes, then CAS all state in one DB transaction."""


class EvolutionPromotionService:
    """Fail-closed orchestration around the durable transactional repository."""

    def __init__(
        self,
        repository: EvolutionPromotionRepository,
        release_verifier: PairedRoundReleaseVerifier,
        *,
        release_source: EvolutionReleaseSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._release_verifier = release_verifier
        self._release_source = release_source
        self._clock = clock or (lambda: datetime.now(UTC))

    def archive(
        self,
        target: PromotionTarget,
        evidence: PromotionEvidence | None,
        paired_ace_release: CanonicalPairedACERelease,
        final_cadence_evidence: CanonicalArtifactBytes,
        *,
        archived_at: datetime | None = None,
    ) -> CandidateVersionDetail:
        release = self._build_verified_release(
            target,
            evidence,
            paired_ace_release,
            final_cadence_evidence,
            archived_at=_utc(archived_at or self._clock(), "archived_at"),
        )
        return self._repository.archive(release)

    def archive_from_source(
        self,
        command: ArchiveEvolutionReleaseCommand,
        principal: PersonaPrincipal,
    ) -> CandidateVersionDetail:
        """Archive a digest resolved wholly inside the trusted offline host."""

        _require_command(principal)
        if self._release_source is None:
            raise EvolutionPromotionError("evolution_release_source_unavailable")
        try:
            release = self._release_source.load_release(command.target_sha256)
        except EvolutionPromotionError:
            raise
        except Exception as exc:
            raise EvolutionIntegrityError("evolution_release_source_failed") from exc
        if release.target.target_sha256 != command.target_sha256:
            raise EvolutionIntegrityError("evolution_release_source_digest_mismatch")
        self._verify_release(release)
        return self._repository.archive(release)

    def list_versions(
        self,
        principal: PersonaPrincipal,
        *,
        limit: int = 50,
    ) -> tuple[CandidateVersionSummary, ...]:
        _require_command(principal)
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return self._repository.list_versions(limit=limit)

    def get_version(
        self,
        version_sha256: str,
        principal: PersonaPrincipal,
    ) -> CandidateVersionDetail:
        _require_command(principal)
        return self._repository.get_version(_sha256(version_sha256))

    def get_active(self, principal: PersonaPrincipal) -> ActiveEvolutionVersion:
        _require_command(principal)
        return self._repository.get_active()

    def initialize_active(
        self,
        version_sha256: str,
        principal: PersonaPrincipal,
        *,
        activated_at: datetime | None = None,
    ) -> ActiveEvolutionVersion:
        _require_command(principal)
        digest = _sha256(version_sha256)
        return self._repository.initialize_active(
            digest,
            principal=principal,
            activated_at=_utc(activated_at or self._clock(), "activated_at"),
            verify_release=self._verify_release,
        )

    def promote(
        self,
        command: EvolutionTransitionCommand,
        principal: PersonaPrincipal,
        *,
        occurred_at: datetime | None = None,
    ) -> EvolutionTransitionResult:
        return self._transition(
            PromotionAction.PROMOTE,
            command,
            principal,
            occurred_at=occurred_at,
        )

    def rollback(
        self,
        command: EvolutionTransitionCommand,
        principal: PersonaPrincipal,
        *,
        occurred_at: datetime | None = None,
    ) -> EvolutionTransitionResult:
        return self._transition(
            PromotionAction.ROLLBACK,
            command,
            principal,
            occurred_at=occurred_at,
        )

    def _transition(
        self,
        action: PromotionAction,
        command: EvolutionTransitionCommand,
        principal: PersonaPrincipal,
        *,
        occurred_at: datetime | None,
    ) -> EvolutionTransitionResult:
        _require_command(principal)
        return self._repository.transition(
            action,
            command,
            principal=principal,
            occurred_at=_utc(occurred_at or self._clock(), "occurred_at"),
            verify_release=self._verify_release,
        )

    def _build_verified_release(
        self,
        target: PromotionTarget,
        evidence: PromotionEvidence | None,
        paired_ace_release: CanonicalPairedACERelease,
        final_cadence_evidence: CanonicalArtifactBytes,
        *,
        archived_at: datetime,
    ) -> ArchivedCandidateVersion:
        release = ArchivedCandidateVersion(
            target=target,
            target_canonical_bytes=canonical_json_bytes(target),
            evidence=evidence,
            evidence_canonical_bytes=(
                canonical_json_bytes(evidence) if evidence is not None else None
            ),
            paired_ace_release=paired_ace_release,
            final_cadence_evidence=final_cadence_evidence,
            archived_at=archived_at,
        )
        self._verify_release(release)
        return release

    def _verify_release(self, release: ArchivedCandidateVersion) -> None:
        target = release.target
        evidence = release.evidence
        if target.release_kind is PromotionReleaseKind.PLAYBOOK_ONLY:
            if evidence is not None:
                raise EvolutionIntegrityError("unexpected_legacy_promotion_evidence")
        else:
            if evidence is None:
                raise EvolutionIntegrityError("promotion_evidence_missing")
            self._verify_legacy_evidence(target, evidence)
        try:
            claims = self._release_verifier.verify_release(
                target=target,
                evidence=evidence,
                paired_ace_release=release.paired_ace_release,
                final_cadence_evidence=release.final_cadence_evidence,
            )
        except EvolutionPromotionError:
            raise
        except Exception as exc:
            raise EvolutionIntegrityError("paired_release_verification_failed") from exc
        expected_claims = VerifiedReleaseClaims(
            authenticated_target_sha256=target.target_sha256,
            candidate_sha256=target.candidate_sha256,
            active_baseline_playbook_sha256=(
                target.active_baseline_playbook_sha256
            ),
            selected_playbook_sha256=target.playbook_sha256,
            paired_report_sha256=target.paired_report_sha256,
            ace_release_sha256=target.ace_release_sha256,
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
        )
        try:
            verified_claims = VerifiedReleaseClaims.model_validate(claims)
        except (ValueError, TypeError) as exc:
            raise EvolutionIntegrityError("release_claims_invalid") from exc
        if verified_claims != expected_claims:
            raise EvolutionIntegrityError("release_claims_mismatch")

    @staticmethod
    def _verify_legacy_evidence(
        target: PromotionTarget,
        evidence: PromotionEvidence,
    ) -> None:
        if target.candidate_sha256 != evidence.candidate_sha256:
            raise EvolutionIntegrityError("promotion_candidate_mismatch")
        if (
            target.active_baseline_candidate_sha256
            != evidence.baseline_candidate_sha256
        ):
            raise EvolutionIntegrityError("promotion_baseline_mismatch")
        if target.promotion_evidence_sha256 != evidence.evidence_sha256:
            raise EvolutionIntegrityError("promotion_evidence_mismatch")
        if (
            target.development_report_sha256 != evidence.development.report_sha256
            or target.development_report_signature_sha256
            != evidence.development.issuer_hmac_sha256
            or target.protected_validation_report_sha256
            != evidence.protected_validation.report_sha256
            or target.protected_validation_report_signature_sha256
            != evidence.protected_validation.issuer_hmac_sha256
        ):
            raise EvolutionIntegrityError("promotion_report_binding_mismatch")
        candidate_policy_sha256s = {
            evidence.development.integrity_evidence.policy_sha256,
            evidence.protected_validation.integrity_evidence.policy_sha256,
        }
        baseline_policy_sha256s = {
            evidence.baseline_development.integrity_evidence.policy_sha256,
            evidence.baseline_protected_validation.integrity_evidence.policy_sha256,
        }
        if candidate_policy_sha256s != {target.policy.sha256}:
            raise EvolutionIntegrityError("promotion_policy_binding_mismatch")
        if baseline_policy_sha256s != {target.active_baseline_policy.sha256}:
            raise EvolutionIntegrityError("promotion_baseline_policy_mismatch")


def _require_command(principal: PersonaPrincipal) -> None:
    if principal.persona is not Persona.COMMAND:
        raise EvolutionPromotionError("persona_not_authorized")


def _sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("value must be a lowercase SHA-256")
    return value


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)
