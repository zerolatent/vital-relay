"""Live-first routing with one explicit, truthful static fallback."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from vital_relay.adapters.static_routing import StaticVenueRoutingProvider
from vital_relay.application.routing import LiveRoutingError, RoutingProvider
from vital_relay.domain.dispatch import (
    AEDSiteView,
    Coordinate,
    RouteFallbackReason,
    RoutePlan,
)
from vital_relay.domain.incidents import GeoLocation


class LiveFirstRoutingProvider:
    """Use configured live directions and fall back only on bounded failures."""

    def __init__(
        self,
        live_provider: RoutingProvider | None,
        static_fallback: StaticVenueRoutingProvider | None = None,
    ) -> None:
        self._live_provider = live_provider
        self._static_fallback = static_fallback or StaticVenueRoutingProvider()

    def close(self) -> None:
        close = getattr(self._live_provider, "close", None)
        if callable(close):
            close()

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
        if self._live_provider is None:
            fallback_reason = RouteFallbackReason.PROVIDER_NOT_CONFIGURED
        else:
            try:
                return self._live_provider.route(
                    route_id=route_id,
                    responder_location=responder_location,
                    aed=aed,
                    wearer_location=wearer_location,
                    responder_to_aed_distance_m=responder_to_aed_distance_m,
                    aed_to_wearer_distance_m=aed_to_wearer_distance_m,
                    created_at=created_at,
                )
            except LiveRoutingError as exc:
                fallback_reason = exc.fallback_reason

        return self._static_fallback.route(
            route_id=route_id,
            responder_location=responder_location,
            aed=aed,
            wearer_location=wearer_location,
            responder_to_aed_distance_m=responder_to_aed_distance_m,
            aed_to_wearer_distance_m=aed_to_wearer_distance_m,
            created_at=created_at,
            fallback_reason=fallback_reason,
        )
