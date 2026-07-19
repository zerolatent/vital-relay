"""Real Mapbox Directions-compatible walking route adapter."""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from collections.abc import Sequence
from contextlib import AbstractContextManager, suppress
from dataclasses import dataclass
from datetime import datetime
from math import atan2, ceil, cos, isclose, isfinite, radians, sin, sqrt
from typing import Literal, Protocol
from urllib.parse import unquote_to_bytes, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from vital_relay.application.routing import (
    LiveRoutingError,
    LiveRoutingInvalidResponseError,
    LiveRoutingTimeoutError,
    LiveRoutingUnavailableError,
)
from vital_relay.domain.dispatch import (
    AEDSiteView,
    Coordinate,
    RouteLeg,
    RouteLegType,
    RoutePlan,
    RouteProvider,
    RouteSource,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import GeoLocation


DEFAULT_MAPBOX_DIRECTIONS_BASE_URL = (
    "https://api.mapbox.com/directions/v5/mapbox"
)
MAX_PROVIDER_RESPONSE_BYTES = 2_000_000
MAX_WAYPOINT_OFFSET_M = 100.0
MAX_GEOMETRY_JOIN_OFFSET_M = 25.0

_ACCESS_TOKEN_PATTERN = re.compile(r"([?&]access_token=)[^&\s]+")
_INVALID_PERCENT_ESCAPE_PATTERN = re.compile(r"%(?![0-9A-Fa-f]{2})")
_HTTPS_AUTHORITY_PATTERN = re.compile(
    r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?"
)
_DNS_LABEL_PATTERN = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)
_RAW_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z0-9._~/-]|%[0-9A-Fa-f]{2})*"
)
_UNRESERVED_PATH_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-._~"
)


class _MapboxURLRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_access_token(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_log_argument(item) for item in record.args)
        elif isinstance(record.args, dict):
            record.args = {
                key: _redact_log_argument(value) for key, value in record.args.items()
            }
        return True


_MAPBOX_URL_REDACTION_FILTER = _MapboxURLRedactionFilter()


def validate_mapbox_directions_base_url(value: str) -> str:
    """Return a canonical reviewed-ASCII HTTPS provider URL or fail closed."""

    invalid = ValueError(
        "Mapbox-compatible base_url must be a clean HTTPS URL"
    )
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or value != value.strip()
        or any(
            ord(character) < 0x21 or ord(character) > 0x7E
            for character in value
        )
        or not value.startswith("https://")
        or "\\" in value
        or "?" in value
        or "#" in value
        or _INVALID_PERCENT_ESCAPE_PATTERN.search(value) is not None
    ):
        raise invalid

    try:
        parsed = urlsplit(value)
        port = parsed.port
        decoded_path_bytes = unquote_to_bytes(parsed.path)
        decoded_path = decoded_path_bytes.decode("utf-8", errors="strict")
    except (TypeError, UnicodeError, ValueError) as exc:
        raise invalid from exc
    host = parsed.hostname
    host_labels = host.split(".") if host is not None else []
    escaped_octets = tuple(
        int(escape, 16)
        for escape in re.findall(r"%([0-9A-Fa-f]{2})", parsed.path)
    )
    if (
        parsed.scheme != "https"
        or host is None
        or len(host) > 253
        or not host_labels
        or any(
            _DNS_LABEL_PATTERN.fullmatch(label) is None
            for label in host_labels
        )
        or _HTTPS_AUTHORITY_PATTERN.fullmatch(parsed.netloc) is None
        or (port is not None and not 1 <= port <= 65_535)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or _RAW_PATH_PATTERN.fullmatch(parsed.path) is None
        or (parsed.path and not parsed.path.startswith("/"))
        or any(octet in {0x25, 0x2F, 0x5C} for octet in escaped_octets)
        or any(
            character.isspace()
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in decoded_path
        )
        or any(
            character != "/"
            and character not in _UNRESERVED_PATH_CHARACTERS
            for character in decoded_path
        )
        or "%" in decoded_path
        or "//" in decoded_path
        or any(
            segment in {".", ".."}
            for segment in decoded_path.split("/")
        )
    ):
        raise invalid
    return value.rstrip("/")


class DirectionsHTTPClient(Protocol):
    """Small injectable seam for the real synchronous HTTP request."""

    def stream(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> AbstractContextManager[httpx.Response]: ...


class _ActiveResponse:
    """Close a streamed response promptly when the total deadline expires."""

    def __init__(self, cancelled: threading.Event) -> None:
        self._cancelled = cancelled
        self._lock = threading.Lock()
        self._response: httpx.Response | None = None

    def publish(self, response: httpx.Response) -> None:
        with self._lock:
            self._response = response
            cancelled = self._cancelled.is_set()
        if cancelled:
            with suppress(Exception):
                response.close()

    def cancel_and_close(self) -> None:
        self._cancelled.set()
        with self._lock:
            response = self._response
        if response is not None:
            # A third-party stream's close hook is not part of our trusted
            # deadline budget, so cancellation must never wait for it inline.
            threading.Thread(
                target=_close_response,
                args=(response,),
                name="vital-relay-route-response-close",
                daemon=True,
            ).start()


@dataclass
class _DeadlineOutcome:
    route: RoutePlan | None = None
    error: Exception | None = None


class _MapboxGeometry(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    type: Literal["LineString"]
    coordinates: tuple[tuple[FiniteFloat, FiniteFloat], ...] = Field(
        min_length=2,
        max_length=20_000,
    )


class _MapboxManeuver(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )

    instruction: str = Field(min_length=1, max_length=1_024)


class _MapboxStep(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    distance: FiniteFloat = Field(ge=0.0, le=100_000.0)
    duration: FiniteFloat = Field(ge=0.0, le=86_400.0)
    geometry: _MapboxGeometry
    maneuver: _MapboxManeuver


class _MapboxLeg(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    distance: FiniteFloat = Field(ge=0.0, le=100_000.0)
    duration: FiniteFloat = Field(ge=0.0, le=86_400.0)
    steps: tuple[_MapboxStep, ...] = Field(min_length=1, max_length=1_000)


class _MapboxRoute(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    distance: FiniteFloat = Field(ge=0.0, le=200_000.0)
    duration: FiniteFloat = Field(ge=0.0, le=172_800.0)
    geometry: _MapboxGeometry
    legs: tuple[_MapboxLeg, _MapboxLeg]


class _MapboxWaypoint(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    location: tuple[FiniteFloat, FiniteFloat]


class _MapboxDirectionsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    code: Literal["Ok"]
    routes: tuple[_MapboxRoute, ...] = Field(max_length=1)
    waypoints: tuple[_MapboxWaypoint, _MapboxWaypoint, _MapboxWaypoint]


class MapboxDirectionsRoutingProvider:
    """Request and validate one AED-first walking route from live directions."""

    def __init__(
        self,
        *,
        access_token: str,
        base_url: str = DEFAULT_MAPBOX_DIRECTIONS_BASE_URL,
        timeout_seconds: float = 3.0,
        client: DirectionsHTTPClient | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = access_token.strip()
        if (
            not token
            or len(token) > 2_048
            or any(character.isspace() for character in token)
        ):
            raise ValueError("Mapbox access_token is invalid")
        if not isfinite(timeout_seconds) or not 0.05 <= timeout_seconds <= 10.0:
            raise ValueError("routing timeout_seconds must be between 0.05 and 10")
        if client is not None and transport is not None:
            raise ValueError("inject either a routing client or transport, not both")

        self._access_token = token
        self._base_url = validate_mapbox_directions_base_url(base_url)
        self._timeout_seconds = timeout_seconds
        self._timeout = httpx.Timeout(timeout_seconds)
        self._client = client or httpx.Client(
            timeout=self._timeout,
            transport=transport,
            follow_redirects=False,
        )
        self._owns_client = client is None
        logging.getLogger("httpx").addFilter(_MAPBOX_URL_REDACTION_FILTER)

    def close(self) -> None:
        if self._owns_client and isinstance(self._client, httpx.Client):
            self._client.close()

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
        # PostGIS distances remain available only for the static fallback; live
        # distance and ETA must come exclusively from the directions response.
        _ = (responder_to_aed_distance_m, aed_to_wearer_distance_m)
        wearer_coordinate = Coordinate(
            latitude=wearer_location.latitude,
            longitude=wearer_location.longitude,
        )
        requested_waypoints = (
            responder_location,
            aed.coordinate,
            wearer_coordinate,
        )
        coordinates_path = ";".join(
            f"{coordinate.longitude},{coordinate.latitude}"
            for coordinate in requested_waypoints
        )
        endpoint = f"{self._base_url}/walking/{coordinates_path}"
        cancelled = threading.Event()
        active_response = _ActiveResponse(cancelled)
        outcome = _DeadlineOutcome()
        completed = threading.Event()

        def perform_request() -> None:
            try:
                outcome.route = self._streamed_route(
                    endpoint=endpoint,
                    route_id=route_id,
                    requested_waypoints=requested_waypoints,
                    aed_site_id=aed.aed_site_id,
                    created_at=created_at,
                    cancelled=cancelled,
                    active_response=active_response,
                )
            except Exception as exc:
                outcome.error = exc
            finally:
                completed.set()

        worker = threading.Thread(
            target=perform_request,
            name=f"vital-relay-route-{route_id}",
            daemon=True,
        )
        worker.start()
        if not completed.wait(self._timeout_seconds):
            active_response.cancel_and_close()
            raise LiveRoutingTimeoutError("live routing total deadline expired")
        if outcome.error is not None:
            raise outcome.error
        if outcome.route is None:
            raise LiveRoutingUnavailableError("live routing produced no result")
        return outcome.route

    def _streamed_route(
        self,
        *,
        endpoint: str,
        route_id: UUID,
        requested_waypoints: tuple[Coordinate, Coordinate, Coordinate],
        aed_site_id: UUID,
        created_at: datetime,
        cancelled: threading.Event,
        active_response: _ActiveResponse,
    ) -> RoutePlan:
        try:
            with self._client.stream(
                "GET",
                endpoint,
                params={
                    "access_token": self._access_token,
                    "alternatives": "false",
                    "geometries": "geojson",
                    "overview": "full",
                    "radiuses": "50;50;50",
                    "steps": "true",
                },
                headers={"Accept-Encoding": "identity"},
                timeout=self._timeout,
            ) as response:
                active_response.publish(response)
                if cancelled.is_set():
                    raise LiveRoutingTimeoutError(
                        "live routing total deadline expired"
                    )
                if response.status_code != 200:
                    raise LiveRoutingUnavailableError(
                        "live routing returned an error"
                    )
                body = _read_bounded_body(response, cancelled=cancelled)
        except LiveRoutingError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise LiveRoutingTimeoutError("live routing timed out") from None
        except httpx.DecodingError:
            raise LiveRoutingInvalidResponseError(
                "live routing response decoding failed"
            ) from None
        except (httpx.HTTPError, OSError):
            raise LiveRoutingUnavailableError(
                "live routing is unavailable"
            ) from None

        try:
            parsed = _MapboxDirectionsResponse.model_validate_json(body)
            if not parsed.routes:
                raise ValueError("live routing response has no route")
            return _route_plan(
                parsed,
                route_id=route_id,
                requested_waypoints=requested_waypoints,
                aed_site_id=aed_site_id,
                created_at=created_at,
            )
        except (ValidationError, TypeError, ValueError, OverflowError):
            raise LiveRoutingInvalidResponseError(
                "live routing response failed validation"
            ) from None


def _read_bounded_body(
    response: httpx.Response,
    *,
    cancelled: threading.Event,
) -> bytes:
    content_encoding = response.headers.get("content-encoding", "").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise LiveRoutingInvalidResponseError(
            "live routing response encoding is unsupported"
        )

    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError:
            raise LiveRoutingInvalidResponseError(
                "live routing content length is invalid"
            ) from None
        if parsed_length < 0 or parsed_length > MAX_PROVIDER_RESPONSE_BYTES:
            raise LiveRoutingInvalidResponseError(
                "live routing response is too large"
            )

    body = bytearray()
    for chunk in response.iter_raw(chunk_size=64 * 1_024):
        if cancelled.is_set():
            raise LiveRoutingTimeoutError("live routing total deadline expired")
        remaining = MAX_PROVIDER_RESPONSE_BYTES - len(body)
        if len(chunk) > remaining:
            raise LiveRoutingInvalidResponseError(
                "live routing response is too large"
            )
        body.extend(chunk)
    if cancelled.is_set():
        raise LiveRoutingTimeoutError("live routing total deadline expired")
    return bytes(body)


def _close_response(response: httpx.Response) -> None:
    with suppress(Exception):
        response.close()


def _route_plan(
    response: _MapboxDirectionsResponse,
    *,
    route_id: UUID,
    requested_waypoints: tuple[Coordinate, Coordinate, Coordinate],
    aed_site_id: UUID,
    created_at: datetime,
) -> RoutePlan:
    returned_waypoints = tuple(
        _coordinate(waypoint.location) for waypoint in response.waypoints
    )
    for requested, returned in zip(
        requested_waypoints,
        returned_waypoints,
        strict=True,
    ):
        if _distance_m(requested, returned) > MAX_WAYPOINT_OFFSET_M:
            raise ValueError("provider waypoint is too far from the requested point")

    selected = response.routes[0]
    leg_distance_sum = sum(leg.distance for leg in selected.legs)
    leg_duration_sum = sum(leg.duration for leg in selected.legs)
    if not isclose(
        selected.distance,
        leg_distance_sum,
        rel_tol=0.001,
        abs_tol=1.0,
    ) or not isclose(
        selected.duration,
        leg_duration_sum,
        rel_tol=0.001,
        abs_tol=1.0,
    ):
        raise ValueError("provider route totals do not match its legs")

    overview_geometry = tuple(
        _coordinate(coordinate) for coordinate in selected.geometry.coordinates
    )
    if (
        _distance_m(overview_geometry[0], returned_waypoints[0])
        > MAX_WAYPOINT_OFFSET_M
        or _distance_m(overview_geometry[-1], returned_waypoints[-1])
        > MAX_WAYPOINT_OFFSET_M
        or min(
            _distance_m(point, returned_waypoints[1]) for point in overview_geometry
        )
        > MAX_WAYPOINT_OFFSET_M
    ):
        raise ValueError("provider overview geometry is not AED-first")

    route_legs: list[RouteLeg] = []
    for index, provider_leg in enumerate(selected.legs):
        step_distance_sum = sum(step.distance for step in provider_leg.steps)
        step_duration_sum = sum(step.duration for step in provider_leg.steps)
        if not isclose(
            provider_leg.distance,
            step_distance_sum,
            rel_tol=0.001,
            abs_tol=1.0,
        ) or not isclose(
            provider_leg.duration,
            step_duration_sum,
            rel_tol=0.001,
            abs_tol=1.0,
        ):
            raise ValueError("provider leg totals do not match its steps")
        geometry = _leg_geometry(provider_leg.steps)
        if (
            _distance_m(geometry[0], returned_waypoints[index])
            > MAX_WAYPOINT_OFFSET_M
            or _distance_m(geometry[-1], returned_waypoints[index + 1])
            > MAX_WAYPOINT_OFFSET_M
        ):
            raise ValueError("provider leg geometry is not waypoint-anchored")
        leg_type = (
            RouteLegType.RESPONDER_TO_AED
            if index == 0
            else RouteLegType.AED_TO_WEARER
        )
        route_legs.append(
            RouteLeg(
                sequence=index + 1,
                leg_type=leg_type,
                origin=requested_waypoints[index],
                destination=requested_waypoints[index + 1],
                instruction=_leg_instruction(leg_type, provider_leg.steps),
                distance_m=provider_leg.distance,
                estimated_duration_seconds=ceil(provider_leg.duration),
                geometry=geometry,
            )
        )

    if (
        _distance_m(route_legs[0].geometry[-1], route_legs[1].geometry[0])
        > MAX_GEOMETRY_JOIN_OFFSET_M
    ):
        raise ValueError("provider legs do not join at the AED")

    return RoutePlan(
        schema_version=SCHEMA_VERSION,
        route_id=route_id,
        source=RouteSource.LIVE_DIRECTIONS,
        provider=RouteProvider.MAPBOX_DIRECTIONS,
        fallback_reason=None,
        travel_mode="walking",
        aed_site_id=aed_site_id,
        created_at=created_at,
        legs=(route_legs[0], route_legs[1]),
        total_distance_m=sum(leg.distance_m for leg in route_legs),
        estimated_duration_seconds=sum(
            leg.estimated_duration_seconds for leg in route_legs
        ),
    )


def _leg_geometry(steps: Sequence[_MapboxStep]) -> tuple[Coordinate, ...]:
    geometry: list[Coordinate] = []
    for step in steps:
        step_geometry = [
            _coordinate(coordinate) for coordinate in step.geometry.coordinates
        ]
        if geometry:
            if _distance_m(geometry[-1], step_geometry[0]) > MAX_GEOMETRY_JOIN_OFFSET_M:
                raise ValueError("provider step geometries are disconnected")
            if geometry[-1] == step_geometry[0]:
                step_geometry = step_geometry[1:]
        geometry.extend(step_geometry)
        if len(geometry) > 20_000:
            raise ValueError("provider leg geometry is too large")
    if len(geometry) < 2:
        raise ValueError("provider leg geometry is incomplete")
    return tuple(geometry)


def _leg_instruction(
    leg_type: RouteLegType,
    steps: Sequence[_MapboxStep],
) -> str:
    destination = (
        "the assigned AED"
        if leg_type is RouteLegType.RESPONDER_TO_AED
        else "the incident location"
    )
    prefix = f"Live walking directions to {destination}: "
    selected: list[str] = []
    for step in steps:
        candidate = " Then ".join((*selected, step.maneuver.instruction))
        if len(prefix) + len(candidate) > 512:
            break
        selected.append(step.maneuver.instruction)
    if not selected:
        return f"Follow the live walking route to {destination}."
    return prefix + " Then ".join(selected)


def _coordinate(value: tuple[float, float]) -> Coordinate:
    longitude, latitude = value
    return Coordinate(latitude=latitude, longitude=longitude)


def _distance_m(first: Coordinate, second: Coordinate) -> float:
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


def _redact_access_token(value: str) -> str:
    return _ACCESS_TOKEN_PATTERN.sub(r"\1<redacted>", value)


def _redact_log_argument(value: object) -> object:
    if isinstance(value, (str, httpx.URL)):
        return _redact_access_token(str(value))
    return value
