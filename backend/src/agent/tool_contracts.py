"""Privacy-bounded contracts for the first internal coordination tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from vital_relay.domain.dispatch import (
    DistanceBand,
    InvitationStatus,
    ResponderRole,
    ResponderSkill,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentKind, IncidentState, TimelineEventType


GET_INCIDENT = "get_incident"
GET_INCIDENT_TIMELINE = "get_incident_timeline"
GET_DISPATCH_COORDINATION = "get_dispatch_coordination"
COORDINATE_DISPATCH = "coordinate_dispatch"
GET_FIXED_PROTOCOL = "get_fixed_protocol"
INITIAL_AGENT_TOOL_NAMES = (
    GET_INCIDENT,
    GET_INCIDENT_TIMELINE,
    GET_DISPATCH_COORDINATION,
    COORDINATE_DISPATCH,
    GET_FIXED_PROTOCOL,
)


class IncidentBoundToolInput(BaseModel):
    """Model-visible binding duplicated from authority for explicit validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    incident_id: UUID
    expected_state_version: int = Field(ge=1)


class TimelineToolInput(IncidentBoundToolInput):
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=50)


class AgentIncidentToolView(BaseModel):
    """Incident state without wearer identity, location, or health references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    kind: IncidentKind
    state: IncidentState
    state_version: int = Field(ge=1)
    opened_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("opened_at", "updated_at")
    @classmethod
    def normalize_times(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentTimelineEntry(BaseModel):
    """Observable timeline entry stripped of internal correlation identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    event_type: TimelineEventType
    occurred_at: AwareDatetime
    state: IncidentState
    summary: str = Field(min_length=1, max_length=256)

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentTimelineToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    state_version: int = Field(ge=1)
    entries: tuple[AgentTimelineEntry, ...] = Field(max_length=50)
    has_more: bool


class AgentInvitedResponderView(BaseModel):
    """Coarse responder attributes sufficient for coordination strategy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: ResponderRole
    skills: tuple[ResponderSkill, ...] = Field(min_length=1, max_length=8)
    distance_band: DistanceBand
    status: InvitationStatus


class AgentDispatchToolView(BaseModel):
    """Command projection with no exact responder or wearer location."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    state: Literal[IncidentState.ESCALATING, IncidentState.RESPONSE_ACTIVE]
    state_version: int = Field(ge=1)
    candidate_count: int = Field(ge=0, le=50)
    pending_invitation_count: int = Field(ge=0, le=50)
    declined_invitation_count: int = Field(ge=0, le=50)
    accepted_responder_present: bool
    nearest_aed_available: bool
    latest_invitation: AgentInvitedResponderView | None
    updated_at: AwareDatetime

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class AgentProtocolReferenceToolView(BaseModel):
    """Fixed-content identity without medical steps or source text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    state_version: int = Field(ge=1)
    presentation_id: UUID
    protocol_id: str
    protocol_version: str
    emergency_kind: IncidentKind
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str = Field(min_length=1, max_length=160)
    presented_at: AwareDatetime

    @field_validator("presented_at")
    @classmethod
    def normalize_presented_at(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
