# iOS UI-03: Privacy-Redacted Dispatch Coordination

**Status:** Complete and simulator-build verified  
**Scope:** Wearer/device-token dispatch coordination on iPhone  
**Does not complete:** Responder authentication bootstrap, invitation delivery,
accepted-route presentation, HealthKit, Core Location, WatchConnectivity, or
physical-device proof

## Outcome

UI-03 extends the live Getting Help scene with the real Slice 05 coordination
boundary while keeping the wearer app on the device-token-safe side of dispatch:

```text
server escalating
    ↓
POST /v1/incidents/{incident_id}/dispatch
    ↓
GET /v1/incidents/{incident_id}/dispatch
    ↓
searching → invitation recorded → responder accepted
```

The scene presents a minimal OLED-dark relay constellation, responder status,
coarse proximity, and the public AED site. It never requests or models the
responder-authenticated accepted-dispatch route.

## Implemented boundary

- Strict Swift `Codable`, `Equatable`, and `Sendable` models for the
  device-readable Slice 05 response:
  - redacted responder candidates;
  - invitation history;
  - the accepted responder link when one exists;
  - public AED name and location description;
  - coordination state and authoritative update time.
- Validation for schema v1, `simulated: false`, bounded ranks and sequences,
  responder roles and skills, coarse distance bands, invitation/state
  consistency, and required nullable keys.
- Unknown fields are rejected at every modeled level so an accidentally added
  exact coordinate, route, or token cannot silently enter the wearer client.
- `URLSessionIncidentAPIClient` support for exactly:
  - `POST /v1/incidents/{incident_id}/dispatch`;
  - `GET /v1/incidents/{incident_id}/dispatch`.
- Both calls use `X-Vital-Relay-Device-Token` and the existing HTTPS-or-loopback
  transport policy. No responder token is stored or transmitted.
- A presentation mapper converts roles and coarse bands into human-readable,
  display-only state without retaining raw location geometry.

## Privacy and authority boundary

The wearer UI may display:

- the count of qualified, never-invited candidates remaining in the search;
- the invited or accepted responder's display name and role;
- only a coarse responder distance band;
- declined-invitation count;
- the selected public AED's name and public location description.

The wearer UI deliberately has no type or property for:

- the wearer's exact coordinate;
- a responder's exact coordinate;
- route geometry or route legs;
- distance in meters or an ETA;
- an accepted-dispatch payload;
- `X-Vital-Relay-Responder-Token`.

Slice 05 exposes those accepted-only details through a different
responder-token-authenticated endpoint. Rendering that endpoint belongs to a
separate responder entry surface after credential, deep-link, and notification
bootstrap exists; authenticated acceptance does not widen the wearer contract.

The incident projection remains the state authority. Dispatch can enrich an
`escalating` or `response_active` scene, but it cannot advance, rewind, resolve,
or otherwise replace the incident state.

## Live coordination behavior

- Entering or recovering an escalating incident starts dispatch on a
  best-effort basis and reads its redacted coordination state.
- Incident and dispatch are polled independently. An invitation can therefore
  become pending, declined, or accepted even when the incident's
  `state_version` has not changed.
- If a read shows no pending invitation and another eligible never-invited
  candidate remains, the provider asks the server to create the next invitation.
- `no_available_aed` is presented as an explicit public-AED-unavailable state;
  other coordination failures are presented as temporarily unavailable. When
  confirmed coordination already exists, either condition retains those last
  confirmed privacy-safe details.
- A dispatch transport or decode failure preserves the already acknowledged
  Getting Help scene. It does not roll back the SOS or manufacture a local
  result.
- Resolved incidents and explicit return to monitoring clear dispatch
  presentation state.

## Ordering and race safeguards

Dispatch commits are correlated to the active incident identity and lifecycle
generation. The provider also applies these monotonic rules before publishing:

- `updated_at` cannot move backward;
- invitation history cannot shrink;
- declined and accepted invitations cannot return to pending;
- an accepted responder cannot disappear or change identity;
- a later request ordinal wins when concurrent responses have the same
  timestamp;
- foreign-incident and delayed previous-lifecycle results are discarded;
- dispatch cannot advance an `escalating` incident to `response_active`, and a
  stale incident cannot rewind an authoritative active response.

These checks protect presentation ordering only. The backend remains responsible
for candidate eligibility, invitation creation, acceptance, and incident
transitions.

## UI and accessibility

- Added `RelayConstellationView`, a restrained violet-blue-aqua `Canvas` scene
  with connected relay nodes, moving sparks, and phase-specific highlights.
- Searching, pending, and accepted phases use text and symbols in addition to
  color.
- A server-side pending invitation is labeled as recorded and waiting for
  confirmation. The copy explicitly says the demo does not send phone
  notifications.
- The accepted phase says only that the responder accepted the invitation; it
  does not claim that the responder is on the way, has arrived, or has received
  an external notification.
- The public AED is a separate, readable detail rather than an implied route
  waypoint.
- Reduce Motion uses a static constellation and disables root scene
  transitions. VoiceOver receives one combined privacy-safe summary and a phase
  change announcement, while critical content remains in a Dynamic
  Type-friendly scroll layout.
- The existing demo/replay and no-emergency-service boundaries remain visible.

## Run modes

Fixture mode remains the default:

```bash
cd apps/apple
swift test
open VitalRelay.xcodeproj
```

Use either launch argument to inspect a completed UI-03 state directly:

```text
--fixture-dispatch-invited
--fixture-response-active
```

Live mode continues to use `--live-api` and the UI-02 environment values. When
an authoritative incident enters `escalating`, the same configured live client
performs the device-token dispatch POST and GET automatically.

## Verification

```text
Swift package: 68 tests in 10 suites passed
iOS build:     generic arm64 iOS Simulator build passed
```

The UI-03 tests cover:

- canonical coordination decode/encode and strict nested unknown-key rejection;
- invitation, candidate, AED, schema, and privacy invariants;
- exact POST/GET methods, paths, headers, empty POST body, transport errors, and
  absence of responder-token behavior;
- explicit no-public-AED and temporarily-unavailable presentation without
  erasing last confirmed coordination;
- every supported responder role and coarse distance-band presentation;
- searching, recorded, accepted, exhausted decline history, and public AED
  mapping;
- dispatch startup after incident recovery and failure isolation;
- same-incident-version dispatch polling;
- incident-state authority and foreign-incident rejection;
- delayed equal-timestamp response races and monotonic invitation/acceptance
  handling.

The simulator build proves the native app target compiles with the new SwiftUI
scene. This slice does not claim APNs delivery, a responder deep link, an
accepted route, or physical-device performance/accessibility sign-off.

## Handoff

The next frontend boundary is a separate responder-authenticated entry flow:

1. register or otherwise bootstrap a responder destination and credential;
2. deliver an invitation through an allowlisted notification provider or a
   signed/deep-linked equivalent;
3. show only the existing coarse pre-accept context;
4. submit authenticated accept/decline;
5. only after acceptance, fetch and render the exact static dispatch and fixed
   protocol through the responder-token endpoints.

Until that bootstrap exists, the wearer constellation is the complete UI-03
surface and no client route or ETA is claimed.
