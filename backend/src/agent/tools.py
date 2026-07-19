"""Deny-by-default typed tool gateway and observable run dispatcher."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, JsonValue, TypeAdapter, ValidationError

from vital_relay.agent.contracts import (
    AgentToolDefinition,
    AgentToolTrace,
    ToolEffect,
    ToolTraceStatus,
)
from vital_relay.agent.capability_runtime import ToolInvocationContext
from vital_relay.agent.policy import CoordinationPolicySnapshot
from vital_relay.agent.tool_identity import mutation_operation_id


_JSON_VALUE_ADAPTER = TypeAdapter(JsonValue)
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)


class ToolGatewayErrorCode(StrEnum):
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    EXPIRED_CAPABILITY = "expired_capability"
    POLICY_MISMATCH = "policy_mismatch"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    RUN_FAILED_CLOSED = "run_failed_closed"
    INVALID_ARGUMENTS = "invalid_arguments"
    RAW_CREDENTIAL_ARGUMENT = "raw_credential_argument"
    HANDLER_FAILED = "handler_failed"
    INVALID_RESULT = "invalid_result"


class ToolGatewayError(Exception):
    """Bounded error safe to expose in a trace or model tool result."""

    def __init__(self, code: ToolGatewayErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class Clock(Protocol):
    def now(self) -> datetime:
        """Return an aware current time."""


class IdentifierFactory(Protocol):
    def __call__(self) -> UUID:
        """Return one new opaque identifier."""


ToolHandler = Callable[[BaseModel, ToolInvocationContext], JsonValue]


@dataclass(frozen=True, slots=True)
class ToolBinding:
    """One registered typed tool and its trusted implementation."""

    name: str
    description: str
    input_model: type[BaseModel]
    handler: ToolHandler
    effect: ToolEffect = ToolEffect.READ

    def definition(self) -> AgentToolDefinition:
        schema = self.input_model.model_json_schema()
        _reject_sensitive_schema(schema)
        return AgentToolDefinition(
            name=self.name,
            description=self.description,
            effect=self.effect,
            input_schema=schema,
        )


class BoundedToolGateway:
    """Explicit registry; unknown tools and credential-shaped data are denied."""

    def __init__(self, bindings: tuple[ToolBinding, ...]) -> None:
        registered: dict[str, ToolBinding] = {}
        for binding in bindings:
            definition = binding.definition()
            if definition.name in registered:
                raise ValueError(f"duplicate tool binding: {definition.name}")
            registered[definition.name] = binding
        self._bindings = registered

    def definitions(self) -> tuple[AgentToolDefinition, ...]:
        return tuple(binding.definition() for binding in self._bindings.values())

    def input_model_for(self, tool_name: str) -> type[BaseModel]:
        binding = self._bindings.get(tool_name)
        if binding is None:
            raise ToolGatewayError(ToolGatewayErrorCode.UNKNOWN_TOOL)
        return binding.input_model

    def effect_for(self, tool_name: str) -> ToolEffect:
        binding = self._bindings.get(tool_name)
        if binding is None:
            raise ToolGatewayError(ToolGatewayErrorCode.UNKNOWN_TOOL)
        return binding.effect

    def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: ToolInvocationContext,
    ) -> JsonValue:
        _reject_sensitive_values(arguments)
        binding = self._bindings.get(tool_name)
        if binding is None:
            raise ToolGatewayError(ToolGatewayErrorCode.UNKNOWN_TOOL)
        try:
            parsed = binding.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_ARGUMENTS) from exc
        try:
            result = binding.handler(parsed, context)
        except ToolGatewayError:
            raise
        except Exception as exc:
            # Adapter exceptions can contain credentials or provider response
            # bodies. Only the closed code crosses this boundary.
            raise ToolGatewayError(ToolGatewayErrorCode.HANDLER_FAILED) from exc
        try:
            return _JSON_VALUE_ADAPTER.validate_python(result)
        except ValidationError as exc:
            raise ToolGatewayError(ToolGatewayErrorCode.INVALID_RESULT) from exc


@dataclass(frozen=True, slots=True)
class RuntimeTool:
    """Framework-neutral callable converted to a LangChain tool at the edge."""

    definition: AgentToolDefinition
    input_model: type[BaseModel]
    invoke: Callable[[Mapping[str, object]], JsonValue]


class AuditedToolDispatcher:
    """Capture one normalized trace for every attempted model tool call."""

    def __init__(
        self,
        gateway: BoundedToolGateway,
        context: ToolInvocationContext,
        clock: Clock,
        identifier_factory: IdentifierFactory,
        policy_snapshot: CoordinationPolicySnapshot,
    ) -> None:
        self._gateway = gateway
        self._context = context
        self._clock = clock
        self._identifier_factory = identifier_factory
        self._policy = policy_snapshot
        self._trace: list[AgentToolTrace] = []
        self._total_calls = 0
        self._mutating_calls = 0
        self._calls_by_tool: dict[str, int] = {}
        self._failure_latched = False

        if context.policy_sha256 != policy_snapshot.sha256:
            raise ToolGatewayError(ToolGatewayErrorCode.POLICY_MISMATCH)
        if not set(context.allowed_tools).issubset(policy_snapshot.allowed_tools):
            raise ToolGatewayError(ToolGatewayErrorCode.POLICY_MISMATCH)
        for rule in policy_snapshot.tool_budget.tools:
            try:
                effect = gateway.effect_for(rule.name)
            except ToolGatewayError as exc:
                raise ValueError(f"unknown policy tool: {rule.name}") from exc
            if effect is not rule.effect:
                raise ValueError(f"policy effect mismatch for tool: {rule.name}")

    @property
    def trace(self) -> tuple[AgentToolTrace, ...]:
        return tuple(self._trace)

    def runtime_tools(self) -> tuple[RuntimeTool, ...]:
        tools: list[RuntimeTool] = []
        allowed = set(self._policy.allowed_tools).intersection(
            self._context.allowed_tools
        )
        for definition in self._gateway.definitions():
            if definition.name not in allowed:
                continue
            tools.append(
                RuntimeTool(
                    definition=definition,
                    input_model=self._gateway.input_model_for(definition.name),
                    invoke=lambda arguments, name=definition.name: self.invoke(
                        name,
                        arguments,
                    ),
                )
            )
        return tuple(tools)

    def invoke(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
    ) -> JsonValue:
        started_at = self._clock.now()
        tool_call_id = self._identifier_factory()
        trace_arguments: dict[str, JsonValue] = {}
        try:
            if self._failure_latched:
                raise ToolGatewayError(ToolGatewayErrorCode.RUN_FAILED_CLOSED)
            self._authorize(tool_name, started_at)
            _reject_sensitive_values(arguments)
            try:
                parsed_arguments = self._gateway.input_model_for(
                    tool_name
                ).model_validate(dict(arguments))
            except ValidationError as exc:
                raise ToolGatewayError(
                    ToolGatewayErrorCode.INVALID_ARGUMENTS
                ) from exc
            trace_arguments = TypeAdapter(dict[str, JsonValue]).validate_python(
                parsed_arguments.model_dump(mode="json")
            )
            operation_id = (
                mutation_operation_id(
                    self._context.run_id,
                    tool_name,
                    trace_arguments,
                )
                if self._gateway.effect_for(tool_name) is ToolEffect.MUTATE
                else tool_call_id
            )
            result = self._gateway.invoke(
                tool_name,
                trace_arguments,
                self._context.for_tool_call(operation_id),
            )
        except ToolGatewayError as exc:
            status = (
                ToolTraceStatus.DENIED
                if exc.code
                in {
                    ToolGatewayErrorCode.UNKNOWN_TOOL,
                    ToolGatewayErrorCode.TOOL_NOT_ALLOWED,
                    ToolGatewayErrorCode.EXPIRED_CAPABILITY,
                    ToolGatewayErrorCode.POLICY_MISMATCH,
                    ToolGatewayErrorCode.TOOL_BUDGET_EXCEEDED,
                    ToolGatewayErrorCode.RUN_FAILED_CLOSED,
                    ToolGatewayErrorCode.INVALID_ARGUMENTS,
                    ToolGatewayErrorCode.RAW_CREDENTIAL_ARGUMENT,
                }
                else ToolTraceStatus.FAILED
            )
            self._trace.append(
                AgentToolTrace(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    arguments=(
                        {}
                        if exc.code is ToolGatewayErrorCode.RAW_CREDENTIAL_ARGUMENT
                        else trace_arguments
                    ),
                    status=status,
                    started_at=started_at,
                    finished_at=self._clock.now(),
                    error_code=exc.code.value,
                )
            )
            self._failure_latched = True
            raise
        self._trace.append(
            AgentToolTrace(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=trace_arguments,
                status=ToolTraceStatus.COMPLETED,
                started_at=started_at,
                finished_at=self._clock.now(),
                result=result,
            )
        )
        return result

    def _authorize(self, tool_name: str, now: datetime) -> None:
        if now >= self._context.expires_at:
            raise ToolGatewayError(ToolGatewayErrorCode.EXPIRED_CAPABILITY)
        if tool_name not in self._context.allowed_tools:
            raise ToolGatewayError(ToolGatewayErrorCode.TOOL_NOT_ALLOWED)
        rule = self._policy.tool_rule(tool_name)
        if rule is None:
            raise ToolGatewayError(ToolGatewayErrorCode.TOOL_NOT_ALLOWED)
        if self._total_calls >= self._policy.tool_budget.max_total_calls:
            raise ToolGatewayError(ToolGatewayErrorCode.TOOL_BUDGET_EXCEEDED)
        if self._calls_by_tool.get(tool_name, 0) >= rule.max_calls:
            raise ToolGatewayError(ToolGatewayErrorCode.TOOL_BUDGET_EXCEEDED)
        if (
            rule.effect is ToolEffect.MUTATE
            and self._mutating_calls >= self._policy.tool_budget.max_mutating_calls
        ):
            raise ToolGatewayError(ToolGatewayErrorCode.TOOL_BUDGET_EXCEEDED)

        self._total_calls += 1
        self._calls_by_tool[tool_name] = self._calls_by_tool.get(tool_name, 0) + 1
        if rule.effect is ToolEffect.MUTATE:
            self._mutating_calls += 1


def _reject_sensitive_schema(value: object) -> None:
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            for key in properties:
                if _is_sensitive_key(str(key)):
                    raise ValueError(
                        "agent tool schemas cannot contain credential-shaped fields"
                    )
        for nested in value.values():
            _reject_sensitive_schema(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_sensitive_schema(nested)


def _reject_sensitive_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _is_sensitive_key(str(key)):
                raise ToolGatewayError(
                    ToolGatewayErrorCode.RAW_CREDENTIAL_ARGUMENT
                )
            _reject_sensitive_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_sensitive_values(nested)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)
