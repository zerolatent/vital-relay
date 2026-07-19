"""Trusted-host authorization and materialization for typed mutation rounds."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import subprocess
import threading
from typing import Literal, Never, Protocol, Self
from uuid import UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vital_relay.agent.contracts import SandboxKind
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.agent.sandbox import (
    CompletedSandboxCommand,
    NEMOCLAW_HOST_CLI_EXECUTABLE,
    NEMOCLAW_MANAGED_EXEC_LAUNCHER,
    NEMOCLAW_SANDBOX_NAME_PATTERN,
    SandboxCommandExecutor,
    SandboxOutputLimitExceeded,
    SubprocessSandboxCommandExecutor,
)
from vital_relay.agent.source_manifest import (
    MUTATION_WORKER_ENTRYPOINT,
    MUTATION_WORKER_IMPLEMENTATION_PATHS,
    MUTATION_WORKER_SOURCE_MANIFEST,
    MUTATION_WORKER_SOURCE_PATHS,
    ReviewedSourceSnapshot,
    capture_reviewed_source_snapshot,
)
from vital_relay.evolution.ace.contracts import ModelIdentity
from vital_relay.evolution.ace.model_client import (
    MAX_MODEL_RESPONSE_BYTES,
    LocalModelConfig,
    LocalOpenAIModelClient,
    ModelClientFailureCode,
    ModelRequestBinding,
)
from vital_relay.evolution.bundles import (
    CandidateArtifactAttestationAuthority,
    CandidateArtifactAttestationVerifier,
    CandidateBundle,
    CandidateMaterialSourcePartition,
    ImproverMutationReceipt,
)
from vital_relay.evolution.candidate import CandidateFactory, MutationRejected
from vital_relay.evolution.contracts import (
    CandidateManifest,
    EvaluationReport,
    FailurePacket,
    IDENTIFIER_PATTERN,
    InvalidAttemptReason,
    InvalidAttemptRecord,
    MutationManifest,
    MutationOperation,
    MutationOperationKind,
    MutationTarget,
    PartitionName,
    SHA256_PATTERN,
)
from vital_relay.evolution.evaluator import HostIntegrityAuthority, build_failure_packet
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.evolution.improver import (
    CanonicalImproverArtifactAdapter,
    ImproverArtifact,
)
from vital_relay.evolution.mutation_contracts import (
    MAX_MUTATION_SANDBOX_REQUEST_BYTES,
    MAX_MUTATION_SANDBOX_RESPONSE_BYTES,
    MUTATION_MODEL_SCHEMA_NAME,
    MUTATION_ROUND_CANDIDATE_BUDGET,
    MUTATION_SYSTEM_PROMPT,
    EvolvablePolicyTool,
    FailureAnalysisCodesEdit,
    HumanReviewConditionsEdit,
    ImproverMutationProposal,
    MaxMutatingCallsEdit,
    MaxTotalCallsEdit,
    MutationHypothesisCode,
    MutationModelOutput,
    MutationPromptCodesEdit,
    MutationRoundBudget,
    MutationSandboxRequest,
    MutationSandboxResponse,
    MutationWorkerProposalRecord,
    MutationWorkerProposalStatus,
    MutationWorkerResult,
    PolicyEdit,
    PolicyMutationProposal,
    StrategyPrinciplesEdit,
    ToolMaxCallsEdit,
    WorkerCoordinationPolicy,
    WorkerFailurePacket,
    WorkerImproverArtifact,
    MutationWorkerParent,
    normalize_created_at,
    proposal_payload,
)
from vital_relay.evolution.policy import A2PolicyArtifactAdapter


MUTATION_ROUND_SCHEMA_VERSION = 1
MUTATION_WORKER_RUNTIME_SCHEMA_VERSION = 1
MUTATION_WORKER_SOURCE_PATH_COUNT = 16
MUTATION_DOCKER_HOST_CLI_EXECUTABLE = "/usr/bin/docker"
MUTATION_WORKER_EXECUTABLE = (
    "/sandbox/vital-relay-runtime/bin/vital-relay-mutation-worker"
)
MUTATION_DOCKER_ATTEMPT_LABEL = "io.vital-relay.mutation-attempt"
MUTATION_DOCKER_SOURCE_MANIFEST_LABEL = (
    "io.vital-relay.mutation-source-manifest-sha256"
)
MUTATION_DOCKER_SOURCE_ENTRYPOINT_LABEL = (
    "io.vital-relay.mutation-source-entrypoint"
)
MUTATION_DOCKER_NETWORK_LABEL = "io.vital-relay.mutation-network"
MUTATION_NEMO_RUNTIME_IDENTITY_EXECUTABLE = (
    "/sandbox/vital-relay-runtime/bin/vital-relay-mutation-runtime-identity"
)
MUTATION_DOCKER_CLEANUP_TIMEOUT_SECONDS = 10.0
MUTATION_RUNTIME_INSPECTION_TIMEOUT_SECONDS = 10.0
MAX_MUTATION_RUNTIME_INSPECTION_BYTES = 256 * 1024
_MUTATION_DOCKER_IMAGE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9._:/-]{0,190}@sha256:[0-9a-f]{64}$"
)
_MUTATION_DOCKER_NETWORK_PATTERN = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
)
_MUTATION_DOCKER_RESERVED_NETWORKS = frozenset(
    {"bridge", "default", "host", "none"}
)
__all__ = (
    "AuthorizedMutationRoundRunner",
    "BundleReadyMutation",
    "EvolvablePolicyTool",
    "MutationHypothesisCode",
    "MutationModelOutput",
    "MutationProposalRecord",
    "MutationProposalStatus",
    "MutationRoundBudget",
    "MutationRoundResult",
    "MutationRuntimeAuthorization",
    "MutationRuntimeAuthorizationAuthority",
    "MutationSandboxCleanupError",
    "MutationSandboxCleanupEvidence",
    "MutationSandboxError",
    "MutationSandboxRequest",
    "MutationSandboxResponse",
    "MutationSandboxTransport",
    "PolicyEdit",
    "ProcessMutationSandboxTransport",
)
_MALFORMED_RESPONSE_CODES = frozenset(
    {
        ModelClientFailureCode.INVALID_STRUCTURED_JSON,
        ModelClientFailureCode.SCHEMA_MISMATCH,
    }
)


class _MutationHostModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MutationProposalStatus(StrEnum):
    CANDIDATE_BUILT = "candidate_built"
    INVALID = "invalid"
    REQUEST_FAILED = "request_failed"


class MutationProposalRecord(_MutationHostModel):
    """Trusted-host interpretation of one exact sandbox worker record."""

    schema_version: Literal[MUTATION_ROUND_SCHEMA_VERSION] = (
        MUTATION_ROUND_SCHEMA_VERSION
    )
    round_id: UUID
    runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    attempt_id: UUID
    request_binding: ModelRequestBinding
    request_sha256: str = Field(pattern=SHA256_PATTERN)
    response_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    response_bytes: bytes | None = Field(
        default=None,
        max_length=MAX_MODEL_RESPONSE_BYTES,
    )
    model_output: MutationModelOutput | None = None
    status: MutationProposalStatus
    mutation_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    candidate_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    invalid_reason: InvalidAttemptReason | None = None
    model_failure_code: ModelClientFailureCode | None = None
    worker_record_sha256: str = Field(pattern=SHA256_PATTERN)
    record_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_outcome_and_hash(self) -> Self:
        if self.response_bytes is not None:
            if (
                self.response_sha256 != sha256(self.response_bytes).hexdigest()
                or self.model_output is None
                or MutationModelOutput.model_validate_json(self.response_bytes)
                != self.model_output
            ):
                raise ValueError("model output bytes do not match their typed record")
        elif self.model_output is not None:
            raise ValueError("typed model output requires its exact response bytes")
        if (
            self.model_output is not None
            and self.model_output.request_binding != self.request_binding
        ):
            raise ValueError("typed model output does not match the request binding")
        if self.status is MutationProposalStatus.CANDIDATE_BUILT:
            valid = (
                self.model_output is not None
                and self.mutation_sha256 is not None
                and self.candidate_sha256 is not None
                and self.invalid_reason is None
                and self.model_failure_code is None
            )
        elif self.status is MutationProposalStatus.INVALID:
            valid = (
                self.invalid_reason is not None
                and self.candidate_sha256 is None
                and (
                    (
                        self.invalid_reason
                        is InvalidAttemptReason.MALFORMED_MANIFEST
                        and self.model_output is None
                        and self.mutation_sha256 is None
                        and self.model_failure_code in _MALFORMED_RESPONSE_CODES
                    )
                    or (
                        self.invalid_reason
                        is not InvalidAttemptReason.MALFORMED_MANIFEST
                        and self.model_output is not None
                        and self.model_failure_code is None
                    )
                )
            )
        else:
            valid = (
                self.model_failure_code is not None
                and self.model_failure_code not in _MALFORMED_RESPONSE_CODES
                and self.invalid_reason is None
                and self.model_output is None
                and self.mutation_sha256 is None
                and self.candidate_sha256 is None
            )
        if not valid:
            raise ValueError("proposal record outcome fields are inconsistent")
        expected = canonical_sha256(
            self.model_dump(mode="json", exclude={"record_sha256"})
        )
        if self.record_sha256 != expected:
            raise ValueError("proposal record hash does not match canonical content")
        return self

    @classmethod
    def create(cls, **values: object) -> MutationProposalRecord:
        provisional = cls.model_construct(**values, record_sha256="0" * 64)
        digest = canonical_sha256(
            provisional.model_dump(mode="json", exclude={"record_sha256"})
        )
        return cls.model_validate({**values, "record_sha256": digest})

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class BundleReadyMutation(_MutationHostModel):
    """A host-built candidate and the exact bytes required by its bundle."""

    source_partition: Literal[CandidateMaterialSourcePartition.DEVELOPMENT] = (
        CandidateMaterialSourcePartition.DEVELOPMENT
    )
    failure_packet_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    record: MutationProposalRecord
    mutation: MutationManifest
    candidate: CandidateManifest
    policy_bytes: bytes = Field(min_length=1, max_length=2_000_000)
    improver_bytes: bytes = Field(min_length=1, max_length=2_000_000)

    @model_validator(mode="after")
    def validate_exact_bytes(self) -> Self:
        invalid_lineage = (
            self.mutation.target is MutationTarget.COORDINATION_POLICY
            and (
                self.candidate.policy == self.mutation.parent_policy
                or self.candidate.improver != self.mutation.generated_by
            )
        ) or (
            self.mutation.target is MutationTarget.IMPROVER
            and (
                self.candidate.policy != self.mutation.parent_policy
                or self.candidate.improver == self.mutation.generated_by
            )
        )
        if invalid_lineage or (
            self.runtime_authorization_sha256
            != self.record.runtime_authorization_sha256
            or self.runtime_identity_sha256
            != self.record.runtime_identity_sha256
            or self.source_manifest_sha256
            != self.record.source_manifest_sha256
            or self.record.status is not MutationProposalStatus.CANDIDATE_BUILT
            or self.record.mutation_sha256 != self.mutation.mutation_sha256
            or self.record.candidate_sha256 != self.candidate.candidate_sha256
            or self.candidate.mutation_sha256 != self.mutation.mutation_sha256
            or sha256(self.policy_bytes).hexdigest() != self.candidate.policy.sha256
            or sha256(self.improver_bytes).hexdigest()
            != self.candidate.improver.sha256
        ):
            raise ValueError("bundle-ready mutation bytes do not match the candidate")
        return self


class MutationRoundResult(_MutationHostModel):
    """Host-built round evidence bound to the complete sandbox worker result."""

    schema_version: Literal[MUTATION_ROUND_SCHEMA_VERSION] = (
        MUTATION_ROUND_SCHEMA_VERSION
    )
    round_id: UUID
    runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    parent_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    failure_packet_sha256: str = Field(pattern=SHA256_PATTERN)
    attempted_at: datetime
    budget: MutationRoundBudget
    worker_result: MutationWorkerResult
    proposals: tuple[MutationProposalRecord, ...] = Field(
        min_length=MUTATION_ROUND_CANDIDATE_BUDGET,
        max_length=MUTATION_ROUND_CANDIDATE_BUDGET,
    )
    successful: tuple[BundleReadyMutation, ...]
    invalid_attempts: tuple[InvalidAttemptRecord, ...]
    complete: bool
    round_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_complete_round(self) -> Self:
        object.__setattr__(
            self,
            "attempted_at",
            normalize_created_at(self.attempted_at),
        )
        worker = self.worker_result
        if (
            self.round_id != worker.round_id
            or self.runtime_authorization_sha256
            != worker.runtime_authorization_sha256
            or self.runtime_identity_sha256 != worker.runtime_identity_sha256
            or self.source_manifest_sha256 != worker.source_manifest_sha256
            or self.parent_candidate_sha256 != worker.parent_candidate_sha256
            or self.failure_packet_sha256 != worker.failure_packet_sha256
            or self.attempted_at != worker.attempted_at
            or self.budget != worker.budget
            or self.complete != worker.complete
        ):
            raise ValueError("host round does not match its worker result")
        for record, worker_record in zip(
            self.proposals,
            worker.proposals,
            strict=True,
        ):
            if (
                record.runtime_authorization_sha256
                != self.runtime_authorization_sha256
                or record.runtime_identity_sha256 != self.runtime_identity_sha256
                or record.source_manifest_sha256
                != self.source_manifest_sha256
                or record.round_id != worker_record.round_id
                or record.attempt_id != worker_record.attempt_id
                or record.request_binding != worker_record.request_binding
                or record.request_sha256 != worker_record.request_sha256
                or record.response_sha256 != worker_record.response_sha256
                or record.response_bytes != worker_record.response_bytes
                or record.model_output != worker_record.model_output
                or record.worker_record_sha256 != worker_record.record_sha256
            ):
                raise ValueError("host proposal does not match its worker record")
            if worker_record.status is MutationWorkerProposalStatus.PROPOSED:
                if record.status not in {
                    MutationProposalStatus.CANDIDATE_BUILT,
                    MutationProposalStatus.INVALID,
                } or record.model_failure_code is not None:
                    raise ValueError("host proposal outcome is inconsistent")
            elif (
                worker_record.status
                is MutationWorkerProposalStatus.INVALID_RESPONSE
            ):
                if (
                    record.status is not MutationProposalStatus.INVALID
                    or record.invalid_reason
                    is not InvalidAttemptReason.MALFORMED_MANIFEST
                    or record.model_failure_code != worker_record.failure_code
                ):
                    raise ValueError("malformed worker response was relabeled")
            elif (
                record.status is not MutationProposalStatus.REQUEST_FAILED
                or record.model_failure_code != worker_record.failure_code
            ):
                raise ValueError("worker request failure was relabeled")
        successful_records = {
            material.record.record_sha256 for material in self.successful
        }
        expected_successes = {
            record.record_sha256
            for record in self.proposals
            if record.status is MutationProposalStatus.CANDIDATE_BUILT
        }
        if (
            len(successful_records) != len(self.successful)
            or successful_records != expected_successes
        ):
            raise ValueError("round successes do not match proposal records")
        if any(
            material.failure_packet_sha256 != self.failure_packet_sha256
            or material.runtime_authorization_sha256
            != self.runtime_authorization_sha256
            or material.runtime_identity_sha256 != self.runtime_identity_sha256
            or material.source_manifest_sha256 != self.source_manifest_sha256
            for material in self.successful
        ):
            raise ValueError("round successes do not match authorized runtime inputs")
        invalid_by_attempt = {
            attempt.attempt_id: attempt for attempt in self.invalid_attempts
        }
        expected_invalid = {
            record.attempt_id
            for record in self.proposals
            if record.status is MutationProposalStatus.INVALID
        }
        if (
            len(invalid_by_attempt) != len(self.invalid_attempts)
            or set(invalid_by_attempt) != expected_invalid
        ):
            raise ValueError("round invalid attempts do not match proposal records")
        for record in self.proposals:
            if record.status is MutationProposalStatus.INVALID:
                attempt = invalid_by_attempt[record.attempt_id]
                if (
                    attempt.reason is not record.invalid_reason
                    or attempt.proposed_sha256 != record.mutation_sha256
                ):
                    raise ValueError("invalid attempt does not match its proposal")
        expected_hash = canonical_sha256(
            self.model_dump(mode="json", exclude={"round_sha256"})
        )
        if self.round_sha256 != expected_hash:
            raise ValueError("round hash does not match canonical content")
        return self

    @classmethod
    def create(cls, **values: object) -> MutationRoundResult:
        provisional = cls.model_construct(**values, round_sha256="0" * 64)
        digest = canonical_sha256(
            provisional.model_dump(mode="json", exclude={"round_sha256"})
        )
        return cls.model_validate({**values, "round_sha256": digest})

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class MutationSandboxTransport(Protocol):
    @property
    def sandbox(self) -> SandboxKind: ...

    @property
    def runtime_authorization(self) -> MutationRuntimeAuthorization: ...

    def invoke(self, request: bytes, *, timeout_seconds: float) -> bytes: ...


class MutationSandboxError(RuntimeError):
    """Stable host-visible failure of the separate mutation process."""


class MutationRuntimeAuthorization(_MutationHostModel):
    """Host-signed authorization for one independently inspected runtime."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )
    schema_version: Literal[MUTATION_WORKER_RUNTIME_SCHEMA_VERSION] = (
        MUTATION_WORKER_RUNTIME_SCHEMA_VERSION
    )
    sandbox: SandboxKind
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    docker_image_reference: str | None = Field(default=None, max_length=263)
    docker_image_id: str | None = Field(default=None, max_length=71)
    docker_network_name: str | None = Field(default=None, max_length=128)
    docker_network_id: str | None = Field(default=None, max_length=64)
    nemo_sandbox_name: str | None = Field(default=None, max_length=63)
    nemo_sandbox_identity_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    nemo_policy_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    nemo_runtime_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    issued_by: str = Field(pattern=IDENTIFIER_PATTERN)
    signature_sha256: str = Field(pattern=SHA256_PATTERN)
    authorization_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_closed_identity_and_hash(self) -> Self:
        if self.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("mutation runtime authorization must be process-isolated")
        docker_values = (
            self.docker_image_reference,
            self.docker_image_id,
            self.docker_network_name,
            self.docker_network_id,
        )
        nemo_values = (
            self.nemo_sandbox_name,
            self.nemo_sandbox_identity_sha256,
            self.nemo_policy_sha256,
            self.nemo_runtime_sha256,
        )
        if self.sandbox is SandboxKind.DOCKER:
            valid_shape = all(docker_values) and not any(nemo_values)
        else:
            valid_shape = all(nemo_values) and not any(docker_values)
        if not valid_shape:
            raise ValueError("mutation runtime authorization identity is incomplete")
        expected_identity = canonical_sha256(self.identity_material)
        if self.runtime_identity_sha256 != expected_identity:
            raise ValueError("mutation runtime identity digest does not match")
        expected_authorization = canonical_sha256(
            self.model_dump(mode="json", exclude={"authorization_sha256"})
        )
        if self.authorization_sha256 != expected_authorization:
            raise ValueError("mutation runtime authorization digest does not match")
        return self

    @property
    def identity_material(self) -> dict[str, object]:
        source_identity = {
            "source_manifest_name": MUTATION_WORKER_SOURCE_MANIFEST.name,
            "source_manifest_entrypoint": MUTATION_WORKER_ENTRYPOINT,
            "source_manifest_path_count": MUTATION_WORKER_SOURCE_PATH_COUNT,
            "source_manifest_paths_sha256": canonical_sha256(
                MUTATION_WORKER_SOURCE_PATHS
            ),
            "source_manifest_sha256": self.source_manifest_sha256,
        }
        if self.sandbox is SandboxKind.DOCKER:
            return {
                "schema_version": MUTATION_WORKER_RUNTIME_SCHEMA_VERSION,
                "sandbox": self.sandbox.value,
                "image_reference": self.docker_image_reference,
                "image_id": self.docker_image_id,
                "network_name": self.docker_network_name,
                "network_id": self.docker_network_id,
                **source_identity,
                "worker_executable": MUTATION_WORKER_EXECUTABLE,
                "network_driver": "bridge",
                "network_scope": "local",
                "network_internal": True,
            }
        return {
            "schema_version": MUTATION_WORKER_RUNTIME_SCHEMA_VERSION,
            "sandbox": self.sandbox.value,
            "sandbox_name": self.nemo_sandbox_name,
            "sandbox_identity_sha256": self.nemo_sandbox_identity_sha256,
            "policy_sha256": self.nemo_policy_sha256,
            "runtime_sha256": self.nemo_runtime_sha256,
            **source_identity,
            "worker_executable": MUTATION_WORKER_EXECUTABLE,
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class _NemoMutationRuntimeInspection(_MutationHostModel):
    schema_version: Literal[MUTATION_WORKER_RUNTIME_SCHEMA_VERSION] = (
        MUTATION_WORKER_RUNTIME_SCHEMA_VERSION
    )
    sandbox_name: str = Field(min_length=1, max_length=63)
    sandbox_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    runtime_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    source_manifest_entrypoint: Literal[MUTATION_WORKER_ENTRYPOINT]
    source_manifest_path_count: Literal[MUTATION_WORKER_SOURCE_PATH_COUNT]
    worker_executable: Literal[MUTATION_WORKER_EXECUTABLE]


class MutationRuntimeAuthorizationAuthority:
    """Inspect, issue, and verify runtime authorization on the trusted host."""

    def __init__(
        self,
        signing_key: bytes,
        *,
        key_id: str,
        project_root: Path,
    ) -> None:
        self._initialize(
            signing_key=signing_key,
            key_id=key_id,
            executor=SubprocessSandboxCommandExecutor(),
            project_root=project_root,
        )

    @classmethod
    def _for_test(
        cls,
        signing_key: bytes,
        *,
        key_id: str,
        executor: SandboxCommandExecutor,
        project_root: Path,
    ) -> MutationRuntimeAuthorizationAuthority:
        instance = cls.__new__(cls)
        instance._initialize(
            signing_key=signing_key,
            key_id=key_id,
            executor=executor,
            project_root=project_root,
        )
        return instance

    def _initialize(
        self,
        *,
        signing_key: bytes,
        key_id: str,
        executor: SandboxCommandExecutor,
        project_root: Path,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("runtime authorization key must be at least 32 bytes")
        if re.fullmatch(IDENTIFIER_PATTERN, key_id) is None:
            raise ValueError("runtime authorization key ID is invalid")
        self._signing_key = bytes(signing_key)
        self._key_id = key_id
        self._executor = executor
        self._project_root = project_root.resolve(strict=True)
        if (
            MUTATION_WORKER_SOURCE_MANIFEST.entrypoint
            != MUTATION_WORKER_ENTRYPOINT
            or MUTATION_WORKER_SOURCE_MANIFEST.source_paths
            != MUTATION_WORKER_SOURCE_PATHS
            or len(MUTATION_WORKER_SOURCE_PATHS)
            != MUTATION_WORKER_SOURCE_PATH_COUNT
            or not set(MUTATION_WORKER_IMPLEMENTATION_PATHS).issubset(
                MUTATION_WORKER_SOURCE_PATHS
            )
        ):
            raise ValueError("mutation worker source manifest is not exact")
        self._source_snapshot = capture_reviewed_source_snapshot(
            self._project_root,
            MUTATION_WORKER_SOURCE_MANIFEST,
        )

    @property
    def source_snapshot(self) -> ReviewedSourceSnapshot:
        return self._source_snapshot

    def authorize_docker(
        self,
        *,
        image_reference: str,
        model_network: str,
    ) -> MutationRuntimeAuthorization:
        source_snapshot = self._require_current_source_snapshot()
        identity = self._inspect_docker_identity(
            image_reference=image_reference,
            model_network=model_network,
            source_manifest_sha256=source_snapshot.digest,
        )
        return self._issue(identity)

    def authorize_nemoclaw(
        self,
        *,
        sandbox_name: str,
        sandbox_identity_sha256: str,
        policy_sha256: str,
        runtime_sha256: str,
    ) -> MutationRuntimeAuthorization:
        source_snapshot = self._require_current_source_snapshot()
        identity = self._inspect_nemo_identity(
            sandbox_name=sandbox_name,
            sandbox_identity_sha256=sandbox_identity_sha256,
            policy_sha256=policy_sha256,
            runtime_sha256=runtime_sha256,
            source_manifest_sha256=source_snapshot.digest,
        )
        return self._issue(identity)

    def verify(self, authorization: MutationRuntimeAuthorization) -> None:
        authorization = MutationRuntimeAuthorization.model_validate(
            authorization.model_dump(mode="python")
        )
        signature = self._signature(
            authorization.model_dump(
                mode="json",
                exclude={"signature_sha256", "authorization_sha256"},
            )
        )
        if (
            authorization.issued_by != self._key_id
            or authorization.source_manifest_sha256
            != self._source_snapshot.digest
            or not hmac.compare_digest(
                authorization.signature_sha256,
                signature,
            )
        ):
            raise ValueError("mutation runtime authorization signature is invalid")

    def verify_current(
        self,
        authorization: MutationRuntimeAuthorization,
    ) -> None:
        self.verify(authorization)
        source_snapshot = self._require_current_source_snapshot()
        if authorization.sandbox is SandboxKind.DOCKER:
            assert authorization.docker_image_reference is not None
            assert authorization.docker_network_name is not None
            current = self._inspect_docker_identity(
                image_reference=authorization.docker_image_reference,
                model_network=authorization.docker_network_name,
                source_manifest_sha256=source_snapshot.digest,
            )
        else:
            assert authorization.nemo_sandbox_name is not None
            current = self._inspect_nemo_identity(
                sandbox_name=authorization.nemo_sandbox_name,
                sandbox_identity_sha256=(
                    authorization.nemo_sandbox_identity_sha256
                ),
                policy_sha256=authorization.nemo_policy_sha256,
                runtime_sha256=authorization.nemo_runtime_sha256,
                source_manifest_sha256=source_snapshot.digest,
            )
        if current != authorization.identity_material:
            raise ValueError("mutation runtime no longer matches its authorization")

    def _require_current_source_snapshot(self) -> ReviewedSourceSnapshot:
        try:
            current = capture_reviewed_source_snapshot(
                self._project_root,
                MUTATION_WORKER_SOURCE_MANIFEST,
            )
        except (OSError, ValueError) as exc:
            raise ValueError(
                "mutation worker reviewed source is unavailable or changed"
            ) from exc
        if current != self._source_snapshot:
            raise ValueError("mutation worker reviewed source changed")
        return current

    def _issue(self, identity: dict[str, object]) -> MutationRuntimeAuthorization:
        sandbox = SandboxKind(str(identity["sandbox"]))
        values: dict[str, object] = {
            "schema_version": MUTATION_WORKER_RUNTIME_SCHEMA_VERSION,
            "sandbox": sandbox,
            "runtime_identity_sha256": canonical_sha256(identity),
            "source_manifest_sha256": identity["source_manifest_sha256"],
            "docker_image_reference": identity.get("image_reference"),
            "docker_image_id": identity.get("image_id"),
            "docker_network_name": identity.get("network_name"),
            "docker_network_id": identity.get("network_id"),
            "nemo_sandbox_name": identity.get("sandbox_name"),
            "nemo_sandbox_identity_sha256": identity.get(
                "sandbox_identity_sha256"
            ),
            "nemo_policy_sha256": identity.get("policy_sha256"),
            "nemo_runtime_sha256": identity.get("runtime_sha256"),
            "issued_by": self._key_id,
        }
        signature = self._signature(
            MutationRuntimeAuthorization.model_construct(
                **values,
                signature_sha256="0" * 64,
                authorization_sha256="0" * 64,
            ).model_dump(
                mode="json",
                exclude={"signature_sha256", "authorization_sha256"},
            )
        )
        signed = {**values, "signature_sha256": signature}
        return MutationRuntimeAuthorization.model_validate(
            {
                **signed,
                "authorization_sha256": canonical_sha256(signed),
            }
        )

    def _inspect_docker_identity(
        self,
        *,
        image_reference: str,
        model_network: str,
        source_manifest_sha256: str,
    ) -> dict[str, object]:
        if _MUTATION_DOCKER_IMAGE_PATTERN.fullmatch(image_reference) is None:
            raise ValueError("mutation worker image must use an immutable digest")
        if (
            _MUTATION_DOCKER_NETWORK_PATTERN.fullmatch(model_network) is None
            or model_network in _MUTATION_DOCKER_RESERVED_NETWORKS
        ):
            raise ValueError("mutation worker requires a dedicated model network")
        if re.fullmatch(SHA256_PATTERN, source_manifest_sha256) is None:
            raise ValueError("mutation source manifest digest is invalid")
        image = self._inspect_docker_object("image", image_reference)
        network = self._inspect_docker_object("network", model_network)
        image_id = image.get("Id")
        repo_digests = image.get("RepoDigests")
        config = image.get("Config")
        network_id = network.get("Id")
        network_labels = network.get("Labels")
        if not isinstance(config, dict):
            raise ValueError("mutation worker image inspection is incomplete")
        image_labels = config.get("Labels")
        source_digest = (
            image_labels.get(MUTATION_DOCKER_SOURCE_MANIFEST_LABEL)
            if isinstance(image_labels, dict)
            else None
        )
        source_entrypoint = (
            image_labels.get(MUTATION_DOCKER_SOURCE_ENTRYPOINT_LABEL)
            if isinstance(image_labels, dict)
            else None
        )
        valid = (
            isinstance(image_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None
            and isinstance(repo_digests, list)
            and image_reference in repo_digests
            and config.get("Entrypoint") == [MUTATION_WORKER_EXECUTABLE]
            and isinstance(source_digest, str)
            and source_digest == source_manifest_sha256
            and source_entrypoint == MUTATION_WORKER_ENTRYPOINT
            and isinstance(network_id, str)
            and re.fullmatch(r"[0-9a-f]{64}", network_id) is not None
            and network.get("Name") == model_network
            and network.get("Driver") == "bridge"
            and network.get("Scope") == "local"
            and network.get("Internal") is True
            and isinstance(network_labels, dict)
            and network_labels.get(MUTATION_DOCKER_NETWORK_LABEL) == "true"
        )
        if not valid:
            raise ValueError("mutation Docker runtime inspection is not reviewed")
        return {
            "schema_version": MUTATION_WORKER_RUNTIME_SCHEMA_VERSION,
            "sandbox": SandboxKind.DOCKER.value,
            "image_reference": image_reference,
            "image_id": image_id,
            "network_name": model_network,
            "network_id": network_id,
            "source_manifest_name": MUTATION_WORKER_SOURCE_MANIFEST.name,
            "source_manifest_entrypoint": MUTATION_WORKER_ENTRYPOINT,
            "source_manifest_path_count": MUTATION_WORKER_SOURCE_PATH_COUNT,
            "source_manifest_paths_sha256": canonical_sha256(
                MUTATION_WORKER_SOURCE_PATHS
            ),
            "source_manifest_sha256": source_digest,
            "worker_executable": MUTATION_WORKER_EXECUTABLE,
            "network_driver": "bridge",
            "network_scope": "local",
            "network_internal": True,
        }

    def _inspect_docker_object(
        self,
        kind: Literal["image", "network"],
        identity: str,
    ) -> dict[str, object]:
        completed = self._executor.run(
            (MUTATION_DOCKER_HOST_CLI_EXECUTABLE, kind, "inspect", identity),
            input_bytes=b"",
            timeout_seconds=MUTATION_RUNTIME_INSPECTION_TIMEOUT_SECONDS,
        )
        if (
            completed.returncode != 0
            or not completed.stdout
            or len(completed.stdout) > MAX_MUTATION_RUNTIME_INSPECTION_BYTES
        ):
            raise ValueError("mutation Docker runtime inspection failed")
        try:
            decoded = json.loads(completed.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("mutation Docker runtime inspection is invalid") from exc
        if not isinstance(decoded, list) or len(decoded) != 1:
            raise ValueError("mutation Docker runtime inspection is ambiguous")
        value = decoded[0]
        if not isinstance(value, dict):
            raise ValueError("mutation Docker runtime inspection is invalid")
        return value

    def _inspect_nemo_identity(
        self,
        *,
        sandbox_name: str,
        sandbox_identity_sha256: str | None,
        policy_sha256: str | None,
        runtime_sha256: str | None,
        source_manifest_sha256: str,
    ) -> dict[str, object]:
        if NEMOCLAW_SANDBOX_NAME_PATTERN.fullmatch(sandbox_name) is None:
            raise ValueError("invalid mutation NemoClaw sandbox name")
        expected_digests = (
            sandbox_identity_sha256,
            policy_sha256,
            runtime_sha256,
            source_manifest_sha256,
        )
        if any(
            not isinstance(value, str)
            or re.fullmatch(SHA256_PATTERN, value) is None
            for value in expected_digests
        ):
            raise ValueError("mutation NemoClaw reviewed identity is invalid")
        completed = self._executor.run(
            (
                NEMOCLAW_HOST_CLI_EXECUTABLE,
                sandbox_name,
                "exec",
                "--no-tty",
                "--timeout",
                str(int(MUTATION_RUNTIME_INSPECTION_TIMEOUT_SECONDS)),
                "--stdin",
                "--",
                NEMOCLAW_MANAGED_EXEC_LAUNCHER,
                MUTATION_NEMO_RUNTIME_IDENTITY_EXECUTABLE,
            ),
            input_bytes=b"",
            timeout_seconds=MUTATION_RUNTIME_INSPECTION_TIMEOUT_SECONDS,
        )
        if (
            completed.returncode != 0
            or not completed.stdout
            or len(completed.stdout) > 16 * 1024
        ):
            raise ValueError("mutation NemoClaw runtime inspection failed")
        try:
            inspected = _NemoMutationRuntimeInspection.model_validate_json(
                completed.stdout
            )
        except ValueError as exc:
            raise ValueError("mutation NemoClaw runtime inspection is invalid") from exc
        if (
            inspected.sandbox_name != sandbox_name
            or inspected.sandbox_identity_sha256 != sandbox_identity_sha256
            or inspected.policy_sha256 != policy_sha256
            or inspected.runtime_sha256 != runtime_sha256
            or inspected.source_manifest_sha256 != source_manifest_sha256
            or inspected.source_manifest_entrypoint
            != MUTATION_WORKER_ENTRYPOINT
            or inspected.source_manifest_path_count
            != MUTATION_WORKER_SOURCE_PATH_COUNT
        ):
            raise ValueError("mutation NemoClaw sandbox identity does not match")
        return {
            "schema_version": MUTATION_WORKER_RUNTIME_SCHEMA_VERSION,
            "sandbox": SandboxKind.NEMOCLAW.value,
            "sandbox_name": inspected.sandbox_name,
            "sandbox_identity_sha256": inspected.sandbox_identity_sha256,
            "policy_sha256": inspected.policy_sha256,
            "runtime_sha256": inspected.runtime_sha256,
            "source_manifest_name": MUTATION_WORKER_SOURCE_MANIFEST.name,
            "source_manifest_entrypoint": inspected.source_manifest_entrypoint,
            "source_manifest_path_count": inspected.source_manifest_path_count,
            "source_manifest_paths_sha256": canonical_sha256(
                MUTATION_WORKER_SOURCE_PATHS
            ),
            "source_manifest_sha256": inspected.source_manifest_sha256,
            "worker_executable": inspected.worker_executable,
        }

    def _signature(self, material: dict[str, object]) -> str:
        return hmac.new(
            self._signing_key,
            canonical_json_bytes(
                {
                    "contract": "MutationRuntimeAuthorization",
                    "material": material,
                }
            ),
            sha256,
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class MutationSandboxCleanupEvidence:
    """Host-observed custody state for one exact mutation container name."""

    attempt_id: UUID
    container_name: str
    attempt_count: int
    removal_attempted: bool
    removal_returncode: int | None
    absence_observed: bool
    unresolved: bool


class MutationSandboxCleanupError(MutationSandboxError):
    """A named mutation container could not be proven absent."""

    def __init__(self, evidence: MutationSandboxCleanupEvidence) -> None:
        self.evidence = evidence
        super().__init__("mutation_sandbox_cleanup_unresolved")


class ProcessMutationSandboxTransport:
    """Single-use closed launcher with host-owned Docker cleanup custody."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "use the closed docker or nemoclaw mutation transport constructor"
        )

    @classmethod
    def docker(
        cls,
        authorization: MutationRuntimeAuthorization,
        *,
        authority: MutationRuntimeAuthorizationAuthority,
    ) -> ProcessMutationSandboxTransport:
        return cls._initialize_docker(
            authorization=authorization,
            authority=authority,
            attempt_id=uuid4(),
            executor=SubprocessSandboxCommandExecutor(),
        )

    @classmethod
    def nemoclaw(
        cls,
        authorization: MutationRuntimeAuthorization,
        *,
        authority: MutationRuntimeAuthorizationAuthority,
    ) -> ProcessMutationSandboxTransport:
        return cls._initialize_nemoclaw(
            authorization=authorization,
            authority=authority,
            executor=SubprocessSandboxCommandExecutor(),
        )

    @classmethod
    def _docker_for_test(
        cls,
        *,
        authorization: MutationRuntimeAuthorization,
        authority: MutationRuntimeAuthorizationAuthority,
        attempt_id: UUID,
        executor: SandboxCommandExecutor,
    ) -> ProcessMutationSandboxTransport:
        return cls._initialize_docker(
            authorization=authorization,
            authority=authority,
            attempt_id=attempt_id,
            executor=executor,
        )

    @classmethod
    def _nemoclaw_for_test(
        cls,
        *,
        authorization: MutationRuntimeAuthorization,
        authority: MutationRuntimeAuthorizationAuthority,
        executor: SandboxCommandExecutor,
    ) -> ProcessMutationSandboxTransport:
        return cls._initialize_nemoclaw(
            authorization=authorization,
            authority=authority,
            executor=executor,
        )

    @classmethod
    def _initialize_docker(
        cls,
        *,
        authorization: MutationRuntimeAuthorization,
        authority: MutationRuntimeAuthorizationAuthority,
        attempt_id: UUID,
        executor: SandboxCommandExecutor,
    ) -> ProcessMutationSandboxTransport:
        if attempt_id.version != 4:
            raise ValueError("mutation container attempt identity must be UUIDv4")
        authority.verify(authorization)
        if authorization.sandbox is not SandboxKind.DOCKER:
            raise ValueError("Docker transport requires Docker authorization")
        instance = cls.__new__(cls)
        instance._sandbox = SandboxKind.DOCKER
        instance._runtime_authorization = authorization
        instance._runtime_authority = authority
        instance._attempt_id = attempt_id
        instance._container_name = f"vital-relay-mutation-{attempt_id.hex}"
        instance._executor = executor
        instance._used = False
        instance._docker_custody = False
        instance._cleanup_history = ()
        instance._last_command = None
        instance._lifecycle_lock = threading.RLock()
        return instance

    @classmethod
    def _initialize_nemoclaw(
        cls,
        *,
        authorization: MutationRuntimeAuthorization,
        authority: MutationRuntimeAuthorizationAuthority,
        executor: SandboxCommandExecutor,
    ) -> ProcessMutationSandboxTransport:
        authority.verify(authorization)
        if authorization.sandbox is not SandboxKind.NEMOCLAW:
            raise ValueError("NemoClaw transport requires NemoClaw authorization")
        instance = cls.__new__(cls)
        instance._sandbox = SandboxKind.NEMOCLAW
        instance._runtime_authorization = authorization
        instance._runtime_authority = authority
        instance._attempt_id = None
        instance._container_name = None
        instance._executor = executor
        instance._used = False
        instance._docker_custody = False
        instance._cleanup_history = ()
        instance._last_command = None
        instance._lifecycle_lock = threading.RLock()
        return instance

    @property
    def sandbox(self) -> SandboxKind:
        return self._sandbox

    @property
    def container_name(self) -> str | None:
        return self._container_name

    @property
    def runtime_authorization(self) -> MutationRuntimeAuthorization:
        return self._runtime_authorization

    @property
    def cleanup_history(self) -> tuple[MutationSandboxCleanupEvidence, ...]:
        with self._lifecycle_lock:
            return self._cleanup_history

    @property
    def cleanup_required(self) -> bool:
        with self._lifecycle_lock:
            return self._docker_custody

    @property
    def last_command(self) -> tuple[str, ...] | None:
        with self._lifecycle_lock:
            return self._last_command

    def invoke(self, request: bytes, *, timeout_seconds: float) -> bytes:
        if not request or len(request) > MAX_MUTATION_SANDBOX_REQUEST_BYTES:
            raise ValueError("invalid mutation sandbox request size")
        if timeout_seconds < 1 or timeout_seconds > 300:
            raise ValueError("mutation sandbox timeout must be between 1 and 300")
        envelope = MutationSandboxRequest.from_wire_bytes(request)
        if envelope.sandbox.value != self._sandbox.value:
            raise ValueError("mutation request does not match the selected sandbox")
        if (
            envelope.runtime_authorization_sha256
            != self._runtime_authorization.authorization_sha256
            or envelope.runtime_identity_sha256
            != self._runtime_authorization.runtime_identity_sha256
            or envelope.source_manifest_sha256
            != self._runtime_authorization.source_manifest_sha256
        ):
            raise ValueError("mutation request runtime authorization does not match")
        with self._lifecycle_lock:
            if self._used:
                raise MutationSandboxError("mutation_sandbox_transport_already_used")
            self._used = True
            self._runtime_authority.verify_current(self._runtime_authorization)
            if self._sandbox is SandboxKind.DOCKER:
                return self._invoke_docker(
                    request=request,
                    timeout_seconds=timeout_seconds,
                )
            return self._invoke_nemoclaw(
                request=request,
                timeout_seconds=timeout_seconds,
            )

    def retry_cleanup(self) -> MutationSandboxCleanupEvidence:
        """Retry exact-name Docker cleanup while preserving unresolved custody."""

        with self._lifecycle_lock:
            if self._sandbox is not SandboxKind.DOCKER:
                raise ValueError("NemoClaw mutation transports have no Docker cleanup")
            if not self._docker_custody:
                if not self._cleanup_history:
                    raise ValueError("no Docker mutation cleanup has been attempted")
                return self._cleanup_history[-1]
            return self._ensure_docker_absent()

    def _invoke_docker(
        self,
        *,
        request: bytes,
        timeout_seconds: float,
    ) -> bytes:
        if self._observe_container() != "absent":
            raise MutationSandboxError("mutation_sandbox_name_not_available")
        command = self._docker_command()
        self._last_command = command
        self._docker_custody = True
        completed: CompletedSandboxCommand | None = None
        primary: BaseException | None = None
        try:
            completed = self._executor.run(
                command,
                input_bytes=request,
                timeout_seconds=timeout_seconds,
            )
        except BaseException as exc:
            primary = exc
        try:
            self._ensure_docker_absent()
        except MutationSandboxCleanupError as cleanup_exc:
            if primary is not None:
                raise cleanup_exc from primary
            raise
        if primary is not None:
            self._raise_process_failure(primary)
        assert completed is not None
        return self._validated_stdout(completed)

    def _invoke_nemoclaw(
        self,
        *,
        request: bytes,
        timeout_seconds: float,
    ) -> bytes:
        sandbox_name = self._runtime_authorization.nemo_sandbox_name
        assert sandbox_name is not None
        inner_timeout = str(max(1, int(timeout_seconds)))
        command = (
            NEMOCLAW_HOST_CLI_EXECUTABLE,
            sandbox_name,
            "exec",
            "--no-tty",
            "--timeout",
            inner_timeout,
            "--stdin",
            "--",
            NEMOCLAW_MANAGED_EXEC_LAUNCHER,
            MUTATION_WORKER_EXECUTABLE,
        )
        self._last_command = command
        try:
            completed = self._executor.run(
                command,
                input_bytes=request,
                timeout_seconds=timeout_seconds,
            )
        except BaseException as exc:
            self._raise_process_failure(exc)
        return self._validated_stdout(completed)

    def _docker_command(self) -> tuple[str, ...]:
        authorization = self._runtime_authorization
        assert self._attempt_id is not None
        assert self._container_name is not None
        assert authorization.docker_network_id is not None
        assert authorization.docker_image_reference is not None
        return (
            MUTATION_DOCKER_HOST_CLI_EXECUTABLE,
            "run",
            "--rm",
            "--name",
            self._container_name,
            "--interactive",
            "--pull",
            "never",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1.0",
            "--network",
            authorization.docker_network_id,
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONUNBUFFERED=1",
            "--label",
            f"{MUTATION_DOCKER_ATTEMPT_LABEL}={self._attempt_id}",
            "--label",
            (
                f"{MUTATION_DOCKER_SOURCE_MANIFEST_LABEL}="
                f"{authorization.source_manifest_sha256}"
            ),
            "--entrypoint",
            MUTATION_WORKER_EXECUTABLE,
            authorization.docker_image_reference,
        )

    def _container_list_command(self) -> tuple[str, ...]:
        assert self._container_name is not None
        return (
            MUTATION_DOCKER_HOST_CLI_EXECUTABLE,
            "container",
            "ls",
            "--all",
            "--quiet",
            "--no-trunc",
            "--filter",
            f"name=^/{self._container_name}$",
        )

    def _observe_container(self) -> Literal["absent", "present", "unknown"]:
        try:
            completed = self._executor.run(
                self._container_list_command(),
                input_bytes=b"",
                timeout_seconds=MUTATION_DOCKER_CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            return "unknown"
        if completed.returncode != 0:
            return "unknown"
        return "absent" if not completed.stdout.strip() else "present"

    def _container_owner_matches(self) -> bool:
        assert self._container_name is not None
        assert self._attempt_id is not None
        command = (
            MUTATION_DOCKER_HOST_CLI_EXECUTABLE,
            "container",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{MUTATION_DOCKER_ATTEMPT_LABEL}" }}}}',
            self._container_name,
        )
        try:
            completed = self._executor.run(
                command,
                input_bytes=b"",
                timeout_seconds=MUTATION_DOCKER_CLEANUP_TIMEOUT_SECONDS,
            )
        except BaseException:
            return False
        return (
            completed.returncode == 0
            and completed.stdout.strip() == str(self._attempt_id).encode("ascii")
        )

    def _ensure_docker_absent(self) -> MutationSandboxCleanupEvidence:
        assert self._container_name is not None
        assert self._attempt_id is not None
        attempt_count = len(self._cleanup_history) + 1
        state = self._observe_container()
        removal_attempted = False
        removal_returncode: int | None = None
        if state != "absent" and self._container_owner_matches():
            removal_attempted = True
            try:
                removal = self._executor.run(
                    (
                        MUTATION_DOCKER_HOST_CLI_EXECUTABLE,
                        "rm",
                        "--force",
                        self._container_name,
                    ),
                    input_bytes=b"",
                    timeout_seconds=MUTATION_DOCKER_CLEANUP_TIMEOUT_SECONDS,
                )
                removal_returncode = removal.returncode
            except BaseException:
                removal_returncode = None
            state = self._observe_container()
        absence_observed = state == "absent"
        evidence = MutationSandboxCleanupEvidence(
            attempt_id=self._attempt_id,
            container_name=self._container_name,
            attempt_count=attempt_count,
            removal_attempted=removal_attempted,
            removal_returncode=removal_returncode,
            absence_observed=absence_observed,
            unresolved=not absence_observed,
        )
        self._cleanup_history = (*self._cleanup_history, evidence)
        self._docker_custody = not absence_observed
        if not absence_observed:
            raise MutationSandboxCleanupError(evidence)
        return evidence

    @staticmethod
    def _raise_process_failure(exc: BaseException) -> Never:
        if isinstance(exc, subprocess.TimeoutExpired):
            raise MutationSandboxError("mutation_sandbox_timeout") from exc
        if isinstance(exc, SandboxOutputLimitExceeded):
            raise MutationSandboxError("mutation_sandbox_output_overflow") from exc
        if not isinstance(exc, Exception):
            raise exc
        raise MutationSandboxError("mutation_sandbox_unavailable") from exc

    @staticmethod
    def _validated_stdout(completed: CompletedSandboxCommand) -> bytes:
        if completed.returncode != 0:
            raise MutationSandboxError("mutation_sandbox_failed")
        if (
            not completed.stdout
            or len(completed.stdout) > MAX_MUTATION_SANDBOX_RESPONSE_BYTES
        ):
            raise MutationSandboxError("mutation_sandbox_invalid_output")
        return completed.stdout


class AuthorizedMutationRoundRunner:
    """Trusted-host authorization, verification, materialization, and signing."""

    def __init__(
        self,
        *,
        integrity_authority: HostIntegrityAuthority,
        bundle_verifier: CandidateArtifactAttestationVerifier,
        runtime_authority: MutationRuntimeAuthorizationAuthority,
        transport: MutationSandboxTransport,
        local_model: LocalModelConfig,
        budget: MutationRoundBudget,
        sandbox_timeout_seconds: float = 120.0,
    ) -> None:
        if type(transport) is not ProcessMutationSandboxTransport:
            raise TypeError(
                "mutation product path requires the reviewed process transport"
            )
        self._initialize(
            integrity_authority=integrity_authority,
            bundle_verifier=bundle_verifier,
            runtime_authority=runtime_authority,
            transport=transport,
            local_model=local_model,
            budget=budget,
            sandbox_timeout_seconds=sandbox_timeout_seconds,
        )

    @classmethod
    def _for_test(
        cls,
        *,
        integrity_authority: HostIntegrityAuthority,
        bundle_verifier: CandidateArtifactAttestationVerifier,
        runtime_authority: MutationRuntimeAuthorizationAuthority,
        transport: MutationSandboxTransport,
        local_model: LocalModelConfig,
        budget: MutationRoundBudget,
        sandbox_timeout_seconds: float = 120.0,
    ) -> AuthorizedMutationRoundRunner:
        instance = cls.__new__(cls)
        instance._initialize(
            integrity_authority=integrity_authority,
            bundle_verifier=bundle_verifier,
            runtime_authority=runtime_authority,
            transport=transport,
            local_model=local_model,
            budget=budget,
            sandbox_timeout_seconds=sandbox_timeout_seconds,
        )
        return instance

    def _initialize(
        self,
        *,
        integrity_authority: HostIntegrityAuthority,
        bundle_verifier: CandidateArtifactAttestationVerifier,
        runtime_authority: MutationRuntimeAuthorizationAuthority,
        transport: MutationSandboxTransport,
        local_model: LocalModelConfig,
        budget: MutationRoundBudget,
        sandbox_timeout_seconds: float,
    ) -> None:
        if transport.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("mutation product path requires a process sandbox")
        if type(runtime_authority) is not MutationRuntimeAuthorizationAuthority:
            raise TypeError(
                "mutation runtime authorization requires the host authority"
            )
        runtime_authority.verify(transport.runtime_authorization)
        if transport.runtime_authorization.sandbox is not transport.sandbox:
            raise ValueError("mutation transport runtime authorization is mismatched")
        if sandbox_timeout_seconds < 1 or sandbox_timeout_seconds > 300:
            raise ValueError("mutation sandbox timeout must be between 1 and 300")
        self._integrity_authority = integrity_authority
        self._bundle_verifier = bundle_verifier
        self._runtime_authority = runtime_authority
        self._transport = transport
        self._local_model = LocalModelConfig.model_validate(local_model)
        self._budget = MutationRoundBudget.model_validate(budget)
        if self._budget.output_budget_tokens > self._local_model.max_tokens:
            raise ValueError("round output budget exceeds the model configuration")
        self._sandbox_timeout_seconds = sandbox_timeout_seconds

    @property
    def model_identity(self) -> ModelIdentity:
        return self._local_model.model_identity

    def run(
        self,
        *,
        parent_bundle: CandidateBundle,
        development_report: EvaluationReport,
        round_id: UUID,
        created_at: datetime,
    ) -> MutationRoundResult:
        request = self._authorized_request(
            parent_bundle=parent_bundle,
            development_report=development_report,
            round_id=round_id,
            created_at=created_at,
        )
        response = MutationSandboxResponse.from_wire_bytes(
            self._transport.invoke(
                request.to_wire_bytes(),
                timeout_seconds=self._sandbox_timeout_seconds,
            )
        )
        if (
            response.sandbox.value != self._transport.sandbox.value
            or response.runtime_authorization_sha256
            != request.runtime_authorization_sha256
            or response.runtime_identity_sha256 != request.runtime_identity_sha256
            or response.source_manifest_sha256
            != request.source_manifest_sha256
            or response.request_sha256 != request.request_sha256
            or response.result.round_id != request.round_id
            or response.result.attempted_at != request.created_at
        ):
            raise MutationSandboxError("mutation_sandbox_response_mismatch")
        result = _materialize_worker_result(
            parent=parent_bundle.materials.candidate,
            policy=CoordinationPolicySnapshot.model_validate(
                request.policy.model_dump(mode="python")
            ),
            improver=ImproverArtifact.model_validate(
                request.improver.model_dump(mode="python")
            ),
            worker_result=response.result,
        )
        self._verify_result(
            parent_bundle=parent_bundle,
            development_report=development_report,
            result=result,
        )
        return result

    def issue_improver_receipt(
        self,
        *,
        parent_bundle: CandidateBundle,
        development_report: EvaluationReport,
        result: MutationRoundResult,
        selected_record_sha256: str,
        authority: CandidateArtifactAttestationAuthority,
    ) -> ImproverMutationReceipt:
        self._verify_result(
            parent_bundle=parent_bundle,
            development_report=development_report,
            result=result,
        )
        if not result.complete:
            raise ValueError("incomplete mutation rounds are not receipt-eligible")
        selected = tuple(
            material
            for material in result.successful
            if material.record.record_sha256 == selected_record_sha256
        )
        if len(selected) != 1:
            raise ValueError("receipt selection must name one successful round output")
        material = selected[0]
        if material.mutation.target is not MutationTarget.IMPROVER:
            raise ValueError("only improver mutations receive improver receipts")
        parent_bundle.verify_attestations(authority)
        provenance_sha256 = canonical_sha256(
            {
                "contract": "complete_mutation_round_selection_v1",
                "source_report_sha256": development_report.report_sha256,
                "round_sha256": result.round_sha256,
                "worker_result_sha256": result.worker_result.result_sha256,
                "runtime_authorization_sha256": (
                    result.runtime_authorization_sha256
                ),
                "runtime_identity_sha256": result.runtime_identity_sha256,
                "source_manifest_sha256": result.source_manifest_sha256,
                "configured_model_sha256": self.model_identity.model_sha256,
                "budget": self._budget.model_dump(mode="json"),
                "selected_record_sha256": selected_record_sha256,
                "selected_candidate_sha256": material.candidate.candidate_sha256,
                "selected_mutation_sha256": material.mutation.mutation_sha256,
            }
        )
        self._runtime_authority.verify_current(
            self._transport.runtime_authorization
        )
        return authority.issue_improver_mutation_receipt(
            parent=parent_bundle,
            mutation=material.mutation,
            child_improver_bytes=material.improver_bytes,
            runtime_authorization_sha256=result.runtime_authorization_sha256,
            runtime_identity_sha256=result.runtime_identity_sha256,
            source_manifest_sha256=result.source_manifest_sha256,
            round_sha256=result.round_sha256,
            worker_result_sha256=result.worker_result.result_sha256,
            selected_record_sha256=selected_record_sha256,
            selected_candidate_sha256=material.candidate.candidate_sha256,
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        )

    def _authorized_request(
        self,
        *,
        parent_bundle: CandidateBundle,
        development_report: EvaluationReport,
        round_id: UUID,
        created_at: datetime,
    ) -> MutationSandboxRequest:
        self._runtime_authority.verify(self._transport.runtime_authorization)
        parent, report, packet, policy, improver = self._verified_inputs(
            parent_bundle,
            development_report,
        )
        request = MutationSandboxRequest(
            sandbox=self._transport.sandbox.value,
            runtime_authorization_sha256=(
                self._transport.runtime_authorization.authorization_sha256
            ),
            runtime_identity_sha256=(
                self._transport.runtime_authorization.runtime_identity_sha256
            ),
            source_manifest_sha256=(
                self._transport.runtime_authorization.source_manifest_sha256
            ),
            round_id=round_id,
            parent=MutationWorkerParent.model_validate(
                {
                    "candidate_id": parent.candidate_id,
                    "candidate_sha256": parent.candidate_sha256,
                    "generation": parent.generation,
                    "policy": parent.policy.model_dump(mode="python"),
                    "improver": parent.improver.model_dump(mode="python"),
                }
            ),
            source_report_sha256=report.report_sha256,
            failure_packet=WorkerFailurePacket.model_validate(
                packet.model_dump(mode="python")
            ),
            policy=WorkerCoordinationPolicy.model_validate(
                policy.model_dump(mode="python")
            ),
            improver=WorkerImproverArtifact.model_validate(
                improver.model_dump(mode="python")
            ),
            budget=self._budget,
            created_at=created_at,
            local_model=self._local_model,
        )
        self._verify_worker_requests(request, worker_result=None)
        return request

    def _verified_inputs(
        self,
        parent_bundle: CandidateBundle,
        development_report: EvaluationReport,
    ) -> tuple[
        CandidateManifest,
        EvaluationReport,
        FailurePacket,
        CoordinationPolicySnapshot,
        ImproverArtifact,
    ]:
        parent_bundle.verify_attestations(self._bundle_verifier)
        parent = parent_bundle.materials.candidate
        report = EvaluationReport.model_validate(development_report)
        self._integrity_authority.verify_report(parent, report)
        if report.partition is not PartitionName.DEVELOPMENT:
            raise ValueError("mutation rounds require a signed development report")
        packet = build_failure_packet(report)
        policy = CoordinationPolicySnapshot.model_validate_json(
            parent_bundle.materials.policy_bytes
        )
        improver = ImproverArtifact.model_validate_json(
            parent_bundle.materials.improver_bytes
        )
        if (
            policy.canonical_bytes != parent_bundle.materials.policy_bytes
            or improver.canonical_bytes != parent_bundle.materials.improver_bytes
            or policy.reference != parent.policy
            or improver.reference != parent.improver
        ):
            raise ValueError("parent bundle does not contain canonical typed inputs")
        return parent, report, packet, policy, improver

    def _verify_result(
        self,
        *,
        parent_bundle: CandidateBundle,
        development_report: EvaluationReport,
        result: MutationRoundResult,
    ) -> None:
        self._runtime_authority.verify(self._transport.runtime_authorization)
        parent, report, packet, policy, improver = self._verified_inputs(
            parent_bundle,
            development_report,
        )
        result = MutationRoundResult.model_validate(result.model_dump(mode="python"))
        request = MutationSandboxRequest(
            sandbox=self._transport.sandbox.value,
            runtime_authorization_sha256=(
                self._transport.runtime_authorization.authorization_sha256
            ),
            runtime_identity_sha256=(
                self._transport.runtime_authorization.runtime_identity_sha256
            ),
            source_manifest_sha256=(
                self._transport.runtime_authorization.source_manifest_sha256
            ),
            round_id=result.round_id,
            parent=MutationWorkerParent.model_validate(
                {
                    "candidate_id": parent.candidate_id,
                    "candidate_sha256": parent.candidate_sha256,
                    "generation": parent.generation,
                    "policy": parent.policy.model_dump(mode="python"),
                    "improver": parent.improver.model_dump(mode="python"),
                }
            ),
            source_report_sha256=report.report_sha256,
            failure_packet=WorkerFailurePacket.model_validate(
                packet.model_dump(mode="python")
            ),
            policy=WorkerCoordinationPolicy.model_validate(
                policy.model_dump(mode="python")
            ),
            improver=WorkerImproverArtifact.model_validate(
                improver.model_dump(mode="python")
            ),
            budget=self._budget,
            created_at=result.attempted_at,
            local_model=self._local_model,
        )
        worker = result.worker_result
        if (
            result.parent_candidate_sha256 != parent.candidate_sha256
            or result.failure_packet_sha256 != canonical_sha256(packet)
            or result.budget != self._budget
            or result.runtime_authorization_sha256
            != request.runtime_authorization_sha256
            or result.runtime_identity_sha256 != request.runtime_identity_sha256
            or result.source_manifest_sha256 != request.source_manifest_sha256
            or worker.sandbox.value != self._transport.sandbox.value
            or worker.runtime_authorization_sha256
            != request.runtime_authorization_sha256
            or worker.runtime_identity_sha256 != request.runtime_identity_sha256
            or worker.source_manifest_sha256 != request.source_manifest_sha256
            or worker.sandbox_request_sha256 != request.request_sha256
            or worker.source_report_sha256 != report.report_sha256
        ):
            raise ValueError("mutation round does not match authorized host inputs")
        self._verify_worker_requests(request, worker_result=worker)
        expected = _materialize_worker_result(
            parent=parent,
            policy=policy,
            improver=improver,
            worker_result=worker,
        )
        if expected != result:
            raise ValueError("mutation round failed trusted host materialization")

    def _verify_worker_requests(
        self,
        request: MutationSandboxRequest,
        *,
        worker_result: MutationWorkerResult | None,
    ) -> None:
        payload = proposal_payload(request)
        with LocalOpenAIModelClient(self._local_model) as verifier_client:
            for slot, seed in enumerate(self._budget.seeds):
                binding = verifier_client.create_request_binding(
                    seed=seed,
                    budget_slot=slot,
                    candidate_budget=self._budget.candidate_budget,
                    input_budget_bytes=self._budget.input_budget_bytes,
                    output_budget_tokens=self._budget.output_budget_tokens,
                    system_prompt=MUTATION_SYSTEM_PROMPT,
                    response_model=MutationModelOutput,
                    schema_name=MUTATION_MODEL_SCHEMA_NAME,
                )
                request_sha256 = verifier_client.bound_request_sha256(
                    binding=binding,
                    system_prompt=MUTATION_SYSTEM_PROMPT,
                    user_payload=payload,
                    response_model=MutationModelOutput,
                )
                if worker_result is not None:
                    record = worker_result.proposals[slot]
                    if (
                        record.request_binding != binding
                        or record.request_sha256 != request_sha256
                    ):
                        raise ValueError(
                            "worker proposal request binding is invalid"
                        )


def _materialize_worker_result(
    *,
    parent: CandidateManifest,
    policy: CoordinationPolicySnapshot,
    improver: ImproverArtifact,
    worker_result: MutationWorkerResult,
) -> MutationRoundResult:
    policy_adapter = A2PolicyArtifactAdapter((policy,))
    improver_adapter = CanonicalImproverArtifactAdapter((improver,))
    factory = CandidateFactory()
    records: list[MutationProposalRecord] = []
    successful: list[BundleReadyMutation] = []
    invalid_attempts: list[InvalidAttemptRecord] = []
    for worker_record in worker_result.proposals:
        if (
            worker_record.status
            is MutationWorkerProposalStatus.REQUEST_FAILED
        ):
            records.append(
                _host_record(
                    worker_record,
                    worker_result=worker_result,
                    status=MutationProposalStatus.REQUEST_FAILED,
                    model_failure_code=worker_record.failure_code,
                )
            )
            continue
        if (
            worker_record.status
            is MutationWorkerProposalStatus.INVALID_RESPONSE
        ):
            reason = InvalidAttemptReason.MALFORMED_MANIFEST
            records.append(
                _host_record(
                    worker_record,
                    worker_result=worker_result,
                    status=MutationProposalStatus.INVALID,
                    invalid_reason=reason,
                    model_failure_code=worker_record.failure_code,
                )
            )
            invalid_attempts.append(
                factory.invalid_attempt(
                    parent,
                    None,
                    attempt_id=worker_record.attempt_id,
                    attempted_at=worker_result.attempted_at,
                    reason=reason,
                )
            )
            continue
        output = worker_record.model_output
        if output is None:
            raise ValueError("proposed worker record has no typed output")
        mutation: MutationManifest | None = None
        try:
            mutation = _manifest_from_typed_proposal(
                round_id=worker_result.round_id,
                slot=worker_record.request_binding.budget_slot,
                parent=parent,
                proposal=output.proposal,
                policy_adapter=policy_adapter,
                improver_adapter=improver_adapter,
            )
            candidate = _build_candidate(
                factory=factory,
                parent=parent,
                mutation=mutation,
                policy_adapter=policy_adapter,
                improver_adapter=improver_adapter,
                candidate_id=_candidate_id(
                    worker_result.round_id,
                    worker_record.request_binding.budget_slot,
                ),
                created_at=worker_result.attempted_at,
            )
        except MutationRejected as exc:
            record = _host_record(
                worker_record,
                worker_result=worker_result,
                status=MutationProposalStatus.INVALID,
                mutation_sha256=(
                    mutation.mutation_sha256 if mutation is not None else None
                ),
                invalid_reason=exc.reason,
            )
            records.append(record)
            invalid_attempts.append(
                factory.invalid_attempt(
                    parent,
                    mutation,
                    attempt_id=worker_record.attempt_id,
                    attempted_at=worker_result.attempted_at,
                    reason=exc.reason,
                )
            )
            continue
        record = _host_record(
            worker_record,
            worker_result=worker_result,
            status=MutationProposalStatus.CANDIDATE_BUILT,
            mutation_sha256=mutation.mutation_sha256,
            candidate_sha256=candidate.candidate_sha256,
        )
        records.append(record)
        successful.append(
            BundleReadyMutation(
                failure_packet_sha256=worker_result.failure_packet_sha256,
                runtime_authorization_sha256=(
                    worker_result.runtime_authorization_sha256
                ),
                runtime_identity_sha256=worker_result.runtime_identity_sha256,
                source_manifest_sha256=worker_result.source_manifest_sha256,
                record=record,
                mutation=mutation,
                candidate=candidate,
                policy_bytes=policy_adapter.canonical_payload(candidate.policy),
                improver_bytes=improver_adapter.canonical_payload(
                    candidate.improver
                ),
            )
        )
    return MutationRoundResult.create(
        round_id=worker_result.round_id,
        runtime_authorization_sha256=(
            worker_result.runtime_authorization_sha256
        ),
        runtime_identity_sha256=worker_result.runtime_identity_sha256,
        source_manifest_sha256=worker_result.source_manifest_sha256,
        parent_candidate_sha256=worker_result.parent_candidate_sha256,
        failure_packet_sha256=worker_result.failure_packet_sha256,
        attempted_at=worker_result.attempted_at,
        budget=worker_result.budget,
        worker_result=worker_result,
        proposals=tuple(records),
        successful=tuple(successful),
        invalid_attempts=tuple(invalid_attempts),
        complete=worker_result.complete,
    )


def _host_record(
    worker: MutationWorkerProposalRecord,
    *,
    worker_result: MutationWorkerResult,
    status: MutationProposalStatus,
    mutation_sha256: str | None = None,
    candidate_sha256: str | None = None,
    invalid_reason: InvalidAttemptReason | None = None,
    model_failure_code: ModelClientFailureCode | None = None,
) -> MutationProposalRecord:
    return MutationProposalRecord.create(
        round_id=worker.round_id,
        runtime_authorization_sha256=(
            worker_result.runtime_authorization_sha256
        ),
        runtime_identity_sha256=worker_result.runtime_identity_sha256,
        source_manifest_sha256=worker_result.source_manifest_sha256,
        attempt_id=worker.attempt_id,
        request_binding=worker.request_binding,
        request_sha256=worker.request_sha256,
        response_sha256=worker.response_sha256,
        response_bytes=worker.response_bytes,
        model_output=worker.model_output,
        status=status,
        mutation_sha256=mutation_sha256,
        candidate_sha256=candidate_sha256,
        invalid_reason=invalid_reason,
        model_failure_code=model_failure_code,
        worker_record_sha256=worker.record_sha256,
    )


def _candidate_id(round_id: UUID, slot: int) -> str:
    return f"candidate_{round_id.hex[:20]}_{slot}"


def _build_candidate(
    *,
    factory: CandidateFactory,
    parent: CandidateManifest,
    mutation: MutationManifest,
    policy_adapter: A2PolicyArtifactAdapter,
    improver_adapter: CanonicalImproverArtifactAdapter,
    candidate_id: str,
    created_at: datetime,
) -> CandidateManifest:
    if mutation.target is MutationTarget.COORDINATION_POLICY:
        return factory.build_policy_candidate(
            parent,
            mutation,
            policy_adapter,
            candidate_id=candidate_id,
            created_at=created_at,
        )
    return factory.build_improver_candidate(
        parent,
        mutation,
        improver_adapter,
        candidate_id=candidate_id,
        created_at=created_at,
    )


def _manifest_from_typed_proposal(
    *,
    round_id: UUID,
    slot: int,
    parent: CandidateManifest,
    proposal: PolicyMutationProposal | ImproverMutationProposal,
    policy_adapter: A2PolicyArtifactAdapter,
    improver_adapter: CanonicalImproverArtifactAdapter,
) -> MutationManifest:
    if isinstance(proposal, PolicyMutationProposal):
        operations = _policy_operations(proposal, policy_adapter, parent)
    else:
        operations = _improver_operations(proposal, improver_adapter, parent)
    return MutationManifest.create(
        mutation_id=uuid5(round_id, f"mutation:{slot}"),
        parent_policy=parent.policy,
        target=proposal.target,
        generated_by=parent.improver,
        hypothesis_code=proposal.hypothesis_code.value,
        operations=operations,
    )


def _policy_operations(
    proposal: PolicyMutationProposal,
    adapter: A2PolicyArtifactAdapter,
    parent: CandidateManifest,
) -> tuple[MutationOperation, ...]:
    snapshot = adapter.runner_snapshot(parent.policy)
    operations: list[MutationOperation] = []
    for edit in proposal.edits:
        if isinstance(edit, StrategyPrinciplesEdit):
            if edit.values == snapshot.strategy.principles:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/strategy/principles"
            value: object = [item.value for item in edit.values]
        elif isinstance(edit, HumanReviewConditionsEdit):
            if edit.values == snapshot.strategy.human_review_conditions:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/strategy/human_review_conditions"
            value = [item.value for item in edit.values]
        elif isinstance(edit, MaxTotalCallsEdit):
            if edit.value == snapshot.tool_budget.max_total_calls:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/tool_budget/max_total_calls"
            value = edit.value
        elif isinstance(edit, MaxMutatingCallsEdit):
            if edit.value == snapshot.tool_budget.max_mutating_calls:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/tool_budget/max_mutating_calls"
            value = edit.value
        elif isinstance(edit, ToolMaxCallsEdit):
            tool_index = next(
                (
                    index
                    for index, tool in enumerate(snapshot.tool_budget.tools)
                    if tool.name == edit.tool.value
                ),
                None,
            )
            if tool_index is None:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            if edit.value == snapshot.tool_budget.tools[tool_index].max_calls:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = f"/tool_budget/tools/{tool_index}/max_calls"
            value = edit.value
        else:  # pragma: no cover - closed discriminated union guard
            raise TypeError("unknown typed policy edit")
        operations.append(
            MutationOperation(
                op=MutationOperationKind.REPLACE,
                path=path,
                value=value,
            )
        )
    return tuple(operations)


def _improver_operations(
    proposal: ImproverMutationProposal,
    adapter: CanonicalImproverArtifactAdapter,
    parent: CandidateManifest,
) -> tuple[MutationOperation, ...]:
    artifact = adapter.artifact(parent.improver)
    operations: list[MutationOperation] = []
    for edit in proposal.edits:
        if isinstance(edit, FailureAnalysisCodesEdit):
            if edit.values == artifact.failure_analysis_codes:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/failure_analysis_codes"
        elif isinstance(edit, MutationPromptCodesEdit):
            if edit.values == artifact.mutation_prompt_codes:
                raise MutationRejected(InvalidAttemptReason.ADAPTER_REJECTED)
            path = "/mutation_prompt_codes"
        else:  # pragma: no cover - closed discriminated union guard
            raise TypeError("unknown typed improver edit")
        operations.append(
            MutationOperation(
                op=MutationOperationKind.REPLACE,
                path=path,
                value=[item.value for item in edit.values],
            )
        )
    return tuple(operations)
