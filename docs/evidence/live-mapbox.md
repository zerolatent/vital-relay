# Live Mapbox evidence

This lane provides a reproducible command that exercises the production
`MapboxDirectionsRoutingProvider` request and its existing bounded response
parser. A zero exit is possible only after a direct HTTPS Mapbox Directions
response validates as a two-leg AED-first live route and a second direct request
receives Mapbox's documented invalid-token HTTP `401`, which becomes the exact
`provider_unavailable` static fallback through `LiveFirstRoutingProvider`.

No live output is checked in. Unit fixtures validate orchestration, schema, and
redaction only; artifacts produced with an injected transport are permanently
labelled `test_only`, set `live_evidence` to `false`, and cannot satisfy this
gate.

## External prerequisites

The operator must provide all of the following at run time:

- Python 3.14 with the project installed or `backend/src` on `PYTHONPATH`;
- a real Mapbox access token in `VITAL_RELAY_MAPBOX_ACCESS_TOKEN`;
- outbound DNS and TLS/HTTPS access to the fixed Mapbox Directions v5 API;
- token permission and quota for walking directions; and
- three explicit, distinct synthetic or public demo coordinates. They must not
  identify a patient or be copied from patient, incident, wearable, or health
  data.

The harness does not accept a URL override, token argument, response file,
replay, or fixture transport on its command-line path. Missing credentials,
network failures, provider rejection, or parser rejection produce bounded JSON
and a nonzero exit.

The command constructs its own `httpx.Client` with `trust_env=False`, redirects
disabled, and no proxy, custom transport, or CA argument. It accepts requests
only for the fixed official HTTPS Mapbox Directions origin. Consequently
`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, and `SSL_CERT_FILE` cannot redirect or
re-sign a command-line evidence request. Supplying an injected transport through
the Python unit-test seam always produces `status=test_only` and
`live_evidence=false`.

## Run

Set the three coordinate variables to operator-reviewed `latitude,longitude`
demo points. Values are arguments to the command but are never emitted:

```bash
export DEMO_RESPONDER='operator-supplied-demo-lat,operator-supplied-demo-lon'
export DEMO_AED='operator-supplied-demo-lat,operator-supplied-demo-lon'
export DEMO_DESTINATION='operator-supplied-demo-lat,operator-supplied-demo-lon'
export VITAL_RELAY_MAPBOX_ACCESS_TOKEN='operator-supplied-real-token'

PYTHONPATH=backend/src python -m vital_relay.adapters.mapbox_live_evidence \
  --evidence-mode non-production \
  --coordinate-data-class non-patient-demo \
  --confirm-no-patient-data \
  --responder "$DEMO_RESPONDER" \
  --aed "$DEMO_AED" \
  --destination "$DEMO_DESTINATION"
```

`--confirm-no-patient-data` is an explicit operator attestation. The command
also validates finite latitude/longitude bounds and distinct waypoints with the
existing domain coordinate contract. Software cannot independently establish
the provenance of arbitrary coordinates, so the operator must review them
before making this attestation.

The default three-second total deadline can be changed within the adapter's
existing 0.05-to-10-second bound with `--timeout-seconds`.

## Success and forced-failure stages

The first stage constructs `MapboxDirectionsRoutingProvider` with the real
environment token, fixed official API base, direct `httpx` transport, and the
operator's demo coordinates. The provider performs its real streamed request,
bounded body read, Pydantic response parse, geometry checks, waypoint checks,
and distance/duration consistency checks. This stage passes only when the
returned `RoutePlan` has:

- `source=live_directions`;
- `provider=mapbox_directions`; and
- no fallback reason.

The second stage constructs the same real adapter against the same fixed Mapbox
API with an intentionally invalid, non-secret evidence token. Mapbox's real HTTP
`401` rejection is observed directly as a bounded integer status before the
adapter collapses it to `provider_unavailable`. Mapbox documents `401` as the
invalid/deleted-token response in its
[access-token troubleshooting guide](https://docs.mapbox.com/help/troubleshooting/token-errors/).
`LiveFirstRoutingProvider` must then return:

- `source=static_fallback`;
- `provider=static_venue`; and
- `bounded_reason=provider_unavailable`.

Any live route in the second stage or any static route in the first stage fails
the run. The fallback confirms failure labelling only; it cannot substitute for
live success. A DNS, TLS, connection, timeout, HTTP `403`, HTTP `429`, or HTTP
`5xx` failure has no accepted `401` observation and therefore cannot satisfy the
forced-failure stage, even if it maps to the same static fallback reason.

## Canonical privacy-safe output

The command writes one minified, key-sorted UTF-8 JSON object. It includes:

- provider/source and bounded status for both stages;
- direct bounded HTTP status (`200` for success and `401` for forced failure);
- live distance and duration returned through the bounded parser;
- bounded millisecond response timings;
- one HMAC for the request coordinate tuple;
- per-leg geometry and normalized step/instruction HMACs;
- an adapter/configuration SHA-256; and
- `evidence_sha256`, computed over the canonical object with that field omitted.

Location-derived and instruction-derived hashes use a fresh secret HMAC key for
each process. The key is never emitted; `session_id` identifies only the hash
scope. The adapter/configuration hash covers the evidence harness plus
`live_routing.py`, `composite_routing.py`, `static_routing.py`, the routing and
domain contracts they execute, canonical hashing logic, fixed API base hash,
walking profile, accepted authentication status, timeout, `trust_env=false`,
direct-versus-test transport mode, and runtime dependency versions. It does not
include the token or coordinates.

The artifact never contains the access token (or a token hash), raw URL, raw
coordinates, response body, route geometry, health data, maneuver/instruction
text, or hidden reasoning. The content address lets a reviewer verify the
canonical artifact itself without exposing the protected inputs.

Exit codes are:

- `0`: both real stages verified and `status=verified_live`;
- `2`: invalid input, missing explicit attestation, or missing/invalid token;
- `1`: provider, network, bounded-parser, induced-failure, or internal failure.

## Focused verification

```bash
python3.14 -m pytest backend/tests/unit/test_mapbox_live_evidence.py
```

These tests use `httpx.MockTransport` solely to validate two-stage
orchestration, fallback schema, canonical hashing, privacy redaction, and
truthful nonzero behavior without a token. They also prove that network/HTTP
failures cannot masquerade as the expected authentication rejection, proxy/CA
environment settings cannot upgrade a fixture to live evidence, unofficial
origins fail closed, and every executed project source is bound into the
configuration hash. Their output is not live evidence.
