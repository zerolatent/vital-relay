"""Bounded inherited-improver experiments over verified evolution artifacts.

This module deliberately orchestrates the existing trusted mutation boundary.  It
does not provide another mutation implementation, an in-process model path, or a
way for a caller to name a winner or claim status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Protocol, Self
from uuid import UUID, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    model_validator,
)

from vital_relay.agent.contracts import SandboxKind
from vital_relay.evolution.ace.contracts import ModelIdentity
from vital_relay.evolution.ace.model_client import LocalModelConfig
from vital_relay.evolution.bundle_store import CandidateBundleStore
from vital_relay.evolution.bundles import (
    CandidateArtifactAttestationAuthority,
    CandidateBundle,
    CandidateBundleArtifactRole,
    CandidateBundleAttestations,
    CandidateMaterialSourcePartition,
)
from vital_relay.evolution.contracts import (
    CandidateManifest,
    EvaluationReport,
    FailurePacket,
    IDENTIFIER_PATTERN,
    InvalidAttemptReason,
    MutationTarget,
    PartitionName,
    SHA256_PATTERN,
)
from vital_relay.evolution.evaluator import (
    HostIntegrityAuthority,
    build_failure_packet,
)
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.evolution.mutation import (
    AuthorizedMutationRoundRunner,
    BundleReadyMutation,
    MutationProposalStatus,
    MutationRoundBudget,
    MutationRoundResult,
    MutationRuntimeAuthorization,
    MutationRuntimeAuthorizationAuthority,
    MutationSandboxCleanupEvidence,
    ProcessMutationSandboxTransport,
)


DGM_SCHEMA_VERSION = 1
DGM_CANDIDATE_BUDGET = 4
_COUNTERFACTUAL_NAMESPACE = UUID("736329ab-1d34-45d4-85cf-0f5f45f3d2a8")


class DGMIntegrityError(RuntimeError):
    """Experiment evidence failed a closed identity or equality check."""


class DGMClaimStatus(StrEnum):
    NO_INHERITANCE_EVIDENCE = "no_inheritance_evidence"
    MECHANISM_DEMONSTRATED = "mechanism_demonstrated"
    COUNTERFACTUAL_INCONCLUSIVE = "counterfactual_inconclusive"
    RECURSIVE_IMPROVEMENT_DEMONSTRATED = (
        "recursive_improvement_demonstrated"
    )


class DGMArmName(StrEnum):
    I0 = "i0"
    I1 = "i1"


class DGMMetric(StrEnum):
    QUALITY_SCORE = "quality_score"
    WORKFLOW_COMPLETION_RATE = "workflow_completion_rate"
    RESPONDER_SKILL_MATCH_RATE = "responder_skill_match_rate"
    MISSED_REQUIRED_ACTIONS_PER_CASE = "missed_required_actions_per_case"
    DUPLICATE_IRREVERSIBLE_ACTIONS_PER_CASE = (
        "duplicate_irreversible_actions_per_case"
    )
    UNNECESSARY_ACTIONS_PER_CASE = "unnecessary_actions_per_case"
    TOOL_ERRORS_PER_CASE = "tool_errors_per_case"
    NOTIFICATIONS_PER_CASE = "notifications_per_case"


class DGMDirection(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


class DGMDescendantStatus(StrEnum):
    EVALUATED = "evaluated"
    GENERATED_EVALUATION_FAILED = "generated_evaluation_failed"
    INVALID = "invalid"
    REQUEST_FAILED = "request_failed"


class DGMLineageKind(StrEnum):
    INHERITED = "inherited"
    COUNTERFACTUAL = "counterfactual"


class _DGMModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self)


class DGMEvaluationLimits(_DGMModel):
    max_cases: int = Field(ge=1, le=128)
    max_input_bytes_per_case: int = Field(ge=1_024, le=2_000_000)
    max_output_bytes_per_case: int = Field(ge=1_024, le=2_000_000)
    timeout_seconds_per_case: float = Field(ge=1, le=300)


class DGMDevelopmentRunnerIdentity(_DGMModel):
    """Preregistered identity of the runner used for both arm evaluations."""

    sandbox: SandboxKind
    runner_sha256: str = Field(pattern=SHA256_PATTERN)
    model_identity: ModelIdentity
    runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    playbook_bytes: bytes = Field(min_length=1, max_length=2_000_000)
    case_sha256s: tuple[str, ...] = Field(min_length=1, max_length=128)
    scenario_seeds: tuple[int, ...] = Field(min_length=1, max_length=128)
    limits: DGMEvaluationLimits
    identity_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_identity(self, info: ValidationInfo) -> Self:
        if self.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("DGM evaluations require a process sandbox")
        if (
            len(self.case_sha256s) != len(self.scenario_seeds)
            or len(self.case_sha256s) > self.limits.max_cases
            or len(set(self.case_sha256s)) != len(self.case_sha256s)
            or any(seed < 0 or seed > 2_147_483_647 for seed in self.scenario_seeds)
            or sha256(self.playbook_bytes).hexdigest() != self.playbook_sha256
        ):
            raise ValueError("DGM evaluation cases and seeds are not exact")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"identity_sha256"})
            )
            if self.identity_sha256 != expected:
                raise ValueError("development runner identity hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMDevelopmentRunnerIdentity:
        material = cls.model_validate(
            {**values, "identity_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "identity_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"identity_sha256"}
                    )
                ),
            }
        )


class DGMMetricPlan(_DGMModel):
    metric: DGMMetric
    direction: DGMDirection
    minimum_effect: float = Field(gt=0, le=1_000_000)

    @model_validator(mode="after")
    def validate_direction(self) -> Self:
        naturally_higher = {
            DGMMetric.QUALITY_SCORE,
            DGMMetric.WORKFLOW_COMPLETION_RATE,
            DGMMetric.RESPONDER_SKILL_MATCH_RATE,
        }
        expected = (
            DGMDirection.HIGHER
            if self.metric in naturally_higher
            else DGMDirection.LOWER
        )
        if self.direction is not expected:
            raise ValueError("metric comparison direction is not canonical")
        return self


class DGMExperimentPlan(_DGMModel):
    """Immutable preregistration for N→N+1 and the equal-budget N+2 arms."""

    schema_version: Literal[DGM_SCHEMA_VERSION] = DGM_SCHEMA_VERSION
    experiment_id: UUID
    root_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    root_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    root_development_report_sha256: str = Field(pattern=SHA256_PATTERN)
    root_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_model_identity: ModelIdentity
    mutation_runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_budget: MutationRoundBudget
    development_runner: DGMDevelopmentRunnerIdentity
    comparisons: tuple[DGMMetricPlan, ...] = Field(min_length=1, max_length=8)
    require_protected_validation: bool = True
    plan_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_plan(self, info: ValidationInfo) -> Self:
        if (
            self.mutation_budget.candidate_budget != DGM_CANDIDATE_BUDGET
            or len(self.mutation_budget.seeds) != DGM_CANDIDATE_BUDGET
            or len({item.metric for item in self.comparisons})
            != len(self.comparisons)
        ):
            raise ValueError("DGM plan does not preregister one bounded comparison")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"plan_sha256"})
            )
            if self.plan_sha256 != expected:
                raise ValueError("DGM plan hash does not match canonical content")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMExperimentPlan:
        material = cls.model_validate(
            {**values, "plan_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "plan_sha256": canonical_sha256(
                    material.model_dump(mode="json", exclude={"plan_sha256"})
                ),
            }
        )


class DGMEqualBudgetContract(_DGMModel):
    """The exact material that must be shared by I0 and I1."""

    operational_policy_sha256: str = Field(pattern=SHA256_PATTERN)
    playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    failure_observations_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_model_identity: ModelIdentity
    mutation_runtime_authorization_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_runtime_identity_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_source_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_budget: MutationRoundBudget
    development_runner: DGMDevelopmentRunnerIdentity
    contract_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_contract(self, info: ValidationInfo) -> Self:
        if self.playbook_sha256 != self.development_runner.playbook_sha256:
            raise ValueError("equal-budget playbook identity drifted")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"contract_sha256"})
            )
            if self.contract_sha256 != expected:
                raise ValueError("equal-budget contract hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMEqualBudgetContract:
        material = cls.model_validate(
            {**values, "contract_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "contract_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"contract_sha256"}
                    )
                ),
            }
        )


class DGMArmPlan(_DGMModel):
    experiment_id: UUID
    arm: DGMArmName
    root_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    root_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    improver_sha256: str = Field(pattern=SHA256_PATTERN)
    development_report: EvaluationReport
    failure_packet: FailurePacket
    equal_budget: DGMEqualBudgetContract
    arm_plan_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_arm(self, info: ValidationInfo) -> Self:
        if (
            self.development_report.partition is not PartitionName.DEVELOPMENT
            or self.development_report.candidate_sha256
            != self.root_candidate_sha256
            or self.failure_packet.candidate_sha256
            != self.root_candidate_sha256
            or self.failure_packet.source_report_sha256
            != self.development_report.report_sha256
            or self.failure_packet != build_failure_packet(self.development_report)
            or _failure_observations_sha256(self.development_report)
            != self.equal_budget.failure_observations_sha256
            or self.development_report.case_sha256s
            != self.equal_budget.development_runner.case_sha256s
        ):
            raise ValueError("DGM arm does not bind the equal development evidence")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"arm_plan_sha256"})
            )
            if self.arm_plan_sha256 != expected:
                raise ValueError("DGM arm plan hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMArmPlan:
        material = cls.model_validate(
            {**values, "arm_plan_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "arm_plan_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"arm_plan_sha256"}
                    )
                ),
            }
        )


class DGMCleanupEvidence(_DGMModel):
    sandbox: SandboxKind
    disposition: Literal["not_started", "not_applicable", "absence_observed"]
    attempt_id: UUID | None = None
    container_name: str | None = Field(default=None, max_length=128)
    attempt_count: int = Field(default=0, ge=0)
    removal_attempted: bool = False
    removal_returncode: int | None = None
    absence_observed: bool
    unresolved: bool
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_cleanup(self, info: ValidationInfo) -> Self:
        if self.unresolved or not self.absence_observed:
            raise ValueError("DGM process cleanup must fail closed")
        if self.sandbox is SandboxKind.DOCKER:
            if self.disposition == "not_applicable":
                raise ValueError("Docker cleanup cannot be not-applicable")
            if self.disposition == "absence_observed" and (
                self.attempt_id is None or self.container_name is None
            ):
                raise ValueError("Docker cleanup evidence is incomplete")
        elif self.sandbox is SandboxKind.NEMOCLAW:
            if self.disposition != "not_applicable" or any(
                value is not None for value in (self.attempt_id, self.container_name)
            ):
                raise ValueError("NemoClaw cleanup evidence shape is invalid")
        else:
            raise ValueError("DGM cleanup cannot claim an in-process sandbox")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"evidence_sha256"})
            )
            if self.evidence_sha256 != expected:
                raise ValueError("cleanup evidence hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMCleanupEvidence:
        material = cls.model_validate(
            {**values, "evidence_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "evidence_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"evidence_sha256"}
                    )
                ),
            }
        )


class DGMLineageRecord(_DGMModel):
    experiment_id: UUID
    kind: DGMLineageKind
    arm: DGMArmName | None = None
    parent_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    child_bundle_sha256: str = Field(pattern=SHA256_PATTERN)
    parent_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    child_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    generation: int = Field(ge=1, le=10_000)
    mutation_sha256: str = Field(pattern=SHA256_PATTERN)
    generated_by_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    loaded_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    improver_receipt_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    lineage_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_lineage(self, info: ValidationInfo) -> Self:
        if self.generated_by_improver_sha256 != self.loaded_improver_sha256:
            raise ValueError("lineage generator must equal the loaded improver")
        if (self.kind is DGMLineageKind.COUNTERFACTUAL) != (
            self.arm is DGMArmName.I0
        ):
            raise ValueError("counterfactual lineage must remain a separate I0 root")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"lineage_sha256"})
            )
            if self.lineage_sha256 != expected:
                raise ValueError("DGM lineage hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMLineageRecord:
        material = cls.model_validate(
            {**values, "lineage_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "lineage_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"lineage_sha256"}
                    )
                ),
            }
        )


class DGMDescendant(_DGMModel):
    experiment_id: UUID
    arm: DGMArmName
    budget_slot: int = Field(ge=0, lt=DGM_CANDIDATE_BUDGET)
    proposal_seed: int = Field(ge=0, le=2_147_483_647)
    proposal_record_sha256: str = Field(pattern=SHA256_PATTERN)
    status: DGMDescendantStatus
    invalid_reason: InvalidAttemptReason | None = None
    model_failure_code: str | None = Field(default=None, max_length=100)
    loaded_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    bundle: CandidateBundle | None = None
    bundle_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    lineage: DGMLineageRecord | None = None
    development_report: EvaluationReport | None = None
    development_score: float | None = None
    hard_gate_failures: int | None = Field(default=None, ge=0)
    archived: bool
    descendant_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_descendant(self, info: ValidationInfo) -> Self:
        generated = self.status in {
            DGMDescendantStatus.EVALUATED,
            DGMDescendantStatus.GENERATED_EVALUATION_FAILED,
        }
        if generated:
            if (
                self.bundle is None
                or self.bundle_sha256 != self.bundle.manifest.bundle_sha256
                or self.lineage is None
                or self.lineage.child_bundle_sha256 != self.bundle_sha256
                or self.lineage.loaded_improver_sha256
                != self.loaded_improver_sha256
                or not self.archived
            ):
                raise ValueError("generated descendant is not verified and archived")
            mutation = self.bundle.materials.mutation
            if (
                mutation is None
                or mutation.generated_by.sha256 != self.loaded_improver_sha256
                or mutation.mutation_sha256 != self.lineage.mutation_sha256
            ):
                raise ValueError("descendant generated_by does not equal loaded bytes")
        elif any(
            value is not None
            for value in (
                self.bundle,
                self.bundle_sha256,
                self.lineage,
                self.development_report,
                self.development_score,
                self.hard_gate_failures,
            )
        ) or self.archived:
            raise ValueError("failed budget slots cannot claim descendant material")

        if self.status is DGMDescendantStatus.EVALUATED:
            if (
                self.development_report is None
                or self.bundle is None
                or self.development_report.partition
                is not PartitionName.DEVELOPMENT
                or self.development_report.candidate_sha256
                != self.bundle.materials.candidate.candidate_sha256
                or self.development_score
                != development_quality_score(self.development_report)
                or self.hard_gate_failures
                != _hard_gate_failures(self.development_report)
            ):
                raise ValueError("evaluated descendant report binding is invalid")
        elif self.status is DGMDescendantStatus.GENERATED_EVALUATION_FAILED:
            if any(
                value is not None
                for value in (
                    self.development_report,
                    self.development_score,
                    self.hard_gate_failures,
                )
            ):
                raise ValueError("failed evaluation cannot carry a report")
        elif self.status is DGMDescendantStatus.INVALID:
            if self.invalid_reason is None or self.model_failure_code is not None:
                raise ValueError("invalid slot reason is incomplete")
        elif self.status is DGMDescendantStatus.REQUEST_FAILED:
            if self.model_failure_code is None or self.invalid_reason is not None:
                raise ValueError("request-failed slot reason is incomplete")

        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"descendant_sha256"})
            )
            if self.descendant_sha256 != expected:
                raise ValueError("descendant hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMDescendant:
        material = cls.model_validate(
            {**values, "descendant_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "descendant_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"descendant_sha256"}
                    )
                ),
            }
        )


class DGMArmResult(_DGMModel):
    arm_plan: DGMArmPlan
    mutation_round: MutationRoundResult
    cleanup: DGMCleanupEvidence
    descendants: tuple[DGMDescendant, ...] = Field(
        min_length=DGM_CANDIDATE_BUDGET,
        max_length=DGM_CANDIDATE_BUDGET,
    )
    selected_descendant_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    arm_result_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_result(self, info: ValidationInfo) -> Self:
        expected_slots = tuple(range(DGM_CANDIDATE_BUDGET))
        if (
            tuple(item.budget_slot for item in self.descendants) != expected_slots
            or tuple(item.proposal_seed for item in self.descendants)
            != self.arm_plan.equal_budget.mutation_budget.seeds
            or tuple(item.proposal_record_sha256 for item in self.descendants)
            != tuple(item.record_sha256 for item in self.mutation_round.proposals)
            or any(
                item.arm is not self.arm_plan.arm
                or item.experiment_id != self.arm_plan.experiment_id
                or item.loaded_improver_sha256 != self.arm_plan.improver_sha256
                for item in self.descendants
            )
            or self.mutation_round.parent_candidate_sha256
            != self.arm_plan.root_candidate_sha256
            or self.mutation_round.budget
            != self.arm_plan.equal_budget.mutation_budget
            or self.mutation_round.runtime_authorization_sha256
            != self.arm_plan.equal_budget.mutation_runtime_authorization_sha256
            or self.mutation_round.runtime_identity_sha256
            != self.arm_plan.equal_budget.mutation_runtime_identity_sha256
            or self.mutation_round.source_manifest_sha256
            != self.arm_plan.equal_budget.mutation_source_manifest_sha256
            or self.mutation_round.worker_result.source_report_sha256
            != self.arm_plan.development_report.report_sha256
            or self.mutation_round.failure_packet_sha256
            != canonical_sha256(self.arm_plan.failure_packet)
            or any(
                proposal.request_binding.model_identity
                != self.arm_plan.equal_budget.mutation_model_identity
                for proposal in self.mutation_round.proposals
            )
        ):
            raise ValueError("arm result does not consume its exact four-slot budget")
        successful_by_record = {
            item.record.record_sha256: item
            for item in self.mutation_round.successful
        }
        for descendant, proposal in zip(
            self.descendants,
            self.mutation_round.proposals,
            strict=True,
        ):
            material = successful_by_record.get(proposal.record_sha256)
            if material is not None:
                bundle = descendant.bundle
                lineage = descendant.lineage
                if (
                    descendant.status
                    not in {
                        DGMDescendantStatus.EVALUATED,
                        DGMDescendantStatus.GENERATED_EVALUATION_FAILED,
                    }
                    or bundle is None
                    or lineage is None
                    or bundle.materials.candidate != material.candidate
                    or bundle.materials.policy_bytes != material.policy_bytes
                    or bundle.materials.improver_bytes != material.improver_bytes
                    or bundle.materials.mutation != material.mutation
                    or bundle.manifest.parent_bundle_sha256
                    != self.arm_plan.root_bundle_sha256
                    or lineage.parent_candidate_sha256
                    != self.arm_plan.root_candidate_sha256
                    or lineage.experiment_id != self.arm_plan.experiment_id
                    or lineage.arm is not self.arm_plan.arm
                    or lineage.kind
                    is not (
                        DGMLineageKind.COUNTERFACTUAL
                        if self.arm_plan.arm is DGMArmName.I0
                        else DGMLineageKind.INHERITED
                    )
                    or lineage.parent_bundle_sha256
                    != self.arm_plan.root_bundle_sha256
                    or lineage.child_candidate_sha256
                    != material.candidate.candidate_sha256
                    or lineage.child_bundle_sha256
                    != bundle.manifest.bundle_sha256
                    or lineage.generation != material.candidate.generation
                    or lineage.mutation_sha256
                    != material.mutation.mutation_sha256
                    or lineage.generated_by_improver_sha256
                    != material.mutation.generated_by.sha256
                    or lineage.loaded_improver_sha256
                    != self.arm_plan.improver_sha256
                    or lineage.improver_receipt_sha256
                    != _bundle_receipt_sha256(bundle)
                ):
                    raise ValueError(
                        "descendant was substituted for its mutation budget slot"
                    )
            elif proposal.status is MutationProposalStatus.INVALID:
                if (
                    descendant.status is not DGMDescendantStatus.INVALID
                    or descendant.invalid_reason is not proposal.invalid_reason
                ):
                    raise ValueError("invalid descendant slot was relabeled")
            elif (
                descendant.status is not DGMDescendantStatus.REQUEST_FAILED
                or proposal.model_failure_code is None
                or descendant.model_failure_code
                != proposal.model_failure_code.value
            ):
                raise ValueError("request-failed descendant slot was relabeled")
        selected = _select_best_descendant(self.descendants)
        expected_selected = selected.descendant_sha256 if selected else None
        if self.selected_descendant_sha256 != expected_selected:
            raise ValueError("DGM winner must be derived from development evidence")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"arm_result_sha256"})
            )
            if self.arm_result_sha256 != expected:
                raise ValueError("arm result hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMArmResult:
        descendants = tuple(values["descendants"])  # type: ignore[arg-type]
        selected = _select_best_descendant(descendants)
        values = {
            **values,
            "selected_descendant_sha256": (
                selected.descendant_sha256 if selected else None
            ),
        }
        material = cls.model_validate(
            {**values, "arm_result_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "arm_result_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"arm_result_sha256"}
                    )
                ),
            }
        )

    @property
    def selected(self) -> DGMDescendant | None:
        if self.selected_descendant_sha256 is None:
            return None
        return next(
            item
            for item in self.descendants
            if item.descendant_sha256 == self.selected_descendant_sha256
        )


class DGMComparison(_DGMModel):
    experiment_id: UUID
    metric_plan: DGMMetricPlan
    i0_descendant_sha256: str = Field(pattern=SHA256_PATTERN)
    i1_descendant_sha256: str = Field(pattern=SHA256_PATTERN)
    i0_report_sha256: str = Field(pattern=SHA256_PATTERN)
    i1_report_sha256: str = Field(pattern=SHA256_PATTERN)
    i0_value: float
    i1_value: float
    signed_effect: float
    threshold_met: bool
    i1_outperformed: bool
    comparison_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_comparison(self, info: ValidationInfo) -> Self:
        effect = (
            self.i1_value - self.i0_value
            if self.metric_plan.direction is DGMDirection.HIGHER
            else self.i0_value - self.i1_value
        )
        if (
            abs(self.signed_effect - effect) > 1e-12
            or self.i1_outperformed != (effect > 0)
            or self.threshold_met != (effect >= self.metric_plan.minimum_effect)
        ):
            raise ValueError("comparison outcome is not mechanically derived")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"comparison_sha256"})
            )
            if self.comparison_sha256 != expected:
                raise ValueError("comparison hash does not match")
        return self

    @classmethod
    def create(
        cls,
        *,
        experiment_id: UUID,
        metric_plan: DGMMetricPlan,
        i0: DGMDescendant,
        i1: DGMDescendant,
    ) -> DGMComparison:
        if i0.development_report is None or i1.development_report is None:
            raise ValueError("comparison requires two development reports")
        i0_value = _metric_value(metric_plan.metric, i0.development_report)
        i1_value = _metric_value(metric_plan.metric, i1.development_report)
        effect = (
            i1_value - i0_value
            if metric_plan.direction is DGMDirection.HIGHER
            else i0_value - i1_value
        )
        values: dict[str, object] = {
            "experiment_id": experiment_id,
            "metric_plan": metric_plan,
            "i0_descendant_sha256": i0.descendant_sha256,
            "i1_descendant_sha256": i1.descendant_sha256,
            "i0_report_sha256": i0.development_report.report_sha256,
            "i1_report_sha256": i1.development_report.report_sha256,
            "i0_value": i0_value,
            "i1_value": i1_value,
            "signed_effect": effect,
            "threshold_met": effect >= metric_plan.minimum_effect,
            "i1_outperformed": effect > 0,
        }
        material = cls.model_validate(
            {**values, "comparison_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "comparison_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"comparison_sha256"}
                    )
                ),
            }
        )


class DGMClaim(_DGMModel):
    n1_improver_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    i1_loaded_improver_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN
    )
    i1_verified_archived_n2_count: int = Field(ge=0, le=DGM_CANDIDATE_BUDGET)
    equal_budget_verified: bool
    model_visible_parent_invariance_verified: bool
    expected_comparison_count: int = Field(ge=1, le=8)
    completed_comparison_count: int = Field(ge=0, le=8)
    every_comparison_outperformed: bool
    every_effect_threshold_met: bool
    additional_hard_gate_failures: int = Field(ge=0)
    protected_validation_required: bool
    protected_validation_complete: bool
    independent_paired_trial_count: int = Field(ge=0, le=10_000)
    required_independent_paired_trials: Literal[2] = 2
    status: DGMClaimStatus
    claim_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_claim(self, info: ValidationInfo) -> Self:
        expected = _claim_status(
            n1_improver_sha256=self.n1_improver_sha256,
            i1_loaded_improver_sha256=self.i1_loaded_improver_sha256,
            i1_verified_archived_n2_count=self.i1_verified_archived_n2_count,
            equal_budget_verified=self.equal_budget_verified,
            model_visible_parent_invariance_verified=(
                self.model_visible_parent_invariance_verified
            ),
            expected_comparison_count=self.expected_comparison_count,
            completed_comparison_count=self.completed_comparison_count,
            every_comparison_outperformed=self.every_comparison_outperformed,
            every_effect_threshold_met=self.every_effect_threshold_met,
            additional_hard_gate_failures=self.additional_hard_gate_failures,
            protected_validation_required=self.protected_validation_required,
            protected_validation_complete=self.protected_validation_complete,
            independent_paired_trial_count=self.independent_paired_trial_count,
            required_independent_paired_trials=(
                self.required_independent_paired_trials
            ),
        )
        if self.status is not expected:
            raise ValueError("DGM claim status is not mechanically derived")
        if not (info.context and info.context.get("build_dgm_hash")):
            digest = canonical_sha256(
                self.model_dump(mode="json", exclude={"claim_sha256"})
            )
            if self.claim_sha256 != digest:
                raise ValueError("DGM claim hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMClaim:
        status = _claim_status(**values)  # type: ignore[arg-type]
        material_values = {**values, "status": status}
        material = cls.model_validate(
            {**material_values, "claim_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **material_values,
                "claim_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"claim_sha256"}
                    )
                ),
            }
        )


class DGMExperimentResult(_DGMModel):
    schema_version: Literal[DGM_SCHEMA_VERSION] = DGM_SCHEMA_VERSION
    plan: DGMExperimentPlan
    started_at: AwareDatetime
    completed_at: AwareDatetime
    root_bundle: CandidateBundle
    root_development_report: EvaluationReport
    n_to_n1_round: MutationRoundResult | None = None
    n_to_n1_cleanup: DGMCleanupEvidence | None = None
    n1_bundle: CandidateBundle | None = None
    n1_lineage: DGMLineageRecord | None = None
    counterfactual_root_bundle: CandidateBundle | None = None
    equal_budget_verified: bool
    i0_arm: DGMArmResult | None = None
    i1_arm: DGMArmResult | None = None
    comparisons: tuple[DGMComparison, ...] = ()
    i0_protected_report: EvaluationReport | None = None
    i1_protected_report: EvaluationReport | None = None
    claim: DGMClaim
    result_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_result(self, info: ValidationInfo) -> Self:
        object.__setattr__(self, "started_at", self.started_at.astimezone(UTC))
        object.__setattr__(self, "completed_at", self.completed_at.astimezone(UTC))
        if self.completed_at < self.started_at:
            raise ValueError("DGM completion precedes its start")
        if (
            self.root_bundle.manifest.bundle_sha256
            != self.plan.root_bundle_sha256
            or self.root_bundle.materials.candidate.candidate_sha256
            != self.plan.root_candidate_sha256
            or self.root_bundle.materials.candidate.improver.sha256
            != self.plan.root_improver_sha256
            or self.root_development_report.report_sha256
            != self.plan.root_development_report_sha256
            or self.root_development_report.candidate_sha256
            != self.plan.root_candidate_sha256
            or self.root_development_report.partition
            is not PartitionName.DEVELOPMENT
        ):
            raise ValueError("DGM result substituted its verified root")
        if self.n_to_n1_round is not None:
            initial_packet = build_failure_packet(self.root_development_report)
            if (
                self.n_to_n1_round.parent_candidate_sha256
                != self.plan.root_candidate_sha256
                or self.n_to_n1_round.failure_packet_sha256
                != canonical_sha256(initial_packet)
                or self.n_to_n1_round.worker_result.source_report_sha256
                != self.plan.root_development_report_sha256
                or self.n_to_n1_round.budget != self.plan.mutation_budget
                or self.n_to_n1_round.runtime_authorization_sha256
                != self.plan.mutation_runtime_authorization_sha256
                or self.n_to_n1_round.runtime_identity_sha256
                != self.plan.mutation_runtime_identity_sha256
                or self.n_to_n1_round.source_manifest_sha256
                != self.plan.mutation_source_manifest_sha256
                or any(
                    proposal.request_binding.model_identity
                    != self.plan.mutation_model_identity
                    for proposal in self.n_to_n1_round.proposals
                )
            ):
                raise ValueError("N→N+1 round does not bind the preregistered root")
        if (self.n_to_n1_round is None) != (self.n_to_n1_cleanup is None):
            raise ValueError("N→N+1 round and cleanup evidence must stay together")
        if (self.n1_bundle is None) != (self.n1_lineage is None):
            raise ValueError("N+1 bundle and lineage evidence must stay together")
        if self.n1_bundle is not None and self.n_to_n1_round is None:
            raise ValueError("N+1 requires its complete mutation round")
        if self.counterfactual_root_bundle is not None and self.n1_bundle is None:
            raise ValueError("counterfactual root requires a verified N+1")
        expected_n1 = (
            _first_improver(self.n_to_n1_round)
            if self.n_to_n1_round is not None
            else None
        )
        if (self.n1_bundle is None) != (expected_n1 is None):
            raise ValueError("N+1 must be the first valid improver budget slot")
        if self.n1_bundle is not None and self.n1_lineage is not None:
            assert expected_n1 is not None
            if (
                self.n1_bundle.manifest.parent_bundle_sha256
                != self.root_bundle.manifest.bundle_sha256
                or self.n1_lineage.parent_bundle_sha256
                != self.root_bundle.manifest.bundle_sha256
                or self.n1_lineage.child_bundle_sha256
                != self.n1_bundle.manifest.bundle_sha256
                or self.n1_bundle.materials.mutation is None
                or self.n1_bundle.materials.mutation.target
                is not MutationTarget.IMPROVER
                or self.n1_bundle.materials.candidate != expected_n1.candidate
                or self.n1_bundle.materials.policy_bytes
                != expected_n1.policy_bytes
                or self.n1_bundle.materials.improver_bytes
                != expected_n1.improver_bytes
                or self.n1_bundle.materials.mutation != expected_n1.mutation
                or self.n1_lineage.experiment_id != self.plan.experiment_id
                or self.n1_lineage.kind is not DGMLineageKind.INHERITED
                or self.n1_lineage.arm is not None
                or self.n1_lineage.parent_candidate_sha256
                != self.root_bundle.materials.candidate.candidate_sha256
                or self.n1_lineage.child_candidate_sha256
                != self.n1_bundle.materials.candidate.candidate_sha256
                or self.n1_lineage.generation
                != self.n1_bundle.materials.candidate.generation
                or self.n1_lineage.mutation_sha256
                != expected_n1.mutation.mutation_sha256
                or self.n1_lineage.generated_by_improver_sha256
                != self.root_bundle.materials.candidate.improver.sha256
                or self.n1_lineage.loaded_improver_sha256
                != self.root_bundle.materials.candidate.improver.sha256
                or self.n1_lineage.improver_receipt_sha256
                != _bundle_receipt_sha256(self.n1_bundle)
            ):
                raise ValueError("N+1 is not the selected inherited-improver child")
        if self.counterfactual_root_bundle is not None and self.n1_bundle is not None:
            counterfactual = self.counterfactual_root_bundle
            expected_counterfactual_id = (
                "dgm_cf_"
                + uuid5(
                    _COUNTERFACTUAL_NAMESPACE,
                    (
                        f"{self.plan.experiment_id}:"
                        f"{self.n1_bundle.materials.candidate.candidate_sha256}"
                    ),
                ).hex
            )
            if (
                counterfactual.manifest.parent_bundle_sha256 is not None
                or counterfactual.materials.candidate.generation != 0
                or counterfactual.materials.candidate.candidate_id
                != expected_counterfactual_id
                or counterfactual.materials.candidate.created_at != self.started_at
                or counterfactual.manifest.bundle_sha256
                in {
                    self.root_bundle.manifest.bundle_sha256,
                    self.n1_bundle.manifest.bundle_sha256,
                }
                or counterfactual.materials.candidate.policy
                != self.n1_bundle.materials.candidate.policy
                or counterfactual.materials.policy_bytes
                != self.n1_bundle.materials.policy_bytes
                or counterfactual.materials.candidate.improver
                != self.root_bundle.materials.candidate.improver
                or counterfactual.materials.improver_bytes
                != self.root_bundle.materials.improver_bytes
            ):
                raise ValueError(
                    "counterfactual root does not use exact N+1 policy and N improver"
                )
        if (self.i0_arm is None) != (self.i1_arm is None):
            raise ValueError("DGM counterfactual arms must complete together")
        if self.i0_arm is not None and self.i1_arm is not None:
            if (
                self.i0_arm.arm_plan.arm is not DGMArmName.I0
                or self.i1_arm.arm_plan.arm is not DGMArmName.I1
                or self.i0_arm.arm_plan.experiment_id != self.plan.experiment_id
                or self.i1_arm.arm_plan.experiment_id != self.plan.experiment_id
                or self.i0_arm.arm_plan.equal_budget
                != self.i1_arm.arm_plan.equal_budget
                or not self.equal_budget_verified
                or self.counterfactual_root_bundle is None
                or self.i0_arm.arm_plan.root_bundle_sha256
                != self.counterfactual_root_bundle.manifest.bundle_sha256
                or self.n1_bundle is None
                or self.i1_arm.arm_plan.root_bundle_sha256
                != self.n1_bundle.manifest.bundle_sha256
                or self.counterfactual_root_bundle.manifest.parent_bundle_sha256
                is not None
                or self.i0_arm.arm_plan.equal_budget.playbook_sha256
                != self.plan.development_runner.playbook_sha256
                or self.i0_arm.arm_plan.equal_budget.development_runner.playbook_bytes
                != self.plan.development_runner.playbook_bytes
                or self.i0_arm.arm_plan.improver_sha256
                != self.counterfactual_root_bundle.materials.candidate.improver.sha256
                or self.i1_arm.arm_plan.improver_sha256
                != self.n1_bundle.materials.candidate.improver.sha256
                or self.i0_arm.arm_plan.equal_budget.operational_policy_sha256
                != self.counterfactual_root_bundle.materials.candidate.policy.sha256
                or self.i1_arm.arm_plan.equal_budget.operational_policy_sha256
                != self.n1_bundle.materials.candidate.policy.sha256
            ):
                raise ValueError("I0 is not a separate equal-budget root")
        selected_i0 = self.i0_arm.selected if self.i0_arm is not None else None
        selected_i1 = self.i1_arm.selected if self.i1_arm is not None else None
        expected_comparisons = (
            tuple(
                DGMComparison.create(
                    experiment_id=self.plan.experiment_id,
                    metric_plan=metric_plan,
                    i0=selected_i0,
                    i1=selected_i1,
                )
                for metric_plan in self.plan.comparisons
            )
            if selected_i0 is not None and selected_i1 is not None
            else ()
        )
        if self.comparisons != expected_comparisons:
            raise ValueError(
                "DGM comparisons were not derived from the selected descendants"
            )
        for report, selected, label in (
            (self.i0_protected_report, selected_i0, "I0"),
            (self.i1_protected_report, selected_i1, "I1"),
        ):
            if report is None:
                continue
            if (
                selected is None
                or selected.bundle is None
                or report.partition is not PartitionName.PROTECTED_VALIDATION
                or report.candidate_sha256
                != selected.bundle.materials.candidate.candidate_sha256
            ):
                raise ValueError(
                    f"{label} protected report does not bind its preselected descendant"
                )
        expected_claim = _derive_claim(
            self.plan,
            self.n1_bundle,
            self.equal_budget_verified,
            self.i0_arm,
            self.i1_arm,
            self.comparisons,
            self.i0_protected_report,
            self.i1_protected_report,
        )
        if self.claim != expected_claim:
            raise ValueError("DGM result claim was caller-selected")
        if not (info.context and info.context.get("build_dgm_hash")):
            expected = canonical_sha256(
                self.model_dump(mode="json", exclude={"result_sha256"})
            )
            if self.result_sha256 != expected:
                raise ValueError("DGM result hash does not match")
        return self

    @classmethod
    def create(cls, **values: object) -> DGMExperimentResult:
        claim = _derive_claim(
            values["plan"],  # type: ignore[arg-type]
            values.get("n1_bundle"),  # type: ignore[arg-type]
            bool(values["equal_budget_verified"]),
            values.get("i0_arm"),  # type: ignore[arg-type]
            values.get("i1_arm"),  # type: ignore[arg-type]
            tuple(values.get("comparisons", ())),  # type: ignore[arg-type]
            values.get("i0_protected_report"),  # type: ignore[arg-type]
            values.get("i1_protected_report"),  # type: ignore[arg-type]
        )
        material_values = {**values, "claim": claim}
        material = cls.model_validate(
            {**material_values, "result_sha256": "0" * 64},
            context={"build_dgm_hash": True},
        )
        return cls.model_validate(
            {
                **material_values,
                "result_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json", exclude={"result_sha256"}
                    )
                ),
            }
        )


class DGMReportProvider(Protocol):
    """Trusted host port for newly generated candidate evaluations."""

    @property
    def identity(self) -> DGMDevelopmentRunnerIdentity: ...

    @property
    def process_isolated(self) -> bool: ...

    def evaluate(
        self,
        bundle: CandidateBundle,
        *,
        partition: PartitionName,
    ) -> EvaluationReport: ...


class DGMArtifactStore(Protocol):
    def put_plan(self, plan: DGMExperimentPlan) -> str: ...

    def put_round(self, experiment_id: UUID, result: MutationRoundResult) -> str: ...

    def put_bundle(self, experiment_id: UUID, bundle: CandidateBundle) -> str: ...

    def put_lineage(self, record: DGMLineageRecord) -> str: ...

    def put_evaluation(self, experiment_id: UUID, report: EvaluationReport) -> str: ...

    def put_comparison(self, comparison: DGMComparison) -> str: ...

    def publish(self, result: DGMExperimentResult) -> str: ...


@dataclass(frozen=True, slots=True)
class DGMRunnerFactoryIdentity:
    model_identity: ModelIdentity
    runtime_authorization: MutationRuntimeAuthorization
    budget: MutationRoundBudget
    runner_sha256: str


@dataclass(frozen=True, slots=True)
class _ActiveMutationRound:
    runner: AuthorizedMutationRoundRunner
    transport: ProcessMutationSandboxTransport


class DGMAuthorizedMutationRunnerFactory:
    """Create one fresh reviewed process transport for every mutation round."""

    def __init__(
        self,
        *,
        integrity_authority: HostIntegrityAuthority,
        artifact_authority: CandidateArtifactAttestationAuthority,
        runtime_authority: MutationRuntimeAuthorizationAuthority,
        runtime_authorization: MutationRuntimeAuthorization,
        local_model: LocalModelConfig,
        budget: MutationRoundBudget,
        sandbox_timeout_seconds: float = 120.0,
    ) -> None:
        if type(runtime_authority) is not MutationRuntimeAuthorizationAuthority:
            raise TypeError("DGM mutation authorization requires the host authority")
        runtime_authority.verify_current(runtime_authorization)
        if runtime_authorization.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("DGM mutation requires Docker or NemoClaw")
        self._integrity_authority = integrity_authority
        self._artifact_authority = artifact_authority
        self._runtime_authority = runtime_authority
        self._authorization = runtime_authorization
        self._local_model = LocalModelConfig.model_validate(local_model)
        self._budget = MutationRoundBudget.model_validate(budget)
        self._sandbox_timeout_seconds = sandbox_timeout_seconds
        self._runner_sha256 = canonical_sha256(
            {
                "contract": "authorized_mutation_round_runner_v1",
                "sandbox": runtime_authorization.sandbox.value,
                "runtime_authorization_sha256": (
                    runtime_authorization.authorization_sha256
                ),
                "runtime_identity_sha256": (
                    runtime_authorization.runtime_identity_sha256
                ),
                "source_manifest_sha256": (
                    runtime_authorization.source_manifest_sha256
                ),
                "model_identity": self._local_model.model_identity.model_dump(
                    mode="json"
                ),
                "budget": self._budget.model_dump(mode="json"),
                "sandbox_timeout_seconds": sandbox_timeout_seconds,
            }
        )

    @property
    def identity(self) -> DGMRunnerFactoryIdentity:
        return DGMRunnerFactoryIdentity(
            model_identity=self._local_model.model_identity,
            runtime_authorization=self._authorization,
            budget=self._budget,
            runner_sha256=self._runner_sha256,
        )

    def create(self) -> _ActiveMutationRound:
        self._runtime_authority.verify_current(self._authorization)
        if self._authorization.sandbox is SandboxKind.DOCKER:
            transport = ProcessMutationSandboxTransport.docker(
                self._authorization,
                authority=self._runtime_authority,
            )
        elif self._authorization.sandbox is SandboxKind.NEMOCLAW:
            transport = ProcessMutationSandboxTransport.nemoclaw(
                self._authorization,
                authority=self._runtime_authority,
            )
        else:  # pragma: no cover - rejected by authorization model and constructor
            raise ValueError("DGM mutation requires a process sandbox")
        runner = AuthorizedMutationRoundRunner(
            integrity_authority=self._integrity_authority,
            bundle_verifier=self._artifact_authority,
            runtime_authority=self._runtime_authority,
            transport=transport,
            local_model=self._local_model,
            budget=self._budget,
            sandbox_timeout_seconds=self._sandbox_timeout_seconds,
        )
        return _ActiveMutationRound(runner=runner, transport=transport)


class _MutationRoundFactory(Protocol):
    @property
    def identity(self) -> DGMRunnerFactoryIdentity: ...

    def create(self) -> object: ...


class DGMExperimentRunner:
    """Run the bounded inherited-improver experiment without manual selection."""

    def __init__(
        self,
        *,
        plan: DGMExperimentPlan,
        integrity_authority: HostIntegrityAuthority,
        artifact_authority: CandidateArtifactAttestationAuthority,
        mutation_factory: DGMAuthorizedMutationRunnerFactory,
        report_provider: DGMReportProvider,
        bundle_store: CandidateBundleStore,
        experiment_store: DGMArtifactStore,
    ) -> None:
        if type(mutation_factory) is not DGMAuthorizedMutationRunnerFactory:
            raise TypeError("DGM product path requires the authorized runner factory")
        if not report_provider.process_isolated:
            raise TypeError("DGM product evaluations require a process runner")
        self._initialize(
            plan=plan,
            integrity_authority=integrity_authority,
            artifact_authority=artifact_authority,
            mutation_factory=mutation_factory,
            report_provider=report_provider,
            bundle_store=bundle_store,
            experiment_store=experiment_store,
            production=True,
        )

    @classmethod
    def _for_test(
        cls,
        *,
        plan: DGMExperimentPlan,
        integrity_authority: HostIntegrityAuthority,
        artifact_authority: CandidateArtifactAttestationAuthority,
        mutation_factory: _MutationRoundFactory,
        report_provider: DGMReportProvider,
        bundle_store: CandidateBundleStore,
        experiment_store: DGMArtifactStore,
    ) -> DGMExperimentRunner:
        instance = cls.__new__(cls)
        instance._initialize(
            plan=plan,
            integrity_authority=integrity_authority,
            artifact_authority=artifact_authority,
            mutation_factory=mutation_factory,
            report_provider=report_provider,
            bundle_store=bundle_store,
            experiment_store=experiment_store,
            production=False,
        )
        return instance

    def _initialize(
        self,
        *,
        plan: DGMExperimentPlan,
        integrity_authority: HostIntegrityAuthority,
        artifact_authority: CandidateArtifactAttestationAuthority,
        mutation_factory: _MutationRoundFactory,
        report_provider: DGMReportProvider,
        bundle_store: CandidateBundleStore,
        experiment_store: DGMArtifactStore,
        production: bool,
    ) -> None:
        self._plan = DGMExperimentPlan.model_validate(plan)
        self._integrity_authority = integrity_authority
        self._artifact_authority = artifact_authority
        self._mutation_factory = mutation_factory
        self._report_provider = report_provider
        self._bundle_store = bundle_store
        self._experiment_store = experiment_store
        self._production = production
        identity = mutation_factory.identity
        authorization = identity.runtime_authorization
        if (
            identity.model_identity != plan.mutation_model_identity
            or identity.budget != plan.mutation_budget
            or authorization.authorization_sha256
            != plan.mutation_runtime_authorization_sha256
            or authorization.runtime_identity_sha256
            != plan.mutation_runtime_identity_sha256
            or authorization.source_manifest_sha256
            != plan.mutation_source_manifest_sha256
            or report_provider.identity != plan.development_runner
        ):
            raise ValueError("DGM runtime does not match its preregistered plan")

    def run(
        self,
        *,
        root_bundle: CandidateBundle,
        root_development_report: EvaluationReport,
        started_at: datetime,
    ) -> DGMExperimentResult:
        started_at = started_at.astimezone(UTC)
        root = self._verified_root(root_bundle, root_development_report)
        self._experiment_store.put_plan(self._plan)
        self._experiment_store.put_bundle(self._plan.experiment_id, root)
        self._experiment_store.put_evaluation(
            self._plan.experiment_id, root_development_report
        )

        active = self._new_active_round()
        n1_round = active.runner.run(
            parent_bundle=root,
            development_report=root_development_report,
            round_id=_round_id(self._plan.experiment_id, "n_to_n1"),
            created_at=started_at,
        )
        n1_cleanup = self._cleanup_evidence(active)
        self._experiment_store.put_round(self._plan.experiment_id, n1_round)
        selected_improver = _first_improver(n1_round)
        if selected_improver is None:
            result = DGMExperimentResult.create(
                plan=self._plan,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                root_bundle=root,
                root_development_report=root_development_report,
                n_to_n1_round=n1_round,
                n_to_n1_cleanup=n1_cleanup,
                n1_bundle=None,
                n1_lineage=None,
                counterfactual_root_bundle=None,
                equal_budget_verified=False,
                i0_arm=None,
                i1_arm=None,
                comparisons=(),
                i0_protected_report=None,
                i1_protected_report=None,
            )
            self._experiment_store.publish(result)
            return result

        n1_bundle, n1_lineage = self._bundle_from_material(
            active.runner,
            parent=root,
            parent_report=root_development_report,
            round_result=n1_round,
            material=selected_improver,
            kind=DGMLineageKind.INHERITED,
            arm=None,
        )
        loaded_i1_bytes = self._bundle_store.resolve_improver_bytes(
            n1_bundle.manifest.bundle_sha256
        )
        if sha256(loaded_i1_bytes).hexdigest() != n1_bundle.materials.candidate.improver.sha256:
            raise DGMIntegrityError("N+1 improver bytes failed reload verification")

        n1_development = self._evaluate(n1_bundle, PartitionName.DEVELOPMENT)
        counterfactual = self._counterfactual_root(
            n1_bundle=n1_bundle,
            baseline_improver_bytes=root.materials.improver_bytes,
            created_at=started_at,
        )
        i0_development = self._evaluate(
            counterfactual, PartitionName.DEVELOPMENT
        )
        observations_equal = (
            _failure_observations_sha256(n1_development)
            == _failure_observations_sha256(i0_development)
        )
        if not observations_equal:
            result = DGMExperimentResult.create(
                plan=self._plan,
                started_at=started_at,
                completed_at=datetime.now(UTC),
                root_bundle=root,
                root_development_report=root_development_report,
                n_to_n1_round=n1_round,
                n_to_n1_cleanup=n1_cleanup,
                n1_bundle=n1_bundle,
                n1_lineage=n1_lineage,
                counterfactual_root_bundle=counterfactual,
                equal_budget_verified=False,
                i0_arm=None,
                i1_arm=None,
                comparisons=(),
                i0_protected_report=None,
                i1_protected_report=None,
            )
            self._experiment_store.publish(result)
            return result

        equal_budget = DGMEqualBudgetContract.create(
            operational_policy_sha256=n1_bundle.materials.candidate.policy.sha256,
            playbook_sha256=self._plan.development_runner.playbook_sha256,
            failure_observations_sha256=(
                _failure_observations_sha256(n1_development)
            ),
            mutation_model_identity=self._plan.mutation_model_identity,
            mutation_runtime_authorization_sha256=(
                self._plan.mutation_runtime_authorization_sha256
            ),
            mutation_runtime_identity_sha256=(
                self._plan.mutation_runtime_identity_sha256
            ),
            mutation_source_manifest_sha256=(
                self._plan.mutation_source_manifest_sha256
            ),
            mutation_budget=self._plan.mutation_budget,
            development_runner=self._plan.development_runner,
        )
        i0_plan = self._arm_plan(
            DGMArmName.I0,
            counterfactual,
            i0_development,
            equal_budget,
        )
        i1_plan = self._arm_plan(
            DGMArmName.I1,
            n1_bundle,
            n1_development,
            equal_budget,
        )
        i0_result = self._run_arm(i0_plan, counterfactual, started_at)
        i1_result = self._run_arm(i1_plan, n1_bundle, started_at)

        comparisons: tuple[DGMComparison, ...] = ()
        if i0_result.selected is not None and i1_result.selected is not None:
            comparisons = tuple(
                DGMComparison.create(
                    experiment_id=self._plan.experiment_id,
                    metric_plan=metric_plan,
                    i0=i0_result.selected,
                    i1=i1_result.selected,
                )
                for metric_plan in self._plan.comparisons
            )
            for comparison in comparisons:
                self._experiment_store.put_comparison(comparison)

        i0_protected: EvaluationReport | None = None
        i1_protected: EvaluationReport | None = None
        # This is deliberately after both development-only selections.
        if self._plan.require_protected_validation and (
            i0_result.selected is not None and i1_result.selected is not None
        ):
            assert i0_result.selected.bundle is not None
            assert i1_result.selected.bundle is not None
            i0_protected = self._evaluate(
                i0_result.selected.bundle,
                PartitionName.PROTECTED_VALIDATION,
            )
            i1_protected = self._evaluate(
                i1_result.selected.bundle,
                PartitionName.PROTECTED_VALIDATION,
            )

        result = DGMExperimentResult.create(
            plan=self._plan,
            started_at=started_at,
            completed_at=datetime.now(UTC),
            root_bundle=root,
            root_development_report=root_development_report,
            n_to_n1_round=n1_round,
            n_to_n1_cleanup=n1_cleanup,
            n1_bundle=n1_bundle,
            n1_lineage=n1_lineage,
            counterfactual_root_bundle=counterfactual,
            equal_budget_verified=True,
            i0_arm=i0_result,
            i1_arm=i1_result,
            comparisons=comparisons,
            i0_protected_report=i0_protected,
            i1_protected_report=i1_protected,
        )
        self._experiment_store.publish(result)
        return result

    def _verified_root(
        self,
        root_bundle: CandidateBundle,
        root_report: EvaluationReport,
    ) -> CandidateBundle:
        root_bundle.verify_attestations(self._artifact_authority)
        stored = self._bundle_store.get(root_bundle.manifest.bundle_sha256)
        if stored != root_bundle:
            raise DGMIntegrityError("Agent N bundle bytes were substituted")
        self._integrity_authority.verify_report(
            root_bundle.materials.candidate, root_report
        )
        if (
            root_bundle.manifest.bundle_sha256
            != self._plan.root_bundle_sha256
            or root_bundle.materials.candidate.candidate_sha256
            != self._plan.root_candidate_sha256
            or root_bundle.materials.candidate.improver.sha256
            != self._plan.root_improver_sha256
            or root_report.report_sha256
            != self._plan.root_development_report_sha256
            or root_report.partition is not PartitionName.DEVELOPMENT
            or root_report.case_sha256s
            != self._plan.development_runner.case_sha256s
        ):
            raise DGMIntegrityError("Agent N does not match the canonical plan")
        return stored

    def _new_active_round(self):
        active = self._mutation_factory.create()
        runner = getattr(active, "runner", None)
        if type(runner) is not AuthorizedMutationRoundRunner:
            raise TypeError("DGM requires the real AuthorizedMutationRoundRunner")
        if self._production and type(
            getattr(active, "transport", None)
        ) is not ProcessMutationSandboxTransport:
            raise TypeError("DGM product mutation must use the process transport")
        return active

    def _cleanup_evidence(self, active) -> DGMCleanupEvidence:
        supplied = getattr(active, "cleanup", None)
        if isinstance(supplied, DGMCleanupEvidence):
            return supplied
        transport = getattr(active, "transport", None)
        if type(transport) is not ProcessMutationSandboxTransport:
            if self._production:
                raise DGMIntegrityError("process cleanup evidence is unavailable")
            cleanup = getattr(active, "cleanup_evidence", None)
            if isinstance(cleanup, DGMCleanupEvidence):
                return cleanup
            raise DGMIntegrityError("fixture cleanup evidence is unavailable")
        if transport.sandbox is SandboxKind.NEMOCLAW:
            return DGMCleanupEvidence.create(
                sandbox=SandboxKind.NEMOCLAW,
                disposition="not_applicable",
                attempt_id=None,
                container_name=None,
                attempt_count=0,
                removal_attempted=False,
                removal_returncode=None,
                absence_observed=True,
                unresolved=False,
            )
        if transport.cleanup_required:
            transport.retry_cleanup()
        history = transport.cleanup_history
        if not history:
            return DGMCleanupEvidence.create(
                sandbox=SandboxKind.DOCKER,
                disposition="not_started",
                attempt_id=None,
                container_name=None,
                attempt_count=0,
                removal_attempted=False,
                removal_returncode=None,
                absence_observed=True,
                unresolved=False,
            )
        return _cleanup_from_transport(history[-1])

    def _bundle_from_material(
        self,
        runner: AuthorizedMutationRoundRunner,
        *,
        parent: CandidateBundle,
        parent_report: EvaluationReport,
        round_result: MutationRoundResult,
        material: BundleReadyMutation,
        kind: DGMLineageKind,
        arm: DGMArmName | None,
    ) -> tuple[CandidateBundle, DGMLineageRecord]:
        receipt = None
        if material.mutation.target is MutationTarget.IMPROVER:
            receipt = runner.issue_improver_receipt(
                parent_bundle=parent,
                development_report=parent_report,
                result=round_result,
                selected_record_sha256=material.record.record_sha256,
                authority=self._artifact_authority,
            )
        provenance = canonical_sha256(
            {
                "contract": "dgm_development_material_v1",
                "experiment_id": str(self._plan.experiment_id),
                "source_report_sha256": parent_report.report_sha256,
                "round_sha256": round_result.round_sha256,
                "record_sha256": material.record.record_sha256,
            }
        )
        attestations = _development_attestations(
            self._artifact_authority,
            material.candidate,
            material.policy_bytes,
            material.improver_bytes,
            material.mutation,
            provenance,
        )
        bundle = CandidateBundle.create(
            candidate=material.candidate,
            policy_bytes=material.policy_bytes,
            improver_bytes=material.improver_bytes,
            mutation=material.mutation,
            attestations=attestations,
            attestation_verifier=self._artifact_authority,
            parent=parent,
            improver_mutation_receipt=receipt,
        )
        digest = self._bundle_store.put(bundle)
        reloaded = self._bundle_store.get(digest)
        generated_by = self._bundle_store.resolve_generated_by_improver_bytes(
            digest
        )
        if generated_by != parent.materials.improver_bytes:
            raise DGMIntegrityError("archived descendant loaded another improver")
        lineage = DGMLineageRecord.create(
            experiment_id=self._plan.experiment_id,
            kind=kind,
            arm=arm,
            parent_bundle_sha256=parent.manifest.bundle_sha256,
            child_bundle_sha256=digest,
            parent_candidate_sha256=(
                parent.materials.candidate.candidate_sha256
            ),
            child_candidate_sha256=(
                reloaded.materials.candidate.candidate_sha256
            ),
            generation=reloaded.materials.candidate.generation,
            mutation_sha256=material.mutation.mutation_sha256,
            generated_by_improver_sha256=(
                material.mutation.generated_by.sha256
            ),
            loaded_improver_sha256=sha256(generated_by).hexdigest(),
            improver_receipt_sha256=(
                canonical_sha256(receipt) if receipt is not None else None
            ),
        )
        self._experiment_store.put_bundle(self._plan.experiment_id, reloaded)
        self._experiment_store.put_lineage(lineage)
        return reloaded, lineage

    def _counterfactual_root(
        self,
        *,
        n1_bundle: CandidateBundle,
        baseline_improver_bytes: bytes,
        created_at: datetime,
    ) -> CandidateBundle:
        n1 = n1_bundle.materials.candidate
        candidate = CandidateManifest.create(
            candidate_id=(
                "dgm_cf_"
                + uuid5(
                    _COUNTERFACTUAL_NAMESPACE,
                    f"{self._plan.experiment_id}:{n1.candidate_sha256}",
                ).hex
            ),
            parent_candidate_id=None,
            generation=0,
            created_at=created_at,
            policy=n1.policy,
            improver=self._plan_root_improver_reference(),
            mutation_sha256=None,
        )
        provenance = canonical_sha256(
            {
                "contract": "dgm_explicit_counterfactual_root_v1",
                "experiment_id": str(self._plan.experiment_id),
                "n1_bundle_sha256": n1_bundle.manifest.bundle_sha256,
                "operational_policy_sha256": n1.policy.sha256,
                "baseline_improver_sha256": self._plan.root_improver_sha256,
            }
        )
        attestations = _root_attestations(
            self._artifact_authority,
            candidate,
            n1_bundle.materials.policy_bytes,
            baseline_improver_bytes,
            provenance,
        )
        root = CandidateBundle.create(
            candidate=candidate,
            policy_bytes=n1_bundle.materials.policy_bytes,
            improver_bytes=baseline_improver_bytes,
            attestations=attestations,
            attestation_verifier=self._artifact_authority,
        )
        digest = self._bundle_store.put(root)
        reloaded = self._bundle_store.get(digest)
        if (
            reloaded.manifest.parent_bundle_sha256 is not None
            or reloaded.materials.candidate.policy != n1.policy
            or reloaded.materials.candidate.improver.sha256
            != self._plan.root_improver_sha256
        ):
            raise DGMIntegrityError("counterfactual root was attached to N lineage")
        self._experiment_store.put_bundle(self._plan.experiment_id, reloaded)
        return reloaded

    def _plan_root_improver_reference(self):
        root = self._bundle_store.get(self._plan.root_bundle_sha256)
        return root.materials.candidate.improver

    def _evaluate(
        self,
        bundle: CandidateBundle,
        partition: PartitionName,
    ) -> EvaluationReport:
        if partition is PartitionName.FINAL_TEST:
            raise ValueError("DGM never consumes final-test evidence")
        report = self._report_provider.evaluate(bundle, partition=partition)
        self._integrity_authority.verify_report(
            bundle.materials.candidate, report
        )
        if (
            report.partition is not partition
            or report.candidate_sha256
            != bundle.materials.candidate.candidate_sha256
        ):
            raise DGMIntegrityError("evaluation report substitution detected")
        if partition is PartitionName.DEVELOPMENT and (
            report.case_sha256s
            != self._plan.development_runner.case_sha256s
        ):
            raise DGMIntegrityError("development cases differ from preregistration")
        self._experiment_store.put_evaluation(self._plan.experiment_id, report)
        return report

    def _arm_plan(
        self,
        arm: DGMArmName,
        root: CandidateBundle,
        report: EvaluationReport,
        equal_budget: DGMEqualBudgetContract,
    ) -> DGMArmPlan:
        packet = build_failure_packet(report)
        return DGMArmPlan.create(
            experiment_id=self._plan.experiment_id,
            arm=arm,
            root_bundle_sha256=root.manifest.bundle_sha256,
            root_candidate_sha256=root.materials.candidate.candidate_sha256,
            improver_sha256=root.materials.candidate.improver.sha256,
            development_report=report,
            failure_packet=packet,
            equal_budget=equal_budget,
        )

    def _run_arm(
        self,
        arm_plan: DGMArmPlan,
        root: CandidateBundle,
        started_at: datetime,
    ) -> DGMArmResult:
        active = self._new_active_round()
        round_result = active.runner.run(
            parent_bundle=root,
            development_report=arm_plan.development_report,
            round_id=_round_id(
                self._plan.experiment_id,
                f"arm_{arm_plan.arm.value}",
            ),
            created_at=started_at,
        )
        cleanup = self._cleanup_evidence(active)
        self._experiment_store.put_round(self._plan.experiment_id, round_result)
        material_by_record = {
            item.record.record_sha256: item for item in round_result.successful
        }
        invalid_by_attempt = {
            item.attempt_id: item for item in round_result.invalid_attempts
        }
        descendants: list[DGMDescendant] = []
        for slot, record in enumerate(round_result.proposals):
            common: dict[str, object] = {
                "experiment_id": self._plan.experiment_id,
                "arm": arm_plan.arm,
                "budget_slot": slot,
                "proposal_seed": arm_plan.equal_budget.mutation_budget.seeds[slot],
                "proposal_record_sha256": record.record_sha256,
                "loaded_improver_sha256": arm_plan.improver_sha256,
            }
            material = material_by_record.get(record.record_sha256)
            if material is not None:
                kind = (
                    DGMLineageKind.COUNTERFACTUAL
                    if arm_plan.arm is DGMArmName.I0
                    else DGMLineageKind.INHERITED
                )
                bundle, lineage = self._bundle_from_material(
                    active.runner,
                    parent=root,
                    parent_report=arm_plan.development_report,
                    round_result=round_result,
                    material=material,
                    kind=kind,
                    arm=arm_plan.arm,
                )
                try:
                    report = self._evaluate(bundle, PartitionName.DEVELOPMENT)
                except Exception:
                    descendants.append(
                        DGMDescendant.create(
                            **common,
                            status=(
                                DGMDescendantStatus.GENERATED_EVALUATION_FAILED
                            ),
                            invalid_reason=None,
                            model_failure_code=None,
                            bundle=bundle,
                            bundle_sha256=bundle.manifest.bundle_sha256,
                            lineage=lineage,
                            development_report=None,
                            development_score=None,
                            hard_gate_failures=None,
                            archived=True,
                        )
                    )
                else:
                    descendants.append(
                        DGMDescendant.create(
                            **common,
                            status=DGMDescendantStatus.EVALUATED,
                            invalid_reason=None,
                            model_failure_code=None,
                            bundle=bundle,
                            bundle_sha256=bundle.manifest.bundle_sha256,
                            lineage=lineage,
                            development_report=report,
                            development_score=development_quality_score(report),
                            hard_gate_failures=_hard_gate_failures(report),
                            archived=True,
                        )
                    )
            elif record.status is MutationProposalStatus.INVALID:
                invalid = invalid_by_attempt[record.attempt_id]
                descendants.append(
                    DGMDescendant.create(
                        **common,
                        status=DGMDescendantStatus.INVALID,
                        invalid_reason=invalid.reason,
                        model_failure_code=None,
                        bundle=None,
                        bundle_sha256=None,
                        lineage=None,
                        development_report=None,
                        development_score=None,
                        hard_gate_failures=None,
                        archived=False,
                    )
                )
            else:
                descendants.append(
                    DGMDescendant.create(
                        **common,
                        status=DGMDescendantStatus.REQUEST_FAILED,
                        invalid_reason=None,
                        model_failure_code=record.model_failure_code.value,
                        bundle=None,
                        bundle_sha256=None,
                        lineage=None,
                        development_report=None,
                        development_score=None,
                        hard_gate_failures=None,
                        archived=False,
                    )
                )
        return DGMArmResult.create(
            arm_plan=arm_plan,
            mutation_round=round_result,
            cleanup=cleanup,
            descendants=tuple(descendants),
        )


def development_quality_score(report: EvaluationReport) -> float:
    """Fixed development-only selection score; protected data is not accepted."""

    if report.partition is not PartitionName.DEVELOPMENT:
        raise ValueError("DGM selection accepts development reports only")
    metrics = report.metrics
    latency = metrics.qualified_acceptance_latency_seconds
    latency_score = 0.0 if latency is None else 1.0 / (1.0 + latency / 30.0)
    notifications_per_case = metrics.notifications_sent / metrics.scenario_count
    return (
        0.65 * metrics.workflow_completion_rate
        + 0.20 * metrics.responder_skill_match_rate
        + 0.10 * latency_score
        + 0.05 * (1.0 / (1.0 + notifications_per_case))
        - 0.10 * metrics.missed_required_actions / metrics.scenario_count
        - 0.05
        * metrics.duplicate_irreversible_actions
        / metrics.scenario_count
        - 0.01 * metrics.unnecessary_actions / metrics.scenario_count
        - 0.01 * metrics.tool_error_count / metrics.scenario_count
    )


def _metric_value(metric: DGMMetric, report: EvaluationReport) -> float:
    metrics = report.metrics
    if metric is DGMMetric.QUALITY_SCORE:
        return development_quality_score(report)
    if metric is DGMMetric.WORKFLOW_COMPLETION_RATE:
        return metrics.workflow_completion_rate
    if metric is DGMMetric.RESPONDER_SKILL_MATCH_RATE:
        return metrics.responder_skill_match_rate
    denominator = metrics.scenario_count
    values = {
        DGMMetric.MISSED_REQUIRED_ACTIONS_PER_CASE: metrics.missed_required_actions,
        DGMMetric.DUPLICATE_IRREVERSIBLE_ACTIONS_PER_CASE: (
            metrics.duplicate_irreversible_actions
        ),
        DGMMetric.UNNECESSARY_ACTIONS_PER_CASE: metrics.unnecessary_actions,
        DGMMetric.TOOL_ERRORS_PER_CASE: metrics.tool_error_count,
        DGMMetric.NOTIFICATIONS_PER_CASE: metrics.notifications_sent,
    }
    return float(values[metric]) / denominator


def _select_best_descendant(
    descendants: tuple[DGMDescendant, ...],
) -> DGMDescendant | None:
    evaluated = [
        item
        for item in descendants
        if item.status is DGMDescendantStatus.EVALUATED
        and item.development_score is not None
        and item.bundle_sha256 is not None
    ]
    if not evaluated:
        return None
    return min(
        evaluated,
        key=lambda item: (-float(item.development_score), item.bundle_sha256),
    )


def _failure_observations_sha256(report: EvaluationReport) -> str:
    """Hash the exact failure observations while excluding arm-specific IDs."""

    return canonical_sha256(
        {
            "benchmark_manifest_sha256": report.benchmark_manifest_sha256,
            "case_sha256s": report.case_sha256s,
            "scenario_scores": [
                item.model_dump(mode="json") for item in report.scenario_scores
            ],
            "metrics": report.metrics.model_dump(mode="json"),
            "hard_gates": [
                item.model_dump(mode="json") for item in report.hard_gates
            ],
            "eligible": report.eligible,
            "failure_packet_observations": {
                "aggregate": build_failure_packet(report).aggregate.model_dump(
                    mode="json"
                ),
                "clusters": [
                    item.model_dump(mode="json")
                    for item in build_failure_packet(report).clusters
                ],
            },
        }
    )


def _hard_gate_failures(report: EvaluationReport) -> int:
    return sum(item.violation_count for item in report.hard_gates)


def _bundle_receipt_sha256(bundle: CandidateBundle) -> str | None:
    receipt = bundle.manifest.improver_mutation_receipt
    return canonical_sha256(receipt) if receipt is not None else None


def _first_improver(result: MutationRoundResult) -> BundleReadyMutation | None:
    if not result.complete:
        return None
    candidates = sorted(
        (
            item
            for item in result.successful
            if item.mutation.target is MutationTarget.IMPROVER
        ),
        key=lambda item: item.record.request_binding.budget_slot,
    )
    return candidates[0] if candidates else None


def _round_id(experiment_id: UUID, label: str) -> UUID:
    return uuid5(experiment_id, f"vital-relay:dgm:{label}")


def _development_attestations(
    authority: CandidateArtifactAttestationAuthority,
    candidate: CandidateManifest,
    policy_bytes: bytes,
    improver_bytes: bytes,
    mutation,
    provenance_sha256: str,
) -> CandidateBundleAttestations:
    return CandidateBundleAttestations(
        candidate_manifest=authority.issue_artifact(
            CandidateBundleArtifactRole.CANDIDATE_MANIFEST,
            canonical_json_bytes(candidate),
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
        coordination_policy=authority.issue_artifact(
            CandidateBundleArtifactRole.COORDINATION_POLICY,
            policy_bytes,
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
        improver=authority.issue_artifact(
            CandidateBundleArtifactRole.IMPROVER,
            improver_bytes,
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
        mutation_manifest=authority.issue_artifact(
            CandidateBundleArtifactRole.MUTATION_MANIFEST,
            canonical_json_bytes(mutation),
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
    )


def _root_attestations(
    authority: CandidateArtifactAttestationAuthority,
    candidate: CandidateManifest,
    policy_bytes: bytes,
    improver_bytes: bytes,
    provenance_sha256: str,
) -> CandidateBundleAttestations:
    return CandidateBundleAttestations(
        candidate_manifest=authority.issue_artifact(
            CandidateBundleArtifactRole.CANDIDATE_MANIFEST,
            canonical_json_bytes(candidate),
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
        coordination_policy=authority.issue_artifact(
            CandidateBundleArtifactRole.COORDINATION_POLICY,
            policy_bytes,
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
        improver=authority.issue_artifact(
            CandidateBundleArtifactRole.IMPROVER,
            improver_bytes,
            source_partition=CandidateMaterialSourcePartition.DEVELOPMENT,
            provenance_sha256=provenance_sha256,
        ),
    )


def _cleanup_from_transport(
    evidence: MutationSandboxCleanupEvidence,
) -> DGMCleanupEvidence:
    return DGMCleanupEvidence.create(
        sandbox=SandboxKind.DOCKER,
        disposition="absence_observed",
        attempt_id=evidence.attempt_id,
        container_name=evidence.container_name,
        attempt_count=evidence.attempt_count,
        removal_attempted=evidence.removal_attempted,
        removal_returncode=evidence.removal_returncode,
        absence_observed=evidence.absence_observed,
        unresolved=evidence.unresolved,
    )


def _derive_claim(
    plan: DGMExperimentPlan,
    n1_bundle: CandidateBundle | None,
    equal_budget_verified: bool,
    i0_arm: DGMArmResult | None,
    i1_arm: DGMArmResult | None,
    comparisons: tuple[DGMComparison, ...],
    i0_protected: EvaluationReport | None,
    i1_protected: EvaluationReport | None,
) -> DGMClaim:
    n1_improver = (
        n1_bundle.materials.candidate.improver.sha256
        if n1_bundle is not None
        else None
    )
    i1_loaded = i1_arm.arm_plan.improver_sha256 if i1_arm else None
    generated = (
        sum(
            item.archived
            and item.bundle is not None
            and item.lineage is not None
            and item.lineage.generated_by_improver_sha256 == i1_loaded
            for item in i1_arm.descendants
        )
        if i1_arm is not None
        else 0
    )
    i0_selected = i0_arm.selected if i0_arm else None
    i1_selected = i1_arm.selected if i1_arm else None
    i0_failures = (
        i0_selected.hard_gate_failures
        if i0_selected and i0_selected.hard_gate_failures is not None
        else 0
    )
    i1_failures = (
        i1_selected.hard_gate_failures
        if i1_selected and i1_selected.hard_gate_failures is not None
        else 0
    )
    if i0_protected is not None:
        i0_failures += _hard_gate_failures(i0_protected)
    if i1_protected is not None:
        i1_failures += _hard_gate_failures(i1_protected)
    protected_complete = (
        not plan.require_protected_validation
        or (i0_protected is not None and i1_protected is not None)
    )
    model_visible_invariance = False
    if (
        i0_arm is not None
        and i1_arm is not None
        and n1_bundle is not None
    ):
        i0_root = i0_arm.arm_plan
        i1_root = i1_arm.arm_plan
        model_visible_invariance = (
            i0_root.root_candidate_sha256 == i1_root.root_candidate_sha256
            and i0_root.development_report.report_sha256
            == i1_root.development_report.report_sha256
            and i0_root.failure_packet == i1_root.failure_packet
        )
    return DGMClaim.create(
        n1_improver_sha256=n1_improver,
        i1_loaded_improver_sha256=i1_loaded,
        i1_verified_archived_n2_count=generated,
        equal_budget_verified=equal_budget_verified,
        model_visible_parent_invariance_verified=model_visible_invariance,
        expected_comparison_count=len(plan.comparisons),
        completed_comparison_count=len(comparisons),
        every_comparison_outperformed=(
            bool(comparisons) and all(item.i1_outperformed for item in comparisons)
        ),
        every_effect_threshold_met=(
            bool(comparisons) and all(item.threshold_met for item in comparisons)
        ),
        additional_hard_gate_failures=max(0, i1_failures - i0_failures),
        protected_validation_required=plan.require_protected_validation,
        protected_validation_complete=protected_complete,
        # One DGM artifact is one paired trial. Candidate slots are proposals,
        # not independent trials. Strong evidence requires external aggregation.
        independent_paired_trial_count=(
            1 if i0_arm is not None and i1_arm is not None else 0
        ),
        required_independent_paired_trials=2,
    )


def _claim_status(
    *,
    n1_improver_sha256: str | None,
    i1_loaded_improver_sha256: str | None,
    i1_verified_archived_n2_count: int,
    equal_budget_verified: bool,
    model_visible_parent_invariance_verified: bool,
    expected_comparison_count: int,
    completed_comparison_count: int,
    every_comparison_outperformed: bool,
    every_effect_threshold_met: bool,
    additional_hard_gate_failures: int,
    protected_validation_required: bool,
    protected_validation_complete: bool,
    independent_paired_trial_count: int,
    required_independent_paired_trials: int,
) -> DGMClaimStatus:
    if n1_improver_sha256 is None:
        return DGMClaimStatus.NO_INHERITANCE_EVIDENCE
    if not equal_budget_verified:
        return DGMClaimStatus.COUNTERFACTUAL_INCONCLUSIVE
    mechanism = (
        i1_verified_archived_n2_count > 0
        and i1_loaded_improver_sha256 == n1_improver_sha256
    )
    if not mechanism:
        return DGMClaimStatus.NO_INHERITANCE_EVIDENCE
    if not model_visible_parent_invariance_verified:
        return DGMClaimStatus.COUNTERFACTUAL_INCONCLUSIVE
    complete = (
        completed_comparison_count == expected_comparison_count
        and (not protected_validation_required or protected_validation_complete)
    )
    if not complete:
        return DGMClaimStatus.COUNTERFACTUAL_INCONCLUSIVE
    if (
        every_comparison_outperformed
        and every_effect_threshold_met
        and additional_hard_gate_failures == 0
        and protected_validation_required
        and protected_validation_complete
        and independent_paired_trial_count >= required_independent_paired_trials
    ):
        return DGMClaimStatus.RECURSIVE_IMPROVEMENT_DEMONSTRATED
    return DGMClaimStatus.MECHANISM_DEMONSTRATED


__all__ = (
    "DGMAuthorizedMutationRunnerFactory",
    "DGMArmName",
    "DGMArmPlan",
    "DGMArmResult",
    "DGMClaim",
    "DGMClaimStatus",
    "DGMComparison",
    "DGMCleanupEvidence",
    "DGMDescendant",
    "DGMDescendantStatus",
    "DGMDevelopmentRunnerIdentity",
    "DGMDirection",
    "DGMEqualBudgetContract",
    "DGMEvaluationLimits",
    "DGMExperimentPlan",
    "DGMExperimentResult",
    "DGMExperimentRunner",
    "DGMIntegrityError",
    "DGMLineageKind",
    "DGMLineageRecord",
    "DGMMetric",
    "DGMMetricPlan",
    "DGMReportProvider",
    "DGMRunnerFactoryIdentity",
    "development_quality_score",
)
