# iOS UI-02: Live Incident Client

## Outcome

UI-02 connects the existing dark iPhone scenes to the frozen real-incident
boundary without giving the client authority over incident state:

```text
Hold for SOS
    ↓
persist exact event envelope
    ↓
POST /v1/wearable/events
    ↓
server escalating → Getting Help
```

A fall safety check remains a separate, Apple-callback-driven path. Its visible
countdown comes from backend `verification_expires_at`. Reaching zero performs
only an incident refresh; the client waits for the durable backend worker to
return `escalating`.

## Implemented boundary

- Immutable `Codable`, `Equatable`, and `Sendable` Swift contracts for:
  - wearable event requests/results and both payload variants;
  - incident projections and state/version metadata;
  - check-in requests/results and transitions;
  - ordered timeline entries.
- Contract validation for schema v1, `simulated: false`, finite/ranged
  coordinates, Apple-fall proof literals, and event/source/payload pairing.
- Flexible RFC 3339 decoding for whole-second, fractional, and offset values.
- `URLSessionIncidentAPIClient` support for:
  - `POST /v1/wearable/events`;
  - `GET /v1/incidents/{incident_id}`;
  - `POST /v1/incidents/{incident_id}/check-in`;
  - `GET /v1/incidents/{incident_id}/timeline`.
- Exact `X-Vital-Relay-Device-Token` authentication and typed, token-free error
  presentation.
- HTTPS is mandatory outside `localhost`, `127.0.0.1`, and `::1`; the client
  rejects unsafe transport before constructing a network request.
- `LiveVitalRelayDataProvider` scene mapping for `verifying`, `escalating`,
  `response_active`, and `resolved`.
- Bounded incident polling with `state_version` ordering. A transient failure
  preserves the last authoritative scene.
- Durable `UserDefaults` recovery for:
  - the active incident ID;
  - an exact pending manual-SOS envelope;
  - an exact pending check-in envelope;
  - the next monotonic device sequence.
- A namespace isolates persisted envelopes by live environment/user/device.
- Cold launch retries an exact pending check-in before interaction is enabled;
  an unrecoverable active incident blocks creation of a second SOS.
- Failed initial recovery remains fail-closed but presents an in-app Retry, so
  restored connectivity does not require a force-relaunch.
- A `409` timeout race reconciles through `GET` instead of applying a local
  transition.
- Incident-generation guards prevent a late request from a previous lifecycle
  from overwriting a newer incident, and polling survives return/new-SOS cycles.
- Every authoritative incident is correlated to the configured user. A manual
  SOS may validly attach to and escalate an already-active fall incident.
- Fixture mode remains the default and requires no backend or secret.

## Corrected interaction semantics

The UI-01 monitoring button previously entered the fixture safety check. UI-02
separates the two real meanings:

| Input | Authoritative path | iPhone scene |
|---|---|---|
| Deliberate iPhone SOS | `manual_sos` event → `escalating` | Getting Help |
| Genuine accepted Apple fall | `fall_detected` event → `verifying` | Safety Check |
| `I'm okay` | check-in → `resolved` | Incident Resolved |
| `I need help` | check-in → `escalating` | Getting Help |
| Countdown reaches zero | incident `GET`; backend worker decides | Remain at zero or Getting Help |

The live provider cannot manufacture a fall. A live Safety Check requires an
existing incident ID created from a genuine entitled Apple callback. Replay
fixtures continue to exercise this scene without entering the real endpoint.

## Location boundary

Every live wearable event requires a fresh timestamped location. UI-02 does not
claim Core Location integration. Consequently:

- live SOS fails closed when no location source is injected;
- the app never silently substitutes the Chicago example coordinate;
- an explicit `--demo-venue-location` launch argument may inject operator-
  supplied coordinates for a locally contained demo only;
- the persistent banner still states that no emergency service is contacted.

## UI changes

- Monitoring now emits `activateSOS`, not `beginSafetyCheck`.
- Live and replay badges are derived from presentation state.
- Getting Help uses honest live/replay copy and does not expose a dismissal
  action for an unresolved live incident.
- `response_active` is acknowledged without inventing responder identity,
  route, or ETA.
- A minimal gradient-ring Resolved scene displays the server result and permits
  return to monitoring.
- Added `--fixture-resolved` for visual QA.

## Run modes

Default fixture mode:

```bash
cd apps/apple
swift test
open VitalRelay.xcodeproj
```

Opt-in live mode requires the `--live-api` scheme argument and these environment
values:

```text
VITAL_RELAY_API_BASE_URL=http://127.0.0.1:8000
VITAL_RELAY_DEVICE_TOKEN=<local demo token>
VITAL_RELAY_USER_ID=<contract-safe user ID>
VITAL_RELAY_DEVICE_ID=<stable contract-safe device ID>
```

The loopback URL above is the only intended cleartext mode. Use HTTPS for every
non-loopback API host.

Optional recovery/configuration values:

```text
VITAL_RELAY_INCIDENT_ID=<existing incident UUID>
VITAL_RELAY_STORAGE_NAMESPACE=<stable local environment name>
```

For an explicit local demo-venue manual SOS, also add
`--demo-venue-location` and set:

```text
VITAL_RELAY_DEMO_LATITUDE=<finite -90...90>
VITAL_RELAY_DEMO_LONGITUDE=<finite -180...180>
VITAL_RELAY_DEMO_ACCURACY_M=<finite 0...10000>
```

Do not use fixed demo coordinates as a product location source.

## Verification

```text
Swift package: 38 tests in 6 suites passed
iOS build:     arm64 iOS Simulator build passed
Simulator QA:  Getting Help and Resolved scenes inspected on iPhone 17 Pro
Loopback API:  app GET reached FastAPI and rendered the typed expected 503
Backend gate:  2 focused PostgreSQL incident acceptance tests passed
```

The tests cover contract decoding/encoding, RFC 3339 variants, auth headers and
paths, safe HTTP errors, manual-SOS exact retry, server-owned expiry, check-in
resolution, `409` timeout reconciliation, state-version rewind prevention,
cross-user rejection, active-fall/manual-SOS attachment, initial-load retry,
manual-SOS verification rejection, cleartext transport rejection, location
failure, and fixture behavior.

The Swift transport tests use an intercepted `URLSession`, and the simulator app
also reached a loopback in-memory FastAPI process at the real incident path. Its
expected `incident_persistence_unavailable` response rendered as the typed live
error, proving the app URLSession/ATS path. The backend acceptance gate
independently exercises the real FastAPI/PostgreSQL incident boundary. A
physical-device Apple callback and Core Location path are not claimed by this
slice.

## Remaining limitations and UI-03 handoff

- HealthKit, Core Location, WatchConnectivity, the Apple fall entitlement, and
  physical-device proof remain unimplemented.
- Polling is implemented; WebSocket/SSE delivery and visible connectivity
  status are not.
- The device token is a bounded hackathon secret, not production identity,
  attestation, storage, or rotation.
- UI tests do not yet long-press SOS or tap the safety-check responses.
- UI-02 intentionally does not consume Slice 05 dispatch coordination. UI-03
  can add the redacted responder constellation and accepted-only route using
  those frozen read models without changing the incident authority boundary.

**Status update:** The redacted constellation handoff is complete in
[iOS UI-03](ios-ui-03-dispatch-coordination.md). UI-03 deliberately does not add
the accepted route to the wearer app; exact dispatch remains a future,
separately authenticated responder surface.
