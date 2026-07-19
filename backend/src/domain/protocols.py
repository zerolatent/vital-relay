"""Fixed, source-visible first-aid protocol presentation contracts.

The protocol selected for an incident is determined only by ``IncidentKind``.
These contracts intentionally contain no health values, diagnosis fields, or
generated text hooks.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import IncidentKind


class FirstAidProtocolSource(BaseModel):
    """One authoritative public source used to review fixed protocol wording."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    organization: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    url: HttpUrl


class FirstAidProtocolStep(BaseModel):
    """One ordered, concise instruction in a fixed protocol."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    sequence: int = Field(ge=1, le=12)
    title: str = Field(min_length=1, max_length=100)
    instruction: str = Field(min_length=1, max_length=600)


class FixedFirstAidProtocol(BaseModel):
    """An immutable, versioned protocol whose exact content is hash-addressed."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    protocol_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    emergency_kind: IncidentKind
    version: str = Field(
        min_length=5,
        max_length=32,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$",
    )
    title: str = Field(min_length=1, max_length=160)
    disclaimer: str = Field(min_length=1, max_length=500)
    sources: tuple[FirstAidProtocolSource, ...] = Field(min_length=1, max_length=4)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: tuple[FirstAidProtocolStep, ...] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def validate_order_and_sources(self) -> FixedFirstAidProtocol:
        expected_sequence = list(range(1, len(self.steps) + 1))
        if [step.sequence for step in self.steps] != expected_sequence:
            raise ValueError("protocol steps must have contiguous ordered sequences")

        source_urls = [str(source.url) for source in self.sources]
        if len(source_urls) != len(set(source_urls)):
            raise ValueError("protocol source URLs must be unique")
        return self


class ProtocolPresentationView(BaseModel):
    """The exact fixed protocol attached to one accepted responder assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    presentation_id: UUID
    assignment_id: UUID
    incident_id: UUID
    responder_id: UUID
    presented_at: AwareDatetime
    protocol: FixedFirstAidProtocol
    simulated: Literal[False]

    @field_validator("presented_at")
    @classmethod
    def normalize_presented_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
