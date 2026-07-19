"""Authenticated persona-session and role-scoped discovery contracts.

Persona selection is presentation state, not authority.  Every authenticated
principal carries exactly the subject identity allowed by its persona, and an
active-incident list can expose responder invitation metadata only to a
responder session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from vital_relay.domain.dispatch import InvitationStatus
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentKind, IncidentState


class Persona(StrEnum):
    """A server-authorized capability boundary in the single native app."""

    COMMUNITY = "community"
    RESPONDER = "responder"
    COMMAND = "command"


class PersonaSessionRevocationStatus(StrEnum):
    """Idempotent result of revoking a persona session."""

    REVOKED = "revoked"
    ALREADY_REVOKED = "already_revoked"


class PersonaAccountView(BaseModel):
    """The durable account identity and its single authorized persona."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    account_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    persona: Persona
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    responder_id: UUID | None = None

    @model_validator(mode="after")
    def validate_persona_subject(self) -> PersonaAccountView:
        _validate_persona_subject(
            persona=self.persona,
            user_id=self.user_id,
            responder_id=self.responder_id,
        )
        return self


class PersonaSessionCreateRequest(BaseModel):
    """Bind a pre-authorized persona account to one native installation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    installation_id: UUID


class PersonaSessionReceipt(BaseModel):
    """One-time credential-bearing receipt for a newly created session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    session_id: UUID
    account: PersonaAccountView
    installation_id: UUID
    access_token: str = Field(
        min_length=43,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )
    refresh_token: str = Field(
        min_length=43,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )
    issued_at: AwareDatetime
    rotated_at: AwareDatetime
    access_expires_at: AwareDatetime
    refresh_expires_at: AwareDatetime

    @field_validator(
        "issued_at",
        "rotated_at",
        "access_expires_at",
        "refresh_expires_at",
    )
    @classmethod
    def normalize_session_times_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_session_lifetime(self) -> PersonaSessionReceipt:
        _validate_session_times(
            issued_at=self.issued_at,
            rotated_at=self.rotated_at,
            access_expires_at=self.access_expires_at,
            refresh_expires_at=self.refresh_expires_at,
        )
        if self.access_token == self.refresh_token:
            raise ValueError("access_token and refresh_token must be distinct")
        return self


class PersonaSessionView(BaseModel):
    """Restorable session metadata that never re-discloses bearer tokens."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    session_id: UUID
    account: PersonaAccountView
    installation_id: UUID
    issued_at: AwareDatetime
    rotated_at: AwareDatetime
    access_expires_at: AwareDatetime
    refresh_expires_at: AwareDatetime

    @field_validator(
        "issued_at",
        "rotated_at",
        "access_expires_at",
        "refresh_expires_at",
    )
    @classmethod
    def normalize_session_times_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_session_lifetime(self) -> PersonaSessionView:
        _validate_session_times(
            issued_at=self.issued_at,
            rotated_at=self.rotated_at,
            access_expires_at=self.access_expires_at,
            refresh_expires_at=self.refresh_expires_at,
        )
        return self


class PersonaSessionRotateRequest(BaseModel):
    """Proof that a refresh belongs to the installation that created it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    installation_id: UUID


class PersonaSessionRotationReceipt(BaseModel):
    """A rotated access credential; the refresh credential is never echoed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    session_id: UUID
    access_token: str = Field(
        min_length=43,
        max_length=256,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
    )
    rotated_at: AwareDatetime
    access_expires_at: AwareDatetime

    @field_validator("rotated_at", "access_expires_at")
    @classmethod
    def normalize_rotation_times_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_rotation_lifetime(self) -> PersonaSessionRotationReceipt:
        if self.access_expires_at <= self.rotated_at:
            raise ValueError("access_expires_at must follow rotated_at")
        return self


class PersonaSessionRevocationReceipt(BaseModel):
    """Stable server-timed result for idempotent logout/session revocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    session_id: UUID
    status: PersonaSessionRevocationStatus
    revoked_at: AwareDatetime

    @field_validator("revoked_at")
    @classmethod
    def normalize_revoked_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class ActiveIncidentSummary(BaseModel):
    """A redacted active incident with optional responder invitation identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    kind: IncidentKind
    state: Literal[
        IncidentState.VERIFYING,
        IncidentState.ESCALATING,
        IncidentState.RESPONSE_ACTIVE,
    ]
    state_version: int = Field(ge=1)
    updated_at: AwareDatetime
    invitation_id: UUID | None = None
    invitation_status: Literal[
        InvitationStatus.PENDING,
        InvitationStatus.ACCEPTED,
    ] | None = None

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_invitation_state(self) -> ActiveIncidentSummary:
        if (self.invitation_id is None) != (self.invitation_status is None):
            raise ValueError(
                "invitation_id and invitation_status must both be present or null"
            )
        if self.invitation_status is InvitationStatus.PENDING:
            if self.state is not IncidentState.ESCALATING:
                raise ValueError("a pending invitation requires an escalating incident")
        elif self.invitation_status is InvitationStatus.ACCEPTED:
            if self.state is not IncidentState.RESPONSE_ACTIVE:
                raise ValueError(
                    "an accepted invitation requires a response_active incident"
                )
        return self


class ActiveIncidentList(BaseModel):
    """Server-timed active incidents filtered for one authenticated persona."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    persona: Persona
    incidents: tuple[ActiveIncidentSummary, ...] = Field(max_length=100)
    server_received_at: AwareDatetime

    @field_validator("server_received_at")
    @classmethod
    def normalize_received_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_role_scoped_incidents(self) -> ActiveIncidentList:
        incident_ids = [incident.incident_id for incident in self.incidents]
        if len(incident_ids) != len(set(incident_ids)):
            raise ValueError("active incident IDs must be unique")

        for incident in self.incidents:
            has_invitation = incident.invitation_id is not None
            if self.persona is Persona.RESPONDER and not has_invitation:
                raise ValueError(
                    "responder discovery requires invitation metadata"
                )
            if self.persona is not Persona.RESPONDER and has_invitation:
                raise ValueError(
                    "only responder discovery can include invitation metadata"
                )
            if incident.updated_at > self.server_received_at:
                raise ValueError("incident updated_at cannot follow server_received_at")
        return self


class PersonaPrincipal(BaseModel):
    """Internal authenticated identity passed from transport to application code."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    scope_id: UUID
    session_id: UUID
    account_id: UUID
    installation_id: UUID
    persona: Persona
    user_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    responder_id: UUID | None = None

    @model_validator(mode="after")
    def validate_persona_subject(self) -> PersonaPrincipal:
        _validate_persona_subject(
            persona=self.persona,
            user_id=self.user_id,
            responder_id=self.responder_id,
        )
        return self


def _validate_persona_subject(
    *,
    persona: Persona,
    user_id: str | None,
    responder_id: UUID | None,
) -> None:
    if persona is Persona.COMMUNITY:
        if user_id is None or responder_id is not None:
            raise ValueError("community accounts require only user_id")
    elif persona is Persona.RESPONDER:
        if responder_id is None or user_id is not None:
            raise ValueError("responder accounts require only responder_id")
    elif user_id is not None or responder_id is not None:
        raise ValueError("command accounts cannot include a user or responder subject")


def _validate_session_times(
    *,
    issued_at: datetime,
    rotated_at: datetime,
    access_expires_at: datetime,
    refresh_expires_at: datetime,
) -> None:
    if rotated_at < issued_at:
        raise ValueError("rotated_at cannot precede issued_at")
    if access_expires_at <= rotated_at:
        raise ValueError("access_expires_at must follow rotated_at")
    if refresh_expires_at <= access_expires_at:
        raise ValueError("refresh_expires_at must follow access_expires_at")
