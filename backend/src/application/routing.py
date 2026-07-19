"""Typed routing port for accepted responder dispatch."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from vital_relay.domain.dispatch import (
    AEDSiteView,
    Coordinate,
    RouteFallbackReason,
    RoutePlan,
)
from vital_relay.domain.incidents import GeoLocation


class RoutingProvider(Protocol):
    """Build a route without owning incident authorization or persistence."""

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
    ) -> RoutePlan:
        """Return a validated route for one already-authorized dispatch."""


class LiveRoutingError(RuntimeError):
    """A bounded live-provider failure that is safe to map to static fallback."""

    fallback_reason: RouteFallbackReason


class LiveRoutingTimeoutError(LiveRoutingError):
    fallback_reason = RouteFallbackReason.PROVIDER_TIMEOUT


class LiveRoutingUnavailableError(LiveRoutingError):
    fallback_reason = RouteFallbackReason.PROVIDER_UNAVAILABLE


class LiveRoutingInvalidResponseError(LiveRoutingError):
    fallback_reason = RouteFallbackReason.PROVIDER_INVALID_RESPONSE
