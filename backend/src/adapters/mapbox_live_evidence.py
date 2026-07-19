"""Privacy-safe live Mapbox product-path evidence harness.

The command-line path in this module never accepts an injected HTTP transport.
Tests may inject one through :func:`collect_evidence`, but the resulting artifact
is permanently labelled ``test_only`` and cannot produce a successful CLI exit.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
import hmac
import logging
import os
from pathlib import Path
import secrets
import sys
import time
from types import ModuleType
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import pydantic
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vital_relay.adapters import composite_routing, live_routing, static_routing
from vital_relay.adapters.composite_routing import LiveFirstRoutingProvider
from vital_relay.adapters.live_routing import (
    DEFAULT_MAPBOX_DIRECTIONS_BASE_URL,
    MapboxDirectionsRoutingProvider,
)
from vital_relay.application import routing as application_routing
from vital_relay.application.routing import LiveRoutingError
from vital_relay.domain import dispatch as dispatch_domain
from vital_relay.domain import health as health_domain
from vital_relay.domain import incidents as incidents_domain
from vital_relay.domain.dispatch import (
    AEDSiteView,
    Coordinate,
    RouteFallbackReason,
    RouteProvider,
    RouteSource,
)
from vital_relay.domain.health import SCHEMA_VERSION
from vital_relay.domain.incidents import GeoLocation
from vital_relay.evolution import hashing as evolution_hashing
from vital_relay.evolution.hashing import canonical_json_bytes, canonical_sha256


MAPBOX_ACCESS_TOKEN_ENV = "VITAL_RELAY_MAPBOX_ACCESS_TOKEN"
EVIDENCE_SCHEMA_VERSION = "vital-relay.live-mapbox.v1"
EVIDENCE_MODE = "non-production"
COORDINATE_DATA_CLASS = "non-patient-demo"
HASH_SCOPE = "ephemeral-session-hmac-sha256"
_FORCED_FAILURE_TOKEN = "vital-relay-evidence-intentionally-invalid"
_EXPECTED_AUTH_REJECTION_STATUS = 401
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_TIMING_MS = 60_000
_OFFICIAL_BASE_URL = urlsplit(DEFAULT_MAPBOX_DIRECTIONS_BASE_URL)
_EVIDENCE_SOURCE_MODULES: tuple[tuple[str, ModuleType], ...] = (
    ("adapters/mapbox_live_evidence.py", sys.modules[__name__]),
    ("adapters/live_routing.py", live_routing),
    ("adapters/composite_routing.py", composite_routing),
    ("adapters/static_routing.py", static_routing),
    ("application/routing.py", application_routing),
    ("domain/dispatch.py", dispatch_domain),
    ("domain/health.py", health_domain),
    ("domain/incidents.py", incidents_domain),
    ("evolution/hashing.py", evolution_hashing),
)

BoundedReason = Literal[
    "complete",
    "test_transport_not_live_evidence",
    "invalid_arguments",
    "evidence_mode_required",
    "non_patient_demo_attestation_required",
    "invalid_coordinates",
    "token_not_configured",
    "token_invalid",
    "provider_timeout",
    "provider_unavailable",
    "provider_invalid_response",
    "live_success_not_observed",
    "live_http_success_not_observed",
    "mapbox_auth_rejection_not_observed",
    "static_fallback_not_observed",
    "direct_transport_not_guaranteed",
    "internal_error",
]


class LiveSuccessEvidence(BaseModel):
    """Hash-only observation of one successfully parsed live route."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["verified"]
    source: Literal[RouteSource.LIVE_DIRECTIONS]
    provider: Literal[RouteProvider.MAPBOX_DIRECTIONS]
    observed_http_status: Literal[200]
    response_time_ms: int = Field(ge=0, le=_MAX_TIMING_MS)
    distance_m: float = Field(ge=0.0, le=200_000.0, allow_inf_nan=False)
    duration_seconds: int = Field(ge=0, le=172_800)
    leg_geometry_sha256: tuple[
        str,
        str,
    ] = Field(min_length=2, max_length=2)
    leg_steps_sha256: tuple[str, str] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_hashes(self) -> LiveSuccessEvidence:
        for digest in (*self.leg_geometry_sha256, *self.leg_steps_sha256):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("evidence hashes must be lowercase SHA-256")
        return self


class LiveFailureEvidence(BaseModel):
    """Bounded observation of one real provider rejection and its fallback."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["verified"]
    source: Literal[RouteSource.STATIC_FALLBACK]
    provider: Literal[RouteProvider.STATIC_VENUE]
    bounded_reason: Literal[RouteFallbackReason.PROVIDER_UNAVAILABLE]
    observed_http_status: Literal[401]
    response_time_ms: int = Field(ge=0, le=_MAX_TIMING_MS)


class MapboxLiveEvidence(BaseModel):
    """Canonical, content-addressed evidence with no raw provider material."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[EVIDENCE_SCHEMA_VERSION]
    evidence_mode: Literal[EVIDENCE_MODE]
    coordinate_data_class: Literal[COORDINATE_DATA_CLASS]
    hash_scope: Literal[HASH_SCOPE]
    session_id: UUID
    request_coordinates_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_PATTERN,
    )
    adapter_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    live_evidence: bool
    status: Literal["verified_live", "test_only", "blocked"]
    bounded_reason: BoundedReason
    live_success: LiveSuccessEvidence | None = None
    live_failure: LiveFailureEvidence | None = None
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_claim_and_content_address(self) -> MapboxLiveEvidence:
        if self.status == "verified_live":
            if (
                not self.live_evidence
                or self.bounded_reason != "complete"
                or self.request_coordinates_sha256 is None
                or self.live_success is None
                or self.live_failure is None
            ):
                raise ValueError("verified live evidence requires both stages")
        elif self.status == "test_only":
            if (
                self.live_evidence
                or self.bounded_reason != "test_transport_not_live_evidence"
                or self.request_coordinates_sha256 is None
                or self.live_success is None
                or self.live_failure is None
            ):
                raise ValueError("test evidence must remain non-live")
        elif (
            self.live_evidence
            or self.bounded_reason
            in {"complete", "test_transport_not_live_evidence"}
            or self.live_success is not None
            or self.live_failure is not None
        ):
            raise ValueError("blocked evidence cannot include verified stages")

        material = self.model_dump(
            mode="json",
            exclude={"evidence_sha256"},
            exclude_none=True,
        )
        if canonical_sha256(material) != self.evidence_sha256:
            raise ValueError("evidence_sha256 does not match canonical content")
        return self


class EvidenceInputError(ValueError):
    """A privacy-safe, allowlisted command input failure."""

    def __init__(self, reason: BoundedReason) -> None:
        super().__init__(reason)
        self.reason = reason


class _PrivacySafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _ = message
        raise EvidenceInputError("invalid_arguments")


class EvidenceConfiguration(BaseModel):
    """Validated non-patient coordinates and bounded request timeout."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    responder: Coordinate
    aed: Coordinate
    destination: Coordinate
    timeout_seconds: float = Field(ge=0.05, le=10.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_distinct_waypoints(self) -> EvidenceConfiguration:
        if len({self.responder, self.aed, self.destination}) != 3:
            raise ValueError("evidence waypoints must be distinct")
        return self


class _ObservedDirectionsHTTPClient:
    """Direct, environment-isolated HTTP client with status-only observation."""

    def __init__(
        self,
        *,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None,
    ) -> None:
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )
        self._observed_statuses: list[int] = []

    @property
    def observed_statuses(self) -> tuple[int, ...]:
        return tuple(self._observed_statuses)

    @property
    def trust_env(self) -> bool:
        return self._client.trust_env

    @contextmanager
    def stream(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: httpx.Timeout,
    ) -> Iterator[httpx.Response]:
        if self._client.trust_env or not _is_official_directions_request(
            method,
            url,
        ):
            raise EvidenceInputError("direct_transport_not_guaranteed")
        with self._client.stream(
            method,
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        ) as response:
            self._observed_statuses.append(response.status_code)
            yield response

    def close(self) -> None:
        self._client.close()


def collect_evidence(
    configuration: EvidenceConfiguration,
    *,
    access_token: str,
    transport: httpx.BaseTransport | None = None,
    session_id: UUID | None = None,
    session_secret: bytes | None = None,
) -> MapboxLiveEvidence:
    """Exercise success and forced-failure paths through the real adapters.

    Supplying ``transport`` is an intentionally narrow unit-test seam. Its
    presence irrevocably marks the result as test-only.
    """

    current_session_id = session_id or uuid4()
    current_session_secret = session_secret or secrets.token_bytes(32)
    route_arguments = _route_arguments(configuration)
    coordinate_digest = _session_digest(
        current_session_secret,
        {
            "session_id": str(current_session_id),
            "waypoints": [
                configuration.responder.model_dump(mode="json"),
                configuration.aed.model_dump(mode="json"),
                configuration.destination.model_dump(mode="json"),
            ],
        },
    )
    adapter_config_digest = _adapter_config_sha256(
        timeout_seconds=configuration.timeout_seconds,
        test_transport=transport is not None,
    )

    live_client = _ObservedDirectionsHTTPClient(
        timeout_seconds=configuration.timeout_seconds,
        transport=transport,
    )
    live_provider = MapboxDirectionsRoutingProvider(
        access_token=access_token,
        base_url=DEFAULT_MAPBOX_DIRECTIONS_BASE_URL,
        timeout_seconds=configuration.timeout_seconds,
        client=live_client,
    )
    started_ns = time.monotonic_ns()
    with _provider_logs_disabled():
        try:
            live_route = live_provider.route(**route_arguments)
        finally:
            success_response_time_ms = _elapsed_ms(started_ns)
            live_provider.close()
            live_client.close()

    if live_client.observed_statuses != (200,):
        raise EvidenceInputError("live_http_success_not_observed")
    if (
        live_route.source is not RouteSource.LIVE_DIRECTIONS
        or live_route.provider is not RouteProvider.MAPBOX_DIRECTIONS
        or live_route.fallback_reason is not None
    ):
        raise EvidenceInputError("live_success_not_observed")

    success_evidence = LiveSuccessEvidence(
        status="verified",
        source=live_route.source,
        provider=live_route.provider,
        observed_http_status=200,
        response_time_ms=success_response_time_ms,
        distance_m=round(live_route.total_distance_m, 3),
        duration_seconds=live_route.estimated_duration_seconds,
        leg_geometry_sha256=tuple(
            _session_digest(
                current_session_secret,
                [coordinate.model_dump(mode="json") for coordinate in leg.geometry],
            )
            for leg in live_route.legs
        ),
        leg_steps_sha256=tuple(
            _session_digest(current_session_secret, leg.instruction)
            for leg in live_route.legs
        ),
    )

    failing_live_client = _ObservedDirectionsHTTPClient(
        timeout_seconds=configuration.timeout_seconds,
        transport=transport,
    )
    failing_live_provider = MapboxDirectionsRoutingProvider(
        access_token=_FORCED_FAILURE_TOKEN,
        base_url=DEFAULT_MAPBOX_DIRECTIONS_BASE_URL,
        timeout_seconds=configuration.timeout_seconds,
        client=failing_live_client,
    )
    live_first = LiveFirstRoutingProvider(failing_live_provider)
    started_ns = time.monotonic_ns()
    with _provider_logs_disabled():
        try:
            fallback_route = live_first.route(**route_arguments)
        finally:
            failure_response_time_ms = _elapsed_ms(started_ns)
            live_first.close()
            failing_live_client.close()

    if failing_live_client.observed_statuses != (
        _EXPECTED_AUTH_REJECTION_STATUS,
    ):
        raise EvidenceInputError("mapbox_auth_rejection_not_observed")
    if (
        fallback_route.source is not RouteSource.STATIC_FALLBACK
        or fallback_route.provider is not RouteProvider.STATIC_VENUE
        or fallback_route.fallback_reason
        is not RouteFallbackReason.PROVIDER_UNAVAILABLE
    ):
        raise EvidenceInputError("static_fallback_not_observed")

    failure_evidence = LiveFailureEvidence(
        status="verified",
        source=fallback_route.source,
        provider=fallback_route.provider,
        bounded_reason=fallback_route.fallback_reason,
        observed_http_status=_EXPECTED_AUTH_REJECTION_STATUS,
        response_time_ms=failure_response_time_ms,
    )
    test_only = transport is not None
    return _build_evidence(
        session_id=current_session_id,
        request_coordinates_sha256=coordinate_digest,
        adapter_config_sha256=adapter_config_digest,
        live_evidence=not test_only,
        status="test_only" if test_only else "verified_live",
        bounded_reason=(
            "test_transport_not_live_evidence" if test_only else "complete"
        ),
        live_success=success_evidence,
        live_failure=failure_evidence,
    )


def main(argv: list[str] | None = None) -> int:
    """Run direct live evidence collection and write one canonical JSON line."""

    session_id = uuid4()
    session_secret = secrets.token_bytes(32)
    adapter_config_digest = canonical_sha256({"status": "unavailable"})
    configuration: EvidenceConfiguration | None = None
    coordinate_digest: str | None = None
    try:
        adapter_config_digest = _adapter_config_sha256(
            timeout_seconds=3.0,
            test_transport=False,
        )
        configuration = _parse_arguments(argv)
        adapter_config_digest = _adapter_config_sha256(
            timeout_seconds=configuration.timeout_seconds,
            test_transport=False,
        )
        coordinate_digest = _session_digest(
            session_secret,
            {
                "session_id": str(session_id),
                "waypoints": [
                    configuration.responder.model_dump(mode="json"),
                    configuration.aed.model_dump(mode="json"),
                    configuration.destination.model_dump(mode="json"),
                ],
            },
        )
        token = os.environ.get(MAPBOX_ACCESS_TOKEN_ENV)
        if token is None or not token.strip():
            raise EvidenceInputError("token_not_configured")
        if (
            token != token.strip()
            or len(token) > 2_048
            or any(character.isspace() for character in token)
        ):
            raise EvidenceInputError("token_invalid")

        # This module is an isolated CLI. Keep logging globally disabled until
        # process exit so a provider worker that outlives a total deadline can
        # never emit its coordinate-bearing request URL after control returns.
        logging.disable(logging.CRITICAL)
        evidence = collect_evidence(
            configuration,
            access_token=token,
            session_id=session_id,
            session_secret=session_secret,
        )
    except EvidenceInputError as exc:
        evidence = _blocked_evidence(
            session_id=session_id,
            request_coordinates_sha256=coordinate_digest,
            adapter_config_sha256=adapter_config_digest,
            bounded_reason=exc.reason,
        )
    except LiveRoutingError as exc:
        evidence = _blocked_evidence(
            session_id=session_id,
            request_coordinates_sha256=coordinate_digest,
            adapter_config_sha256=adapter_config_digest,
            bounded_reason=exc.fallback_reason.value,
        )
    except ValueError:
        evidence = _blocked_evidence(
            session_id=session_id,
            request_coordinates_sha256=coordinate_digest,
            adapter_config_sha256=adapter_config_digest,
            bounded_reason="internal_error",
        )
    except Exception:
        evidence = _blocked_evidence(
            session_id=session_id,
            request_coordinates_sha256=coordinate_digest,
            adapter_config_sha256=adapter_config_digest,
            bounded_reason="internal_error",
        )

    sys.stdout.buffer.write(canonical_json_bytes(evidence) + b"\n")
    if evidence.status == "verified_live" and evidence.live_evidence:
        return 0
    if evidence.bounded_reason in {
        "invalid_arguments",
        "evidence_mode_required",
        "non_patient_demo_attestation_required",
        "invalid_coordinates",
        "token_not_configured",
        "token_invalid",
    }:
        return 2
    return 1


def _parse_arguments(argv: list[str] | None) -> EvidenceConfiguration:
    parser = _PrivacySafeArgumentParser(
        description="Collect privacy-safe non-production Mapbox evidence.",
    )
    parser.add_argument("--evidence-mode", required=True)
    parser.add_argument("--coordinate-data-class", required=True)
    parser.add_argument("--confirm-no-patient-data", action="store_true")
    parser.add_argument("--responder", required=True, metavar="LAT,LON")
    parser.add_argument("--aed", required=True, metavar="LAT,LON")
    parser.add_argument("--destination", required=True, metavar="LAT,LON")
    parser.add_argument("--timeout-seconds", default="3.0", metavar="SECONDS")
    namespace = parser.parse_args(argv)

    if namespace.evidence_mode != EVIDENCE_MODE:
        raise EvidenceInputError("evidence_mode_required")
    if (
        namespace.coordinate_data_class != COORDINATE_DATA_CLASS
        or not namespace.confirm_no_patient_data
    ):
        raise EvidenceInputError("non_patient_demo_attestation_required")
    try:
        timeout_seconds = float(namespace.timeout_seconds)
        return EvidenceConfiguration(
            responder=_parse_coordinate(namespace.responder),
            aed=_parse_coordinate(namespace.aed),
            destination=_parse_coordinate(namespace.destination),
            timeout_seconds=timeout_seconds,
        )
    except (TypeError, ValueError):
        raise EvidenceInputError("invalid_coordinates") from None


def _parse_coordinate(value: object) -> Coordinate:
    if not isinstance(value, str) or value != value.strip():
        raise ValueError("coordinate must be a clean string")
    parts = value.split(",")
    if len(parts) != 2 or any(not part or part != part.strip() for part in parts):
        raise ValueError("coordinate must contain latitude and longitude")
    return Coordinate(latitude=float(parts[0]), longitude=float(parts[1]))


def _route_arguments(configuration: EvidenceConfiguration) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "route_id": uuid4(),
        "responder_location": configuration.responder,
        "aed": AEDSiteView(
            schema_version=SCHEMA_VERSION,
            aed_site_id=uuid4(),
            name="Non-patient evidence AED",
            coordinate=configuration.aed,
            location_description="Operator-attested non-patient demo point",
            access_instructions="Non-production evidence only.",
            publicly_accessible=True,
            available=True,
            availability_confirmed_at=now,
        ),
        "wearer_location": GeoLocation(
            latitude=configuration.destination.latitude,
            longitude=configuration.destination.longitude,
            horizontal_accuracy_m=1.0,
            captured_at=now,
        ),
        "responder_to_aed_distance_m": 1.0,
        "aed_to_wearer_distance_m": 1.0,
        "created_at": now,
    }


def _adapter_config_sha256(
    *,
    timeout_seconds: float,
    test_transport: bool,
) -> str:
    return canonical_sha256(
        {
            "adapter": "MapboxDirectionsRoutingProvider",
            "executed_source_sha256": _evidence_source_sha256(),
            "base_url_sha256": sha256(
                DEFAULT_MAPBOX_DIRECTIONS_BASE_URL.encode("ascii")
            ).hexdigest(),
            "profile": "walking",
            "expected_auth_rejection_status": (
                _EXPECTED_AUTH_REJECTION_STATUS
            ),
            "timeout_seconds": timeout_seconds,
            "trust_env": False,
            "transport": (
                "injected_test_transport" if test_transport else "direct_httpx"
            ),
            "runtime": {
                "httpx": httpx.__version__,
                "pydantic": pydantic.__version__,
                "python": ".".join(str(part) for part in sys.version_info[:3]),
            },
        }
    )


def _evidence_source_sha256() -> dict[str, str]:
    digests: dict[str, str] = {}
    for logical_name, module in _EVIDENCE_SOURCE_MODULES:
        source_file = module.__file__
        if source_file is None:
            raise EvidenceInputError("direct_transport_not_guaranteed")
        digests[logical_name] = sha256(Path(source_file).read_bytes()).hexdigest()
    return digests


def _is_official_directions_request(method: str, url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        method == "GET"
        and parsed.scheme == "https"
        and parsed.hostname == _OFFICIAL_BASE_URL.hostname
        and parsed.netloc == _OFFICIAL_BASE_URL.netloc
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.startswith(f"{_OFFICIAL_BASE_URL.path}/walking/")
    )


def _session_digest(secret: bytes, value: object) -> str:
    return hmac.new(secret, canonical_json_bytes(value), sha256).hexdigest()


def _elapsed_ms(started_ns: int) -> int:
    elapsed_ns = max(0, time.monotonic_ns() - started_ns)
    return min(_MAX_TIMING_MS, (elapsed_ns + 999_999) // 1_000_000)


@contextmanager
def _provider_logs_disabled() -> Iterator[None]:
    """Prevent HTTP libraries from logging secret-bearing coordinate URLs."""

    loggers = tuple(logging.getLogger(name) for name in ("httpx", "httpcore"))
    prior_states = tuple(logger.disabled for logger in loggers)
    for logger in loggers:
        logger.disabled = True
    try:
        yield
    finally:
        for logger, disabled in zip(loggers, prior_states, strict=True):
            logger.disabled = disabled


def _build_evidence(
    *,
    session_id: UUID,
    request_coordinates_sha256: str | None,
    adapter_config_sha256: str,
    live_evidence: bool,
    status: Literal["verified_live", "test_only", "blocked"],
    bounded_reason: BoundedReason,
    live_success: LiveSuccessEvidence | None,
    live_failure: LiveFailureEvidence | None,
) -> MapboxLiveEvidence:
    material = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "evidence_mode": EVIDENCE_MODE,
        "coordinate_data_class": COORDINATE_DATA_CLASS,
        "hash_scope": HASH_SCOPE,
        "session_id": str(session_id),
        "adapter_config_sha256": adapter_config_sha256,
        "live_evidence": live_evidence,
        "status": status,
        "bounded_reason": bounded_reason,
    }
    if request_coordinates_sha256 is not None:
        material["request_coordinates_sha256"] = request_coordinates_sha256
    if live_success is not None:
        material["live_success"] = live_success.model_dump(mode="json")
    if live_failure is not None:
        material["live_failure"] = live_failure.model_dump(mode="json")
    return MapboxLiveEvidence(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        evidence_mode=EVIDENCE_MODE,
        coordinate_data_class=COORDINATE_DATA_CLASS,
        hash_scope=HASH_SCOPE,
        session_id=session_id,
        request_coordinates_sha256=request_coordinates_sha256,
        adapter_config_sha256=adapter_config_sha256,
        live_evidence=live_evidence,
        status=status,
        bounded_reason=bounded_reason,
        live_success=live_success,
        live_failure=live_failure,
        evidence_sha256=canonical_sha256(material),
    )


def _blocked_evidence(
    *,
    session_id: UUID,
    request_coordinates_sha256: str | None,
    adapter_config_sha256: str,
    bounded_reason: BoundedReason,
) -> MapboxLiveEvidence:
    return _build_evidence(
        session_id=session_id,
        request_coordinates_sha256=request_coordinates_sha256,
        adapter_config_sha256=adapter_config_sha256,
        live_evidence=False,
        status="blocked",
        bounded_reason=bounded_reason,
        live_success=None,
        live_failure=None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
