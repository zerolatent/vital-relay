"""Opaque, run-scoped authority for the internal agent tool proxy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from vital_relay.agent.capability_runtime import (
    MAX_CAPABILITY_TOKEN_LENGTH,
    SCOPE_ID_PATTERN,
    ToolInvocationContext,
)
from vital_relay.agent.contracts import SHA256_PATTERN, TOOL_NAME_PATTERN


CAPABILITY_TOKEN_VERSION = 1
TOOL_PROXY_AUDIENCE = "vital-relay-tool-proxy"
MAX_CAPABILITY_LIFETIME = timedelta(minutes=15)


class CapabilityErrorCode(StrEnum):
    INVALID_CAPABILITY = "invalid_capability"
    EXPIRED_CAPABILITY = "expired_capability"


class CapabilityError(Exception):
    """Authentication failure with a closed code and no token material."""

    def __init__(self, code: CapabilityErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class ToolCapabilityClaims(BaseModel):
    """Signed immutable claims carried by an opaque capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token_version: Literal[CAPABILITY_TOKEN_VERSION]
    audience: Literal[TOOL_PROXY_AUDIENCE]
    scope_id: str = Field(pattern=SCOPE_ID_PATTERN)
    run_id: UUID
    incident_id: UUID
    state_version: int = Field(ge=1)
    policy_sha256: str = Field(pattern=SHA256_PATTERN)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    allowed_tools: tuple[str, ...] = Field(min_length=1, max_length=20)

    @field_validator("issued_at", "expires_at")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("capability timestamps must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("allowed_tools")
    @classmethod
    def validate_allowed_tools(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("allowed capability tools must be unique")
        if tuple(sorted(value)) != value:
            raise ValueError("allowed capability tools must be canonically sorted")
        for name in value:
            from re import fullmatch

            if fullmatch(TOOL_NAME_PATTERN, name) is None:
                raise ValueError("invalid capability tool name")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0):
            raise ValueError("capability expires_at must follow issued_at")
        if lifetime > MAX_CAPABILITY_LIFETIME:
            raise ValueError("capability lifetime exceeds the maximum")
        return self

    @property
    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class ToolCapabilityAuthority:
    """Issue and authenticate HMAC-SHA256 capabilities for one proxy audience."""

    def __init__(self, signing_key: bytes) -> None:
        if len(signing_key) < 32:
            raise ValueError("capability signing key must contain at least 32 bytes")
        self._signing_key = bytes(signing_key)

    def issue(
        self,
        *,
        run_id: UUID,
        scope_id: str,
        incident_id: UUID,
        state_version: int,
        policy_sha256: str,
        allowed_tools: tuple[str, ...],
        issued_at: datetime,
        lifetime: timedelta,
    ) -> ToolInvocationContext:
        claims = ToolCapabilityClaims(
            token_version=CAPABILITY_TOKEN_VERSION,
            audience=TOOL_PROXY_AUDIENCE,
            scope_id=scope_id,
            run_id=run_id,
            incident_id=incident_id,
            state_version=state_version,
            policy_sha256=policy_sha256,
            issued_at=issued_at,
            expires_at=issued_at + lifetime,
            allowed_tools=tuple(sorted(allowed_tools)),
        )
        encoded_payload = _encode_base64url(claims.canonical_bytes)
        signed_input = f"v{CAPABILITY_TOKEN_VERSION}.{encoded_payload}".encode()
        signature = hmac.new(
            self._signing_key,
            signed_input,
            hashlib.sha256,
        ).digest()
        token = signed_input.decode() + "." + _encode_base64url(signature)
        return ToolInvocationContext(
            run_id=claims.run_id,
            scope_id=claims.scope_id,
            incident_id=claims.incident_id,
            state_version=claims.state_version,
            policy_sha256=claims.policy_sha256,
            issued_at=claims.issued_at,
            expires_at=claims.expires_at,
            allowed_tools=claims.allowed_tools,
            raw_capability=SecretStr(token),
        )

    def authenticate(
        self,
        raw_capability: str,
        *,
        now: datetime,
    ) -> ToolCapabilityClaims:
        """Authenticate a token with constant-time signature comparison."""

        try:
            if not raw_capability or len(raw_capability) > MAX_CAPABILITY_TOKEN_LENGTH:
                raise ValueError
            version, payload, encoded_signature = raw_capability.split(".")
            if version != f"v{CAPABILITY_TOKEN_VERSION}":
                raise ValueError
            supplied_signature = _decode_base64url(encoded_signature)
            signed_input = f"{version}.{payload}".encode()
            expected_signature = hmac.new(
                self._signing_key,
                signed_input,
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError
            raw_claims = json.loads(_decode_base64url(payload).decode("utf-8"))
            claims = ToolCapabilityClaims.model_validate(raw_claims)
        except Exception as exc:
            raise CapabilityError(CapabilityErrorCode.INVALID_CAPABILITY) from exc

        normalized_now = _aware_utc(now)
        if normalized_now < claims.issued_at:
            raise CapabilityError(CapabilityErrorCode.INVALID_CAPABILITY)
        if normalized_now >= claims.expires_at:
            raise CapabilityError(CapabilityErrorCode.EXPIRED_CAPABILITY)
        return claims


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_base64url(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        value + padding,
        altchars=b"-_",
        validate=True,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapabilityError(CapabilityErrorCode.INVALID_CAPABILITY)
    return value.astimezone(UTC)
