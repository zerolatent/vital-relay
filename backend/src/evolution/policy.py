"""Adapter boundary between the laboratory and the production policy schema.

WT-60 deliberately does not define the coordination-policy fields. Agent A2 owns
that production schema. The laboratory records immutable references and asks an
adapter to validate and apply a generic, typed patch.
"""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Protocol

from pydantic import JsonValue

from vital_relay.agent.contracts import AgentPolicyReference
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.evolution.contracts import (
    MutationManifest,
    MutationOperation,
    MutationOperationKind,
    MutationTarget,
)


class PolicyArtifactAdapter(Protocol):
    """Bridge supplied by the policy owner during integration."""

    def canonical_payload(self, reference: AgentPolicyReference) -> bytes:
        """Resolve an immutable policy reference to canonical bytes."""

    def runner_snapshot(
        self,
        reference: AgentPolicyReference,
    ) -> CoordinationPolicySnapshot:
        """Resolve the validated A2 snapshot passed explicitly to AgentRunner."""

    def apply_mutation(
        self,
        parent: AgentPolicyReference,
        mutation: MutationManifest,
    ) -> AgentPolicyReference:
        """Validate safe paths/ranges, apply the patch, and return its identity."""

    def public_diff(
        self,
        parent: AgentPolicyReference,
        child: AgentPolicyReference,
    ) -> tuple[dict[str, JsonValue], ...]:
        """Return a bounded, credential-free typed diff for operator review."""


class A2PolicyArtifactAdapter:
    """Concrete schema-owning adapter for bounded A2 policy evolution."""

    def __init__(self, snapshots: tuple[CoordinationPolicySnapshot, ...]) -> None:
        if not snapshots:
            raise ValueError("at least one policy snapshot is required")
        self._snapshots = {snapshot.reference: snapshot for snapshot in snapshots}
        if len(self._snapshots) != len(snapshots):
            raise ValueError("policy snapshot references must be unique")
        self._lock = RLock()

    def canonical_payload(self, reference: AgentPolicyReference) -> bytes:
        return self.runner_snapshot(reference).canonical_bytes

    def runner_snapshot(
        self,
        reference: AgentPolicyReference,
    ) -> CoordinationPolicySnapshot:
        with self._lock:
            try:
                return self._snapshots[reference]
            except KeyError as exc:
                raise KeyError(reference.sha256) from exc

    def apply_mutation(
        self,
        parent: AgentPolicyReference,
        mutation: MutationManifest,
    ) -> AgentPolicyReference:
        if mutation.target is not MutationTarget.COORDINATION_POLICY:
            raise ValueError("A2 adapter accepts only coordination-policy mutations")
        if mutation.parent_policy != parent:
            raise ValueError("mutation is bound to another parent policy")
        snapshot = self.runner_snapshot(parent)
        payload = deepcopy(snapshot.model_dump(mode="json"))
        for operation in mutation.operations:
            _apply_a2_operation(payload, operation)
        payload["version"] = _next_patch_version(snapshot.version)
        child = CoordinationPolicySnapshot.model_validate(payload)
        if child.reference == parent:
            raise ValueError("policy mutation did not change canonical content")
        with self._lock:
            existing = self._snapshots.get(child.reference)
            if existing is not None and existing != child:
                raise ValueError("policy reference collision")
            self._snapshots[child.reference] = child
        return child.reference

    def public_diff(
        self,
        parent: AgentPolicyReference,
        child: AgentPolicyReference,
    ) -> tuple[dict[str, JsonValue], ...]:
        before = self.runner_snapshot(parent).model_dump(mode="json")
        after = self.runner_snapshot(child).model_dump(mode="json")
        changes: list[dict[str, JsonValue]] = []
        for path in (
            "/strategy/principles",
            "/strategy/human_review_conditions",
            "/tool_budget/max_total_calls",
            "/tool_budget/max_mutating_calls",
        ):
            previous = _value_at_path(before, path)
            current = _value_at_path(after, path)
            if previous != current:
                changes.append(
                    {"path": path, "before": previous, "after": current}
                )
        before_tools = before["tool_budget"]["tools"]
        after_tools = after["tool_budget"]["tools"]
        for index, (previous, current) in enumerate(
            zip(before_tools, after_tools, strict=True)
        ):
            if previous["max_calls"] != current["max_calls"]:
                changes.append(
                    {
                        "path": f"/tool_budget/tools/{index}/max_calls",
                        "before": previous["max_calls"],
                        "after": current["max_calls"],
                    }
                )
        return tuple(changes)


def _apply_a2_operation(
    policy: dict[str, JsonValue],
    operation: MutationOperation,
) -> None:
    path = operation.path
    if path in {
        "/strategy/principles",
        "/strategy/human_review_conditions",
    }:
        if operation.op is not MutationOperationKind.REPLACE or not isinstance(
            operation.value,
            list,
        ):
            raise ValueError("strategy collection mutations require replacement")
        _set_value_at_path(policy, path, operation.value)
        return
    for prefix in (
        "/strategy/principles/",
        "/strategy/human_review_conditions/",
    ):
        if path.startswith(prefix):
            collection = _value_at_path(policy, prefix[:-1])
            if not isinstance(collection, list):
                raise ValueError("strategy mutation target is not a collection")
            _apply_list_operation(collection, path.removeprefix(prefix), operation)
            return
    if path in {
        "/tool_budget/max_total_calls",
        "/tool_budget/max_mutating_calls",
    }:
        if (
            operation.op is not MutationOperationKind.REPLACE
            or type(operation.value) is not int
        ):
            raise ValueError("tool budget mutations require integer replacement")
        _set_value_at_path(policy, path, operation.value)
        return
    parts = path.split("/")
    if (
        len(parts) == 5
        and parts[1:3] == ["tool_budget", "tools"]
        and parts[4] == "max_calls"
        and parts[3].isdigit()
        and operation.op is MutationOperationKind.REPLACE
        and type(operation.value) is int
    ):
        tools = policy["tool_budget"]["tools"]
        try:
            tools[int(parts[3])]["max_calls"] = operation.value
        except (IndexError, TypeError) as exc:
            raise ValueError("tool budget index is out of range") from exc
        return
    raise ValueError("mutation path is not evolvable in the A2 policy")


def _apply_list_operation(
    values: list[JsonValue],
    raw_index: str,
    operation: MutationOperation,
) -> None:
    if raw_index == "-" and operation.op is MutationOperationKind.ADD:
        values.append(operation.value)
        return
    if not raw_index.isdigit():
        raise ValueError("strategy index must be numeric")
    index = int(raw_index)
    if operation.op is MutationOperationKind.ADD:
        if index > len(values):
            raise ValueError("strategy insertion index is out of range")
        values.insert(index, operation.value)
        return
    if index >= len(values):
        raise ValueError("strategy index is out of range")
    if operation.op is MutationOperationKind.REPLACE:
        values[index] = operation.value
    else:
        values.pop(index)


def _value_at_path(value: dict[str, JsonValue], path: str) -> JsonValue:
    current: JsonValue = value
    for part in path.removeprefix("/").split("/"):
        if not isinstance(current, dict):
            raise ValueError("policy diff path is invalid")
        current = current[part]
    return current


def _set_value_at_path(
    value: dict[str, JsonValue],
    path: str,
    replacement: JsonValue,
) -> None:
    parts = path.removeprefix("/").split("/")
    current = value
    for part in parts[:-1]:
        child = current[part]
        if not isinstance(child, dict):
            raise ValueError("policy mutation path is invalid")
        current = child
    current[parts[-1]] = replacement


def _next_patch_version(version: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    return f"{major}.{minor}.{patch + 1}"
