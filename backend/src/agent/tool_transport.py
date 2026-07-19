"""Sandbox-safe wire contracts for the internal agent tool proxy."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from vital_relay.agent.capability_runtime import SCOPE_ID_PATTERN
from vital_relay.agent.contracts import SHA256_PATTERN, TOOL_NAME_PATTERN


class ToolProxyErrorCode(StrEnum):
    INVALID_CAPABILITY = "invalid_capability"
    EXPIRED_CAPABILITY = "expired_capability"
    WRONG_RUN = "wrong_run"
    WRONG_SCOPE = "wrong_scope"
    WRONG_INCIDENT = "wrong_incident"
    POLICY_MISMATCH = "policy_mismatch"
    RUN_NOT_ACTIVE = "run_not_active"
    TOOL_NOT_REGISTERED = "tool_not_registered"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    TOOL_BUDGET_EXCEEDED = "tool_budget_exceeded"
    INVALID_ARGUMENTS = "invalid_arguments"
    STALE_STATE = "stale_state"
    INCIDENT_NOT_ACTIVE = "incident_not_active"
    IDEMPOTENCY_REQUIRED = "idempotency_required"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IDEMPOTENCY_CAPACITY_EXCEEDED = "idempotency_capacity_exceeded"
    IDEMPOTENCY_IN_DOUBT = "idempotency_in_doubt"
    APPLICATION_FAILED = "application_failed"
    INVALID_RESULT = "invalid_result"
    AUDIT_UNAVAILABLE = "audit_unavailable"


class ToolProxyInvocation(BaseModel):
    """Trusted transport envelope; idempotency is not a model argument."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    invocation_id: UUID
    scope_id: str = Field(pattern=SCOPE_ID_PATTERN)
    run_id: UUID
    incident_id: UUID
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    tool_name: str = Field(pattern=TOOL_NAME_PATTERN)
    arguments: dict[str, JsonValue]
    idempotency_key: UUID | None = None


class ToolProxyTransportSuccess(BaseModel):
    """Versioned success envelope shared by the host route and sandbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    result: JsonValue
