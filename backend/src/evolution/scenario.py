"""Partitioned replay scenarios and a virtual-time scripted tool world."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Protocol
from typing import Self
from uuid import NAMESPACE_URL, UUID, uuid5

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

from vital_relay.agent.contracts import (
    AgentFailureCode,
    AgentIncidentSummary,
    AgentPolicyReference,
    AgentRunRequest,
    AgentRunResult,
    AgentRunStatus,
    ToolEffect,
    ToolTraceStatus,
)
from vital_relay.agent.capabilities import ToolInvocationContext
from vital_relay.agent.runner import AgentRunner
from vital_relay.agent.tools import (
    BoundedToolGateway,
    ToolBinding,
    ToolGatewayErrorCode,
)
from vital_relay.agent.tool_contracts import (
    COORDINATE_DISPATCH,
    GET_DISPATCH_COORDINATION,
    GET_FIXED_PROTOCOL,
    GET_INCIDENT,
    GET_INCIDENT_TIMELINE,
    INITIAL_AGENT_TOOL_NAMES,
    AgentDispatchToolView,
    AgentIncidentToolView,
    AgentProtocolReferenceToolView,
    AgentTimelineToolResult,
    IncidentBoundToolInput,
    TimelineToolInput,
)
from vital_relay.evolution.ace.contracts import SelectedContext
from vital_relay.evolution.ace.selection import (
    GeneratorContextSelector,
    verify_selected_context,
)
from vital_relay.evolution.contracts import (
    ACEPairArm,
    CandidateManifest,
    IDENTIFIER_PATTERN,
    PartitionName,
    PartitionVisibility,
    SHA256_PATTERN,
)
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256
from vital_relay.evolution.policy import PolicyArtifactAdapter


class ScenarioFamily(StrEnum):
    DISPATCH_INVITATION = "dispatch_invitation"
    IMMEDIATE_ACCEPTANCE = "immediate_acceptance"
    DECLINE_THEN_ACCEPT = "decline_then_accept"
    STALE_RESPONDER = "stale_responder"
    MODEL_UNAVAILABLE = "model_unavailable"
    TIMEOUT_ESCALATION = "timeout_escalation"
    NO_SKILL_MATCH = "no_skill_match"
    ROUTING_FALLBACK = "routing_fallback"
    NOTIFICATION_FALLBACK = "notification_fallback"
    PROTOCOL_SELECTION = "protocol_selection"
    CANCELLATION = "cancellation"
    SAFETY_ADVERSARIAL = "safety_adversarial"


class FallbackLabel(StrEnum):
    """Host-observed fallback disclosure, never inferred from prose."""

    STATIC_AED_ROUTE = "static_aed_route"
    IN_APP_NOTIFICATION = "in_app_notification"
    MANUAL_COORDINATION = "manual_coordination"
    MODEL_MANUAL_REQUIRED = "model_manual_required"


class ScenarioModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_TOOL_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    GET_INCIDENT: AgentIncidentToolView,
    GET_INCIDENT_TIMELINE: AgentTimelineToolResult,
    GET_DISPATCH_COORDINATION: AgentDispatchToolView,
    COORDINATE_DISPATCH: AgentDispatchToolView,
    GET_FIXED_PROTOCOL: AgentProtocolReferenceToolView,
}


class ExpectedAction(ScenarioModel):
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments_include: dict[str, JsonValue] = Field(default_factory=dict)
    minimum_count: int = Field(default=1, ge=0, le=20)
    maximum_count: int = Field(default=1, ge=0, le=20)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.maximum_count < self.minimum_count:
            raise ValueError("maximum_count cannot be smaller than minimum_count")
        return self


class ScenarioOracle(ScenarioModel):
    expected_actions: tuple[ExpectedAction, ...] = ()
    allowed_recipient_ids: tuple[str, ...] = ()
    maximum_notifications: int = Field(default=3, ge=0, le=20)
    maximum_elapsed_seconds: float = Field(default=120, ge=0, le=3_600)
    expected_run_status: AgentRunStatus = AgentRunStatus.COMPLETED
    expected_failure_code: AgentFailureCode | None = None
    required_fallback_labels: tuple[FallbackLabel, ...] = ()
    required_protocol_sha256s: tuple[str, ...] = ()

    @field_validator("required_protocol_sha256s")
    @classmethod
    def validate_protocol_hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(item) != 64 or any(char not in "0123456789abcdef" for char in item)
            for item in value
        ):
            raise ValueError("required protocol identities must be SHA-256 digests")
        if len(value) != len(set(value)):
            raise ValueError("required protocol identities must be unique")
        return value

    @model_validator(mode="after")
    def validate_expected_outcome(self) -> Self:
        if self.expected_run_status is AgentRunStatus.COMPLETED:
            if self.expected_failure_code is not None:
                raise ValueError("completed scenarios cannot expect a failure code")
        elif self.expected_failure_code is None:
            raise ValueError("manual-required scenarios must name the safe failure")
        if len(self.required_fallback_labels) != len(
            set(self.required_fallback_labels)
        ):
            raise ValueError("required fallback labels must be unique")
        return self


class WorldEffects(ScenarioModel):
    """Evaluator-only consequences that are never returned to the agent."""

    notification_delta: int = Field(default=0, ge=0, le=50)
    recipient_ids: tuple[str, ...] = Field(default=(), max_length=50)
    accepted_responder_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    accepted_responder_qualified: bool | None = None
    exact_location_disclosed_without_acceptance: bool = False
    irreversible_action_ids: tuple[str, ...] = Field(default=(), max_length=50)
    generated_medical_content: bool = False
    mutated_protocol_content: bool = False
    fallback_labels: tuple[FallbackLabel, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def validate_acceptance(self) -> Self:
        if self.accepted_responder_id is None and self.accepted_responder_qualified is not None:
            raise ValueError("skill qualification requires an accepted responder effect")
        if len(self.fallback_labels) != len(set(self.fallback_labels)):
            raise ValueError("one tool effect cannot repeat a fallback label")
        return self


class ScriptedToolResponse(ScenarioModel):
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    ordinal: int = Field(ge=1, le=100)
    expected_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    result: JsonValue | None = None
    effects: WorldEffects = Field(default_factory=lambda: WorldEffects())
    elapsed_seconds: float = Field(default=0, ge=0, le=3_600)
    fail: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.fail and self.result is not None:
            raise ValueError("failed scripted responses cannot include a result")
        output_model = _TOOL_OUTPUT_MODELS.get(self.tool_name)
        if output_model is None:
            raise ValueError("scripted response uses an unknown A2 tool")
        if not self.fail:
            output_model.model_validate(self.result)
        return self


class ScenarioCase(ScenarioModel):
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    candidate_view_id: str | None = Field(default=None, pattern=IDENTIFIER_PATTERN)
    partition: PartitionName
    family: ScenarioFamily
    seed: int = Field(ge=0)
    virtual_start: AwareDatetime
    incident: AgentIncidentSummary
    public_inputs: dict[str, JsonValue]
    tool_responses: tuple[ScriptedToolResponse, ...]
    oracle: ScenarioOracle
    case_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("virtual_start")
    @classmethod
    def normalize_start(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_case(self, info: ValidationInfo) -> Self:
        if self.partition is PartitionName.PROTECTED_VALIDATION:
            if self.candidate_view_id is None:
                raise ValueError(
                    "protected scenarios require an opaque candidate-view ID"
                )
            if self.candidate_view_id == self.scenario_id:
                raise ValueError(
                    "protected candidate-view IDs must hide host scenario IDs"
                )
        elif self.candidate_view_id is not None:
            raise ValueError("only protected scenarios use candidate-view aliases")
        keys = {(item.tool_name, item.ordinal) for item in self.tool_responses}
        if len(keys) != len(self.tool_responses):
            raise ValueError("scripted response ordinals must be unique per tool")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(self.model_dump(mode="json", exclude={"case_sha256"}))
        if self.case_sha256 != actual:
            raise ValueError("case_sha256 does not match canonical scenario")
        return self

    @classmethod
    def create(cls, **values: object) -> ScenarioCase:
        material = cls.model_validate(
            {**values, "case_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"case_sha256"})
        )
        return cls.model_validate({**values, "case_sha256": digest})


class PartitionEntry(ScenarioModel):
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    case_sha256: str = Field(pattern=SHA256_PATTERN)
    candidate_view_sha256: str = Field(pattern=SHA256_PATTERN)


class PartitionManifest(ScenarioModel):
    partition: PartitionName
    visibility: PartitionVisibility
    entries: tuple[PartitionEntry, ...] = Field(min_length=1)
    trusted_python_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    protected_artifacts_sha256: str = Field(pattern=SHA256_PATTERN)
    final_test_cadence_limit: int | None = Field(default=None, ge=1, le=100)
    manifest_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_manifest(self, info: ValidationInfo) -> Self:
        expected_visibility = {
            PartitionName.DEVELOPMENT: PartitionVisibility.FULL,
            PartitionName.PROTECTED_VALIDATION: PartitionVisibility.INPUTS_ONLY,
            PartitionName.FINAL_TEST: PartitionVisibility.LIMITED_CADENCE,
        }[self.partition]
        if self.visibility is not expected_visibility:
            raise ValueError("partition visibility is fixed by partition type")
        if (self.partition is PartitionName.FINAL_TEST) != (
            self.final_test_cadence_limit is not None
        ):
            raise ValueError("only final-test manifests require a cadence limit")
        if len({entry.scenario_id for entry in self.entries}) != len(self.entries):
            raise ValueError("partition scenario IDs must be unique")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        if self.manifest_sha256 != actual:
            raise ValueError("manifest_sha256 does not match canonical partition")
        return self

    @classmethod
    def create(cls, **values: object) -> PartitionManifest:
        material = cls.model_validate(
            {**values, "manifest_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"manifest_sha256"})
        )
        return cls.model_validate({**values, "manifest_sha256": digest})


class PublicPartitionDescriptor(ScenarioModel):
    """Candidate-visible partition metadata without private case commitments."""

    partition: PartitionName
    visibility: PartitionVisibility
    candidate_visible_scenario_ids: tuple[str, ...] = ()
    final_test_cadence_limit: int | None = Field(default=None, ge=1, le=100)
    descriptor_sha256: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_descriptor(self, info: ValidationInfo) -> Self:
        if self.partition is PartitionName.DEVELOPMENT:
            raise ValueError("development publishes its full partition manifest")
        expected_visibility = {
            PartitionName.PROTECTED_VALIDATION: PartitionVisibility.INPUTS_ONLY,
            PartitionName.FINAL_TEST: PartitionVisibility.LIMITED_CADENCE,
        }[self.partition]
        if self.visibility is not expected_visibility:
            raise ValueError("public descriptor visibility is fixed by partition")
        if self.partition is PartitionName.PROTECTED_VALIDATION:
            if not self.candidate_visible_scenario_ids:
                raise ValueError("protected inputs require visible scenario IDs")
            if self.final_test_cadence_limit is not None:
                raise ValueError("protected validation has no final-test cadence")
        elif (
            self.candidate_visible_scenario_ids
            or self.final_test_cadence_limit is None
        ):
            raise ValueError("final-test descriptors expose only cadence metadata")
        if len(set(self.candidate_visible_scenario_ids)) != len(
            self.candidate_visible_scenario_ids
        ):
            raise ValueError("candidate-visible scenario IDs must be unique")
        if info.context and info.context.get("build_canonical_hash"):
            return self
        actual = canonical_sha256(
            self.model_dump(mode="json", exclude={"descriptor_sha256"})
        )
        if self.descriptor_sha256 != actual:
            raise ValueError("descriptor_sha256 does not match public metadata")
        return self

    @classmethod
    def create(cls, **values: object) -> PublicPartitionDescriptor:
        material = cls.model_validate(
            {**values, "descriptor_sha256": "0" * 64},
            context={"build_canonical_hash": True},
        )
        digest = canonical_sha256(
            material.model_dump(mode="json", exclude={"descriptor_sha256"})
        )
        return cls.model_validate({**values, "descriptor_sha256": digest})


class CandidateScenarioView(ScenarioModel):
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    partition: PartitionName
    family: ScenarioFamily | None = None
    seed: int | None = Field(default=None, ge=0)
    incident: AgentIncidentSummary
    public_inputs: dict[str, JsonValue]
    visible_expectations: tuple[ExpectedAction, ...] | None = None

    @model_validator(mode="after")
    def validate_visibility(self) -> Self:
        if self.partition is PartitionName.FINAL_TEST:
            raise ValueError("final-test inputs are not candidate-visible")
        if self.partition is PartitionName.DEVELOPMENT:
            if (
                self.family is None
                or self.seed is None
                or self.visible_expectations is None
            ):
                raise ValueError(
                    "development views publish family, seed, and expectations"
                )
        elif any(
            value is not None
            for value in (self.family, self.seed, self.visible_expectations)
        ):
            raise ValueError("protected views publish initial conditions only")
        return self


class FinalTestCadenceExceeded(RuntimeError):
    pass


class FinalTestUsageStore(Protocol):
    def consume_if_below(
        self,
        manifest_sha256: str,
        candidate_sha256: str,
        session_id: UUID,
        limit: int,
    ) -> bool:
        """Atomically consume one final-test use when it remains below limit."""


class InMemoryFinalTestUsageStore:
    """Single-process lab adapter; integration replaces this with durable CAS."""

    def __init__(self) -> None:
        self._uses: defaultdict[str, int] = defaultdict(int)
        self._sessions: set[UUID] = set()
        self._lock = Lock()

    def consume_if_below(
        self,
        manifest_sha256: str,
        candidate_sha256: str,
        session_id: UUID,
        limit: int,
    ) -> bool:
        del candidate_sha256
        with self._lock:
            if session_id in self._sessions or self._uses[manifest_sha256] >= limit:
                return False
            self._sessions.add(session_id)
            self._uses[manifest_sha256] += 1
            return True

    def uses(self, manifest_sha256: str) -> int:
        with self._lock:
            return self._uses[manifest_sha256]


class ScenarioCatalog:
    """Development and protected-validation catalog; final cases never enter it."""

    def __init__(
        self,
        manifests: tuple[PartitionManifest, ...],
        cases: tuple[ScenarioCase, ...],
    ) -> None:
        self._manifests = {manifest.partition: manifest for manifest in manifests}
        self._cases = {(case.partition, case.scenario_id): case for case in cases}
        if len(self._manifests) != len(manifests) or len(self._cases) != len(cases):
            raise ValueError("duplicate partition or scenario identity")
        expected_partitions = {
            PartitionName.DEVELOPMENT,
            PartitionName.PROTECTED_VALIDATION,
        }
        if set(self._manifests) != expected_partitions:
            raise ValueError(
                "ordinary catalogs contain development and protected data only"
            )
        seen_ids: set[str] = set()
        for partition, manifest in self._manifests.items():
            for entry in manifest.entries:
                if entry.scenario_id in seen_ids:
                    raise ValueError("scenario IDs must be unique across partitions")
                seen_ids.add(entry.scenario_id)
                case = self._cases.get((partition, entry.scenario_id))
                if case is None or case.case_sha256 != entry.case_sha256:
                    raise ValueError("partition entry does not match its scenario")
                if (
                    canonical_sha256(candidate_view_for_case(case))
                    != entry.candidate_view_sha256
                ):
                    raise ValueError("partition candidate-view hash mismatch")
        if len(seen_ids) != len(self._cases):
            raise ValueError("catalog contains a case missing from its manifest")

    @classmethod
    def load(
        cls,
        public_assets_root: str | Path,
        protected_assets_root: str | Path,
    ) -> ScenarioCatalog:
        """Load development and protected validation without touching final cases."""

        public_root = Path(public_assets_root)
        protected_root = Path(protected_assets_root)
        manifests = (
            _load_model(
                PartitionManifest,
                public_root / "development" / "manifest.json",
            ),
            _load_model(
                PartitionManifest,
                protected_root / "protected-manifest.json",
            ),
        )
        if tuple(manifest.partition for manifest in manifests) != (
            PartitionName.DEVELOPMENT,
            PartitionName.PROTECTED_VALIDATION,
        ):
            raise ValueError("catalog manifests are stored under the wrong partition")
        if len(
            {manifest.protected_artifacts_sha256 for manifest in manifests}
        ) != 1:
            raise ValueError("catalog manifests bind different protected artifacts")
        if len(
            {manifest.trusted_python_tree_sha256 for manifest in manifests}
        ) != 1:
            raise ValueError("catalog manifests bind different trusted Python trees")

        cases: list[ScenarioCase] = []
        for manifest in manifests:
            case_root = (
                public_root / "development" / "cases"
                if manifest.partition is PartitionName.DEVELOPMENT
                else protected_root / manifest.partition.value
            )
            for entry in manifest.entries:
                case = _load_model(
                    ScenarioCase,
                    case_root / f"{entry.scenario_id}.json",
                )
                if case.partition is not manifest.partition:
                    raise ValueError("catalog case is stored under the wrong partition")
                cases.append(case)

        protected_descriptor = _load_model(
            PublicPartitionDescriptor,
            public_root / "protected" / "manifest.json",
        )
        if protected_descriptor.partition is not PartitionName.PROTECTED_VALIDATION:
            raise ValueError("public descriptor is stored under the wrong partition")
        if protected_descriptor.candidate_visible_scenario_ids != tuple(
            case.candidate_view_id
            for case in cases
            if case.partition is PartitionName.PROTECTED_VALIDATION
        ):
            raise ValueError("protected public descriptor does not match host manifest")

        for case in cases:
            public_partition = (
                "development"
                if case.partition is PartitionName.DEVELOPMENT
                else "protected"
            )
            public_id = case.candidate_view_id or case.scenario_id
            public_view = _load_model(
                CandidateScenarioView,
                public_root / public_partition / "inputs" / f"{public_id}.json",
            )
            if public_view != candidate_view_for_case(case):
                raise ValueError("candidate input does not match its host scenario")
        return cls(manifests, tuple(cases))

    def evaluator_cases(
        self,
        partition: PartitionName,
    ) -> tuple[ScenarioCase, ...]:
        if partition is PartitionName.FINAL_TEST:
            raise ValueError("final tests require the separate host authority")
        manifest = self._manifests[partition]
        return tuple(
            self._cases[(partition, entry.scenario_id)] for entry in manifest.entries
        )

    def candidate_view(
        self,
        partition: PartitionName,
    ) -> tuple[CandidateScenarioView, ...]:
        if partition is PartitionName.FINAL_TEST:
            raise ValueError("final-test inputs are not candidate-visible")
        manifest = self._manifests[partition]
        return tuple(
            candidate_view_for_case(self._cases[(partition, entry.scenario_id)])
            for entry in manifest.entries
        )


class _FinalTestEvaluationCapability:
    """Opaque, identity-checked grant created only after ledger consumption."""

    __slots__ = ("candidate_sha256", "manifest_sha256", "session_id")

    def __init__(
        self,
        candidate_sha256: str,
        manifest_sha256: str,
        session_id: UUID,
    ) -> None:
        self.candidate_sha256 = candidate_sha256
        self.manifest_sha256 = manifest_sha256
        self.session_id = session_id


class FinalTestAuthority:
    """Trusted final-test loader and ledger-backed capability issuer."""

    def __init__(
        self,
        manifest: PartitionManifest,
        cases: tuple[ScenarioCase, ...],
        evaluator,
        usage: FinalTestUsageStore,
    ) -> None:
        from vital_relay.evolution.evaluator import ObservableEvaluator

        if type(evaluator) is not ObservableEvaluator:
            raise TypeError(
                "final-test authority requires the exact protected evaluator"
            )
        if manifest.partition is not PartitionName.FINAL_TEST:
            raise ValueError("final-test authority requires the final manifest")
        if tuple(entry.scenario_id for entry in manifest.entries) != tuple(
            case.scenario_id for case in cases
        ):
            raise ValueError("final manifest does not match host cases")
        for entry, case in zip(manifest.entries, cases, strict=True):
            if case.partition is not PartitionName.FINAL_TEST:
                raise ValueError("final authority cannot retain another partition")
            expected = partition_entry(case)
            if entry != expected:
                raise ValueError("final manifest entry does not match its host case")
        self._manifest = manifest
        self._cases = cases
        self._evaluator = evaluator
        self._issuance_port = evaluator._final_test_issuance_port()
        self._usage = usage
        self._capabilities: dict[UUID, _FinalTestEvaluationCapability] = {}
        self._lock = Lock()

    @classmethod
    def load(
        cls,
        public_assets_root: str | Path,
        protected_assets_root: str | Path,
        evaluator,
        usage: FinalTestUsageStore,
    ) -> FinalTestAuthority:
        public_root = Path(public_assets_root)
        protected_root = Path(protected_assets_root)
        descriptor = _load_model(
            PublicPartitionDescriptor,
            public_root / "final" / "manifest.json",
        )
        manifest = _load_model(
            PartitionManifest,
            protected_root / "final-manifest.json",
        )
        if (
            descriptor.partition is not PartitionName.FINAL_TEST
            or manifest.partition is not PartitionName.FINAL_TEST
            or descriptor.final_test_cadence_limit
            != manifest.final_test_cadence_limit
        ):
            raise ValueError("final public cadence does not match host manifest")
        cases = tuple(
            _load_model(
                ScenarioCase,
                protected_root
                / PartitionName.FINAL_TEST.value
                / f"{entry.scenario_id}.json",
            )
            for entry in manifest.entries
        )
        return cls(manifest, cases, evaluator, usage)

    def open(self, candidate_sha256: str, session_id: UUID) -> FinalTestSession:
        limit = self._manifest.final_test_cadence_limit
        assert limit is not None
        if not self._usage.consume_if_below(
            self._manifest.manifest_sha256,
            candidate_sha256,
            session_id,
            limit,
        ):
            raise FinalTestCadenceExceeded("final-test cadence limit exhausted")
        capability = _FinalTestEvaluationCapability(
            candidate_sha256,
            self._manifest.manifest_sha256,
            session_id,
        )
        with self._lock:
            self._capabilities[session_id] = capability

        def revoke() -> None:
            self._revoke_evaluation_capability(capability, self._evaluator)

        def finalize(
            candidate: CandidateManifest,
            executions: tuple[ScenarioExecution, ...],
            *unexpected_args,
            **unexpected_kwargs,
        ):
            try:
                if unexpected_args or unexpected_kwargs:
                    raise TypeError(
                        "final issuance got an unexpected argument"
                    )
                return self._issuance_port(
                    candidate,
                    self._manifest,
                    self._cases,
                    executions,
                    final_test_authority=self,
                    final_test_capability=capability,
                )
            except BaseException:
                revoke()
                raise

        return FinalTestSession(
            session_id,
            candidate_sha256,
            self._manifest,
            self._cases,
            finalize,
            revoke,
        )

    def _consume_evaluation_capability(
        self,
        capability: object,
        evaluator: object,
        candidate_sha256: str,
        manifest_sha256: str,
    ) -> bool:
        if not self._capability_matches(
            capability,
            evaluator,
            candidate_sha256,
            manifest_sha256,
        ):
            return False
        with self._lock:
            if self._capabilities.get(capability.session_id) is not capability:
                return False
            del self._capabilities[capability.session_id]
            return True

    def _revoke_evaluation_capability(
        self,
        capability: object,
        evaluator: object,
    ) -> None:
        if (
            type(capability) is not _FinalTestEvaluationCapability
            or evaluator is not self._evaluator
        ):
            return
        with self._lock:
            if self._capabilities.get(capability.session_id) is capability:
                del self._capabilities[capability.session_id]

    def _capability_matches(
        self,
        capability: object,
        evaluator: object,
        candidate_sha256: str,
        manifest_sha256: str,
    ) -> bool:
        return (
            type(capability) is _FinalTestEvaluationCapability
            and evaluator is self._evaluator
            and capability.candidate_sha256 == candidate_sha256
            and capability.manifest_sha256 == manifest_sha256
        )


_TOOL_MODELS: dict[str, type[BaseModel]] = {
    GET_INCIDENT: IncidentBoundToolInput,
    GET_INCIDENT_TIMELINE: TimelineToolInput,
    GET_DISPATCH_COORDINATION: IncidentBoundToolInput,
    COORDINATE_DISPATCH: IncidentBoundToolInput,
    GET_FIXED_PROTOCOL: IncidentBoundToolInput,
}


class WorldInvocation(ScenarioModel):
    tool_name: str
    ordinal: int
    arguments: dict[str, JsonValue]
    status: ToolTraceStatus
    result: JsonValue | None = None
    error_code: str | None = None
    replayed: bool = False
    effects: WorldEffects = Field(default_factory=WorldEffects)
    virtual_at_seconds: float = Field(ge=0)


class WorldSnapshot(ScenarioModel):
    elapsed_seconds: float = Field(ge=0)
    accepted_responder_id: str | None = None
    accepted_responder_qualified: bool | None = None
    acceptance_at_seconds: float | None = Field(default=None, ge=0)
    exact_location_shared: bool = False
    notification_count: int = Field(ge=0)
    invocations: tuple[WorldInvocation, ...]


class ScriptedToolWorld:
    """Replay-only world. It supplies tool outcomes but never chooses actions."""

    def __init__(self, scenario: ScenarioCase) -> None:
        self._scenario = scenario
        self._responses = {
            (item.tool_name, item.ordinal): item for item in scenario.tool_responses
        }
        self._counts: defaultdict[str, int] = defaultdict(int)
        self._elapsed_seconds = 0.0
        self._accepted_responder_id: str | None = (
            "preaccepted_responder"
            if scenario.incident.accepted_responder_present
            else None
        )
        self._acceptance_at_seconds: float | None = (
            0.0 if scenario.incident.accepted_responder_present else None
        )
        self._accepted_responder_qualified: bool | None = None
        self._exact_location_shared = False
        self._notification_count = 0
        self._invocations: list[WorldInvocation] = []
        self._mutation_results: dict[UUID, tuple[bytes, JsonValue, int]] = {}
        self._mutation_failures: dict[UUID, tuple[bytes, int]] = {}

    @property
    def virtual_now(self) -> datetime:
        return self._scenario.virtual_start + timedelta(seconds=self._elapsed_seconds)

    def gateway(self) -> BoundedToolGateway:
        scripted_names = {item.tool_name for item in self._scenario.tool_responses}
        unknown = scripted_names - set(_TOOL_MODELS)
        if unknown:
            raise ValueError(f"scenario uses unknown tool schemas: {sorted(unknown)}")
        return BoundedToolGateway(
            tuple(
                _tool_binding(name, _TOOL_MODELS[name], self._handler(name))
                for name in INITIAL_AGENT_TOOL_NAMES
            )
        )

    def snapshot(self) -> WorldSnapshot:
        return WorldSnapshot(
            elapsed_seconds=self._elapsed_seconds,
            accepted_responder_id=self._accepted_responder_id,
            accepted_responder_qualified=self._accepted_responder_qualified,
            acceptance_at_seconds=self._acceptance_at_seconds,
            exact_location_shared=self._exact_location_shared,
            notification_count=self._notification_count,
            invocations=tuple(self._invocations),
        )

    def _handler(self, tool_name: str):
        def invoke(arguments: BaseModel, context: ToolInvocationContext) -> JsonValue:
            if context.incident_id != self._scenario.incident.incident_id:
                raise RuntimeError("scenario incident authority mismatch")
            serialized = arguments.model_dump(mode="json")
            request_bytes = canonical_json_bytes(serialized)
            if tool_name == COORDINATE_DISPATCH:
                operation_id = context.idempotency_key
                if operation_id is None:
                    raise RuntimeError("mutating scenario calls require operation identity")
                replay = self._mutation_results.get(operation_id)
                if replay is not None:
                    original_request, result, ordinal = replay
                    if original_request != request_bytes:
                        raise RuntimeError("mutation operation identity was reused")
                    self._invocations.append(
                        WorldInvocation(
                            tool_name=tool_name,
                            ordinal=ordinal,
                            arguments=serialized,
                            status=ToolTraceStatus.COMPLETED,
                            result=result,
                            replayed=True,
                            virtual_at_seconds=self._elapsed_seconds,
                        )
                    )
                    return result
                failed = self._mutation_failures.get(operation_id)
                if failed is not None:
                    original_request, ordinal = failed
                    if original_request != request_bytes:
                        raise RuntimeError("mutation operation identity was reused")
                    self._invocations.append(
                        WorldInvocation(
                            tool_name=tool_name,
                            ordinal=ordinal,
                            arguments=serialized,
                            status=ToolTraceStatus.FAILED,
                            error_code=ToolGatewayErrorCode.HANDLER_FAILED.value,
                            replayed=True,
                            virtual_at_seconds=self._elapsed_seconds,
                        )
                    )
                    raise RuntimeError("mutation outcome remains in doubt")
            self._counts[tool_name] += 1
            ordinal = self._counts[tool_name]
            response = self._responses.get((tool_name, ordinal))
            if response is None or not _mapping_contains(
                serialized,
                response.expected_arguments,
            ):
                if tool_name == COORDINATE_DISPATCH:
                    self._mutation_failures[context.idempotency_key] = (
                        request_bytes,
                        ordinal,
                    )
                self._invocations.append(
                    WorldInvocation(
                        tool_name=tool_name,
                        ordinal=ordinal,
                        arguments=serialized,
                        status=ToolTraceStatus.FAILED,
                        error_code=ToolGatewayErrorCode.HANDLER_FAILED.value,
                        effects=WorldEffects(),
                        virtual_at_seconds=self._elapsed_seconds,
                    )
                )
                raise RuntimeError("scripted tool call did not match the frozen world")
            self._elapsed_seconds += response.elapsed_seconds
            if response.fail:
                if tool_name == COORDINATE_DISPATCH:
                    self._mutation_failures[context.idempotency_key] = (
                        request_bytes,
                        ordinal,
                    )
                self._invocations.append(
                    WorldInvocation(
                        tool_name=tool_name,
                        ordinal=ordinal,
                        arguments=serialized,
                        status=ToolTraceStatus.FAILED,
                        error_code=ToolGatewayErrorCode.HANDLER_FAILED.value,
                        effects=response.effects,
                        virtual_at_seconds=self._elapsed_seconds,
                    )
                )
                raise RuntimeError("frozen synthetic tool failure")
            output_model = _TOOL_OUTPUT_MODELS[tool_name]
            normalized_result = output_model.model_validate(
                response.result
            ).model_dump(mode="json")
            self._apply_observable_state(response.effects)
            if tool_name == COORDINATE_DISPATCH:
                self._mutation_results[context.idempotency_key] = (
                    request_bytes,
                    normalized_result,
                    ordinal,
                )
            self._invocations.append(
                WorldInvocation(
                    tool_name=tool_name,
                    ordinal=ordinal,
                    arguments=serialized,
                    status=ToolTraceStatus.COMPLETED,
                    result=normalized_result,
                    effects=response.effects,
                    virtual_at_seconds=self._elapsed_seconds,
                )
            )
            return normalized_result

        return invoke

    def _apply_observable_state(self, effects: WorldEffects) -> None:
        self._notification_count += effects.notification_delta
        if effects.accepted_responder_id is not None:
            self._accepted_responder_id = effects.accepted_responder_id
            self._accepted_responder_qualified = effects.accepted_responder_qualified
            self._acceptance_at_seconds = self._elapsed_seconds
        if effects.exact_location_disclosed_without_acceptance:
            self._exact_location_shared = True


class ScenarioExecution(ScenarioModel):
    scenario_id: str = Field(pattern=IDENTIFIER_PATTERN)
    partition: PartitionName
    request: AgentRunRequest
    selected_context: SelectedContext
    result: AgentRunResult
    world: WorldSnapshot


class ScenarioRunner:
    """Invoke an AgentRunner against a frozen world; no strategy lives here."""

    def execute(
        self,
        scenario: ScenarioCase,
        candidate_id: str,
        policy: AgentPolicyReference,
        policy_adapter: PolicyArtifactAdapter,
        runner: AgentRunner,
        invocation_context: ToolInvocationContext,
        context_selector: GeneratorContextSelector,
        *,
        paired_round_id: UUID | None = None,
        paired_arm: ACEPairArm | None = None,
    ) -> ScenarioExecution:
        if (paired_round_id is None) != (paired_arm is None):
            raise ValueError("paired round ID and arm must be provided together")
        run_id = (
            paired_scenario_run_id(
                paired_round_id,
                paired_arm,
                candidate_id,
                scenario,
            )
            if paired_round_id is not None and paired_arm is not None
            else scenario_run_id(candidate_id, scenario)
        )
        if invocation_context.run_id != run_id:
            raise ValueError("scenario invocation capability binds another run ID")
        request = AgentRunRequest(
            schema_version=scenario.incident.schema_version,
            run_id=run_id,
            objective="coordinate_emergency_response",
            requested_at=scenario.virtual_start,
            incident=scenario.incident,
            policy=policy,
        )
        world = ScriptedToolWorld(scenario)
        selected_context = context_selector.select(
            request,
            available_tools=invocation_context.allowed_tools,
        )
        result = runner.run(
            request,
            world.gateway(),
            policy_snapshot=policy_adapter.runner_snapshot(policy),
            invocation_context=invocation_context,
            selected_context=selected_context,
        )
        selected_context = verify_selected_context(
            selected_context,
            request,
            available_tools=invocation_context.allowed_tools,
            model_id=result.model_id,
        )
        if (
            result.run_id != request.run_id
            or result.incident_id != request.incident.incident_id
            or result.policy != request.policy
            or result.model_id != selected_context.model_identity.model_id
        ):
            raise ValueError(
                "agent result identity does not match the scenario request"
            )
        return ScenarioExecution(
            scenario_id=scenario.scenario_id,
            partition=scenario.partition,
            request=request,
            selected_context=selected_context,
            result=result,
            world=world.snapshot(),
        )


class FinalTestSession:
    """One candidate-bound host session; raw final cases never leave this object."""

    def __init__(
        self,
        session_id: UUID,
        candidate_sha256: str,
        manifest: PartitionManifest,
        cases: tuple[ScenarioCase, ...],
        finalize,
        revoke,
    ) -> None:
        self.session_id = session_id
        self._candidate_sha256 = candidate_sha256
        self._manifest = manifest
        self._cases = cases
        self._finalize = finalize
        self._revoke = revoke
        self._consumed = False
        self._lock = Lock()

    def evaluate(
        self,
        candidate: CandidateManifest,
        policy_adapter: PolicyArtifactAdapter,
        runner: AgentRunner,
        invocation_contexts: Mapping[str, ToolInvocationContext],
        context_selector: GeneratorContextSelector,
    ):
        with self._lock:
            if self._consumed:
                raise FinalTestCadenceExceeded("final-test session already consumed")
            self._consumed = True
        try:
            if candidate.candidate_sha256 != self._candidate_sha256:
                raise ValueError("final-test session is bound to another candidate")
            if set(invocation_contexts) != {case.scenario_id for case in self._cases}:
                raise ValueError("every final scenario requires pre-issued context")
            executions = tuple(
                ScenarioRunner().execute(
                    case,
                    candidate.candidate_id,
                    candidate.policy,
                    policy_adapter,
                    runner,
                    invocation_contexts[case.scenario_id],
                    context_selector,
                )
                for case in self._cases
            )
            return self._finalize(candidate, executions)
        except BaseException:
            self._revoke()
            raise


def partition_entry(case: ScenarioCase) -> PartitionEntry:
    candidate_commitment = (
        canonical_sha256(candidate_view_for_case(case))
        if case.partition is not PartitionName.FINAL_TEST
        else canonical_sha256(
            {
                "partition": case.partition,
                "scenario_id": case.scenario_id,
                "case_sha256": case.case_sha256,
            }
        )
    )
    return PartitionEntry(
        scenario_id=case.scenario_id,
        case_sha256=case.case_sha256,
        candidate_view_sha256=candidate_commitment,
    )


def candidate_view_for_case(case: ScenarioCase) -> CandidateScenarioView:
    if case.partition is PartitionName.FINAL_TEST:
        raise ValueError("final-test inputs are not candidate-visible")
    protected = case.partition is PartitionName.PROTECTED_VALIDATION
    return CandidateScenarioView(
        scenario_id=(case.candidate_view_id if protected else case.scenario_id),
        partition=case.partition,
        family=None if protected else case.family,
        seed=None if protected else case.seed,
        incident=case.incident,
        public_inputs=case.public_inputs,
        visible_expectations=(
            case.oracle.expected_actions
            if case.partition is PartitionName.DEVELOPMENT
            else None
        ),
    )


def scenario_run_id(candidate_id: str, scenario: ScenarioCase) -> UUID:
    """Expose the stable run ID so an authority can pre-issue lab context."""

    return uuid5(
        NAMESPACE_URL,
        f"vital-relay:evolution:{candidate_id}:{scenario.scenario_id}:{scenario.seed}",
    )


def paired_scenario_run_id(
    round_id: UUID,
    arm: ACEPairArm,
    candidate_id: str,
    scenario: ScenarioCase,
) -> UUID:
    """Derive a non-reusable execution identity for one exact pair arm."""

    return uuid5(
        NAMESPACE_URL,
        (
            "vital-relay:evolution:paired:"
            f"{round_id}:{arm.value}:{candidate_id}:"
            f"{scenario.partition.value}:{scenario.scenario_id}:{scenario.seed}:"
            f"{scenario.case_sha256}"
        ),
    )


def paired_evaluation_id(
    round_id: UUID,
    arm: ACEPairArm,
    partition: PartitionName,
    manifest_sha256: str,
) -> UUID:
    """Derive the signed identity for one exact arm/partition batch."""

    return uuid5(
        NAMESPACE_URL,
        (
            "vital-relay:evolution:paired-evaluation:"
            f"{round_id}:{arm.value}:{partition.value}:{manifest_sha256}"
        ),
    )


def _mapping_contains(
    observed: Mapping[str, JsonValue],
    expected: Mapping[str, JsonValue],
) -> bool:
    return all(observed.get(key) == value for key, value in expected.items())


def _load_model(model_type, path: Path):
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"catalog asset is unavailable: {path.name}") from exc
    try:
        return model_type.model_validate_json(payload)
    except ValueError as exc:
        raise ValueError(f"catalog asset is invalid: {path.name}") from exc


def _tool_binding(name: str, input_model: type[BaseModel], handler) -> ToolBinding:
    return ToolBinding(
        name=name,
        description=f"Synthetic offline scenario tool: {name}.",
        input_model=input_model,
        handler=handler,
        effect=(
            ToolEffect.MUTATE if name == COORDINATE_DISPATCH else ToolEffect.READ
        ),
    )
