"""Truthfully labelled static venue fallback routing."""

from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from vital_relay.domain.dispatch import (
    AEDSiteView,
    Coordinate,
    RouteFallbackReason,
    RouteLeg,
    RouteLegType,
    RoutePlan,
    RouteProvider,
    RouteSource,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import GeoLocation


WALKING_SPEED_M_PER_SECOND = 1.3


class StaticVenueRoutingProvider:
    """Create endpoint-only fallback legs from real persisted venue points."""

    def __init__(
        self,
        fallback_reason: RouteFallbackReason = (
            RouteFallbackReason.PROVIDER_NOT_CONFIGURED
        ),
    ) -> None:
        self._fallback_reason = fallback_reason

    def route(
        self,
        *,
        route_id: UUID,
        responder_location: Coordinate,
        aed: AEDSiteView,
        wearer_location: GeoLocation,
        responder_to_aed_distance_m: float,
        aed_to_wearer_distance_m: float,
        created_at: datetime,
        fallback_reason: RouteFallbackReason | None = None,
    ) -> RoutePlan:
        normalized_created_at = _utc(created_at)
        wearer_coordinate = Coordinate(
            latitude=wearer_location.latitude,
            longitude=wearer_location.longitude,
        )
        first_duration = _walking_seconds(responder_to_aed_distance_m)
        second_duration = _walking_seconds(aed_to_wearer_distance_m)
        first_leg = RouteLeg(
            sequence=1,
            leg_type=RouteLegType.RESPONDER_TO_AED,
            origin=responder_location,
            destination=aed.coordinate,
            instruction=(
                f"Proceed to {aed.name} at the mapped AED coordinate, then "
                "follow the AED access instructions in this dispatch."
            ),
            distance_m=responder_to_aed_distance_m,
            estimated_duration_seconds=first_duration,
            geometry=(responder_location, aed.coordinate),
        )
        second_leg = RouteLeg(
            sequence=2,
            leg_type=RouteLegType.AED_TO_WEARER,
            origin=aed.coordinate,
            destination=wearer_coordinate,
            instruction=(
                "Continue from the AED to the exact wearer marker in this "
                "accepted dispatch view."
            ),
            distance_m=aed_to_wearer_distance_m,
            estimated_duration_seconds=second_duration,
            geometry=(aed.coordinate, wearer_coordinate),
        )
        return RoutePlan(
            schema_version=SCHEMA_VERSION,
            route_id=route_id,
            source=RouteSource.STATIC_FALLBACK,
            provider=RouteProvider.STATIC_VENUE,
            fallback_reason=fallback_reason or self._fallback_reason,
            travel_mode="walking",
            aed_site_id=aed.aed_site_id,
            created_at=normalized_created_at,
            legs=(first_leg, second_leg),
            total_distance_m=(
                responder_to_aed_distance_m + aed_to_wearer_distance_m
            ),
            estimated_duration_seconds=first_duration + second_duration,
        )


def _walking_seconds(distance_m: float) -> int:
    if distance_m < 0:
        raise ValueError("route distances cannot be negative")
    return ceil(distance_m / WALKING_SPEED_M_PER_SECOND)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("route created_at must be timezone-aware")
    return value.astimezone(UTC)
