"""Closed, canonical inherited-improver artifact and mutation adapter."""

from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
import re
from threading import RLock
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vital_relay.evolution.contracts import (
    ArtifactKind,
    ArtifactReference,
    MutationManifest,
    MutationOperationKind,
    MutationTarget,
)
from vital_relay.evolution.hashing import canonical_json_bytes


IMPROVER_SCHEMA_VERSION = 1
IMPROVER_MEDIA_TYPE = "application/vnd.vital-relay.improver+json"
_SEMVER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+$"
_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,95}$"
_FAILURE_ANALYSIS_PATH = "/failure_analysis_codes"
_MUTATION_PROMPT_PATH = "/mutation_prompt_codes"


class FailureAnalysisCode(StrEnum):
    """Reviewed diagnoses the inherited improver may prioritize or reorder."""

    HARD_GATE_FIRST = "hard_gate_first"
    CLUSTER_REPEATED_FAILURES = "cluster_repeated_failures"
    AUTHORITATIVE_OBSERVATION_GAPS = "authoritative_observation_gaps"
    STALE_OR_DECLINED_RECOVERY = "stale_or_declined_recovery"
    DUPLICATE_ACTION_RISK = "duplicate_action_risk"
    TOOL_ERROR_CONCENTRATION = "tool_error_concentration"


class MutationPromptCode(StrEnum):
    """Reviewed mutation tactics; none render model-authored instructions."""

    SMALLEST_EFFECTIVE_CHANGE = "smallest_effective_change"
    REORDER_EXISTING_STRATEGY = "reorder_existing_strategy"
    TUNE_BOUNDED_TOOL_BUDGET = "tune_bounded_tool_budget"
    MUTATE_ONE_SURFACE = "mutate_one_surface"
    REQUIRE_HYPOTHESIS_BINDING = "require_hypothesis_binding"
    PRESERVE_AUTHORITY_BOUNDARIES = "preserve_authority_boundaries"


class ImproverArtifact(BaseModel):
    """The complete self-mutable surface: two ordered lists of reviewed codes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IMPROVER_SCHEMA_VERSION] = IMPROVER_SCHEMA_VERSION
    improver_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    version: str = Field(pattern=_SEMVER_PATTERN)
    failure_analysis_codes: tuple[FailureAnalysisCode, ...] = Field(
        min_length=1,
        max_length=len(FailureAnalysisCode),
    )
    mutation_prompt_codes: tuple[MutationPromptCode, ...] = Field(
        min_length=1,
        max_length=len(MutationPromptCode),
    )

    @model_validator(mode="after")
    def validate_codes(self) -> Self:
        if len(self.failure_analysis_codes) != len(
            set(self.failure_analysis_codes)
        ):
            raise ValueError("failure-analysis codes must be unique")
        if len(self.mutation_prompt_codes) != len(
            set(self.mutation_prompt_codes)
        ):
            raise ValueError("mutation-prompt codes must be unique")
        if (
            MutationPromptCode.PRESERVE_AUTHORITY_BOUNDARIES
            not in self.mutation_prompt_codes
        ):
            raise ValueError("improver must preserve authority boundaries")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)

    @property
    def sha256(self) -> str:
        return sha256(self.canonical_bytes).hexdigest()

    @property
    def reference(self) -> ArtifactReference:
        return ArtifactReference(
            kind=ArtifactKind.IMPROVER,
            sha256=self.sha256,
            media_type=IMPROVER_MEDIA_TYPE,
        )


class ImproverArtifactError(ValueError):
    """Stable failure raised while loading a reviewed improver artifact."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CanonicalImproverArtifactAdapter:
    """Apply only whole-list replacements of reviewed improver codes."""

    def __init__(self, artifacts: tuple[ImproverArtifact, ...]) -> None:
        if not artifacts:
            raise ValueError("at least one improver artifact is required")
        self._artifacts = {artifact.reference: artifact for artifact in artifacts}
        if len(self._artifacts) != len(artifacts):
            raise ValueError("improver artifact references must be unique")
        self._lock = RLock()

    def artifact(self, reference: ArtifactReference) -> ImproverArtifact:
        if (
            reference.kind is not ArtifactKind.IMPROVER
            or reference.media_type != IMPROVER_MEDIA_TYPE
        ):
            raise KeyError(reference.sha256)
        with self._lock:
            try:
                return self._artifacts[reference]
            except KeyError as exc:
                raise KeyError(reference.sha256) from exc

    def canonical_payload(self, reference: ArtifactReference) -> bytes:
        return self.artifact(reference).canonical_bytes

    def apply_mutation(
        self,
        parent: ArtifactReference,
        mutation: MutationManifest,
    ) -> ArtifactReference:
        if mutation.target is not MutationTarget.IMPROVER:
            raise ValueError("improver adapter accepts only improver mutations")
        if mutation.generated_by != parent:
            raise ValueError("mutation was not generated by the parent improver")
        artifact = self.artifact(parent)
        payload = deepcopy(artifact.model_dump(mode="json"))
        seen_paths: set[str] = set()
        for operation in mutation.operations:
            if operation.path in seen_paths:
                raise ValueError("improver mutation paths must be unique")
            seen_paths.add(operation.path)
            if (
                operation.op is not MutationOperationKind.REPLACE
                or operation.path
                not in {_FAILURE_ANALYSIS_PATH, _MUTATION_PROMPT_PATH}
                or not isinstance(operation.value, list)
            ):
                raise ValueError("mutation path is not evolvable in the improver")
            field = operation.path.removeprefix("/")
            if operation.value == payload[field]:
                raise ValueError("improver mutation did not change reviewed codes")
            payload[field] = operation.value
        payload["version"] = _next_patch_version(artifact.version)
        child = ImproverArtifact.model_validate(payload)
        if child.canonical_bytes == artifact.canonical_bytes:
            raise ValueError("improver mutation did not change canonical content")
        with self._lock:
            existing = self._artifacts.get(child.reference)
            if existing is not None and existing != child:
                raise ValueError("improver reference collision")
            self._artifacts[child.reference] = child
        return child.reference

    def public_diff(
        self,
        parent: ArtifactReference,
        child: ArtifactReference,
    ) -> tuple[dict[str, object], ...]:
        before = self.artifact(parent)
        after = self.artifact(child)
        changes: list[dict[str, object]] = []
        for path, previous, current in (
            (
                _FAILURE_ANALYSIS_PATH,
                before.failure_analysis_codes,
                after.failure_analysis_codes,
            ),
            (
                _MUTATION_PROMPT_PATH,
                before.mutation_prompt_codes,
                after.mutation_prompt_codes,
            ),
        ):
            if previous != current:
                changes.append(
                    {
                        "path": path,
                        "before": [item.value for item in previous],
                        "after": [item.value for item in current],
                    }
                )
        return tuple(changes)


def load_improver_artifact(path: str | Path) -> ImproverArtifact:
    """Parse reviewed YAML into the canonical typed artifact."""

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - installation guard
        raise ImproverArtifactError("yaml_runtime_unavailable") from exc
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return ImproverArtifact.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise ImproverArtifactError("invalid_improver_artifact") from exc


def load_pinned_improver_artifact(
    path: str | Path,
    digest_path: str | Path,
) -> ImproverArtifact:
    """Load only when canonical bytes match the separately reviewed digest."""

    improver_path = Path(path)
    try:
        raw_pin = Path(digest_path).read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise ImproverArtifactError("improver_digest_unavailable") from exc
    if len(raw_pin) > 256:
        raise ImproverArtifactError("invalid_improver_digest")
    match = re.fullmatch(
        r"([0-9a-f]{64})[ \t]+\*?([A-Za-z0-9._-]+)\r?\n?",
        raw_pin,
    )
    if match is None or match.group(2) != improver_path.name:
        raise ImproverArtifactError("invalid_improver_digest")
    artifact = load_improver_artifact(improver_path)
    if not compare_digest(artifact.sha256, match.group(1)):
        raise ImproverArtifactError("improver_digest_mismatch")
    return artifact


def _next_patch_version(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"
