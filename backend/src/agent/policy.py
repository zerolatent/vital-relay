"""Typed, hash-addressed coordination policy snapshots.

The policy guides an agent's strategy and bounds its tool use. It deliberately
does not encode an ordered production workflow or a deterministic substitute
for model decisions.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from hmac import compare_digest
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vital_relay.agent.contracts import (
    TOOL_NAME_PATTERN,
    AgentPolicyReference,
    ToolEffect,
)
from vital_relay.domain.incidents import IncidentState


POLICY_SCHEMA_VERSION = 1


class CoordinationMission(StrEnum):
    BOUNDED_COORDINATION_PROGRESS = "bounded_coordination_progress"


class CoordinationPrinciple(StrEnum):
    SMALLEST_BOUNDED_ACTION = "smallest_bounded_action"
    AUTHORITATIVE_RESULT_FEEDBACK = "authoritative_result_feedback"
    MINIMIZE_SENSITIVE_DATA = "minimize_sensitive_data"
    PRESERVE_PROTECTED_BOUNDARIES = "preserve_protected_boundaries"


class HumanReviewCondition(StrEnum):
    NO_SAFE_BOUNDED_TOOL = "no_safe_bounded_tool"
    TOOL_DENIED_FAILED_OR_STALE = "tool_denied_failed_or_stale"
    CONFLICTING_AUTHORITATIVE_OBSERVATIONS = (
        "conflicting_authoritative_observations"
    )
    OPERATION_OUTSIDE_CAPABILITY = "operation_outside_capability"


_MISSION_GUIDANCE = {
    CoordinationMission.BOUNDED_COORDINATION_PROGRESS: (
        "Choose safe, bounded coordination actions for one active incident using "
        "only authoritative tool observations and application-enforced operations."
    )
}
_PRINCIPLE_GUIDANCE = {
    CoordinationPrinciple.SMALLEST_BOUNDED_ACTION: (
        "Prefer the smallest bounded action that can make coordination progress."
    ),
    CoordinationPrinciple.AUTHORITATIVE_RESULT_FEEDBACK: (
        "Re-evaluate strategy from authoritative results instead of assuming an "
        "action succeeded."
    ),
    CoordinationPrinciple.MINIMIZE_SENSITIVE_DATA: (
        "Minimize sensitive data and never seek exact wearer or responder location."
    ),
    CoordinationPrinciple.PRESERVE_PROTECTED_BOUNDARIES: (
        "Preserve responder, recipient, state, protocol, and authorization boundaries."
    ),
}
_HUMAN_REVIEW_GUIDANCE = {
    HumanReviewCondition.NO_SAFE_BOUNDED_TOOL: (
        "Request human review when no registered bounded tool can safely make progress."
    ),
    HumanReviewCondition.TOOL_DENIED_FAILED_OR_STALE: (
        "Request human review when a tool is denied, fails, or reports stale state."
    ),
    HumanReviewCondition.CONFLICTING_AUTHORITATIVE_OBSERVATIONS: (
        "Request human review when authoritative observations conflict or remain "
        "ambiguous."
    ),
    HumanReviewCondition.OPERATION_OUTSIDE_CAPABILITY: (
        "Request human review when the incident requires an operation outside the "
        "granted capability."
    ),
}


class CoordinationStrategy(BaseModel):
    """Allowlisted strategy codes; candidate policies cannot inject prompt text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mission: Literal[CoordinationMission.BOUNDED_COORDINATION_PROGRESS]
    principles: tuple[CoordinationPrinciple, ...] = Field(min_length=1, max_length=4)
    human_review_conditions: tuple[HumanReviewCondition, ...] = Field(
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def validate_unique_guidance(self) -> Self:
        if len(self.principles) != len(set(self.principles)):
            raise ValueError("coordination principles must be unique")
        if len(self.human_review_conditions) != len(
            set(self.human_review_conditions)
        ):
            raise ValueError("human-review conditions must be unique")
        return self


class PolicyToolBudget(BaseModel):
    """One explicit policy-visible tool and its per-run call ceiling."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    effect: ToolEffect
    max_calls: int = Field(ge=1, le=20)


class CoordinationToolBudget(BaseModel):
    """Resource bounds, not an instruction about which action to take next."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_total_calls: int = Field(ge=1, le=50)
    max_mutating_calls: int = Field(ge=0, le=10)
    tools: tuple[PolicyToolBudget, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_tool_budgets(self) -> Self:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("policy tool names must be unique")
        if self.max_total_calls > sum(tool.max_calls for tool in self.tools):
            raise ValueError("max_total_calls cannot exceed all per-tool budgets")
        mutating_budget = sum(
            tool.max_calls
            for tool in self.tools
            if tool.effect is ToolEffect.MUTATE
        )
        if self.max_mutating_calls > self.max_total_calls:
            raise ValueError("max_mutating_calls cannot exceed max_total_calls")
        if self.max_mutating_calls > mutating_budget:
            raise ValueError("max_mutating_calls exceeds mutating tool budgets")
        if not mutating_budget:
            if self.max_mutating_calls != 0:
                raise ValueError(
                    "max_mutating_calls must be zero without mutating tools"
                )
        elif self.max_mutating_calls == 0:
            raise ValueError("mutating tools require a positive mutation budget")
        return self


class CoordinationPolicySnapshot(BaseModel):
    """Complete immutable input used by one coordination-agent run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[POLICY_SCHEMA_VERSION]
    policy_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
    )
    version: str = Field(
        min_length=1,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    objective: Literal["coordinate_emergency_response"]
    strategy: CoordinationStrategy
    tool_budget: CoordinationToolBudget

    @property
    def canonical_bytes(self) -> bytes:
        """Return the sole byte representation used for identity and signing."""

        return json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def prompt_bytes(self) -> bytes:
        """Render only host-owned prose for allowlisted policy strategy codes."""

        payload = {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "version": self.version,
            "objective": self.objective,
            "strategy": {
                "mission": _MISSION_GUIDANCE[self.strategy.mission],
                "principles": [
                    _PRINCIPLE_GUIDANCE[principle]
                    for principle in self.strategy.principles
                ],
                "human_review_conditions": [
                    _HUMAN_REVIEW_GUIDANCE[condition]
                    for condition in self.strategy.human_review_conditions
                ],
            },
            "tool_budget": self.tool_budget.model_dump(mode="json"),
        }
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    @property
    def reference(self) -> AgentPolicyReference:
        return AgentPolicyReference(
            policy_id=self.policy_id,
            version=self.version,
            sha256=self.sha256,
        )

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(tool.name for tool in self.tool_budget.tools)

    def tool_rule(self, name: str) -> PolicyToolBudget | None:
        return next((tool for tool in self.tool_budget.tools if tool.name == name), None)

    def verify_reference(self, expected: AgentPolicyReference) -> None:
        if self.reference != expected:
            raise PolicyVerificationError("policy_reference_mismatch")


class PolicyVerificationError(ValueError):
    """A policy file is invalid or does not match its requested identity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def allowed_tools_for_state(
    snapshot: CoordinationPolicySnapshot,
    state: IncidentState,
) -> tuple[str, ...]:
    """Return the single state-specific grant used by host and runner.

    Keeping capability issuance and runtime filtering on one function prevents
    a broader host grant from becoming usable only because the model runtime
    happened to hide a tool from its prompt.
    """

    exclusions = {
        IncidentState.ESCALATING: {"get_fixed_protocol"},
        IncidentState.RESPONSE_ACTIVE: {"coordinate_dispatch"},
    }
    if state not in exclusions:
        raise PolicyVerificationError("incident_state_not_agent_eligible")
    allowed = tuple(
        tool for tool in snapshot.allowed_tools if tool not in exclusions[state]
    )
    if not allowed:
        raise PolicyVerificationError("policy_has_no_state_tools")
    return allowed


def load_policy_snapshot(
    path: str | Path,
    *,
    expected: AgentPolicyReference | None = None,
) -> CoordinationPolicySnapshot:
    """Parse YAML into a typed snapshot and optionally verify its full identity."""

    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - installation guard
        raise PolicyVerificationError("yaml_runtime_unavailable") from exc

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        snapshot = CoordinationPolicySnapshot.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        if isinstance(exc, PolicyVerificationError):
            raise
        raise PolicyVerificationError("invalid_policy_snapshot") from exc
    if expected is not None:
        snapshot.verify_reference(expected)
    return snapshot


def load_pinned_policy_snapshot(
    path: str | Path,
    digest_path: str | Path,
) -> CoordinationPolicySnapshot:
    """Load a policy only when its canonical identity matches a reviewed pin."""

    policy_path = Path(path)
    try:
        raw_pin = Path(digest_path).read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise PolicyVerificationError("policy_digest_unavailable") from exc
    if len(raw_pin) > 256:
        raise PolicyVerificationError("invalid_policy_digest")
    match = re.fullmatch(
        r"([0-9a-f]{64})[ \t]+\*?([A-Za-z0-9._-]+)\r?\n?",
        raw_pin,
    )
    if match is None or match.group(2) != policy_path.name:
        raise PolicyVerificationError("invalid_policy_digest")
    snapshot = load_policy_snapshot(policy_path)
    if not compare_digest(snapshot.sha256, match.group(1)):
        raise PolicyVerificationError("policy_digest_mismatch")
    return snapshot
