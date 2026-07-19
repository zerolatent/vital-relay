"""HMAC-authenticated, immutable filesystem storage for DGM experiments."""

from __future__ import annotations

from hashlib import sha256
import hmac
import os
from pathlib import Path
import re
from stat import S_ISDIR, S_ISREG
from tempfile import NamedTemporaryFile
from typing import Literal, Self, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from vital_relay.evolution.bundle_store import CandidateBundleStore
from vital_relay.evolution.bundles import CandidateBundle
from vital_relay.evolution.contracts import (
    EvaluationReport,
    IDENTIFIER_PATTERN,
    PartitionName,
    SHA256_PATTERN,
)
from vital_relay.evolution.dgm import (
    DGMClaim,
    DGMComparison,
    DGMExperimentPlan,
    DGMExperimentResult,
    DGMLineageRecord,
)
from vital_relay.evolution.evaluator import HostIntegrityAuthority
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.evolution.mutation import MutationRoundResult


_SAFE_REF_PART = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class DGMStoreIntegrityError(RuntimeError):
    """Stored bytes, authentication, or immutable references failed closed."""


class _StoreModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class _ObjectAuthentication(_StoreModel):
    kind: str = Field(pattern=IDENTIFIER_PATTERN)
    object_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=1, le=64_000_000)
    issued_by: str = Field(pattern=IDENTIFIER_PATTERN)
    hmac_sha256: str = Field(pattern=SHA256_PATTERN)


class _ImmutableReference(_StoreModel):
    namespace: str = Field(pattern=IDENTIFIER_PATTERN)
    reference_key: str = Field(min_length=1, max_length=512)
    object_sha256: str = Field(pattern=SHA256_PATTERN)
    issued_by: str = Field(pattern=IDENTIFIER_PATTERN)
    hmac_sha256: str = Field(pattern=SHA256_PATTERN)


class DGMExperimentManifest(_StoreModel):
    schema_version: Literal[1] = 1
    experiment_id: UUID
    plan_sha256: str = Field(pattern=SHA256_PATTERN)
    round_sha256s: tuple[str, ...]
    bundle_sha256s: tuple[str, ...]
    lineage_sha256s: tuple[str, ...]
    evaluation_sha256s: tuple[str, ...]
    comparison_sha256s: tuple[str, ...]
    claim_sha256: str = Field(pattern=SHA256_PATTERN)
    result_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self, info: ValidationInfo) -> Self:
        digest_sets = (
            self.round_sha256s,
            self.bundle_sha256s,
            self.lineage_sha256s,
            self.evaluation_sha256s,
            self.comparison_sha256s,
        )
        if any(
            len(values) != len(set(values)) or tuple(sorted(values)) != values
            for values in digest_sets
        ):
            raise ValueError("DGM manifest object sets must be unique and sorted")
        if not (info.context and info.context.get("build_manifest_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"manifest_sha256"})
            )
            if self.manifest_sha256 != expected:
                raise ValueError("DGM top-level manifest hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMExperimentManifest:
        material = cls.model_validate(
            {**values, "manifest_sha256": "0" * 64},
            context={"build_manifest_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "manifest_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"manifest_sha256"}
                    )
                ),
            }
        )


class FilesystemDGMExperimentStore:
    """Authenticated CAS plus write-once semantic references.

    Payload SHA-256 catches altered bytes; a separate HMAC authenticates both
    the payload kind and digest.  Write-once references close the substitution
    gap that a bare content-addressed object store leaves for plans, reports,
    lineage edges, and top-level experiment manifests.
    """

    def __init__(
        self,
        root: Path,
        signing_key: bytes,
        *,
        key_id: str,
        bundle_store: CandidateBundleStore,
        integrity_authority: HostIntegrityAuthority,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("DGM store authentication key must be at least 32 bytes")
        if re.fullmatch(IDENTIFIER_PATTERN, key_id) is None:
            raise ValueError("DGM store key ID is invalid")
        self._key = bytes(signing_key)
        self._key_id = key_id
        self._bundle_store = bundle_store
        self._integrity_authority = integrity_authority
        self._root = self._prepare_root(root)
        self._objects = self._safe_directory(self._root / "objects")
        self._auth = self._safe_directory(self._root / "auth")
        self._refs = self._safe_directory(self._root / "refs")

    def put_plan(self, plan: DGMExperimentPlan) -> str:
        plan = DGMExperimentPlan.model_validate(plan)
        digest = self._put_object("dgm_plan", plan)
        self._bind_reference("plans", str(plan.experiment_id), digest)
        return digest

    def get_plan(self, experiment_id: UUID) -> DGMExperimentPlan:
        digest = self._reference("plans", str(experiment_id))
        return self._get_object(digest, "dgm_plan", DGMExperimentPlan)

    def put_round(
        self,
        experiment_id: UUID,
        result: MutationRoundResult,
    ) -> str:
        result = MutationRoundResult.model_validate(result)
        digest = self._put_object("dgm_round", result)
        self._bind_reference(
            "rounds",
            f"{experiment_id}/{result.round_id}",
            digest,
        )
        return digest

    def get_round(
        self,
        experiment_id: UUID,
        round_id: UUID,
    ) -> MutationRoundResult:
        digest = self._reference("rounds", f"{experiment_id}/{round_id}")
        return self._get_object(digest, "dgm_round", MutationRoundResult)

    def put_bundle(self, experiment_id: UUID, bundle: CandidateBundle) -> str:
        bundle = CandidateBundle.model_validate(bundle)
        stored = self._bundle_store.get(bundle.manifest.bundle_sha256)
        if stored != bundle:
            raise DGMStoreIntegrityError("DGM bundle differs from verified bundle CAS")
        digest = self._put_object("dgm_bundle", bundle)
        self._bind_reference(
            "bundles",
            f"{experiment_id}/{bundle.materials.candidate.candidate_id}",
            digest,
        )
        return digest

    def get_bundle(
        self,
        experiment_id: UUID,
        candidate_id: str,
    ) -> CandidateBundle:
        digest = self._reference("bundles", f"{experiment_id}/{candidate_id}")
        bundle = self._get_object(digest, "dgm_bundle", CandidateBundle)
        if self._bundle_store.get(bundle.manifest.bundle_sha256) != bundle:
            raise DGMStoreIntegrityError("authenticated bundle CAS was substituted")
        return bundle

    def put_lineage(self, record: DGMLineageRecord) -> str:
        record = DGMLineageRecord.model_validate(record)
        self.get_plan(record.experiment_id)
        parent = self._candidate_bundle_for_digest(
            record.experiment_id, record.parent_candidate_sha256
        )
        child = self._candidate_bundle_for_digest(
            record.experiment_id, record.child_candidate_sha256
        )
        mutation = child.materials.mutation
        receipt = child.manifest.improver_mutation_receipt
        if (
            mutation is None
            or parent.manifest.bundle_sha256 != record.parent_bundle_sha256
            or child.manifest.bundle_sha256 != record.child_bundle_sha256
            or child.materials.candidate.parent_candidate_id
            != parent.materials.candidate.candidate_id
            or child.materials.candidate.generation != record.generation
            or mutation.mutation_sha256 != record.mutation_sha256
            or mutation.generated_by.sha256
            != record.generated_by_improver_sha256
            or parent.materials.candidate.improver.sha256
            != record.loaded_improver_sha256
            or record.improver_receipt_sha256
            != (canonical_sha256(receipt) if receipt is not None else None)
        ):
            raise DGMStoreIntegrityError(
                "DGM lineage does not derive from stored parent and child bundles"
            )
        digest = self._put_object("dgm_lineage", record)
        self._bind_reference(
            "lineage",
            f"{record.experiment_id}/{record.child_candidate_sha256}",
            digest,
        )
        return digest

    def get_lineage(
        self,
        experiment_id: UUID,
        child_candidate_sha256: str,
    ) -> DGMLineageRecord:
        digest = self._reference(
            "lineage", f"{experiment_id}/{child_candidate_sha256}"
        )
        return self._get_object(digest, "dgm_lineage", DGMLineageRecord)

    def put_evaluation(
        self,
        experiment_id: UUID,
        report: EvaluationReport,
    ) -> str:
        report = EvaluationReport.model_validate(report)
        candidate = self._candidate_bundle_for_digest(
            experiment_id, report.candidate_sha256
        ).materials.candidate
        self._integrity_authority.verify_report(candidate, report)
        digest = self._put_object("dgm_evaluation", report)
        self._bind_reference(
            "evaluations",
            f"{experiment_id}/{report.candidate_sha256}/{report.partition.value}",
            digest,
        )
        return digest

    def get_evaluation(
        self,
        experiment_id: UUID,
        candidate_sha256: str,
        partition: PartitionName,
    ) -> EvaluationReport:
        digest = self._reference(
            "evaluations",
            f"{experiment_id}/{candidate_sha256}/{partition.value}",
        )
        report = self._get_object(
            digest, "dgm_evaluation", EvaluationReport
        )
        candidate = self._candidate_bundle_for_digest(
            experiment_id, candidate_sha256
        ).materials.candidate
        self._integrity_authority.verify_report(candidate, report)
        if report.partition is not partition:
            raise DGMStoreIntegrityError("evaluation partition was substituted")
        return report

    def put_comparison(self, comparison: DGMComparison) -> str:
        comparison = DGMComparison.model_validate(comparison)
        digest = self._put_object("dgm_comparison", comparison)
        self._bind_reference(
            "comparisons",
            f"{comparison.experiment_id}/{comparison.metric_plan.metric.value}",
            digest,
        )
        return digest

    def get_comparison(
        self,
        experiment_id: UUID,
        metric: str,
    ) -> DGMComparison:
        digest = self._reference(
            "comparisons", f"{experiment_id}/{metric}"
        )
        return self._get_object(digest, "dgm_comparison", DGMComparison)

    def publish(self, result: DGMExperimentResult) -> str:
        """Publish a complete top-level manifest exactly once."""

        result = DGMExperimentResult.model_validate(result)
        experiment_id = result.plan.experiment_id
        plan_digest = self.put_plan(result.plan)

        bundles = _result_bundles(result)
        bundle_digests = tuple(
            sorted(self.put_bundle(experiment_id, bundle) for bundle in bundles)
        )
        rounds = _result_rounds(result)
        round_digests = tuple(
            sorted(self.put_round(experiment_id, round) for round in rounds)
        )
        lineages = _result_lineages(result)
        lineage_digests = tuple(
            sorted(self.put_lineage(record) for record in lineages)
        )
        evaluations = _result_evaluations(result)
        evaluation_digests = tuple(
            sorted(
                self.put_evaluation(experiment_id, report)
                for report in evaluations
            )
        )
        comparison_digests = tuple(
            sorted(self.put_comparison(item) for item in result.comparisons)
        )
        claim_digest = self._put_object("dgm_claim", result.claim)
        result_digest = self._put_object("dgm_result", result)
        manifest = DGMExperimentManifest.create(
            experiment_id=experiment_id,
            plan_sha256=plan_digest,
            round_sha256s=round_digests,
            bundle_sha256s=bundle_digests,
            lineage_sha256s=lineage_digests,
            evaluation_sha256s=evaluation_digests,
            comparison_sha256s=comparison_digests,
            claim_sha256=claim_digest,
            result_sha256=result_digest,
        )
        manifest_digest = self._put_object("dgm_manifest", manifest)
        self._bind_reference("manifests", str(experiment_id), manifest_digest)
        return manifest_digest

    def verify_experiment(
        self,
        experiment_id: UUID,
    ) -> DGMExperimentResult:
        manifest_digest = self._reference("manifests", str(experiment_id))
        manifest = self._get_object(
            manifest_digest,
            "dgm_manifest",
            DGMExperimentManifest,
        )
        plan = self._get_object(
            manifest.plan_sha256, "dgm_plan", DGMExperimentPlan
        )
        result = self._get_object(
            manifest.result_sha256, "dgm_result", DGMExperimentResult
        )
        claim = self._get_object(
            manifest.claim_sha256, "dgm_claim", DGMClaim
        )
        if (
            manifest.experiment_id != experiment_id
            or plan != result.plan
            or claim != result.claim
        ):
            raise DGMStoreIntegrityError("top-level DGM manifest was substituted")
        expected = DGMExperimentManifest.create(
            experiment_id=experiment_id,
            plan_sha256=canonical_sha256(plan),
            round_sha256s=tuple(
                sorted(
                    canonical_sha256(item) for item in _result_rounds(result)
                )
            ),
            bundle_sha256s=tuple(
                sorted(
                    canonical_sha256(item) for item in _result_bundles(result)
                )
            ),
            lineage_sha256s=tuple(
                sorted(
                    canonical_sha256(item) for item in _result_lineages(result)
                )
            ),
            evaluation_sha256s=tuple(
                sorted(
                    canonical_sha256(item)
                    for item in _result_evaluations(result)
                )
            ),
            comparison_sha256s=tuple(
                sorted(canonical_sha256(item) for item in result.comparisons)
            ),
            claim_sha256=canonical_sha256(result.claim),
            result_sha256=canonical_sha256(result),
        )
        if expected != manifest:
            raise DGMStoreIntegrityError("DGM manifest object accounting changed")
        # Read every named object through its authenticated path.
        for digest in manifest.round_sha256s:
            self._get_object(digest, "dgm_round", MutationRoundResult)
        for digest in manifest.bundle_sha256s:
            self._get_object(digest, "dgm_bundle", CandidateBundle)
        for digest in manifest.lineage_sha256s:
            self._get_object(digest, "dgm_lineage", DGMLineageRecord)
        for digest in manifest.evaluation_sha256s:
            self._get_object(digest, "dgm_evaluation", EvaluationReport)
        for digest in manifest.comparison_sha256s:
            self._get_object(digest, "dgm_comparison", DGMComparison)
        return result

    def object_path(self, digest: str) -> Path:
        """Expose a verified object path for audit and tamper tests."""

        path = self._object_path(self._objects, digest)
        self._read_file(path)
        return path

    def _put_object(self, kind: str, value: BaseModel) -> str:
        payload = canonical_json_bytes(value)
        digest = sha256(payload).hexdigest()
        object_path = self._object_path(self._objects, digest)
        auth_path = self._object_path(self._auth, digest, suffix=".json")
        authentication = self._authentication(kind, digest, len(payload))
        self._put_immutable(object_path, payload)
        self._put_immutable(auth_path, canonical_json_bytes(authentication))
        # Re-read both after publication, including a concurrent publication.
        self._authenticated_bytes(digest, kind)
        return digest

    def _get_object(
        self,
        digest: str,
        kind: str,
        model: type[_ModelT],
    ) -> _ModelT:
        payload = self._authenticated_bytes(digest, kind)
        try:
            parsed = model.model_validate_json(payload)
        except ValueError as exc:
            raise DGMStoreIntegrityError("authenticated DGM object is malformed") from exc
        if canonical_json_bytes(parsed) != payload:
            raise DGMStoreIntegrityError("authenticated DGM object is not canonical")
        return parsed

    def _authenticated_bytes(self, digest: str, kind: str) -> bytes:
        payload = self._read_file(self._object_path(self._objects, digest))
        if sha256(payload).hexdigest() != digest:
            raise DGMStoreIntegrityError("DGM object bytes were altered")
        auth_payload = self._read_file(
            self._object_path(self._auth, digest, suffix=".json")
        )
        try:
            authentication = _ObjectAuthentication.model_validate_json(auth_payload)
        except ValueError as exc:
            raise DGMStoreIntegrityError("DGM object authentication is malformed") from exc
        expected = self._authentication(kind, digest, len(payload))
        if (
            canonical_json_bytes(authentication) != auth_payload
            or authentication != expected
        ):
            raise DGMStoreIntegrityError("DGM object authentication failed")
        return payload

    def _authentication(
        self,
        kind: str,
        digest: str,
        size_bytes: int,
    ) -> _ObjectAuthentication:
        unsigned = {
            "kind": kind,
            "object_sha256": digest,
            "size_bytes": size_bytes,
            "issued_by": self._key_id,
        }
        return _ObjectAuthentication(
            **unsigned,
            hmac_sha256=self._signature("dgm_object_v1", unsigned),
        )

    def _bind_reference(self, namespace: str, key: str, digest: str) -> None:
        path = self._reference_path(namespace, key)
        unsigned = {
            "namespace": namespace,
            "reference_key": key,
            "object_sha256": digest,
            "issued_by": self._key_id,
        }
        reference = _ImmutableReference(
            **unsigned,
            hmac_sha256=self._signature("dgm_reference_v1", unsigned),
        )
        self._put_immutable(path, canonical_json_bytes(reference))

    def _reference(self, namespace: str, key: str) -> str:
        payload = self._read_file(self._reference_path(namespace, key))
        try:
            reference = _ImmutableReference.model_validate_json(payload)
        except ValueError as exc:
            raise DGMStoreIntegrityError("DGM immutable reference is malformed") from exc
        unsigned = reference.model_dump(mode="json", exclude={"hmac_sha256"})
        if (
            canonical_json_bytes(reference) != payload
            or reference.namespace != namespace
            or reference.reference_key != key
            or reference.issued_by != self._key_id
            or not hmac.compare_digest(
                reference.hmac_sha256,
                self._signature("dgm_reference_v1", unsigned),
            )
        ):
            raise DGMStoreIntegrityError("DGM immutable reference authentication failed")
        return reference.object_sha256

    def _candidate_bundle_for_digest(
        self,
        experiment_id: UUID,
        candidate_sha256: str,
    ) -> CandidateBundle:
        namespace = self._refs / "bundles" / str(experiment_id)
        self._assert_directory_chain(namespace, create=False)
        try:
            entries = tuple(namespace.iterdir())
        except OSError as exc:
            raise DGMStoreIntegrityError("DGM bundle references are unavailable") from exc
        for path in entries:
            if path.is_symlink() or not path.is_file():
                raise DGMStoreIntegrityError("DGM bundle reference path is unsafe")
            candidate_id = path.name.removesuffix(".json")
            bundle = self.get_bundle(experiment_id, candidate_id)
            if bundle.materials.candidate.candidate_sha256 == candidate_sha256:
                return bundle
        raise DGMStoreIntegrityError("evaluation candidate bundle is not stored")

    def _signature(self, contract: str, material: object) -> str:
        return hmac.new(
            self._key,
            canonical_json_bytes({"contract": contract, "material": material}),
            sha256,
        ).hexdigest()

    def _put_immutable(self, path: Path, payload: bytes) -> None:
        self._assert_directory_chain(path.parent, create=True)
        if path.exists() or path.is_symlink():
            if self._read_file(path) != payload:
                raise DGMStoreIntegrityError(
                    "immutable DGM identity was rewritten or collided"
                )
            return
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=path.parent,
                prefix=".dgm-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                if self._read_file(path) != payload:
                    raise DGMStoreIntegrityError(
                        "concurrent DGM publication changed immutable bytes"
                    )
            _fsync_directory(path.parent)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_file(self, path: Path) -> bytes:
        self._assert_directory_chain(path.parent, create=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise DGMStoreIntegrityError("DGM store path is missing or unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            if not S_ISREG(metadata.st_mode):
                raise DGMStoreIntegrityError("DGM store object is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                return stream.read(64_000_001)
        finally:
            os.close(descriptor)

    def _object_path(
        self,
        root: Path,
        digest: str,
        *,
        suffix: str = "",
    ) -> Path:
        if re.fullmatch(SHA256_PATTERN, digest) is None:
            raise ValueError("DGM object digest must be lowercase SHA-256")
        path = root / digest[:2] / f"{digest[2:]}{suffix}"
        self._assert_beneath(path, root)
        return path

    def _reference_path(self, namespace: str, key: str) -> Path:
        parts = (namespace, *key.split("/"))
        if any(_SAFE_REF_PART.fullmatch(part) is None for part in parts):
            raise ValueError("DGM reference key is unsafe")
        path = self._refs.joinpath(*parts[:-1], f"{parts[-1]}.json")
        self._assert_beneath(path, self._refs)
        return path

    def _prepare_root(self, root: Path) -> Path:
        if root.exists() and root.is_symlink():
            raise ValueError("DGM store root cannot be a symbolic link")
        root.mkdir(parents=True, exist_ok=True)
        metadata = root.stat(follow_symlinks=False)
        if not S_ISDIR(metadata.st_mode):
            raise ValueError("DGM store root must be a directory")
        return root.resolve(strict=True)

    def _safe_directory(self, path: Path) -> Path:
        self._assert_directory_chain(path, create=True)
        return path

    def _assert_directory_chain(self, path: Path, *, create: bool) -> None:
        self._assert_beneath(path, self._root)
        relative = path.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current = current / part
            if not current.exists():
                if not create:
                    raise DGMStoreIntegrityError("DGM store directory is missing")
                current.mkdir()
            metadata = current.stat(follow_symlinks=False)
            if not S_ISDIR(metadata.st_mode) or current.is_symlink():
                raise DGMStoreIntegrityError("DGM store directory path is unsafe")

    @staticmethod
    def _assert_beneath(path: Path, root: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise DGMStoreIntegrityError("DGM store path escaped its root") from exc


def _result_bundles(result: DGMExperimentResult) -> tuple[CandidateBundle, ...]:
    bundles = [result.root_bundle]
    for optional in (result.n1_bundle, result.counterfactual_root_bundle):
        if optional is not None:
            bundles.append(optional)
    for arm in (result.i0_arm, result.i1_arm):
        if arm is not None:
            bundles.extend(
                item.bundle for item in arm.descendants if item.bundle is not None
            )
    return tuple({item.manifest.bundle_sha256: item for item in bundles}.values())


def _result_rounds(result: DGMExperimentResult) -> tuple[MutationRoundResult, ...]:
    rounds: list[MutationRoundResult] = []
    if result.n_to_n1_round is not None:
        rounds.append(result.n_to_n1_round)
    for arm in (result.i0_arm, result.i1_arm):
        if arm is not None:
            rounds.append(arm.mutation_round)
    return tuple(rounds)


def _result_lineages(result: DGMExperimentResult) -> tuple[DGMLineageRecord, ...]:
    records: list[DGMLineageRecord] = []
    if result.n1_lineage is not None:
        records.append(result.n1_lineage)
    for arm in (result.i0_arm, result.i1_arm):
        if arm is not None:
            records.extend(
                item.lineage for item in arm.descendants if item.lineage is not None
            )
    return tuple(records)


def _result_evaluations(result: DGMExperimentResult) -> tuple[EvaluationReport, ...]:
    reports = [result.root_development_report]
    for arm in (result.i0_arm, result.i1_arm):
        if arm is not None:
            reports.append(arm.arm_plan.development_report)
            reports.extend(
                item.development_report
                for item in arm.descendants
                if item.development_report is not None
            )
    for optional in (result.i0_protected_report, result.i1_protected_report):
        if optional is not None:
            reports.append(optional)
    return tuple({item.report_sha256: item for item in reports}.values())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "DGMExperimentManifest",
    "DGMStoreIntegrityError",
    "FilesystemDGMExperimentStore",
)
