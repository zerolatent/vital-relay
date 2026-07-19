# ROUTE-01 — Live Responder Routing

## Outcome

ROUTE-01 replaces the static-only responder route contract with a durable,
source-labelled walking route. When a Mapbox Directions-compatible provider is
configured, acceptance makes one real HTTPS Directions v5 request for the
ordered responder → AED → wearer waypoints. A valid provider response supplies
the two leg distances, route ETA, turn-derived instructions, and GeoJSON
geometry rendered by the native responder map.

If live routing is not configured or the configured request times out, is
unavailable, or returns invalid data, acceptance uses the existing endpoint-only
venue route. That result is always persisted and rendered as
`source=static_fallback`, `provider=static_venue`, with one explicit bounded
`fallback_reason`. Static coordinates and straight-line estimates are never
described as live navigation.

## Durable route contract

Every accepted route contains:

- `source`: `live_directions` or `static_fallback`;
- `provider`: `mapbox_directions` or `static_venue`;
- `fallback_reason`: null for live directions, otherwise one of
  `provider_not_configured`, `provider_timeout`, `provider_unavailable`, or
  `provider_invalid_response`;
- `travel_mode=walking`, the assigned AED identifier, creation time, total
  distance, and total estimated duration;
- exactly two ordered legs, responder → AED and AED → wearer;
- per-leg requested endpoints, instruction, distance, duration, and ordered
  coordinate geometry.

The domain rejects mismatched source/provider/fallback metadata, non-finite or
out-of-range values, missing or misordered legs, disconnected AED links,
geometry outside bounded waypoint tolerances, and totals that do not equal the
leg sums. Static fallback geometry must be anchored to its exact endpoints.
Live provider geometry may use provider-snapped waypoints only within the
bounded routing tolerance.

The frozen v1 wire contract remains readable. A legacy `static_venue` route
that omits both `source` and `fallback_reason` is normalized to the truthful
unconfigured static fallback, and a legacy leg without `geometry` derives the
two-point line from its origin and destination. That compatibility path is
limited to the complete pre-ROUTE-01 static shape: partially supplied new
metadata, live metadata without geometry, and legacy-looking Mapbox payloads
still fail strict validation. The route schemas and canonical examples cover
legacy static, explicit static fallback, and explicit live provider shapes.

The complete validated model is serialized into both the immutable decision
snapshot and `responder_assignments.static_route`. Accepted-dispatch reads parse
that stored JSON back through `RoutePlan` and compare it with the decision
snapshot before returning it.

## Live HTTP adapter

`MapboxDirectionsRoutingProvider` uses the documented
[Mapbox Directions v5](https://docs.mapbox.com/api/navigation/directions/)
walking shape:

```text
GET {base_url}/walking/{responder};{aed};{wearer}
```

The request supplies `alternatives=false`, `steps=true`,
`geometries=geojson`, `overview=full`, and a 50-meter radius for each input
waypoint. The base URL must be clean HTTPS. The access token is required only
when constructing the configured live adapter; static builds and tests do not
need one. The client or its `httpx` transport is injectable, while the default
path constructs a real synchronous HTTPS client. HTTPX URL logging receives a
targeted access-token redaction filter.

The timeout is explicitly configured and bounded from 0.05 through 10 seconds.
It is enforced as a total wall-clock deadline around the entire request and
body read, in addition to HTTPX's per-operation timeout. A deadline expiry
closes any published response and releases the acceptance transaction without
waiting for another provider byte. Bodies are read as a stream with identity
encoding and a 2,000,000-byte raw cap; an oversized or unexpectedly encoded
response is closed before JSON parsing. Timeout, transport, and
provider-response failures are converted into bounded application errors
consumed by `LiveFirstRoutingProvider`; provider bodies and transport exception
details are never put into fallback metadata.

Provider JSON is accepted only when it has `code=Ok`, three returned waypoints,
one bounded route (an empty successful route list is invalid), its two required
legs, finite bounded
distance/duration values, step instructions, GeoJSON LineString geometries,
matching route/leg/step totals, waypoint proximity, and continuously ordered step,
leg, and overview geometry through the AED. Invalid provider data cannot create
a live `RoutePlan`.

## Native responder experience

The responder contract decodes and validates both sources. Existing static-only
fixtures decode conservatively as `provider_not_configured` with endpoint-only
geometry, while new server responses encode the source, fallback reason, and
geometry explicitly.

The accepted responder view now:

- draws every ordered geometry coordinate rather than connecting only the three
  endpoint markers;
- labels live results as a Mapbox provider walking route with provider distance
  and route ETA;
- labels static results as `STATIC VENUE FALLBACK`, shows the exact bounded
  failure reason, and says `NOT LIVE NAVIGATION`;
- uses `Static estimate` for fallback leg metrics and does not claim those
  points are a live route.

This is a route preview, not continuously updating turn-by-turn navigation.

## Focused verification

The backend route tests cover:

1. one intercepted Mapbox-compatible success response, including request shape,
   provider distance/ETA, ordered geometry, and live metadata;
2. a deterministic `httpx.ReadTimeout`, followed by serialized and revalidated
   static fallback metadata with `provider_timeout`;
3. disconnected provider step geometry rejected at the live boundary and
   converted to `provider_invalid_response` fallback;
4. `code=Ok` with an empty route list converted to the precise invalid-response
   fallback;
5. a drip response that exceeds the total deadline, is closed, and returns the
   timeout fallback before its next byte;
6. a streamed body beyond the byte cap that is closed and returns the
   invalid-response fallback.

The PostGIS dispatch test checks that default unconfigured routing persists and
returns `static_fallback`, `static_venue`, `provider_not_configured`, and both
leg geometries. It also reads a simulated pre-ROUTE-01 immutable assignment and
decision snapshot through both the dispatch endpoint and idempotent response
replay. The focused native suite covers legacy static fallback decoding, live
Mapbox geometry decoding, required geometry for new shapes, and rejection of
partially or falsely labelled provider metadata.

No live Mapbox request was made during slice verification because no external
access token was supplied. Focused HTTP interception is test-only.

Verification evidence from the isolated ROUTE-01 worktree:

```text
.venv/bin/pytest
390 passed, 3 skipped, 45 deselected

.venv/bin/pytest -q backend/tests/unit/test_live_routing.py \
  backend/tests/contract
48 passed

.venv/bin/pytest -q -m postgres \
  backend/tests/postgres/test_postgis_dispatch.py
3 passed

swift test --package-path apps/apple
127 tests passed
```

## Integration handoff

Shared runtime wiring is intentionally not part of this bounded slice. The
integration task must update `backend/src/vital_relay/main.py` and the shared
configuration surface as follows:

1. Read a server-owned `VITAL_RELAY_MAPBOX_ACCESS_TOKEN`. Absence is valid and
   must select the explicitly labelled unconfigured fallback.
2. Optionally read a clean HTTPS
   `VITAL_RELAY_MAPBOX_DIRECTIONS_BASE_URL` and a bounded
   `VITAL_RELAY_ROUTING_TIMEOUT_SECONDS`; otherwise use the adapter defaults.
3. When the token is present, construct `MapboxDirectionsRoutingProvider` with
   it. When absent, use `None`.
4. Construct `LiveFirstRoutingProvider(live_provider)` and pass that composite
   to `PostgresDispatchRepository` in place of the direct
   `StaticVenueRoutingProvider()`.
5. Track the composite as an active runtime provider. Call `close()` during
   lifespan teardown and database-construction failure cleanup so its owned
   HTTP client is released.
6. Add the shared environment documentation only in the integration-owned
   files; this slice deliberately does not edit `.env.example`, `README.md`,
   `pyproject.toml`, `Makefile`, or `main.py`.

The integration must not create a fixture-backed or fake production provider.
The provided adapter already performs the real request whenever it is
configured.
