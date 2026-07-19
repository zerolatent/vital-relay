"""Privacy-bounded responder discovery and dispatch contracts.

The command coordination model deliberately cannot carry wearer coordinates.
Exact wearer location and route geometry exist only in ``AcceptedDispatchView``,
which is produced after an invited responder accepts an escalating incident.
Health context is not an input to responder eligibility or dispatch state.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from math import atan2, cos, isclose, radians, sin, sqrt
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import (
    GeoLocation,
    IncidentKind,
    IncidentState,
    IncidentTransition,
    IncidentTrigger,
)


class DistanceBand(StrEnum):
    """Coarse proximity disclosed before a responder accepts."""

    WITHIN_100_M = "within_100_m"
    M_100_TO_250 = "100_to_250_m"
    M_250_TO_500 = "250_to_500_m"
    M_500_TO_1000 = "500_to_1000_m"
    M_1000_TO_2000 = "1000_to_2000_m"


class ResponderRole(StrEnum):
    VENUE_STAFF = "venue_staff"
    TRAINED_VOLUNTEER = "trained_volunteer"
    MEDICAL_PROFESSIONAL = "medical_professional"


class ResponderSkill(StrEnum):
    FIRST_AID = "first_aid"
    CPR = "cpr"
    AED = "aed"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    DECLINED = "declined"
    ACCEPTED = "accepted"


class ResponderDecision(StrEnum):
    DECLINE = "decline"
    ACCEPT = "accept"


class ResponderDecisionResultStatus(StrEnum):
    ACCEPTED = "accepted"
    ALREADY_PROCESSED = "already_processed"


class RouteLegType(StrEnum):
    RESPONDER_TO_AED = "responder_to_aed"
    AED_TO_WEARER = "aed_to_wearer"


class RouteProvider(StrEnum):
    MAPBOX_DIRECTIONS = "mapbox_directions"
    STATIC_VENUE = "static_venue"


class RouteSource(StrEnum):
    LIVE_DIRECTIONS = "live_directions"
    STATIC_FALLBACK = "static_fallback"


class RouteFallbackReason(StrEnum):
    PROVIDER_NOT_CONFIGURED = "provider_not_configured"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"


class Coordinate(BaseModel):
    """A public or accepted-dispatch coordinate without capture metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    latitude: FiniteFloat = Field(ge=-90.0, le=90.0)
    longitude: FiniteFloat = Field(ge=-180.0, le=180.0)


class ResponderCandidateView(BaseModel):
    """An eligible responder with coarse proximity and no exact location."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    responder_id: UUID
    display_name: str = Field(min_length=1, max_length=128)
    role: ResponderRole
    skills: tuple[ResponderSkill, ...] = Field(min_length=1, max_length=8)
    rank: int = Field(ge=1)
    distance_band: DistanceBand
    available: Literal[True]
    location_updated_at: AwareDatetime

    @field_validator("location_updated_at")
    @classmethod
    def normalize_location_updated_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_unique_skills(self) -> ResponderCandidateView:
        if len(self.skills) != len(set(self.skills)):
            raise ValueError("responder skills must be unique")
        return self


class AEDSiteView(BaseModel):
    """Public data for one available, statically seeded AED site."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    schema_version: Literal[SCHEMA_VERSION]
    aed_site_id: UUID
    name: str = Field(min_length=1, max_length=160)
    coordinate: Coordinate
    location_description: str = Field(min_length=1, max_length=256)
    access_instructions: str = Field(min_length=1, max_length=512)
    publicly_accessible: bool
    available: Literal[True]
    availability_confirmed_at: AwareDatetime | None

    @field_validator("availability_confirmed_at")
    @classmethod
    def normalize_availability_time_to_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class ResponderInvitationView(BaseModel):
    """One durable invitation and its terminal responder decision, if any."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    invitation_id: UUID
    incident_id: UUID
    sequence: int = Field(ge=1)
    responder: ResponderCandidateView
    status: InvitationStatus
    invited_at: AwareDatetime
    responded_at: AwareDatetime | None
    decision_id: UUID | None

    @field_validator("invited_at", "responded_at")
    @classmethod
    def normalize_invitation_times_to_utc(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @model_validator(mode="after")
    def validate_status_metadata(self) -> ResponderInvitationView:
        pending = self.status is InvitationStatus.PENDING
        if pending and (self.responded_at is not None or self.decision_id is not None):
            raise ValueError("pending invitations cannot include response metadata")
        if not pending and (self.responded_at is None or self.decision_id is None):
            raise ValueError("terminal invitations require response metadata")
        if self.responded_at is not None and self.responded_at < self.invited_at:
            raise ValueError("responded_at cannot precede invited_at")
        return self


class ResponderIncidentView(BaseModel):
    """Current incident state plus only the authenticated responder's invitation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    kind: IncidentKind
    state: IncidentState
    state_version: int = Field(ge=1)
    updated_at: AwareDatetime
    invitation: ResponderInvitationView

    @field_validator("updated_at")
    @classmethod
    def normalize_updated_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_invitation_link(self) -> ResponderIncidentView:
        if self.invitation.incident_id != self.incident_id:
            raise ValueError("invitation incident_id must match responder incident")
        return self


class DispatchCoordinationView(BaseModel):
    """Command coordination data with no wearer-location field by design."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    state: Literal[IncidentState.ESCALATING, IncidentState.RESPONSE_ACTIVE]
    search_radius_m: FiniteFloat = Field(gt=0.0, le=20_000.0)
    search_started_at: AwareDatetime
    candidates: tuple[ResponderCandidateView, ...] = Field(max_length=50)
    invitations: tuple[ResponderInvitationView, ...] = Field(max_length=50)
    nearest_aed: AEDSiteView
    accepted_responder_id: UUID | None
    updated_at: AwareDatetime
    simulated: Literal[False]

    @field_validator("search_started_at", "updated_at")
    @classmethod
    def normalize_coordination_times_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_coordination_links(self) -> DispatchCoordinationView:
        if self.updated_at < self.search_started_at:
            raise ValueError("updated_at cannot precede search_started_at")

        candidate_ids = [candidate.responder_id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("responder candidates must be unique")
        ranks = [candidate.rank for candidate in self.candidates]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("responder candidates must have contiguous ordered ranks")

        invitation_ids = [invitation.invitation_id for invitation in self.invitations]
        if len(invitation_ids) != len(set(invitation_ids)):
            raise ValueError("invitation IDs must be unique")
        invitation_responder_ids = [
            invitation.responder.responder_id for invitation in self.invitations
        ]
        if len(invitation_responder_ids) != len(set(invitation_responder_ids)):
            raise ValueError("a responder can have only one incident invitation")
        sequences = [invitation.sequence for invitation in self.invitations]
        if sequences != list(range(1, len(sequences) + 1)):
            raise ValueError("invitations must have contiguous ordered sequences")

        candidates_by_id = {
            candidate.responder_id: candidate for candidate in self.candidates
        }
        for invitation in self.invitations:
            candidate = candidates_by_id.get(invitation.responder.responder_id)
            if candidate is None or candidate != invitation.responder:
                raise ValueError("every invitation must reference its candidate snapshot")
            if invitation.incident_id != self.incident_id:
                raise ValueError("every invitation incident_id must match coordination")

        accepted = [
            invitation
            for invitation in self.invitations
            if invitation.status is InvitationStatus.ACCEPTED
        ]
        if len(accepted) > 1:
            raise ValueError("an incident can have at most one accepted responder")
        if self.state is IncidentState.ESCALATING:
            if accepted or self.accepted_responder_id is not None:
                raise ValueError("escalating coordination cannot have an accepted responder")
        else:
            if len(accepted) != 1:
                raise ValueError("response_active coordination requires one acceptance")
            if accepted[0].responder.responder_id != self.accepted_responder_id:
                raise ValueError("accepted_responder_id must match the accepted invitation")
        return self


class ResponderDecisionRequest(BaseModel):
    """One idempotent decision for a specific pending invitation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    decision_id: UUID
    invitation_id: UUID
    decision: ResponderDecision
    responded_at: AwareDatetime

    @field_validator("responded_at")
    @classmethod
    def normalize_responded_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class RouteLeg(BaseModel):
    """One validated leg and its ordered walking geometry."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    sequence: int = Field(ge=1)
    leg_type: RouteLegType
    origin: Coordinate
    destination: Coordinate
    instruction: str = Field(min_length=1, max_length=512)
    distance_m: FiniteFloat = Field(ge=0.0, le=100_000.0)
    estimated_duration_seconds: int = Field(ge=0, le=86_400)
    geometry: tuple[Coordinate, ...] = Field(min_length=2, max_length=20_000)


class RoutePlan(BaseModel):
    """A durable two-leg responder-to-AED-to-wearer walking route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    route_id: UUID
    source: RouteSource
    provider: RouteProvider
    fallback_reason: RouteFallbackReason | None
    travel_mode: Literal["walking"]
    aed_site_id: UUID
    created_at: AwareDatetime
    legs: tuple[RouteLeg, RouteLeg] = Field(min_length=2, max_length=2)
    total_distance_m: FiniteFloat = Field(ge=0.0, le=200_000.0)
    estimated_duration_seconds: int = Field(ge=0, le=172_800)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_static_route(cls, value: object) -> object:
        """Upgrade the pre-ROUTE-01 static v1 shape before strict validation."""

        if not isinstance(value, Mapping):
            return value
        if "source" in value or "fallback_reason" in value:
            return value
        if value.get("provider") not in {
            RouteProvider.STATIC_VENUE,
            RouteProvider.STATIC_VENUE.value,
        }:
            return value

        normalized = dict(value)
        normalized["source"] = RouteSource.STATIC_FALLBACK
        normalized["fallback_reason"] = (
            RouteFallbackReason.PROVIDER_NOT_CONFIGURED
        )
        raw_legs = value.get("legs")
        if isinstance(raw_legs, (list, tuple)):
            normalized_legs: list[object] = []
            for raw_leg in raw_legs:
                if not isinstance(raw_leg, Mapping):
                    normalized_legs.append(raw_leg)
                    continue
                leg = dict(raw_leg)
                if (
                    "geometry" not in leg
                    and "origin" in leg
                    and "destination" in leg
                ):
                    leg["geometry"] = (leg["origin"], leg["destination"])
                normalized_legs.append(leg)
            normalized["legs"] = normalized_legs
        return normalized

    @field_validator("created_at")
    @classmethod
    def normalize_created_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_ordered_route(self) -> RoutePlan:
        if self.source is RouteSource.LIVE_DIRECTIONS:
            if self.provider is not RouteProvider.MAPBOX_DIRECTIONS:
                raise ValueError("live directions require the Mapbox provider")
            if self.fallback_reason is not None:
                raise ValueError("live directions cannot include a fallback reason")
        else:
            if self.provider is not RouteProvider.STATIC_VENUE:
                raise ValueError("static fallback requires the static venue provider")
            if self.fallback_reason is None:
                raise ValueError("static fallback requires an explicit reason")

        if [leg.sequence for leg in self.legs] != [1, 2]:
            raise ValueError("route legs must be ordered 1, 2")
        if self.legs[0].leg_type is not RouteLegType.RESPONDER_TO_AED:
            raise ValueError("the first route leg must lead from responder to AED")
        if self.legs[1].leg_type is not RouteLegType.AED_TO_WEARER:
            raise ValueError("the second route leg must lead from AED to wearer")
        if self.legs[0].destination != self.legs[1].origin:
            raise ValueError("route legs must meet at the same AED coordinate")

        maximum_anchor_offset_m = (
            100.0 if self.source is RouteSource.LIVE_DIRECTIONS else 0.01
        )
        for leg in self.legs:
            if (
                _coordinate_distance_m(leg.origin, leg.geometry[0])
                > maximum_anchor_offset_m
                or _coordinate_distance_m(leg.destination, leg.geometry[-1])
                > maximum_anchor_offset_m
            ):
                raise ValueError("route geometry must remain anchored to its leg")
        maximum_join_offset_m = (
            25.0 if self.source is RouteSource.LIVE_DIRECTIONS else 0.01
        )
        if (
            _coordinate_distance_m(
                self.legs[0].geometry[-1],
                self.legs[1].geometry[0],
            )
            > maximum_join_offset_m
        ):
            raise ValueError("route geometry must remain ordered through the AED")

        distance_sum = sum(leg.distance_m for leg in self.legs)
        if not isclose(self.total_distance_m, distance_sum, abs_tol=0.01):
            raise ValueError("total_distance_m must equal the route-leg sum")
        duration_sum = sum(leg.estimated_duration_seconds for leg in self.legs)
        if self.estimated_duration_seconds != duration_sum:
            raise ValueError(
                "estimated_duration_seconds must equal the route-leg sum"
            )
        return self


# Compatibility imports remain available while callers move from static-only
# naming to the route contract that now represents live and fallback sources.
StaticRouteLegType = RouteLegType
StaticRouteLeg = RouteLeg
StaticRoutePlan = RoutePlan


class AcceptedDispatchView(BaseModel):
    """Bounded view released only to the responder who accepted the incident."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    incident_id: UUID
    state: Literal[IncidentState.RESPONSE_ACTIVE]
    invitation: ResponderInvitationView
    wearer_location: GeoLocation
    aed: AEDSiteView
    route: RoutePlan
    activated_at: AwareDatetime
    simulated: Literal[False]

    @field_validator("activated_at")
    @classmethod
    def normalize_activated_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_dispatch_links(self) -> AcceptedDispatchView:
        if self.invitation.incident_id != self.incident_id:
            raise ValueError("invitation incident_id must match dispatch")
        if self.invitation.status is not InvitationStatus.ACCEPTED:
            raise ValueError("accepted dispatch requires an accepted invitation")
        if self.route.aed_site_id != self.aed.aed_site_id:
            raise ValueError("route aed_site_id must match dispatch AED")
        if self.route.legs[0].destination != self.aed.coordinate:
            raise ValueError("the first route leg must end at the assigned AED")
        if self.route.legs[1].origin != self.aed.coordinate:
            raise ValueError("the second route leg must start at the assigned AED")
        wearer_coordinate = Coordinate(
            latitude=self.wearer_location.latitude,
            longitude=self.wearer_location.longitude,
        )
        if self.route.legs[1].destination != wearer_coordinate:
            raise ValueError("the final route leg must end at the wearer location")
        if self.activated_at < self.invitation.responded_at:  # type: ignore[operator]
            raise ValueError("activated_at cannot precede responder acceptance")
        return self


class ResponderDecisionResult(BaseModel):
    """Stable decline or acceptance result for idempotent responder decisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    decision_id: UUID
    status: ResponderDecisionResultStatus
    decision: ResponderDecision
    invitation: ResponderInvitationView
    transition: IncidentTransition | None
    coordination: DispatchCoordinationView | None
    accepted_dispatch: AcceptedDispatchView | None
    server_received_at: AwareDatetime

    @field_validator("server_received_at")
    @classmethod
    def normalize_server_received_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_decision_result(self) -> ResponderDecisionResult:
        if self.invitation.decision_id != self.decision_id:
            raise ValueError("invitation decision_id must match result")

        if self.decision is ResponderDecision.DECLINE:
            if self.invitation.status is not InvitationStatus.DECLINED:
                raise ValueError("decline result requires a declined invitation")
            if self.transition is not None:
                raise ValueError("decline cannot include an incident transition")
            if self.coordination is None or self.accepted_dispatch is not None:
                raise ValueError("decline returns coordination only")
            if self.coordination.incident_id != self.invitation.incident_id:
                raise ValueError("coordination incident_id must match invitation")
        else:
            if self.invitation.status is not InvitationStatus.ACCEPTED:
                raise ValueError("accept result requires an accepted invitation")
            if self.coordination is not None or self.accepted_dispatch is None:
                raise ValueError("accept returns accepted_dispatch only")
            if self.transition is None:
                raise ValueError("accept result requires an incident transition")
            if self.transition.incident_id != self.invitation.incident_id:
                raise ValueError("transition incident_id must match invitation")
            if self.transition.trigger is not IncidentTrigger.RESPONDER_ACCEPTED:
                raise ValueError("accept transition requires responder_accepted trigger")
            if self.transition.to_state is not IncidentState.RESPONSE_ACTIVE:
                raise ValueError("accept transition must activate response")
            if self.accepted_dispatch.incident_id != self.invitation.incident_id:
                raise ValueError("accepted dispatch incident_id must match invitation")
        return self


class ResponderDecisionReceiptView(BaseModel):
    """Privacy-bounded responder receipt with no command coordination field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[SCHEMA_VERSION]
    decision_id: UUID
    status: ResponderDecisionResultStatus
    decision: ResponderDecision
    invitation: ResponderInvitationView
    transition: IncidentTransition | None
    accepted_dispatch: AcceptedDispatchView | None
    server_received_at: AwareDatetime

    @field_validator("server_received_at")
    @classmethod
    def normalize_server_received_at_to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_receipt(self) -> ResponderDecisionReceiptView:
        if self.invitation.decision_id != self.decision_id:
            raise ValueError("invitation decision_id must match receipt")

        if self.decision is ResponderDecision.DECLINE:
            if self.invitation.status is not InvitationStatus.DECLINED:
                raise ValueError("decline receipt requires a declined invitation")
            if self.transition is not None or self.accepted_dispatch is not None:
                raise ValueError(
                    "decline receipt cannot include a transition or accepted dispatch"
                )
        else:
            if self.invitation.status is not InvitationStatus.ACCEPTED:
                raise ValueError("accept receipt requires an accepted invitation")
            if self.transition is None or self.accepted_dispatch is None:
                raise ValueError(
                    "accept receipt requires a transition and accepted dispatch"
                )
            if self.transition.incident_id != self.invitation.incident_id:
                raise ValueError("transition incident_id must match invitation")
            if self.transition.trigger is not IncidentTrigger.RESPONDER_ACCEPTED:
                raise ValueError("accept receipt requires responder_accepted trigger")
            if self.transition.to_state is not IncidentState.RESPONSE_ACTIVE:
                raise ValueError("accept receipt transition must activate response")
            if self.accepted_dispatch.incident_id != self.invitation.incident_id:
                raise ValueError("accepted dispatch incident_id must match invitation")
        return self

    @classmethod
    def from_result(cls, result: ResponderDecisionResult) -> Self:
        """Map an internal workflow result without serializing command coordination."""

        return cls(
            schema_version=result.schema_version,
            decision_id=result.decision_id,
            status=result.status,
            decision=result.decision,
            invitation=result.invitation,
            transition=result.transition,
            accepted_dispatch=result.accepted_dispatch,
            server_received_at=result.server_received_at,
        )


def _coordinate_distance_m(first: Coordinate, second: Coordinate) -> float:
    """Return the great-circle distance used for route-anchor validation."""

    earth_radius_m = 6_371_008.8
    first_latitude = radians(first.latitude)
    second_latitude = radians(second.latitude)
    latitude_delta = second_latitude - first_latitude
    longitude_delta = radians(second.longitude - first.longitude)
    haversine = sin(latitude_delta / 2.0) ** 2 + (
        cos(first_latitude)
        * cos(second_latitude)
        * sin(longitude_delta / 2.0) ** 2
    )
    return earth_radius_m * 2.0 * atan2(
        sqrt(haversine),
        sqrt(max(0.0, 1.0 - haversine)),
    )


def distance_band_for_meters(distance_m: float) -> DistanceBand:
    """Redact an internal exact PostGIS distance into the public coarse band."""

    if distance_m < 0:
        raise ValueError("distance_m cannot be negative")
    if distance_m <= 100:
        return DistanceBand.WITHIN_100_M
    if distance_m <= 250:
        return DistanceBand.M_100_TO_250
    if distance_m <= 500:
        return DistanceBand.M_250_TO_500
    if distance_m <= 1_000:
        return DistanceBand.M_500_TO_1000
    if distance_m <= 2_000:
        return DistanceBand.M_1000_TO_2000
    raise ValueError("eligible responder distance exceeds the 2000 m disclosure band")
