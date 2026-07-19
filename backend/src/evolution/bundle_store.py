"""Filesystem content-addressed storage for verified candidate bundles."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from stat import S_ISREG
from tempfile import NamedTemporaryFile

from pydantic import ValidationError

from vital_relay.evolution.bundles import (
    CandidateArtifactAttestationVerifier,
    CandidateBundle,
    CandidateBundleArtifactReference,
    CandidateBundleManifest,
    CandidateBundleVerificationError,
)
from vital_relay.evolution.contracts import CandidateManifest, MutationManifest
from vital_relay.evolution.hashing import canonical_json_bytes


class BundleStoreIntegrityError(RuntimeError):
    """Stored bytes, paths, or immutable identities failed verification."""


class CandidateBundleStore:
    """Persist and resolve complete verified bundles by manifest SHA-256.

    The store performs no mutation generation or candidate improvement.  It only
    records and verifies artifacts already produced by the evolution boundary.
    """

    def __init__(
        self,
        root: Path,
        attestation_verifier: CandidateArtifactAttestationVerifier,
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("bundle store root cannot be a symbolic link")
        self._root = root.resolve()
        self._objects = self._root / "objects"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._attestation_verifier = attestation_verifier
        if self._objects.is_symlink():  # pragma: no cover - defensive race check
            raise ValueError("bundle object root cannot be a symbolic link")

    def put(self, bundle: CandidateBundle) -> str:
        """Atomically publish an immutable bundle, returning its manifest digest."""

        try:
            bundle = CandidateBundle.model_validate(bundle.model_dump())
            parent_digest = bundle.manifest.parent_bundle_sha256
            parent = self.get(parent_digest) if parent_digest is not None else None
            bundle.verify_attestations(self._attestation_verifier)
            bundle.verify_parent(parent, self._attestation_verifier)
        except (
            CandidateBundleVerificationError,
            ValidationError,
            FileNotFoundError,
        ) as exc:
            raise BundleStoreIntegrityError(
                "candidate bundle failed verification before storage"
            ) from exc

        material_by_reference: tuple[
            tuple[CandidateBundleArtifactReference, bytes], ...
        ] = (
            (
                bundle.manifest.candidate_manifest,
                bundle.materials.candidate_bytes,
            ),
            (
                bundle.manifest.coordination_policy,
                bundle.materials.policy_bytes,
            ),
            (bundle.manifest.improver, bundle.materials.improver_bytes),
        )
        mutation_reference = bundle.manifest.mutation_manifest
        mutation_bytes = bundle.materials.mutation_bytes
        if mutation_reference is not None and mutation_bytes is not None:
            material_by_reference = (
                *material_by_reference,
                (mutation_reference, mutation_bytes),
            )

        unique_payloads: dict[str, bytes] = {}
        for reference, payload in material_by_reference:
            self._verify_reference(reference, payload)
            existing = unique_payloads.setdefault(reference.sha256, payload)
            if existing != payload:  # pragma: no cover - SHA-256 collision guard
                raise BundleStoreIntegrityError(
                    "one artifact digest identifies different bundle bytes"
                )

        manifest_payload = canonical_json_bytes(bundle.manifest)
        bundle_digest = _sha256(manifest_payload)
        if bundle_digest != bundle.manifest.bundle_sha256:
            raise BundleStoreIntegrityError("bundle manifest identity changed")

        # Artifacts are published first.  The manifest is the completion marker,
        # so readers never discover a bundle that references missing artifacts.
        for digest, payload in unique_payloads.items():
            self._put_blob(digest, payload)
        self._put_blob(bundle_digest, manifest_payload)
        return bundle_digest

    def get(self, bundle_sha256: str) -> CandidateBundle:
        """Return a bundle only after exact bytes and its direct parent verify."""

        return self._get(bundle_sha256, seen=set())

    def manifest(self, bundle_sha256: str) -> CandidateBundleManifest:
        """Resolve a manifest only after its complete bundle lineage verifies."""

        return self.get(bundle_sha256).manifest

    def _read_manifest(self, bundle_sha256: str) -> CandidateBundleManifest:
        """Parse canonical manifest bytes during private verified traversal."""

        payload = self._read_blob(bundle_sha256)
        try:
            manifest = CandidateBundleManifest.model_validate_json(payload)
        except (ValidationError, ValueError) as exc:
            raise BundleStoreIntegrityError(
                "stored bundle manifest is malformed"
            ) from exc
        if canonical_json_bytes(manifest) != payload:
            raise BundleStoreIntegrityError(
                "stored bundle manifest is not canonical"
            )
        if manifest.bundle_sha256 != bundle_sha256:
            raise BundleStoreIntegrityError(
                "stored bundle manifest hash verification failed"
            )
        return manifest

    def resolve_candidate_manifest_bytes(self, bundle_sha256: str) -> bytes:
        """Resolve the exact canonical CandidateManifest artifact bytes."""

        bundle = self.get(bundle_sha256)
        return self._read_artifact(bundle.manifest.candidate_manifest)

    def resolve_candidate_manifest(self, bundle_sha256: str) -> CandidateManifest:
        """Resolve the validated shared CandidateManifest model."""

        return self.get(bundle_sha256).materials.candidate

    def resolve_policy_bytes(self, bundle_sha256: str) -> bytes:
        """Resolve the exact canonical coordination-policy bytes."""

        bundle = self.get(bundle_sha256)
        return self._read_artifact(bundle.manifest.coordination_policy)

    def resolve_improver_bytes(self, bundle_sha256: str) -> bytes:
        """Resolve the exact current improver bytes named by the candidate."""

        bundle = self.get(bundle_sha256)
        return self._read_artifact(bundle.manifest.improver)

    def resolve_mutation_manifest_bytes(
        self,
        bundle_sha256: str,
    ) -> bytes | None:
        """Resolve exact mutation bytes, or ``None`` for a root bundle."""

        bundle = self.get(bundle_sha256)
        reference = bundle.manifest.mutation_manifest
        return self._read_artifact(reference) if reference is not None else None

    def resolve_mutation_manifest(
        self,
        bundle_sha256: str,
    ) -> MutationManifest | None:
        """Resolve the validated shared MutationManifest model."""

        return self.get(bundle_sha256).materials.mutation

    def resolve_generated_by_improver_bytes(
        self,
        bundle_sha256: str,
    ) -> bytes | None:
        """Resolve bytes proving a descendant mutation's ``generated_by``.

        A root has no mutation and returns ``None``.  For a descendant this is
        the direct parent's verified improver artifact, which may differ from the
        child's current improver after an improver-targeted mutation.
        """

        bundle = self.get(bundle_sha256)
        parent_digest = bundle.manifest.parent_bundle_sha256
        if parent_digest is None:
            return None
        parent = self.get(parent_digest)
        bundle.verify_parent(parent, self._attestation_verifier)
        return self._read_artifact(parent.manifest.improver)

    def path_for_bundle(self, bundle_sha256: str) -> Path:
        self.get(bundle_sha256)
        return self._object_path(bundle_sha256)

    def path_for_artifact(
        self,
        bundle_sha256: str,
        reference: CandidateBundleArtifactReference,
    ) -> Path:
        manifest = self.get(bundle_sha256).manifest
        references = {
            manifest.candidate_manifest,
            manifest.coordination_policy,
            manifest.improver,
        }
        if manifest.mutation_manifest is not None:
            references.add(manifest.mutation_manifest)
        if reference not in references:
            raise ValueError("artifact reference does not belong to the bundle")
        return self._object_path(reference.sha256)

    def _get(self, bundle_sha256: str, seen: set[str]) -> CandidateBundle:
        if bundle_sha256 in seen:
            raise BundleStoreIntegrityError("bundle parent references form a cycle")
        seen = {*seen, bundle_sha256}
        manifest = self._read_manifest(bundle_sha256)
        candidate_bytes = self._read_artifact(manifest.candidate_manifest)
        policy_bytes = self._read_artifact(manifest.coordination_policy)
        improver_bytes = self._read_artifact(manifest.improver)
        mutation_bytes = (
            self._read_artifact(manifest.mutation_manifest)
            if manifest.mutation_manifest is not None
            else None
        )
        parent = (
            self._get(manifest.parent_bundle_sha256, seen)
            if manifest.parent_bundle_sha256 is not None
            else None
        )
        try:
            return CandidateBundle.from_artifact_bytes(
                manifest=manifest,
                candidate_bytes=candidate_bytes,
                policy_bytes=policy_bytes,
                improver_bytes=improver_bytes,
                mutation_bytes=mutation_bytes,
                parent=parent,
                attestation_verifier=self._attestation_verifier,
            )
        except (CandidateBundleVerificationError, ValidationError) as exc:
            raise BundleStoreIntegrityError(
                "stored candidate bundle failed verification"
            ) from exc

    def _read_artifact(self, reference: CandidateBundleArtifactReference) -> bytes:
        payload = self._read_blob(reference.sha256)
        self._verify_reference(reference, payload)
        return payload

    def _verify_reference(
        self,
        reference: CandidateBundleArtifactReference,
        payload: bytes,
    ) -> None:
        if len(payload) != reference.size_bytes or _sha256(payload) != reference.sha256:
            raise BundleStoreIntegrityError(
                f"{reference.role.value} artifact hash or size verification failed"
            )

    def _put_blob(self, digest: str, payload: bytes) -> None:
        if _sha256(payload) != digest:
            raise BundleStoreIntegrityError("object digest does not match payload")
        target = self._object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._assert_safe_path(target)
        if target.exists() or target.is_symlink():
            if self._read_blob(digest) != payload:
                raise BundleStoreIntegrityError(
                    "content-address collision or stored-object tampering"
                )
            return

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=target.parent,
                prefix=".candidate-bundle-",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
            try:
                os.link(temporary_path, target)
            except FileExistsError:
                if self._read_blob(digest) != payload:
                    raise BundleStoreIntegrityError(
                        "content-address collision or concurrent tampering"
                    )
            _fsync_directory(target.parent)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _read_blob(self, digest: str) -> bytes:
        path = self._object_path(digest)
        self._assert_safe_path(path)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise BundleStoreIntegrityError("stored object path is unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            if not S_ISREG(metadata.st_mode):
                raise BundleStoreIntegrityError("stored object is not a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                payload = stream.read()
        finally:
            os.close(descriptor)
        if _sha256(payload) != digest:
            raise BundleStoreIntegrityError("stored object hash verification failed")
        return payload

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("object digest must be a lowercase SHA-256")
        path = self._objects / digest[:2] / digest[2:]
        self._assert_safe_path(path)
        return path

    def _assert_safe_path(self, path: Path) -> None:
        try:
            path.resolve(strict=False).relative_to(self._objects)
        except ValueError as exc:
            raise BundleStoreIntegrityError(
                "object path escapes the content-addressed store"
            ) from exc


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
