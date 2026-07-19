"""Sandbox-safe runtime context for invoking bounded agent tools.

This module deliberately contains no capability claims, signing primitive, or
authentication authority.  A worker may carry an already-issued opaque token,
but only the trusted host can import the authority that creates or verifies it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)

from vital_relay.agent.contracts import SHA256_PATTERN, TOOL_NAME_PATTERN


MAX_CAPABILITY_TOKEN_LENGTH = 8_192
SCOPE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ToolInvocationContext(BaseModel):
    """Closed runtime authority that is never built from model arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    scope_id: str = Field(pattern=SCOPE_ID_PATTERN)
    incident_id: UUID
    state_version: int = Field(ge=1)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    allowed_tools: tuple[str, ...] = Field(min_length=1, max_length=20)
    raw_capability: SecretStr = Field(exclude=True, repr=False)
    idempotency_key: UUID | None = Field(default=None, exclude=True, repr=False)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("invocation timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or tuple(sorted(value)) != value:
            raise ValueError("allowed invocation tools must be unique and sorted")
        import re

        if any(re.fullmatch(TOOL_NAME_PATTERN, name) is None for name in value):
            raise ValueError("invalid invocation tool name")
        return value

    @field_validator("expires_at")
    @classmethod
    def require_expiry_after_issue(
        cls,
        value: datetime,
        info,
    ) -> datetime:
        issued_at = info.data.get("issued_at")
        if issued_at is not None and value <= issued_at:
            raise ValueError("invocation expires_at must follow issued_at")
        return value

    def for_tool_call(self, tool_call_id: UUID) -> Self:
        """Attach trusted transport idempotency without exposing it to the model."""

        return self.model_copy(update={"idempotency_key": tool_call_id})
