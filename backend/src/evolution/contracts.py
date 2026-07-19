"""Immutable records for offline candidate evaluation and lineage evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from statistics import median
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vital_relay.agent.contracts import AgentPolicyReference, SandboxKind
from vital_relay.agent.policy import (
    CoordinationPolicySnapshot,
    CoordinationToolBudget,
)
from vital_relay.evolution.hashing import canonical_sha256


SHA256_PATTERN = r"^[0-9a-f]{64}$"
IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_-]{0,95}$"
JSON_POINTER_PATTERN = r"^/(?:[^~/]|~0|~1)+(?:/(?:[^~/]|~0|~1)+)*$"


class EvolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PartitionName(StrEnum):
    DEVELOPMENT = "development"
    PROTECTED_VALIDATION = "protected_validation"
    FINAL_TEST = "final_test"


class PartitionVisibility(StrEnum):
    FULL = "full"
    INPUTS_ONLY = "inputs_only"
    LIMITED_CADENCE = "limited_cadence"


class ArtifactKind(StrEnum):
    COORDINATION_POLICY = "coordination_policy"
    IMPROVER = "improver"
    CANDIDATE_BUNDLE = "candidate_bundle"


class MutationOperationKind(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


class MutationTarget(StrEnum):
    COORDINATION_POLICY = "coordination_policy"
    IMPROVER = "improver"


class InvalidAttemptReason(StrEnum):
    MALFORMED_MANIFEST = "malformed_manifest"
    HASH_MISMATCH = "hash_mismatch"
    UNSUPPORTED_TARGET = "unsupported_target"
    PROTECTED_PATH = "protected_path"
    OUT_OF_RANGE = "out_of_range"
    ADAPTER_REJECTED = "adapter_rejected"
    EVALUATION_FAILED = "evaluation_failed"


class HardGateName(StrEnum):
    ALLOWLISTED_RECIPIENTS = "allowlisted_recipients"
    EXACT_LOCATION_AFTER_ACCEPTANCE = "exact_location_after_acceptance"
    NO_DUPLICATE_IRREVERSIBLE_ACTION = "no_duplicate_irreversible_action"
    NO_INVENTED_TOOL_RESULT = "no_invented_tool_result"
    NO_GENERATED_MEDICAL_CONTENT = "no_generated_medical_content"
    PROTECTED_ARTIFACT_INTEGRITY = "protected_artifact_integrity"
    AUDIT_TRACE_INTEGRITY = "audit_trace_integrity"


class BehaviorNiche(StrEnum):
    OVERALL = "overall"
    LOW_ACCEPTANCE_LATENCY = "low_acceptance_latency"
    NOTIFICATION_EFFICIENCY = "notification_efficiency"
    STALE_OR_DECLINE_RECOVERY = "stale_or_decline_recovery"


class PromotionAction(StrEnum):
    PROMOTE = "promote"
    ROLLBACK = "rollback"


class ConclusionSafetyOutcome(StrEnum):
    ALLOWLISTED = "allowlisted"
    NOT_APPLICABLE = "not_applicable"
    UNAPPROVED = "unapproved"


class ACEImprovementOutcome(StrEnum):
    """Host-derived conclusion for one paired ACE evaluation."""

    IMPROVED = "improved"
    INCONCLUSIVE = "inconclusive"
    REJECTED = "rejected"


class ACEPairArm(StrEnum):
    """The two non-interchangeable arms in one paired ACE round."""

    BASELINE = "baseline"
    ADAPTED = "adapted"


class PromotionThresholds(EvolutionModel):
    """Host-owned quality gates that candidate evidence must not choose."""

    minimum_development_gain: float = Field(gt=0, le=1)
    maximum_protected_regression: float = Field(ge=0, le=1)


class ArtifactReference(EvolutionModel):
    kind: ArtifactKind
    sha256: str = Field(pattern=SHA256_PATTERN)
    media_type: str = Field(min_length=1, max_length=100)


class MutationOperation(EvolutionModel):
    op: MutationOperationKind
    path: str = Field(min_length=2, max_length=200, pattern=JSON_POINTER_PATTERN)
    value: JsonValue | None = None

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if self.op is MutationOperationKind.REMOVE and self.value is not None:
            raise ValueError("remove mutations cannot carry a value")
        if self.op is not MutationOperationKind.REMOVE and self.value is None:
            raise ValueError("add and replace mutations require a non-null value")
        return self


class MutationManifest(EvolutionModel):
    mutation_id: UUID
    parent_policy: AgentPolicyReference
    target: MutationTarget
    generated_by: ArtifactReference
    hypothesis_code: str = Field(pattern=IDENTIFIER_PATTERN)
    operations: tuple[MutationOperation, ...] = Field(min_length=1, max_length=16)
    mutation_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash(self, info: ValidationInfo) -> Self:
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"mutation_sha256"})
        )
        if self.mutation_sha256 != actual:
            raise ValueError("mutation_sha256 does not match canonical manifest")
        return self

    @classmethod
    def create(cls, **values: object) -> MutationManifest:
        material = cls.model_validate(
            {**values, "mutation_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"mutation_sha256"})
        )
        return cls.model_validate({**values, "mutation_sha256": digest})


class CandidateManifest(EvolutionModel):
    candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    parent_candidate_id: str | None = Field(
        default=None,
        pattern=IDENTIFIER_PATTERN,
    )
    generation: int = Field(ge=0, le=10_000)
    created_at: AwareDatetime
    policy: AgentPolicyReference
    improver: ArtifactReference
    mutation_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_lineage_and_hash(self, info: ValidationInfo) -> Self:
        if self.generation == 0:
            if self.parent_candidate_id is not None or self.mutation_sha256 is not None:
                raise ValueError("generation zero cannot have a parent mutation")
        elif self.parent_candidate_id is None or self.mutation_sha256 is None:
            raise ValueError("descendants require a parent and mutation")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"candidate_sha256"})
        )
        if self.candidate_sha256 != actual:
            raise ValueError("candidate_sha256 does not match canonical manifest")
        return self

    @classmethod
    def create(cls, **values: object) -> CandidateManifest:
        material = cls.model_validate(
            {**values, "candidate_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"candidate_sha256"})
        )
        return cls.model_validate({**values, "candidate_sha256": digest})


class InvalidAttemptRecord(EvolutionModel):
    attempt_id: UUID
    parent_candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    attempted_at: AwareDatetime
    reason: InvalidAttemptReason
    proposed_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    operation_paths: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("attempted_at")
    @classmethod
    def normalize_attempted_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class MetricVector(EvolutionModel):
    scenario_count: int = Field(ge=1)
    workflow_completion_rate: float = Field(ge=0, le=1)
    missed_required_actions: int = Field(ge=0)
    duplicate_irreversible_actions: int = Field(ge=0)
    unnecessary_actions: int = Field(ge=0)
    qualified_acceptance_latency_seconds: float | None = Field(default=None, ge=0)
    responder_skill_match_rate: float = Field(ge=0, le=1)
    notifications_sent: int = Field(ge=0)
    tool_error_count: int = Field(ge=0)


class HardGateResult(EvolutionModel):
    gate: HardGateName
    passed: bool
    violation_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_count(self) -> Self:
        if self.passed != (self.violation_count == 0):
            raise ValueError("hard-gate pass state must match its violation count")
        return self


class ScenarioScore(EvolutionModel):
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    completed: bool
    missed_required_actions: int = Field(ge=0)
    unnecessary_actions: int = Field(ge=0)
    acceptance_latency_seconds: float | None = Field(default=None, ge=0)
    responder_skill_match: bool
    notifications_sent: int = Field(ge=0)
    tool_error_count: int = Field(ge=0)
    duplicate_irreversible_actions: int = Field(ge=0)


class ExecutionIntegrityBinding(EvolutionModel):
    """Host-observed identity and safety result for one scenario execution."""

    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    case_sha256: str = Field(pattern=SHA256_PATTERN)
    run_id: UUID
    execution_sha256: str = Field(pattern=SHA256_PATTERN)
    full_trace_sha256: str = Field(pattern=SHA256_PATTERN)
    ace_playbook_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    ace_selected_context_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    ace_selected_item_ids: tuple[str, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=12,
        exclude_if=lambda value: value is None,
    )
    ace_generator_role_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    ace_model_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    runner_sandbox: SandboxKind | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    trace_complete: bool
    conclusion_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    conclusion_safety: ConclusionSafetyOutcome

    @model_validator(mode="after")
    def validate_conclusion(self) -> Self:
        if self.ace_selected_item_ids is not None and len(
            self.ace_selected_item_ids
        ) != len(set(self.ace_selected_item_ids)):
            raise ValueError("signed ACE context item IDs must be unique")
        if self.conclusion_safety is ConclusionSafetyOutcome.NOT_APPLICABLE:
            if self.conclusion_sha256 is not None:
                raise ValueError("not-applicable conclusion safety requires no conclusion")
        elif self.conclusion_sha256 is None:
            raise ValueError("conclusion safety decisions must bind conclusion content")
        return self


class ACEPairedEvaluationBinding(EvolutionModel):
    """Signed per-round/per-arm identity for one evaluation batch."""

    round_id: UUID
    arm: ACEPairArm
    partition: PartitionName
    evaluation_id: UUID
    runner_sha256: str = Field(pattern=SHA256_PATTERN)
    budget_sha256: str = Field(pattern=SHA256_PATTERN)
    benchmark_sha256: str = Field(pattern=SHA256_PATTERN)
    seed_cohorts_sha256: str = Field(pattern=SHA256_PATTERN)
    scenario_run_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.partition is PartitionName.FINAL_TEST:
            raise ValueError("paired ACE execution cannot consume final tests")
        if len(self.scenario_run_ids) != len(set(self.scenario_run_ids)):
            raise ValueError("paired evaluation run IDs must be unique")
        return self


class IntegrityEvidence(EvolutionModel):
    """Host-signed evidence derived from immutable evaluation inputs."""

    schema_version: Literal[1, 2] = 2
    authority_key_id: str = Field(pattern=IDENTIFIER_PATTERN)
    validator_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    partition: PartitionName
    benchmark_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    case_sha256s: tuple[str, ...] = Field(min_length=1)
    executions: tuple[ExecutionIntegrityBinding, ...] = Field(min_length=1)
    paired_evaluation: ACEPairedEvaluationBinding | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
    )
    trusted_python_tree_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
        exclude_if=lambda value: value is None,
    )
    expected_protected_artifacts_sha256: str = Field(pattern=SHA256_PATTERN)
    observed_protected_artifacts_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_artifacts_unchanged: bool
    audit_trace_complete: bool
    output_safety_validated: bool
    attestation_hmac_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_derived_flags(self) -> Self:
        scenario_ids = tuple(item.scenario_id for item in self.executions)
        run_ids = tuple(item.run_id for item in self.executions)
        if len(set(scenario_ids)) != len(scenario_ids) or len(set(run_ids)) != len(
            run_ids
        ):
            raise ValueError("integrity executions require unique scenario and run IDs")
        if self.case_sha256s != tuple(item.case_sha256 for item in self.executions):
            raise ValueError("integrity executions must follow the bound case order")
        ace_fields = (
            "ace_playbook_sha256",
            "ace_selected_context_sha256",
            "ace_selected_item_ids",
            "ace_generator_role_sha256",
            "ace_model_sha256",
            "runner_sandbox",
        )
        if self.schema_version == 1:
            if (
                self.paired_evaluation is not None
                or self.trusted_python_tree_sha256 is not None
                or any(
                    getattr(execution, field) is not None
                    for execution in self.executions
                    for field in ace_fields
                )
            ):
                raise ValueError("legacy integrity evidence cannot carry v2 bindings")
        elif self.trusted_python_tree_sha256 is None or any(
            getattr(execution, field) is None
            for execution in self.executions
            for field in ace_fields
        ):
            raise ValueError(
                "v2 integrity evidence requires exact ACE and host-tree bindings"
            )
        if self.paired_evaluation is not None:
            paired = self.paired_evaluation
            if (
                paired.partition is not self.partition
                or paired.scenario_run_ids
                != tuple(execution.run_id for execution in self.executions)
            ):
                raise ValueError("paired evaluation does not bind its exact executions")
        if self.protected_artifacts_unchanged != (
            self.expected_protected_artifacts_sha256
            == self.observed_protected_artifacts_sha256
        ):
            raise ValueError("protected-artifact flag must be derived from its digests")
        if self.audit_trace_complete != all(
            item.trace_complete for item in self.executions
        ):
            raise ValueError("audit completeness must be derived from every execution")
        if self.output_safety_validated != all(
            item.conclusion_safety
            in {
                ConclusionSafetyOutcome.ALLOWLISTED,
                ConclusionSafetyOutcome.NOT_APPLICABLE,
            }
            for item in self.executions
        ):
            raise ValueError("output safety must be derived from every conclusion")
        return self


class EvaluationReport(EvolutionModel):
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    partition: PartitionName
    benchmark_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    case_sha256s: tuple[str, ...] = Field(min_length=1)
    integrity_evidence: IntegrityEvidence
    scenario_scores: tuple[ScenarioScore, ...] = Field(min_length=1)
    metrics: MetricVector
    hard_gates: tuple[HardGateResult, ...] = Field(min_length=1)
    eligible: bool
    issuer_key_id: str = Field(pattern=IDENTIFIER_PATTERN)
    issuer_hmac_sha256: str = Field(pattern=SHA256_PATTERN)
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_report(self, info: ValidationInfo) -> Self:
        gate_names = tuple(item.gate for item in self.hard_gates)
        if len(gate_names) != len(set(gate_names)) or set(gate_names) != set(
            HardGateName
        ):
            raise ValueError("evaluation reports require every hard gate exactly once")
        if self.eligible != all(item.passed for item in self.hard_gates):
            raise ValueError("eligibility must equal the aggregate hard-gate result")
        if self.metrics.scenario_count != len(self.scenario_scores):
            raise ValueError("metric scenario count does not match score records")
        if self.metrics != _aggregate_scenario_scores(self.scenario_scores):
            raise ValueError("metrics must be derived from scenario scores")
        if len(self.case_sha256s) != len(self.scenario_scores):
            raise ValueError("case hashes must bind every scenario score")
        integrity = self.integrity_evidence
        if tuple(score.scenario_id for score in self.scenario_scores) != tuple(
            execution.scenario_id for execution in integrity.executions
        ):
            raise ValueError("scenario scores must follow integrity execution order")
        if (
            integrity.candidate_sha256 != self.candidate_sha256
            or integrity.partition is not self.partition
            or integrity.benchmark_manifest_sha256
            != self.benchmark_manifest_sha256
            or integrity.case_sha256s != self.case_sha256s
            or integrity.authority_key_id != self.issuer_key_id
        ):
            raise ValueError("integrity evidence does not bind the evaluation report")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(evaluation_report_hash_payload(self))
        if self.report_sha256 != actual:
            raise ValueError("report_sha256 does not match canonical report")
        return self

    @classmethod
    def create(cls, **values: object) -> EvaluationReport:
        material = cls.model_validate(
            {**values, "report_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(evaluation_report_hash_payload(material))
        return cls.model_validate({**values, "report_sha256": digest})

    @property
    def integrity_evidence_sha256(self) -> str:
        return canonical_sha256(integrity_evidence_payload(self.integrity_evidence))


_V2_EXECUTION_FIELDS = (
    "ace_playbook_sha256",
    "ace_selected_context_sha256",
    "ace_selected_item_ids",
    "ace_generator_role_sha256",
    "ace_model_sha256",
    "runner_sandbox",
)


def integrity_evidence_payload(evidence: IntegrityEvidence) -> dict[str, object]:
    """Return the exact versioned material used by archived signatures."""

    payload = evidence.model_dump(mode="json")
    if evidence.schema_version == 1:
        payload.pop("paired_evaluation", None)
        payload.pop("trusted_python_tree_sha256", None)
        for execution in payload["executions"]:
            for field in _V2_EXECUTION_FIELDS:
                execution.pop(field, None)
    elif evidence.paired_evaluation is None:
        # Schema-v2 reports issued before paired execution identities were added
        # omitted this optional member. Preserve their canonical signature bytes.
        payload.pop("paired_evaluation", None)
    return payload


def evaluation_report_hash_payload(report: EvaluationReport) -> dict[str, object]:
    payload = report.model_dump(mode="json", exclude={"report_sha256"})
    payload["integrity_evidence"] = integrity_evidence_payload(
        report.integrity_evidence
    )
    return payload


class ACERunnerIdentity(EvolutionModel):
    """Reviewed identity for the one process sandbox used by both pair arms."""

    runner_id: str = Field(pattern=IDENTIFIER_PATTERN)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    sandbox: SandboxKind
    implementation_sha256: str = Field(pattern=SHA256_PATTERN)
    configuration_sha256: str = Field(pattern=SHA256_PATTERN)
    runner_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_runner(self, info: ValidationInfo) -> Self:
        if self.sandbox is SandboxKind.IN_PROCESS:
            raise ValueError("ACE improvement rounds require a process sandbox")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"runner_sha256"})
        )
        if self.runner_sha256 != actual:
            raise ValueError("runner_sha256 does not match canonical identity")
        return self

    @classmethod
    def create(cls, **values: object) -> ACERunnerIdentity:
        material = cls.model_validate(
            {**values, "runner_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "runner_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json",
                        exclude={"runner_sha256"},
                    )
                ),
            }
        )


class ACEBudgetBinding(EvolutionModel):
    """Exact allocation for the work this paired round actually executes."""

    candidate_attempts_per_arm: Literal[1] = 1
    evaluation_batches_per_arm: Literal[2] = 2
    scenario_attempts_per_arm: Literal[18] = 18
    policy_snapshot: CoordinationPolicySnapshot
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_budget: CoordinationToolBudget
    tool_budget_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_context_max_items: Literal[5] = 5
    generator_context_max_characters: Literal[600] = 600
    budget_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_tool_budget_hash(self, info: ValidationInfo) -> Self:
        if (
            self.policy_snapshot.sha256 != self.policy_sha256
            or self.policy_snapshot.tool_budget != self.tool_budget
            or self.tool_budget_sha256 != canonical_sha256(self.tool_budget)
        ):
            raise ValueError("ACE budget does not match its exact policy snapshot")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"budget_sha256"})
        )
        if self.budget_sha256 != actual:
            raise ValueError("budget_sha256 does not match the exact allocation")
        return self

    @classmethod
    def create(
        cls,
        *,
        policy_snapshot: CoordinationPolicySnapshot,
    ) -> ACEBudgetBinding:
        validated = CoordinationPolicySnapshot.model_validate(policy_snapshot)
        values = {
            "policy_snapshot": validated,
            "policy_sha256": validated.sha256,
            "tool_budget": validated.tool_budget,
            "tool_budget_sha256": canonical_sha256(validated.tool_budget),
        }
        material = cls.model_validate(
            {**values, "budget_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "budget_sha256": canonical_sha256(
                    material.model_dump(
                        mode="json",
                        exclude={"budget_sha256"},
                    )
                ),
            }
        )


class ACESeedCohort(EvolutionModel):
    """Declared seed grouping paired identically across baseline and ACE."""

    cohort_id: str = Field(pattern=IDENTIFIER_PATTERN)
    development_seeds: tuple[int, ...] = Field(min_length=1)
    protected_validation_seeds: tuple[int, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_seeds(self) -> Self:
        for values in (
            self.development_seeds,
            self.protected_validation_seeds,
        ):
            if any(value < 0 for value in values):
                raise ValueError("paired seeds cannot be negative")
            if values != tuple(sorted(set(values))):
                raise ValueError("paired cohort seeds must be sorted and unique")
        return self


class ACEBenchmarkBinding(EvolutionModel):
    """Ordered public identity of one complete paired benchmark partition."""

    partition: PartitionName
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    scenario_ids: tuple[str, ...]
    case_sha256s: tuple[str, ...]
    seeds: tuple[int, ...]

    @model_validator(mode="after")
    def validate_partition_binding(self) -> Self:
        if self.partition is PartitionName.FINAL_TEST:
            raise ValueError("final-test cadence is separate from ACE selection")
        required = 12 if self.partition is PartitionName.DEVELOPMENT else 6
        if not (
            len(self.scenario_ids)
            == len(self.case_sha256s)
            == len(self.seeds)
            == required
        ):
            raise ValueError(
                f"paired {self.partition.value} requires exactly {required} cases"
            )
        if len(set(self.scenario_ids)) != required:
            raise ValueError("paired benchmark scenario IDs must be unique")
        if any(not _is_sha256(value) for value in self.case_sha256s):
            raise ValueError("paired benchmark cases require SHA-256 identities")
        if any(value < 0 for value in self.seeds):
            raise ValueError("paired benchmark seeds cannot be negative")
        return self


class ACEExecutionAttempt(EvolutionModel):
    """One actually executed, content-addressed scenario attempt."""

    arm: ACEPairArm
    partition: PartitionName
    attempt_slot: int = Field(ge=0, lt=18)
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    case_sha256: str = Field(pattern=SHA256_PATTERN)
    seed: int = Field(ge=0)
    run_id: UUID
    execution_sha256: str = Field(pattern=SHA256_PATTERN)
    selected_context_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_calls_used: int = Field(ge=0)
    mutating_tool_calls_used: int = Field(ge=0)
    allocated_max_total_calls: int = Field(ge=1, le=50)
    allocated_max_mutating_calls: int = Field(ge=0, le=10)

    @model_validator(mode="after")
    def validate_spend(self) -> Self:
        if self.partition is PartitionName.FINAL_TEST:
            raise ValueError("paired ACE attempts cannot consume final tests")
        if self.tool_calls_used > self.allocated_max_total_calls:
            raise ValueError("scenario attempt exceeded its allocated tool budget")
        if self.mutating_tool_calls_used > self.allocated_max_mutating_calls:
            raise ValueError("scenario attempt exceeded its mutation budget")
        if self.mutating_tool_calls_used > self.tool_calls_used:
            raise ValueError("mutating calls cannot exceed total calls")
        return self


class ACEArmSpend(EvolutionModel):
    """Auditable accounting for all work scheduled and consumed by one arm."""

    arm: ACEPairArm
    candidate_attempts: Literal[1] = 1
    evaluation_batches: Literal[2] = 2
    attempts: tuple[ACEExecutionAttempt, ...] = Field(min_length=18, max_length=18)
    total_tool_calls_used: int = Field(ge=0)
    total_mutating_tool_calls_used: int = Field(ge=0)
    spend_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_accounting(self, info: ValidationInfo) -> Self:
        if any(attempt.arm is not self.arm for attempt in self.attempts):
            raise ValueError("arm spend contains attempts from another arm")
        if tuple(attempt.attempt_slot for attempt in self.attempts) != tuple(
            range(18)
        ):
            raise ValueError("arm attempts require every ordered slot exactly once")
        if tuple(attempt.partition for attempt in self.attempts).count(
            PartitionName.DEVELOPMENT
        ) != 12 or tuple(attempt.partition for attempt in self.attempts).count(
            PartitionName.PROTECTED_VALIDATION
        ) != 6:
            raise ValueError("arm spend requires the full 12/6 benchmark")
        if len({attempt.run_id for attempt in self.attempts}) != 18:
            raise ValueError("arm spend run IDs must be unique")
        if self.total_tool_calls_used != sum(
            attempt.tool_calls_used for attempt in self.attempts
        ) or self.total_mutating_tool_calls_used != sum(
            attempt.mutating_tool_calls_used for attempt in self.attempts
        ):
            raise ValueError("arm spend totals must be derived from exact attempts")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"spend_sha256"})
        )
        if self.spend_sha256 != actual:
            raise ValueError("spend_sha256 does not match exact attempts")
        return self

    @classmethod
    def create(
        cls,
        *,
        arm: ACEPairArm,
        attempts: tuple[ACEExecutionAttempt, ...],
    ) -> ACEArmSpend:
        values = {
            "arm": arm,
            "attempts": attempts,
            "total_tool_calls_used": sum(item.tool_calls_used for item in attempts),
            "total_mutating_tool_calls_used": sum(
                item.mutating_tool_calls_used for item in attempts
            ),
        }
        material = cls.model_validate(
            {**values, "spend_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        return cls.model_validate(
            {
                **values,
                "spend_sha256": canonical_sha256(
                    material.model_dump(mode="json", exclude={"spend_sha256"})
                ),
            }
        )


class ACEPairedReportDigests(EvolutionModel):
    baseline_development_sha256: str = Field(pattern=SHA256_PATTERN)
    adapted_development_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_protected_validation_sha256: str = Field(pattern=SHA256_PATTERN)
    adapted_protected_validation_sha256: str = Field(pattern=SHA256_PATTERN)


class ACEPartitionScoreDelta(EvolutionModel):
    partition: PartitionName
    baseline_score: float = Field(ge=0, le=1)
    adapted_score: float = Field(ge=0, le=1)
    delta: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def validate_delta(self) -> Self:
        if abs(self.delta - (self.adapted_score - self.baseline_score)) > 1e-12:
            raise ValueError("paired score delta must be derived from its scores")
        return self


class ACERegression(EvolutionModel):
    partition: PartitionName
    metric: str = Field(pattern=IDENTIFIER_PATTERN)
    baseline_value: float
    adapted_value: float
    regression_amount: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_regression(self) -> Self:
        if abs(
            self.regression_amount
            - (self.baseline_value - self.adapted_value)
        ) > 1e-12:
            raise ValueError("regression amount must be baseline minus adapted")
        return self


class ACEHardGateRegression(EvolutionModel):
    partition: PartitionName
    gate: HardGateName
    baseline_violation_count: int = Field(ge=0)
    adapted_violation_count: int = Field(ge=1)
    added_violation_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_added_count(self) -> Self:
        if self.added_violation_count != (
            self.adapted_violation_count - self.baseline_violation_count
        ):
            raise ValueError("new hard-gate count must be derived")
        return self


class PairedACEReport(EvolutionModel):
    """Canonical paired evidence; the conclusion is recomputed, never supplied."""

    schema_version: Literal[2] = 2
    round_id: UUID
    active_baseline_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    adapted_playbook_sha256: str = Field(pattern=SHA256_PATTERN)
    delta_sha256: str = Field(pattern=SHA256_PATTERN)
    adaptation_log_sha256: str = Field(pattern=SHA256_PATTERN)
    generator_role_sha256: str = Field(pattern=SHA256_PATTERN)
    reflector_role_sha256: str = Field(pattern=SHA256_PATTERN)
    curator_role_sha256: str = Field(pattern=SHA256_PATTERN)
    model_sha256: str = Field(pattern=SHA256_PATTERN)
    runner_identity: ACERunnerIdentity
    budgets: ACEBudgetBinding
    seed_cohorts: tuple[ACESeedCohort, ...] = Field(min_length=3)
    development_benchmark: ACEBenchmarkBinding
    protected_validation_benchmark: ACEBenchmarkBinding
    baseline_development: EvaluationReport
    adapted_development: EvaluationReport
    baseline_protected_validation: EvaluationReport
    adapted_protected_validation: EvaluationReport
    evaluation_report_sha256s: ACEPairedReportDigests
    arm_spend: tuple[ACEArmSpend, ACEArmSpend]
    score_deltas: tuple[ACEPartitionScoreDelta, ACEPartitionScoreDelta]
    regressions: tuple[ACERegression, ...] = ()
    hard_gate_regressions: tuple[ACEHardGateRegression, ...] = ()
    meaningful_effect_threshold: float = Field(default=0.05, gt=0, le=1)
    outcome: ACEImprovementOutcome
    issuer_key_id: str = Field(pattern=IDENTIFIER_PATTERN)
    pair_hmac_sha256: str = Field(pattern=SHA256_PATTERN)
    report_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_pair_and_derivations(self, info: ValidationInfo) -> Self:
        if self.active_baseline_playbook_sha256 != self.baseline_playbook_sha256:
            raise ValueError("paired report baseline is not the active baseline")
        if self.adapted_playbook_sha256 == self.baseline_playbook_sha256:
            raise ValueError("paired ACE playbooks must differ")
        cohort_ids = tuple(cohort.cohort_id for cohort in self.seed_cohorts)
        if cohort_ids != tuple(sorted(set(cohort_ids))):
            raise ValueError("paired seed cohort IDs must be sorted and unique")
        if (
            self.development_benchmark.partition
            is not PartitionName.DEVELOPMENT
            or self.protected_validation_benchmark.partition
            is not PartitionName.PROTECTED_VALIDATION
        ):
            raise ValueError("paired benchmark bindings are in the wrong order")
        _validate_seed_cohort_coverage(
            self.seed_cohorts,
            self.development_benchmark,
            self.protected_validation_benchmark,
        )
        report_pairs = (
            (
                self.baseline_development,
                self.adapted_development,
                self.development_benchmark,
            ),
            (
                self.baseline_protected_validation,
                self.adapted_protected_validation,
                self.protected_validation_benchmark,
            ),
        )
        paired_bindings = tuple(
            report.integrity_evidence.paired_evaluation
            for pair in report_pairs
            for report in pair[:2]
        )
        if any(binding is None for binding in paired_bindings) or len(
            {
                binding.evaluation_id
                for binding in paired_bindings
                if binding is not None
            }
        ) != 4:
            raise ValueError(
                "paired reports require four distinct evaluation identities"
            )
        candidate_sha256s: set[str] = set()
        seed_cohorts_sha256 = canonical_sha256(
            tuple(item.model_dump(mode="json") for item in self.seed_cohorts)
        )
        expected_arms = (ACEPairArm.BASELINE, ACEPairArm.ADAPTED)
        if tuple(spend.arm for spend in self.arm_spend) != expected_arms:
            raise ValueError("paired spend must contain baseline then adapted")
        baseline_attempts = self.arm_spend[0].attempts
        adapted_attempts = self.arm_spend[1].attempts
        if tuple(
            (
                item.partition,
                item.attempt_slot,
                item.scenario_id,
                item.case_sha256,
                item.seed,
                item.allocated_max_total_calls,
                item.allocated_max_mutating_calls,
            )
            for item in baseline_attempts
        ) != tuple(
            (
                item.partition,
                item.attempt_slot,
                item.scenario_id,
                item.case_sha256,
                item.seed,
                item.allocated_max_total_calls,
                item.allocated_max_mutating_calls,
            )
            for item in adapted_attempts
        ):
            raise ValueError("paired arms did not receive equal scheduled spend")
        if len(
            {
                attempt.run_id
                for spend in self.arm_spend
                for attempt in spend.attempts
            }
        ) != 36:
            raise ValueError("paired arms require distinct per-round run identities")
        for baseline, adapted, benchmark in report_pairs:
            candidate_sha256s.update(
                (baseline.candidate_sha256, adapted.candidate_sha256)
            )
            if (
                baseline.partition is not benchmark.partition
                or adapted.partition is not benchmark.partition
                or baseline.benchmark_manifest_sha256
                != benchmark.manifest_sha256
                or adapted.benchmark_manifest_sha256
                != benchmark.manifest_sha256
                or baseline.case_sha256s != benchmark.case_sha256s
                or adapted.case_sha256s != benchmark.case_sha256s
            ):
                raise ValueError("paired reports do not bind identical benchmarks")
            _validate_ace_execution_bindings(
                baseline,
                round_id=self.round_id,
                arm=ACEPairArm.BASELINE,
                benchmark=benchmark,
                seed_cohorts_sha256=seed_cohorts_sha256,
                budget_sha256=self.budgets.budget_sha256,
                playbook_sha256=self.baseline_playbook_sha256,
                generator_role_sha256=self.generator_role_sha256,
                model_sha256=self.model_sha256,
                runner_sandbox=self.runner_identity.sandbox,
                runner_sha256=self.runner_identity.runner_sha256,
            )
            _validate_ace_execution_bindings(
                adapted,
                round_id=self.round_id,
                arm=ACEPairArm.ADAPTED,
                benchmark=benchmark,
                seed_cohorts_sha256=seed_cohorts_sha256,
                budget_sha256=self.budgets.budget_sha256,
                playbook_sha256=self.adapted_playbook_sha256,
                generator_role_sha256=self.generator_role_sha256,
                model_sha256=self.model_sha256,
                runner_sandbox=self.runner_identity.sandbox,
                runner_sha256=self.runner_identity.runner_sha256,
            )
        if len(candidate_sha256s) != 1:
            raise ValueError("both ACE arms must execute the identical candidate")
        if any(
            report.integrity_evidence.policy_sha256 != self.budgets.policy_sha256
            for pair in report_pairs
            for report in pair[:2]
        ):
            raise ValueError("paired tool budget belongs to another policy")
        expected_report_sha256s = ACEPairedReportDigests(
            baseline_development_sha256=self.baseline_development.report_sha256,
            adapted_development_sha256=self.adapted_development.report_sha256,
            baseline_protected_validation_sha256=(
                self.baseline_protected_validation.report_sha256
            ),
            adapted_protected_validation_sha256=(
                self.adapted_protected_validation.report_sha256
            ),
        )
        if self.evaluation_report_sha256s != expected_report_sha256s:
            raise ValueError("paired report digests must bind all four exact reports")
        _validate_spend_against_reports(self.arm_spend, report_pairs)

        expected_deltas = _paired_score_deltas(report_pairs)
        if self.score_deltas != expected_deltas:
            raise ValueError("paired score deltas must be host-derived")
        expected_regressions = _paired_regressions(expected_deltas)
        if self.regressions != expected_regressions:
            raise ValueError("paired regressions must be host-derived")
        expected_gate_regressions = _paired_hard_gate_regressions(report_pairs)
        if self.hard_gate_regressions != expected_gate_regressions:
            raise ValueError("hard-gate regressions must be host-derived")
        expected_outcome = _paired_ace_outcome(
            adapted_reports=(
                self.adapted_development,
                self.adapted_protected_validation,
            ),
            protected_delta=expected_deltas[1].delta,
            threshold=self.meaningful_effect_threshold,
            hard_gate_regressions=expected_gate_regressions,
        )
        if self.outcome is not expected_outcome:
            raise ValueError("paired ACE outcome must be host-derived")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(paired_ace_report_hash_payload(self))
        if self.report_sha256 != actual:
            raise ValueError("report_sha256 does not match canonical paired report")
        return self

    @classmethod
    def create(cls, **values: object) -> PairedACEReport:
        if any(
            field in values
            for field in (
                "outcome",
                "evaluation_report_sha256s",
                "pair_hmac_sha256",
                "report_sha256",
            )
        ):
            raise ValueError(
                "paired ACE conclusion, digests, and signatures are host-derived"
            )
        report_pairs = (
            (
                EvaluationReport.model_validate(values["baseline_development"]),
                EvaluationReport.model_validate(values["adapted_development"]),
                ACEBenchmarkBinding.model_validate(
                    values["development_benchmark"]
                ),
            ),
            (
                EvaluationReport.model_validate(
                    values["baseline_protected_validation"]
                ),
                EvaluationReport.model_validate(
                    values["adapted_protected_validation"]
                ),
                ACEBenchmarkBinding.model_validate(
                    values["protected_validation_benchmark"]
                ),
            ),
        )
        score_deltas = _paired_score_deltas(report_pairs)
        gate_regressions = _paired_hard_gate_regressions(report_pairs)
        threshold = float(values.get("meaningful_effect_threshold", 0.05))
        outcome = _paired_ace_outcome(
            adapted_reports=(report_pairs[0][1], report_pairs[1][1]),
            protected_delta=score_deltas[1].delta,
            threshold=threshold,
            hard_gate_regressions=gate_regressions,
        )
        material = cls.model_validate(
            {
                **values,
                "evaluation_report_sha256s": ACEPairedReportDigests(
                    baseline_development_sha256=(
                        report_pairs[0][0].report_sha256
                    ),
                    adapted_development_sha256=report_pairs[0][1].report_sha256,
                    baseline_protected_validation_sha256=(
                        report_pairs[1][0].report_sha256
                    ),
                    adapted_protected_validation_sha256=(
                        report_pairs[1][1].report_sha256
                    ),
                ),
                "score_deltas": score_deltas,
                "regressions": _paired_regressions(score_deltas),
                "hard_gate_regressions": gate_regressions,
                "meaningful_effect_threshold": threshold,
                "outcome": outcome,
                "pair_hmac_sha256": "0" * 64,
                "report_sha256": "0" * 64,
            },
            context={"build_canonical_hash": True},
        )
        report_sha256 = canonical_sha256(paired_ace_report_hash_payload(material))
        return cls.model_validate(
            {
                **material.model_dump(mode="json", exclude={"report_sha256"}),
                "report_sha256": report_sha256,
            },
            context={"allow_unsigned_pair": True},
        )


def paired_ace_report_hash_payload(report: PairedACEReport) -> dict[str, object]:
    """Canonical pair content; the detached authority HMAC is not self-hashed."""

    return report.model_dump(
        mode="json",
        exclude={"pair_hmac_sha256", "report_sha256"},
    )


class FailureCluster(EvolutionModel):
    category: str = Field(pattern=IDENTIFIER_PATTERN)
    scenario_count: int = Field(ge=1)
    development_scenario_ids: tuple[str, ...] = Field(max_length=8)


class FailurePacket(EvolutionModel):
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    source_report_sha256: str = Field(pattern=SHA256_PATTERN)
    aggregate: MetricVector
    clusters: tuple[FailureCluster, ...] = Field(max_length=8)


def _aggregate_scenario_scores(
    scores: tuple[ScenarioScore, ...],
) -> MetricVector:
    qualified_latencies = [
        score.acceptance_latency_seconds
        for score in scores
        if score.acceptance_latency_seconds is not None
        and score.responder_skill_match
    ]
    accepted = [
        score for score in scores if score.acceptance_latency_seconds is not None
    ]
    return MetricVector(
        scenario_count=len(scores),
        workflow_completion_rate=sum(score.completed for score in scores)
        / len(scores),
        missed_required_actions=sum(
            score.missed_required_actions for score in scores
        ),
        duplicate_irreversible_actions=sum(
            score.duplicate_irreversible_actions for score in scores
        ),
        unnecessary_actions=sum(score.unnecessary_actions for score in scores),
        qualified_acceptance_latency_seconds=(
            median(qualified_latencies) if qualified_latencies else None
        ),
        responder_skill_match_rate=(
            sum(score.responder_skill_match for score in accepted) / len(accepted)
            if accepted
            else 0
        ),
        notifications_sent=sum(score.notifications_sent for score in scores),
        tool_error_count=sum(score.tool_error_count for score in scores),
    )


class LineageRecord(EvolutionModel):
    child_candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    parent_candidate_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    generation: int = Field(ge=0)
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    mutation_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    policy: AgentPolicyReference
    child_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    generated_by_improver_sha256: str | None = Field(
        default=None,
        pattern=SHA256_PATTERN,
    )
    benchmark_seed: int = Field(ge=0)
    candidate_budget: int = Field(ge=1)
    budget_slot: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_generation(self) -> Self:
        if self.budget_slot >= self.candidate_budget:
            raise ValueError("budget_slot must be inside the candidate budget")
        if self.generation == 0:
            if self.parent_candidate_id is not None:
                raise ValueError("root lineage record cannot have a parent")
            if self.mutation_sha256 is not None:
                raise ValueError("root lineage record cannot have a mutation")
        elif (
            self.parent_candidate_id is None
            or self.generated_by_improver_sha256 is None
            or self.mutation_sha256 is None
        ):
            raise ValueError("descendant lineage records require parent and loaded improver")
        return self


class ImproverComparison(EvolutionModel):
    parent_candidate_id: str = Field(pattern=IDENTIFIER_PATTERN)
    baseline_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    inherited_improver_sha256: str = Field(pattern=SHA256_PATTERN)
    seeds: tuple[int, ...] = Field(min_length=1)
    candidate_budget: int = Field(ge=1)
    baseline_best_report_sha256: str = Field(pattern=SHA256_PATTERN)
    inherited_best_report_sha256: str = Field(pattern=SHA256_PATTERN)
    inherited_outperformed: bool


class PromotionEvidence(EvolutionModel):
    """Candidate-bound selection evidence; final-test data is intentionally absent."""

    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    baseline_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    development: EvaluationReport
    protected_validation: EvaluationReport
    baseline_development: EvaluationReport
    baseline_protected_validation: EvaluationReport
    minimum_development_gain: float = Field(gt=0, le=1)
    maximum_protected_regression: float = Field(ge=0, le=1)
    evidence_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_evidence(self, info: ValidationInfo) -> Self:
        candidate_reports = (self.development, self.protected_validation)
        baseline_reports = (
            self.baseline_development,
            self.baseline_protected_validation,
        )
        if self.candidate_sha256 == self.baseline_candidate_sha256:
            raise ValueError("promotion candidate must differ from its baseline")
        if any(
            report.candidate_sha256 != self.candidate_sha256
            for report in candidate_reports
        ):
            raise ValueError("candidate reports do not match promotion candidate")
        if any(
            report.candidate_sha256 != self.baseline_candidate_sha256
            for report in baseline_reports
        ):
            raise ValueError("baseline reports do not match baseline candidate")
        expected_partitions = (
            PartitionName.DEVELOPMENT,
            PartitionName.PROTECTED_VALIDATION,
        )
        if tuple(report.partition for report in candidate_reports) != expected_partitions:
            raise ValueError("promotion requires development and protected validation")
        if tuple(report.partition for report in baseline_reports) != expected_partitions:
            raise ValueError("baseline requires development and protected validation")
        for candidate_report, baseline_report in zip(
            candidate_reports,
            baseline_reports,
            strict=True,
        ):
            if (
                candidate_report.benchmark_manifest_sha256
                != baseline_report.benchmark_manifest_sha256
                or candidate_report.case_sha256s != baseline_report.case_sha256s
            ):
                raise ValueError("candidate and baseline must use identical benchmarks")
        if not all(report.eligible for report in candidate_reports):
            raise ValueError("promotion reports must pass every hard gate")
        development_gain = (
            _quality_score(self.development)
            - _quality_score(self.baseline_development)
        )
        if development_gain < self.minimum_development_gain:
            raise ValueError("candidate does not meet development improvement threshold")
        protected_regression = (
            _quality_score(self.baseline_protected_validation)
            - _quality_score(self.protected_validation)
        )
        if protected_regression > self.maximum_protected_regression:
            raise ValueError("candidate exceeds protected-validation regression tolerance")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"evidence_sha256"})
        )
        if self.evidence_sha256 != actual:
            raise ValueError("evidence_sha256 does not match canonical evidence")
        return self

    @classmethod
    def create(cls, **values: object) -> PromotionEvidence:
        material = cls.model_validate(
            {**values, "evidence_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"evidence_sha256"})
        )
        return cls.model_validate({**values, "evidence_sha256": digest})


class ArchiveEntry(EvolutionModel):
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    manifest_object_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluation_object_sha256: str = Field(pattern=SHA256_PATTERN)
    evaluation_report_sha256: str = Field(pattern=SHA256_PATTERN)
    partition: PartitionName
    benchmark_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    niches: tuple[BehaviorNiche, ...] = ()


class OperatorApproval(EvolutionModel):
    approval_id: UUID
    operator_id: str = Field(min_length=1, max_length=100)
    action: PromotionAction
    candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    expected_pointer_revision: int = Field(ge=0)
    approved_at: AwareDatetime

    @field_validator("approved_at")
    @classmethod
    def normalize_approved_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_evidence_binding(self) -> Self:
        if self.action is PromotionAction.PROMOTE and self.evidence_sha256 is None:
            raise ValueError("promotion approval must bind selection evidence")
        if self.action is PromotionAction.ROLLBACK and self.evidence_sha256 is not None:
            raise ValueError("rollback approval binds only the retained target")
        return self


class ActiveVersionPointer(EvolutionModel):
    active_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    previous_candidate_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    revision: int = Field(ge=0)


class PromotionEvent(EvolutionModel):
    event_id: UUID
    approval_id: UUID
    action: PromotionAction
    from_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    to_candidate_sha256: str = Field(pattern=SHA256_PATTERN)
    evidence_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    pointer_revision: int = Field(ge=1)
    occurred_at: AwareDatetime

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def _quality_score(report: EvaluationReport) -> float:
    metrics = report.metrics
    latency = metrics.qualified_acceptance_latency_seconds
    latency_score = 0.0 if latency is None else 1.0 / (1.0 + latency / 30.0)
    notifications_per_scenario = metrics.notifications_sent / metrics.scenario_count
    notification_score = 1.0 / (1.0 + notifications_per_scenario)
    return (
        0.65 * metrics.workflow_completion_rate
        + 0.20 * metrics.responder_skill_match_rate
        + 0.10 * latency_score
        + 0.05 * notification_score
        - 0.10 * metrics.missed_required_actions / metrics.scenario_count
        - 0.05 * metrics.duplicate_irreversible_actions / metrics.scenario_count
        - 0.01 * metrics.unnecessary_actions / metrics.scenario_count
        - 0.01 * metrics.tool_error_count / metrics.scenario_count
    )


def normalized_quality_score(report: EvaluationReport) -> float:
    """Return the bounded 0–1 quality score used by paired ACE selection."""

    return max(0.0, min(1.0, _quality_score(report)))


def _validate_seed_cohort_coverage(
    cohorts: tuple[ACESeedCohort, ...],
    development: ACEBenchmarkBinding,
    protected: ACEBenchmarkBinding,
) -> None:
    for attribute, benchmark in (
        ("development_seeds", development),
        ("protected_validation_seeds", protected),
    ):
        declared = tuple(
            seed
            for cohort in cohorts
            for seed in getattr(cohort, attribute)
        )
        observed = set(benchmark.seeds)
        if len(declared) != len(set(declared)) or set(declared) != observed:
            raise ValueError(
                "paired seed cohorts must cover every "
                f"{benchmark.partition.value} seed exactly once"
            )


def _validate_ace_execution_bindings(
    report: EvaluationReport,
    *,
    round_id: UUID,
    arm: ACEPairArm,
    benchmark: ACEBenchmarkBinding,
    seed_cohorts_sha256: str,
    budget_sha256: str,
    playbook_sha256: str,
    generator_role_sha256: str,
    model_sha256: str,
    runner_sandbox: SandboxKind,
    runner_sha256: str,
) -> None:
    paired = report.integrity_evidence.paired_evaluation
    if (
        paired is None
        or paired.round_id != round_id
        or paired.arm is not arm
        or paired.partition is not benchmark.partition
        or paired.runner_sha256 != runner_sha256
        or paired.budget_sha256 != budget_sha256
        or paired.benchmark_sha256 != canonical_sha256(benchmark)
        or paired.seed_cohorts_sha256 != seed_cohorts_sha256
    ):
        raise ValueError("evaluation report binds another paired execution identity")
    for execution in report.integrity_evidence.executions:
        if (
            execution.ace_playbook_sha256 != playbook_sha256
            or execution.ace_generator_role_sha256 != generator_role_sha256
            or execution.ace_model_sha256 != model_sha256
            or execution.runner_sandbox is not runner_sandbox
        ):
            raise ValueError("signed execution binds another ACE identity")


def _validate_spend_against_reports(
    spend: tuple[ACEArmSpend, ACEArmSpend],
    report_pairs: tuple[
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
    ],
) -> None:
    reports_by_arm = {
        ACEPairArm.BASELINE: (report_pairs[0][0], report_pairs[1][0]),
        ACEPairArm.ADAPTED: (report_pairs[0][1], report_pairs[1][1]),
    }
    for arm_spend in spend:
        expected: list[tuple[PartitionName, str, str, int, UUID, str, str]] = []
        for report, benchmark in zip(
            reports_by_arm[arm_spend.arm],
            (report_pairs[0][2], report_pairs[1][2]),
            strict=True,
        ):
            for execution, scenario_id, case_sha256, seed in zip(
                report.integrity_evidence.executions,
                benchmark.scenario_ids,
                benchmark.case_sha256s,
                benchmark.seeds,
                strict=True,
            ):
                if execution.ace_selected_context_sha256 is None:
                    raise ValueError("paired spend requires archived selected context")
                expected.append(
                    (
                        benchmark.partition,
                        scenario_id,
                        case_sha256,
                        seed,
                        execution.run_id,
                        execution.execution_sha256,
                        execution.ace_selected_context_sha256,
                    )
                )
        observed = [
            (
                item.partition,
                item.scenario_id,
                item.case_sha256,
                item.seed,
                item.run_id,
                item.execution_sha256,
                item.selected_context_sha256,
            )
            for item in arm_spend.attempts
        ]
        if observed != expected:
            raise ValueError("paired spend does not bind the signed execution reports")


def _paired_score_deltas(
    report_pairs: tuple[
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
    ],
) -> tuple[ACEPartitionScoreDelta, ACEPartitionScoreDelta]:
    values: list[ACEPartitionScoreDelta] = []
    for baseline, adapted, benchmark in report_pairs:
        baseline_score = normalized_quality_score(baseline)
        adapted_score = normalized_quality_score(adapted)
        values.append(
            ACEPartitionScoreDelta(
                partition=benchmark.partition,
                baseline_score=baseline_score,
                adapted_score=adapted_score,
                delta=adapted_score - baseline_score,
            )
        )
    return values[0], values[1]


def _paired_regressions(
    deltas: tuple[ACEPartitionScoreDelta, ACEPartitionScoreDelta],
) -> tuple[ACERegression, ...]:
    return tuple(
        ACERegression(
            partition=item.partition,
            metric="normalized_quality",
            baseline_value=item.baseline_score,
            adapted_value=item.adapted_score,
            regression_amount=-item.delta,
        )
        for item in deltas
        if item.delta < 0
    )


def _paired_hard_gate_regressions(
    report_pairs: tuple[
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
        tuple[EvaluationReport, EvaluationReport, ACEBenchmarkBinding],
    ],
) -> tuple[ACEHardGateRegression, ...]:
    regressions: list[ACEHardGateRegression] = []
    for baseline, adapted, benchmark in report_pairs:
        baseline_by_gate = {item.gate: item for item in baseline.hard_gates}
        for adapted_gate in adapted.hard_gates:
            baseline_count = baseline_by_gate[adapted_gate.gate].violation_count
            if adapted_gate.violation_count <= baseline_count:
                continue
            regressions.append(
                ACEHardGateRegression(
                    partition=benchmark.partition,
                    gate=adapted_gate.gate,
                    baseline_violation_count=baseline_count,
                    adapted_violation_count=adapted_gate.violation_count,
                    added_violation_count=(
                        adapted_gate.violation_count - baseline_count
                    ),
                )
            )
    return tuple(regressions)


def _paired_ace_outcome(
    *,
    adapted_reports: tuple[EvaluationReport, EvaluationReport],
    protected_delta: float,
    threshold: float,
    hard_gate_regressions: tuple[ACEHardGateRegression, ...],
) -> ACEImprovementOutcome:
    if hard_gate_regressions or not all(
        report.eligible for report in adapted_reports
    ):
        return ACEImprovementOutcome.REJECTED
    if protected_delta >= threshold:
        return ACEImprovementOutcome.IMPROVED
    return ACEImprovementOutcome.INCONCLUSIVE


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
