"""Verified, byte-exact candidate bundle contracts.

The shared evolution contracts describe candidate semantics.  This module adds a
closed persistence envelope without changing those contracts: a bundle contains
only the canonical candidate manifest, coordination policy, current improver,
and (for descendants) mutation manifest.
"""

from __future__ import annotations

from enum import StrEnum
from hashlib import sha256
import hmac
import json
import re
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.evolution.contracts import (
    ArtifactKind,
    CandidateManifest,
    IDENTIFIER_PATTERN,
    MutationManifest,
    MutationTarget,
    SHA256_PATTERN,
)
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.evolution.improver import (
    IMPROVER_MEDIA_TYPE,
    ImproverArtifact,
)
from vital_relay.evolution.policy import A2PolicyArtifactAdapter


BUNDLE_SCHEMA_VERSION = 1
CANDIDATE_MANIFEST_MEDIA_TYPE = (
    "application/vnd.vital-relay.candidate-manifest+json"
)
COORDINATION_POLICY_MEDIA_TYPE = (
    "application/vnd.vital-relay.coordination-policy+json"
)
LEGACY_IMPROVER_MEDIA_TYPE = "text/markdown"
MUTATION_MANIFEST_MEDIA_TYPE = (
    "application/vnd.vital-relay.mutation-manifest+json"
)

_MAX_ARTIFACT_BYTES = 2_000_000


class CandidateBundleVerificationError(ValueError):
    """The proposed bundle does not prove the identities it claims."""


class CandidateBundleArtifactRole(StrEnum):
    CANDIDATE_MANIFEST = "candidate_manifest"
    COORDINATION_POLICY = "coordination_policy"
    IMPROVER = "improver"
    MUTATION_MANIFEST = "mutation_manifest"


class CandidateMaterialSourcePartition(StrEnum):
    """Host-attested origin of bundle material, including rejected origins."""

    REVIEWED_BASELINE = "reviewed_baseline"
    DEVELOPMENT = "development"
    LIVE_INCIDENT = "live_incident"
    PROTECTED_VALIDATION = "protected_validation"
    FINAL_TEST = "final_test"


_ALLOWED_SOURCE_PARTITIONS = {
    CandidateMaterialSourcePartition.REVIEWED_BASELINE,
    CandidateMaterialSourcePartition.DEVELOPMENT,
}


_MEDIA_TYPE_BY_ROLE = {
    CandidateBundleArtifactRole.CANDIDATE_MANIFEST: CANDIDATE_MANIFEST_MEDIA_TYPE,
    CandidateBundleArtifactRole.COORDINATION_POLICY: COORDINATION_POLICY_MEDIA_TYPE,
    CandidateBundleArtifactRole.IMPROVER: IMPROVER_MEDIA_TYPE,
    CandidateBundleArtifactRole.MUTATION_MANIFEST: MUTATION_MANIFEST_MEDIA_TYPE,
}

_ALLOWED_MEDIA_TYPES_BY_ROLE = {
    **{
        role: frozenset({media_type})
        for role, media_type in _MEDIA_TYPE_BY_ROLE.items()
    },
    CandidateBundleArtifactRole.IMPROVER: frozenset(
        {IMPROVER_MEDIA_TYPE, LEGACY_IMPROVER_MEDIA_TYPE}
    ),
}


class _BundleModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class CandidateBundleArtifactReference(_BundleModel):
    """Byte identity for one role in the closed candidate bundle."""

    role: CandidateBundleArtifactRole
    media_type: str = Field(min_length=1, max_length=100)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=1, le=_MAX_ARTIFACT_BYTES)

    @model_validator(mode="after")
    def validate_media_type(self) -> Self:
        if self.media_type not in _ALLOWED_MEDIA_TYPES_BY_ROLE[self.role]:
            raise ValueError(f"invalid media type for {self.role.value}")
        return self


class CandidateMaterialAttestation(_BundleModel):
    """Signed host statement binding exact bytes to an evidence partition."""

    role: CandidateBundleArtifactRole
    artifact_sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=1, le=_MAX_ARTIFACT_BYTES)
    source_partition: CandidateMaterialSourcePartition
    provenance_sha256: str = Field(pattern=SHA256_PATTERN)
    issued_by: str = Field(pattern=IDENTIFIER_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_media_type(self) -> Self:
        if self.media_type not in _ALLOWED_MEDIA_TYPES_BY_ROLE[self.role]:
            raise ValueError(f"invalid attested media type for {self.role.value}")
        return self


class ImproverMutationReceipt(_BundleModel):
    """Signed host receipt for an improver adapter's exact output bytes."""

    parent_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    parent_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    parent_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_sha256: str = Field(pattern=SHA256_PATTERN)
    child_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    round_sha256: str = Field(pattern=SHA256_PATTERN)
    worker_result_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_record_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    source_partition: CandidateMaterialSourcePartition
    provenance_sha256: str = Field(pattern=SHA256_PATTERN)
    issued_by: str = Field(pattern=IDENTIFIER_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)


class CandidateBundleAttestations(_BundleModel):
    """Closed attestation set: one statement for every admitted artifact."""

    candidate_manifest: CandidateMaterialAttestation
    coordination_policy: CandidateMaterialAttestation
    improver: CandidateMaterialAttestation
    mutation_manifest: CandidateMaterialAttestation | None = None

    @model_validator(mode="after")
    def validate_roles(self) -> Self:
        expected = (
            (self.candidate_manifest, CandidateBundleArtifactRole.CANDIDATE_MANIFEST),
            (
                self.coordination_policy,
                CandidateBundleArtifactRole.COORDINATION_POLICY,
            ),
            (self.improver, CandidateBundleArtifactRole.IMPROVER),
        )
        if any(attestation.role is not role for attestation, role in expected):
            raise ValueError("bundle attestation is assigned to the wrong role")
        if self.mutation_manifest is not None and (
            self.mutation_manifest.role
            is not CandidateBundleArtifactRole.MUTATION_MANIFEST
        ):
            raise ValueError("mutation attestation is assigned to the wrong role")
        return self


class CandidateArtifactAttestationVerifier(Protocol):
    """Trusted-host verification boundary used for bundle reads and writes."""

    def verify_artifact(
        self,
        attestation: CandidateMaterialAttestation,
        reference: CandidateBundleArtifactReference,
    ) -> None:
        """Reject an invalid artifact signature or identity binding."""

    def verify_improver_mutation(
        self,
        receipt: ImproverMutationReceipt,
        parent: CandidateBundle,
        mutation: MutationManifest,
        child_improver: CandidateBundleArtifactReference,
        child_candidate_sha256: str,
    ) -> None:
        """Reject an invalid trusted improver-generation receipt."""


class CandidateBundleManifest(_BundleModel):
    """Content-addressed index of the four permitted candidate artifacts."""

    schema_version: Literal[BUNDLE_SCHEMA_VERSION] = BUNDLE_SCHEMA_VERSION
    candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    parent_candidate_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
    )
    generation: int = Field(ge=0, le=10_000)
    mutation_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    parent_bundle_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    candidate_manifest: CandidateBundleArtifactReference
    coordination_policy: CandidateBundleArtifactReference
    improver: CandidateBundleArtifactReference
    mutation_manifest: CandidateBundleArtifactReference | None = None
    attestations: CandidateBundleAttestations
    improver_mutation_receipt: ImproverMutationReceipt | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        expected_roles = (
            (self.candidate_manifest, CandidateBundleArtifactRole.CANDIDATE_MANIFEST),
            (
                self.coordination_policy,
                CandidateBundleArtifactRole.COORDINATION_POLICY,
            ),
            (self.improver, CandidateBundleArtifactRole.IMPROVER),
        )
        if any(reference.role is not role for reference, role in expected_roles):
            raise ValueError("bundle artifact is assigned to the wrong role")
        if self.mutation_manifest is not None and (
            self.mutation_manifest.role
            is not CandidateBundleArtifactRole.MUTATION_MANIFEST
        ):
            raise ValueError("bundle mutation artifact is assigned to the wrong role")
        if self.generation == 0:
            if any(
                value is not None
                for value in (
                    self.parent_candidate_id,
                    self.mutation_sha256,
                    self.parent_bundle_sha256,
                    self.mutation_manifest,
                    self.attestations.mutation_manifest,
                    self.improver_mutation_receipt,
                )
            ):
                raise ValueError("root bundles reject descendant-only material")
        elif any(
            value is None
            for value in (
                self.parent_candidate_id,
                self.mutation_sha256,
                self.parent_bundle_sha256,
                self.mutation_manifest,
                self.attestations.mutation_manifest,
            )
        ):
            raise ValueError("descendant bundles require parent and mutation material")
        return self

    @property
    def bundle_sha256(self) -> str:
        """Digest of the complete canonical manifest bytes stored by the CAS."""

        return canonical_sha256(self)


class CandidateBundleMaterials(_BundleModel):
    """The only material admitted to a candidate bundle.

    Candidate and mutation bytes are derived from their validated shared models,
    so callers cannot provide an alternate serialization for either artifact.
    """

    candidate: CandidateManifest
    policy_bytes: bytes = Field(min_length=1, max_length=_MAX_ARTIFACT_BYTES)
    improver_bytes: bytes = Field(min_length=1, max_length=_MAX_ARTIFACT_BYTES)
    mutation: MutationManifest | None = None

    @model_validator(mode="after")
    def verify_materials(self) -> Self:
        candidate, _ = _canonical_model(self.candidate, CandidateManifest, "candidate")
        object.__setattr__(self, "candidate", candidate)

        try:
            policy = CoordinationPolicySnapshot.model_validate_json(self.policy_bytes)
        except (ValueError, json.JSONDecodeError) as exc:
            raise CandidateBundleVerificationError(
                "coordination policy is not a valid policy snapshot"
            ) from exc
        if policy.canonical_bytes != self.policy_bytes:
            raise CandidateBundleVerificationError(
                "coordination policy bytes are not canonical"
            )
        if policy.reference != candidate.policy:
            raise CandidateBundleVerificationError(
                "coordination policy bytes do not match the candidate reference"
            )

        if candidate.improver.kind is not ArtifactKind.IMPROVER:
            raise CandidateBundleVerificationError(
                "candidate improver reference has the wrong artifact kind"
            )
        if candidate.improver.media_type not in {
            IMPROVER_MEDIA_TYPE,
            LEGACY_IMPROVER_MEDIA_TYPE,
        }:
            raise CandidateBundleVerificationError(
                "candidate improver has an unsupported media type"
            )
        if _sha256(self.improver_bytes) != candidate.improver.sha256:
            raise CandidateBundleVerificationError(
                "improver bytes do not match the candidate reference"
            )
        typed_improver: ImproverArtifact | None = None
        if candidate.improver.media_type == IMPROVER_MEDIA_TYPE:
            try:
                typed_improver = ImproverArtifact.model_validate_json(
                    self.improver_bytes
                )
            except ValueError as exc:
                raise CandidateBundleVerificationError(
                    "typed improver is not a valid schema-versioned artifact"
                ) from exc
            if (
                typed_improver.canonical_bytes != self.improver_bytes
                or typed_improver.reference != candidate.improver
            ):
                raise CandidateBundleVerificationError(
                    "typed improver bytes are not canonical or reference-bound"
                )
        else:
            try:
                self.improver_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CandidateBundleVerificationError(
                    "legacy improver markdown must be valid UTF-8"
                ) from exc

        mutation = self.mutation
        if candidate.generation == 0:
            if mutation is not None:
                raise CandidateBundleVerificationError(
                    "root candidates reject mutation material"
                )
        elif mutation is None:
            raise CandidateBundleVerificationError(
                "descendant candidates require mutation material"
            )
        else:
            mutation, _ = _canonical_model(
                mutation,
                MutationManifest,
                "mutation",
            )
            object.__setattr__(self, "mutation", mutation)
            if candidate.mutation_sha256 != mutation.mutation_sha256:
                raise CandidateBundleVerificationError(
                    "mutation bytes do not match the candidate mutation"
                )

        _reject_prohibited_json(policy.model_dump(mode="json"), "policy")
        if typed_improver is None:
            _reject_prohibited_text(self.improver_bytes, "improver")
        else:
            _reject_prohibited_json(
                typed_improver.model_dump(mode="json"),
                "improver",
            )
        if mutation is not None:
            _reject_prohibited_json(
                mutation.model_dump(mode="json"),
                "mutation",
            )
            for operation in mutation.operations:
                if operation.path == "/evaluator" or operation.path.startswith(
                    (
                        "/evaluator/",
                        "/evaluation/",
                        "/protected/",
                        "/final/",
                        "/live_incident/",
                        "/feedback/",
                        "/secrets/",
                        "/credentials/",
                    )
                ):
                    raise CandidateBundleVerificationError(
                        "mutation contains prohibited material"
                    )
        return self

    @property
    def candidate_bytes(self) -> bytes:
        return canonical_json_bytes(self.candidate)

    @property
    def mutation_bytes(self) -> bytes | None:
        if self.mutation is None:
            return None
        return canonical_json_bytes(self.mutation)


class CandidateArtifactAttestationAuthority:
    """Issue and verify HMAC-authenticated host evidence.

    The signing key stays in the trusted host and is never part of a bundle.
    """

    def __init__(self, signing_key: bytes, *, key_id: str) -> None:
        if len(signing_key) < 32:
            raise ValueError("artifact attestation key must be at least 32 bytes")
        if not re.fullmatch(IDENTIFIER_PATTERN, key_id):
            raise ValueError("artifact attestation key ID is invalid")
        self._signing_key = bytes(signing_key)
        self._key_id = key_id

    def issue_artifact(
        self,
        role: CandidateBundleArtifactRole,
        payload: bytes,
        *,
        source_partition: CandidateMaterialSourcePartition,
        provenance_sha256: str,
    ) -> CandidateMaterialAttestation:
        reference = _reference(role, payload)
        unsigned = CandidateMaterialAttestation(
            role=role,
            artifact_sha256=reference.sha256,
            media_type=reference.media_type,
            size_bytes=reference.size_bytes,
            source_partition=source_partition,
            provenance_sha256=provenance_sha256,
            issued_by=self._key_id,
            signature_sha256="0" * 64,
        )
        return CandidateMaterialAttestation.model_validate(
            {
                **unsigned.model_dump(mode="json", exclude={"signature_sha256"}),
                "signature_sha256": self._signature(unsigned, "signature_sha256"),
            }
        )

    def issue_improver_mutation_receipt(
        self,
        *,
        parent: CandidateBundle,
        mutation: MutationManifest,
        child_improver_bytes: bytes,
        runtime_authorization_sha256: str,
        runtime_identity_sha256: str,
        source_manifest_sha256: str,
        round_sha256: str,
        worker_result_sha256: str,
        selected_record_sha256: str,
        selected_candidate_sha256: str,
        source_partition: CandidateMaterialSourcePartition,
        provenance_sha256: str,
    ) -> ImproverMutationReceipt:
        if mutation.target is not MutationTarget.IMPROVER:
            raise ValueError("improver receipt requires an improver mutation")
        unsigned = ImproverMutationReceipt(
            parent_bundle_sha256=parent.manifest.bundle_sha256,
            parent_candidate_sha256=parent.materials.candidate.candidate_sha256,
            parent_improver_sha256=parent.materials.candidate.improver.sha256,
            mutation_sha256=mutation.mutation_sha256,
            child_improver_sha256=_sha256(child_improver_bytes),
            runtime_authorization_sha256=runtime_authorization_sha256,
            runtime_identity_sha256=runtime_identity_sha256,
            source_manifest_sha256=source_manifest_sha256,
            round_sha256=round_sha256,
            worker_result_sha256=worker_result_sha256,
            selected_record_sha256=selected_record_sha256,
            selected_candidate_sha256=selected_candidate_sha256,
            source_partition=source_partition,
            provenance_sha256=provenance_sha256,
            issued_by=self._key_id,
            signature_sha256="0" * 64,
        )
        return ImproverMutationReceipt.model_validate(
            {
                **unsigned.model_dump(mode="json", exclude={"signature_sha256"}),
                "signature_sha256": self._signature(unsigned, "signature_sha256"),
            }
        )

    def verify_artifact(
        self,
        attestation: CandidateMaterialAttestation,
        reference: CandidateBundleArtifactReference,
    ) -> None:
        if (
            attestation.issued_by != self._key_id
            or not _attestation_matches(attestation, reference)
            or not hmac.compare_digest(
                attestation.signature_sha256,
                self._signature(attestation, "signature_sha256"),
            )
        ):
            raise CandidateBundleVerificationError(
                f"invalid host attestation for {reference.role.value}"
            )

    def verify_improver_mutation(
        self,
        receipt: ImproverMutationReceipt,
        parent: CandidateBundle,
        mutation: MutationManifest,
        child_improver: CandidateBundleArtifactReference,
        child_candidate_sha256: str,
    ) -> None:
        parent_candidate = parent.materials.candidate
        expected = (
            (receipt.parent_bundle_sha256, parent.manifest.bundle_sha256),
            (receipt.parent_candidate_sha256, parent_candidate.candidate_sha256),
            (receipt.parent_improver_sha256, parent_candidate.improver.sha256),
            (receipt.mutation_sha256, mutation.mutation_sha256),
            (receipt.child_improver_sha256, child_improver.sha256),
            (receipt.selected_candidate_sha256, child_candidate_sha256),
            (receipt.issued_by, self._key_id),
        )
        identity_mismatch = any(actual != value for actual, value in expected)
        signature_valid = hmac.compare_digest(
            receipt.signature_sha256,
            self._signature(receipt, "signature_sha256"),
        )
        if identity_mismatch or not signature_valid:
            raise CandidateBundleVerificationError(
                "invalid trusted improver mutation receipt"
            )

    def _signature(self, value: BaseModel, signature_field: str) -> str:
        payload = canonical_json_bytes(
            {
                "contract": type(value).__name__,
                "material": value.model_dump(
                    mode="json",
                    exclude={signature_field},
                ),
            }
        )
        return hmac.new(self._signing_key, payload, sha256).hexdigest()


class CandidateBundle(_BundleModel):
    """A verified manifest paired with its exact artifact material."""

    manifest: CandidateBundleManifest
    materials: CandidateBundleMaterials

    @model_validator(mode="after")
    def verify_manifest_bindings(self) -> Self:
        candidate = self.materials.candidate
        expected_scalars = (
            (self.manifest.candidate_id, candidate.candidate_id),
            (self.manifest.candidate_sha256, candidate.candidate_sha256),
            (self.manifest.parent_candidate_id, candidate.parent_candidate_id),
            (self.manifest.generation, candidate.generation),
            (self.manifest.mutation_sha256, candidate.mutation_sha256),
        )
        if any(actual != expected for actual, expected in expected_scalars):
            raise CandidateBundleVerificationError(
                "bundle manifest does not match its candidate"
            )

        expected_references = (
            (
                self.manifest.candidate_manifest,
                _reference(
                    CandidateBundleArtifactRole.CANDIDATE_MANIFEST,
                    self.materials.candidate_bytes,
                ),
            ),
            (
                self.manifest.coordination_policy,
                _reference(
                    CandidateBundleArtifactRole.COORDINATION_POLICY,
                    self.materials.policy_bytes,
                ),
            ),
            (
                self.manifest.improver,
                _reference(
                    CandidateBundleArtifactRole.IMPROVER,
                    self.materials.improver_bytes,
                    media_type=candidate.improver.media_type,
                ),
            ),
        )
        if any(actual != expected for actual, expected in expected_references):
            raise CandidateBundleVerificationError(
                "bundle artifact reference does not match exact bytes"
            )
        mutation_bytes = self.materials.mutation_bytes
        expected_mutation = (
            _reference(
                CandidateBundleArtifactRole.MUTATION_MANIFEST,
                mutation_bytes,
            )
            if mutation_bytes is not None
            else None
        )
        if self.manifest.mutation_manifest != expected_mutation:
            raise CandidateBundleVerificationError(
                "bundle mutation reference does not match exact bytes"
            )
        attestation_pairs = (
            (
                self.manifest.attestations.candidate_manifest,
                self.manifest.candidate_manifest,
            ),
            (
                self.manifest.attestations.coordination_policy,
                self.manifest.coordination_policy,
            ),
            (self.manifest.attestations.improver, self.manifest.improver),
        )
        if any(
            not _attestation_matches(attestation, reference)
            for attestation, reference in attestation_pairs
        ):
            raise CandidateBundleVerificationError(
                "bundle attestation does not match its artifact"
            )
        mutation_attestation = self.manifest.attestations.mutation_manifest
        if expected_mutation is None:
            if mutation_attestation is not None:
                raise CandidateBundleVerificationError(
                    "root bundle cannot attest mutation material"
                )
        elif mutation_attestation is None or not _attestation_matches(
            mutation_attestation,
            expected_mutation,
        ):
            raise CandidateBundleVerificationError(
                "mutation attestation does not match its artifact"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        candidate: CandidateManifest,
        policy_bytes: bytes,
        improver_bytes: bytes,
        attestations: CandidateBundleAttestations,
        attestation_verifier: CandidateArtifactAttestationVerifier,
        mutation: MutationManifest | None = None,
        parent: CandidateBundle | None = None,
        improver_mutation_receipt: ImproverMutationReceipt | None = None,
    ) -> CandidateBundle:
        try:
            materials = CandidateBundleMaterials(
                candidate=candidate,
                policy_bytes=policy_bytes,
                improver_bytes=improver_bytes,
                mutation=mutation,
            )
        except ValidationError as exc:
            raise CandidateBundleVerificationError(str(exc)) from exc
        candidate = materials.candidate
        mutation_bytes = materials.mutation_bytes
        try:
            manifest = CandidateBundleManifest(
                candidate_id=candidate.candidate_id,
                candidate_sha256=candidate.candidate_sha256,
                parent_candidate_id=candidate.parent_candidate_id,
                generation=candidate.generation,
                mutation_sha256=candidate.mutation_sha256,
                parent_bundle_sha256=(
                    parent.manifest.bundle_sha256 if parent is not None else None
                ),
                candidate_manifest=_reference(
                    CandidateBundleArtifactRole.CANDIDATE_MANIFEST,
                    materials.candidate_bytes,
                ),
                coordination_policy=_reference(
                    CandidateBundleArtifactRole.COORDINATION_POLICY,
                    materials.policy_bytes,
                ),
                improver=_reference(
                    CandidateBundleArtifactRole.IMPROVER,
                    materials.improver_bytes,
                    media_type=candidate.improver.media_type,
                ),
                mutation_manifest=(
                    _reference(
                        CandidateBundleArtifactRole.MUTATION_MANIFEST,
                        mutation_bytes,
                    )
                    if mutation_bytes is not None
                    else None
                ),
                attestations=attestations,
                improver_mutation_receipt=improver_mutation_receipt,
            )
            bundle = cls(manifest=manifest, materials=materials)
        except ValidationError as exc:
            raise CandidateBundleVerificationError(str(exc)) from exc
        bundle.verify_attestations(attestation_verifier)
        bundle.verify_parent(parent, attestation_verifier)
        return bundle

    @classmethod
    def from_artifact_bytes(
        cls,
        *,
        manifest: CandidateBundleManifest,
        candidate_bytes: bytes,
        policy_bytes: bytes,
        improver_bytes: bytes,
        mutation_bytes: bytes | None,
        parent: CandidateBundle | None,
        attestation_verifier: CandidateArtifactAttestationVerifier,
    ) -> CandidateBundle:
        candidate = _parse_canonical_bytes(
            candidate_bytes,
            CandidateManifest,
            "candidate",
        )
        mutation = (
            _parse_canonical_bytes(
                mutation_bytes,
                MutationManifest,
                "mutation",
            )
            if mutation_bytes is not None
            else None
        )
        bundle = cls(
            manifest=manifest,
            materials=CandidateBundleMaterials(
                candidate=candidate,
                policy_bytes=policy_bytes,
                improver_bytes=improver_bytes,
                mutation=mutation,
            ),
        )
        bundle.verify_attestations(attestation_verifier)
        bundle.verify_parent(parent, attestation_verifier)
        return bundle

    def verify_attestations(
        self,
        verifier: CandidateArtifactAttestationVerifier,
    ) -> None:
        """Require signed, allowlisted provenance for every artifact."""

        pairs = (
            (
                self.manifest.attestations.candidate_manifest,
                self.manifest.candidate_manifest,
            ),
            (
                self.manifest.attestations.coordination_policy,
                self.manifest.coordination_policy,
            ),
            (self.manifest.attestations.improver, self.manifest.improver),
        )
        mutation_reference = self.manifest.mutation_manifest
        mutation_attestation = self.manifest.attestations.mutation_manifest
        if mutation_reference is not None and mutation_attestation is not None:
            pairs = (*pairs, (mutation_attestation, mutation_reference))
        for attestation, reference in pairs:
            if attestation.source_partition not in _ALLOWED_SOURCE_PARTITIONS:
                raise CandidateBundleVerificationError(
                    f"{reference.role.value} uses a prohibited source partition"
                )
            verifier.verify_artifact(attestation, reference)

    def verify_parent(
        self,
        parent: CandidateBundle | None,
        attestation_verifier: CandidateArtifactAttestationVerifier,
    ) -> None:
        """Verify one explicit lineage edge; this does not run an improver."""

        candidate = self.materials.candidate
        mutation = self.materials.mutation
        if candidate.generation == 0:
            if parent is not None:
                raise CandidateBundleVerificationError(
                    "root bundle cannot be attached to a parent"
                )
            if self.manifest.improver_mutation_receipt is not None:
                raise CandidateBundleVerificationError(
                    "root bundle cannot contain an improver mutation receipt"
                )
            return
        if parent is None or mutation is None:
            raise CandidateBundleVerificationError(
                "descendant bundle is missing its verified parent or mutation"
            )
        try:
            parent = CandidateBundle.model_validate(parent.model_dump())
        except ValidationError as exc:
            raise CandidateBundleVerificationError(
                "descendant parent bundle failed verification"
            ) from exc
        parent.verify_attestations(attestation_verifier)
        parent_candidate = parent.materials.candidate
        if self.manifest.parent_bundle_sha256 != parent.manifest.bundle_sha256:
            raise CandidateBundleVerificationError(
                "descendant references an unrelated parent bundle"
            )
        if candidate.parent_candidate_id != parent_candidate.candidate_id:
            raise CandidateBundleVerificationError(
                "descendant references an unrelated parent candidate"
            )
        if candidate.generation != parent_candidate.generation + 1:
            raise CandidateBundleVerificationError(
                "descendant generation is not the parent generation plus one"
            )
        if mutation.parent_policy != parent_candidate.policy:
            raise CandidateBundleVerificationError(
                "mutation is bound to an unrelated parent policy"
            )
        if mutation.generated_by != parent_candidate.improver:
            raise CandidateBundleVerificationError(
                "mutation generated_by does not identify the parent improver bytes"
            )
        if mutation.target is MutationTarget.COORDINATION_POLICY:
            if self.manifest.improver_mutation_receipt is not None:
                raise CandidateBundleVerificationError(
                    "policy mutation cannot contain an improver mutation receipt"
                )
            if candidate.improver != parent_candidate.improver:
                raise CandidateBundleVerificationError(
                    "policy descendants must inherit the parent improver exactly"
                )
            if candidate.policy == parent_candidate.policy:
                raise CandidateBundleVerificationError(
                    "policy mutation did not change the candidate policy"
                )
            self._verify_policy_mutation_output(parent, mutation)
        elif mutation.target is MutationTarget.IMPROVER:
            if candidate.policy != parent_candidate.policy:
                raise CandidateBundleVerificationError(
                    "improver descendants must inherit the parent policy exactly"
                )
            if candidate.improver == parent_candidate.improver:
                raise CandidateBundleVerificationError(
                    "improver mutation did not change the candidate improver"
                )
            receipt = self.manifest.improver_mutation_receipt
            if receipt is None:
                raise CandidateBundleVerificationError(
                    "improver mutation requires a trusted generation receipt"
                )
            if receipt.source_partition not in _ALLOWED_SOURCE_PARTITIONS:
                raise CandidateBundleVerificationError(
                    "improver mutation receipt uses a prohibited source partition"
                )
            attestation_verifier.verify_improver_mutation(
                receipt,
                parent,
                mutation,
                self.manifest.improver,
                candidate.candidate_sha256,
            )
        else:  # pragma: no cover - closed shared enum
            raise CandidateBundleVerificationError("unsupported mutation target")

    def _verify_policy_mutation_output(
        self,
        parent: CandidateBundle,
        mutation: MutationManifest,
    ) -> None:
        try:
            parent_policy = CoordinationPolicySnapshot.model_validate_json(
                parent.materials.policy_bytes
            )
            adapter = A2PolicyArtifactAdapter((parent_policy,))
            expected_reference = adapter.apply_mutation(
                parent.materials.candidate.policy,
                mutation,
            )
            expected_bytes = adapter.canonical_payload(expected_reference)
        except (KeyError, ValueError) as exc:
            raise CandidateBundleVerificationError(
                "policy mutation cannot be replayed by the trusted schema adapter"
            ) from exc
        if (
            expected_reference != self.materials.candidate.policy
            or expected_bytes != self.materials.policy_bytes
        ):
            raise CandidateBundleVerificationError(
                "policy mutation operations do not produce the bound child policy"
            )


def _reference(
    role: CandidateBundleArtifactRole,
    payload: bytes,
    *,
    media_type: str | None = None,
) -> CandidateBundleArtifactReference:
    if media_type is None:
        media_type = _inferred_media_type(role, payload)
    return CandidateBundleArtifactReference(
        role=role,
        media_type=media_type,
        sha256=_sha256(payload),
        size_bytes=len(payload),
    )


def _inferred_media_type(
    role: CandidateBundleArtifactRole,
    payload: bytes,
) -> str:
    if role is not CandidateBundleArtifactRole.IMPROVER:
        return _MEDIA_TYPE_BY_ROLE[role]
    try:
        improver = ImproverArtifact.model_validate_json(payload)
    except ValueError:
        return LEGACY_IMPROVER_MEDIA_TYPE
    if improver.canonical_bytes != payload:
        return LEGACY_IMPROVER_MEDIA_TYPE
    return IMPROVER_MEDIA_TYPE


def _attestation_matches(
    attestation: CandidateMaterialAttestation,
    reference: CandidateBundleArtifactReference,
) -> bool:
    return (
        attestation.role is reference.role
        and attestation.artifact_sha256 == reference.sha256
        and attestation.media_type == reference.media_type
        and attestation.size_bytes == reference.size_bytes
    )


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _canonical_model(
    value: Any,
    model: type[CandidateManifest] | type[MutationManifest],
    label: str,
) -> tuple[CandidateManifest | MutationManifest, bytes]:
    try:
        validated = model.model_validate(value.model_dump(mode="json"))
    except (AttributeError, ValueError) as exc:
        raise CandidateBundleVerificationError(
            f"{label} manifest failed shared-contract validation"
        ) from exc
    payload = canonical_json_bytes(validated)
    return validated, payload


def _parse_canonical_bytes(
    payload: bytes,
    model: type[CandidateManifest] | type[MutationManifest],
    label: str,
) -> CandidateManifest | MutationManifest:
    try:
        parsed = model.model_validate_json(payload)
    except ValueError as exc:
        raise CandidateBundleVerificationError(
            f"stored {label} manifest failed shared-contract validation"
        ) from exc
    if canonical_json_bytes(parsed) != payload:
        raise CandidateBundleVerificationError(
            f"stored {label} manifest bytes are not canonical"
        )
    return parsed


_PROHIBITED_KEYS = {
    "access_token",
    "api_key",
    "case_sha256",
    "client_secret",
    "credentials",
    "evaluator_feedback",
    "evaluator_key",
    "expected_outcomes",
    "feedback",
    "final_feedback",
    "incident_id",
    "live_incident",
    "oracle",
    "password",
    "patient_id",
    "private_key",
    "protected_feedback",
    "raw_health_data",
    "refresh_token",
    "scenario_id",
    "secret",
    "secrets",
    "signing_key",
    "tool_responses",
}
_PROHIBITED_PARTITIONS = {"live_incident", "protected_validation", "final_test"}
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?i)(?:api[_ -]?key|access[_ -]?token|client[_ -]?secret|password|"
    rb"private[_ -]?key|signing[_ -]?key)\s*[:=]\s*[^\s]+"
)
_STRUCTURED_PROHIBITED_FIELD = re.compile(
    rb"(?i)[\"']?(?:incident_id|patient_id|scenario_id|case_sha256|oracle|"
    rb"evaluator_key|protected_feedback|final_feedback)[\"']?\s*[:=]"
)


def _reject_prohibited_json(value: Any, label: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _PROHIBITED_KEYS:
                raise CandidateBundleVerificationError(
                    f"{label} contains prohibited material"
                )
            if normalized in {"partition", "source_partition"} and (
                isinstance(child, str)
                and child.strip().lower().replace("-", "_")
                in _PROHIBITED_PARTITIONS
            ):
                raise CandidateBundleVerificationError(
                    f"{label} contains prohibited partition material"
                )
            _reject_prohibited_json(child, label)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_prohibited_json(child, label)
    elif isinstance(value, str):
        _reject_prohibited_text(value.encode("utf-8"), label)


def _reject_prohibited_text(payload: bytes, label: str) -> None:
    if b"-----BEGIN " in payload and b"PRIVATE KEY-----" in payload:
        raise CandidateBundleVerificationError(f"{label} contains a private key")
    if _CREDENTIAL_ASSIGNMENT.search(payload):
        raise CandidateBundleVerificationError(
            f"{label} contains credential-shaped material"
        )
    if _STRUCTURED_PROHIBITED_FIELD.search(payload):
        raise CandidateBundleVerificationError(
            f"{label} contains prohibited structured material"
        )
