"""Content-addressed candidate storage with a small quality-diversity index."""

from __future__ import annotations

from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_UN, flock
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Iterator, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vital_relay.evolution.contracts import (
    ArchiveEntry,
    BehaviorNiche,
    CandidateManifest,
    EvaluationReport,
    InvalidAttemptRecord,
)
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256


TModel = TypeVar("TModel", bound=BaseModel)


class ArchiveIntegrityError(RuntimeError):
    pass


class EvaluationReportVerifier(Protocol):
    def verify_report(
        self,
        candidate: CandidateManifest,
        report: EvaluationReport,
    ) -> None:
        """Reject reports not issued by the protected host evaluator."""


class _ArchiveIndex(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(default=0, ge=0)
    entries: dict[str, ArchiveEntry] = Field(default_factory=dict)
    elites: dict[BehaviorNiche, str] = Field(default_factory=dict)
    invalid_attempt_objects: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_references(self) -> _ArchiveIndex:
        for report_sha256, entry in self.entries.items():
            if report_sha256 != entry.evaluation_report_sha256:
                raise ValueError("archive entry key does not match its report")
        for niche, report_sha256 in self.elites.items():
            entry = self.entries.get(report_sha256)
            if entry is None or niche not in entry.niches:
                raise ValueError("archive elite points to an invalid report entry")
        return self


class ContentAddressedStore:
    def __init__(self, root: Path) -> None:
        self._objects = root / "objects"
        self._objects.mkdir(parents=True, exist_ok=True)

    def put(self, value: BaseModel) -> str:
        payload = canonical_json_bytes(value)
        digest = canonical_sha256(value)
        target = self._path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != payload:
                raise ArchiveIntegrityError("content-address collision or tampering")
            return digest
        _atomic_write(target, payload)
        return digest

    def get(self, digest: str, model: type[TModel]) -> TModel:
        target = self._path(digest)
        payload = target.read_bytes()
        parsed = model.model_validate_json(payload)
        if canonical_sha256(parsed) != digest:
            raise ArchiveIntegrityError("stored object hash verification failed")
        return parsed

    def path_for(self, digest: str) -> Path:
        return self._path(digest)

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("object digest must be a lowercase SHA-256")
        return self._objects / digest[:2] / f"{digest[2:]}.json"


class QualityDiversityArchive:
    """Preserve immutable artifacts while mutable pointers select niche elites."""

    def __init__(
        self,
        root: Path,
        report_verifier: EvaluationReportVerifier,
    ) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._store = ContentAddressedStore(root)
        self._report_verifier = report_verifier
        self._index_path = root / "index.json"
        self._lock_path = root / ".archive.lock"
        self._lock = RLock()
        with self._exclusive_lock():
            if not self._index_path.exists():
                self._write_index(_ArchiveIndex())

    def consider(
        self,
        candidate: CandidateManifest,
        evaluation: EvaluationReport,
        niches: tuple[BehaviorNiche, ...],
    ) -> ArchiveEntry:
        with self._exclusive_lock():
            if not evaluation.eligible:
                raise ValueError("hard-gate-failing candidates cannot enter the elite archive")
            if evaluation.candidate_sha256 != candidate.candidate_sha256:
                raise ValueError("candidate and evaluation hashes do not match")
            unique_niches = tuple(dict.fromkeys(niches))
            if not unique_niches:
                raise ValueError("at least one behavior niche is required")
            index = self._read_index()
            entry = self._evaluation_entry(candidate, evaluation, unique_niches)
            entries = dict(index.entries)
            existing = entries.get(evaluation.report_sha256)
            if existing is not None:
                entry = entry.model_copy(
                    update={
                        "niches": tuple(
                            dict.fromkeys((*existing.niches, *unique_niches))
                        )
                    }
                )
            if existing is not None and existing.model_copy(
                update={"niches": entry.niches}
            ) != entry:
                raise ArchiveIntegrityError("report identity maps to another artifact")
            entries[evaluation.report_sha256] = entry
            elites = dict(index.elites)
            for niche in unique_niches:
                incumbent_report = elites.get(niche)
                if incumbent_report is None or self._is_better(
                    niche,
                    evaluation,
                    index.entries[incumbent_report],
                ):
                    elites[niche] = evaluation.report_sha256
            self._write_index(
                _ArchiveIndex(
                    revision=index.revision + 1,
                    entries=entries,
                    elites=elites,
                    invalid_attempt_objects=index.invalid_attempt_objects,
                )
            )
            return entry

    def record_evaluation(
        self,
        candidate: CandidateManifest,
        evaluation: EvaluationReport,
    ) -> ArchiveEntry:
        """Register evaluator-issued evidence without making it a niche elite."""

        with self._exclusive_lock():
            if evaluation.candidate_sha256 != candidate.candidate_sha256:
                raise ValueError("candidate and evaluation hashes do not match")
            entry = self._evaluation_entry(candidate, evaluation, ())
            index = self._read_index()
            existing = index.entries.get(evaluation.report_sha256)
            if existing is not None:
                if existing.model_copy(update={"niches": ()}) != entry:
                    raise ArchiveIntegrityError(
                        "report identity maps to another artifact"
                    )
                return existing
            entries = dict(index.entries)
            entries[evaluation.report_sha256] = entry
            self._write_index(
                index.model_copy(
                    update={"revision": index.revision + 1, "entries": entries}
                )
            )
            return entry

    def record_invalid_attempt(self, attempt: InvalidAttemptRecord) -> str:
        with self._exclusive_lock():
            digest = self._store.put(attempt)
            index = self._read_index()
            if digest not in index.invalid_attempt_objects:
                self._write_index(
                    index.model_copy(
                        update={
                            "revision": index.revision + 1,
                            "invalid_attempt_objects": (
                                *index.invalid_attempt_objects,
                                digest,
                            ),
                        }
                    )
                )
            return digest

    def elite(self, niche: BehaviorNiche) -> ArchiveEntry | None:
        index = self._read_index()
        digest = index.elites.get(niche)
        return index.entries.get(digest) if digest is not None else None

    def entry_for_report(self, report_sha256: str) -> ArchiveEntry | None:
        return self._read_index().entries.get(report_sha256)

    def resolve_verified_entry(self, report_sha256: str) -> ArchiveEntry:
        entry = self.entry_for_report(report_sha256)
        if entry is None:
            raise ValueError("evaluation report is not archived")
        candidate = self.manifest_for(entry)
        evaluation = self.evaluation_for(entry)
        if (
            entry.candidate_sha256 != candidate.candidate_sha256
            or entry.evaluation_report_sha256 != evaluation.report_sha256
            or entry.partition is not evaluation.partition
            or entry.benchmark_manifest_sha256
            != evaluation.benchmark_manifest_sha256
            or evaluation.candidate_sha256 != candidate.candidate_sha256
        ):
            raise ArchiveIntegrityError("archive entry metadata is inconsistent")
        self._report_verifier.verify_report(candidate, evaluation)
        return entry

    def has_verified_candidate(self, candidate_sha256: str) -> bool:
        for entry in self.entries_for_candidate(candidate_sha256):
            try:
                self.resolve_verified_entry(entry.evaluation_report_sha256)
            except (ArchiveIntegrityError, ValueError):
                continue
            return True
        return False

    def entries_for_candidate(
        self,
        candidate_sha256: str,
    ) -> tuple[ArchiveEntry, ...]:
        return tuple(
            entry
            for entry in self._read_index().entries.values()
            if entry.candidate_sha256 == candidate_sha256
        )

    def evaluation_for(self, entry: ArchiveEntry) -> EvaluationReport:
        return self._store.get(entry.evaluation_object_sha256, EvaluationReport)

    def manifest_for(self, entry: ArchiveEntry) -> CandidateManifest:
        return self._store.get(entry.manifest_object_sha256, CandidateManifest)

    def _evaluation_entry(
        self,
        candidate: CandidateManifest,
        evaluation: EvaluationReport,
        niches: tuple[BehaviorNiche, ...],
    ) -> ArchiveEntry:
        self._report_verifier.verify_report(candidate, evaluation)
        return ArchiveEntry(
            candidate_sha256=candidate.candidate_sha256,
            manifest_object_sha256=self._store.put(candidate),
            evaluation_object_sha256=self._store.put(evaluation),
            evaluation_report_sha256=evaluation.report_sha256,
            partition=evaluation.partition,
            benchmark_manifest_sha256=evaluation.benchmark_manifest_sha256,
            niches=niches,
        )

    @property
    def revision(self) -> int:
        return self._read_index().revision

    @property
    def object_store(self) -> ContentAddressedStore:
        return self._store

    def _is_better(
        self,
        niche: BehaviorNiche,
        challenger: EvaluationReport,
        incumbent_entry: ArchiveEntry,
    ) -> bool:
        incumbent = self.evaluation_for(incumbent_entry)
        if (
            challenger.benchmark_manifest_sha256
            != incumbent.benchmark_manifest_sha256
            or challenger.case_sha256s != incumbent.case_sha256s
        ):
            raise ValueError("niche elites must use the same frozen benchmark")
        return _niche_fitness(niche, challenger) > _niche_fitness(niche, incumbent)

    def _read_index(self) -> _ArchiveIndex:
        payload = self._index_path.read_bytes()
        return _ArchiveIndex.model_validate_json(payload)

    def _write_index(self, index: _ArchiveIndex) -> None:
        _atomic_write(self._index_path, canonical_json_bytes(index))

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        """Serialize read-modify-write cycles across workers and processes."""

        with self._lock:
            with self._lock_path.open("a+b") as lock_file:
                flock(lock_file.fileno(), LOCK_EX)
                try:
                    yield
                finally:
                    flock(lock_file.fileno(), LOCK_UN)


def _niche_fitness(niche: BehaviorNiche, report: EvaluationReport) -> tuple[float, ...]:
    metrics = report.metrics
    if niche is BehaviorNiche.LOW_ACCEPTANCE_LATENCY:
        latency = metrics.qualified_acceptance_latency_seconds
        return (-(latency if latency is not None else float("inf")),)
    if niche is BehaviorNiche.NOTIFICATION_EFFICIENCY:
        return (
            metrics.workflow_completion_rate,
            -float(metrics.notifications_sent),
            -float(metrics.unnecessary_actions),
        )
    if niche is BehaviorNiche.STALE_OR_DECLINE_RECOVERY:
        return (
            metrics.workflow_completion_rate,
            -float(metrics.missed_required_actions),
            -float(metrics.tool_error_count),
        )
    return (
        metrics.workflow_completion_rate,
        metrics.responder_skill_match_rate,
        -float(metrics.missed_required_actions),
        -float(metrics.duplicate_irreversible_actions),
        -float(metrics.unnecessary_actions),
    )


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(payload)
        temporary.flush()
        temporary_path = Path(temporary.name)
    replace(temporary_path, path)
