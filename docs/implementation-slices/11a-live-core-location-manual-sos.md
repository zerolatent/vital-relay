# Slice 11A — Live Core Location Manual SOS

## Outcome

An authenticated community user can now hold the native SOS control and send a
real, non-simulated manual-SOS event using a current iPhone Core Location fix.
The existing authenticated incident endpoint remains the only authority that
opens and advances the incident.

This slice replaces the normal community graph's unavailable location source;
it does not add a fallback coordinate or convert simulator data into a real
event.

## Product flow

1. The enrolled community session composes `LiveVitalRelayDataProvider` with
   `CoreLocationIncidentLocationProvider` and its current access token.
2. The user deliberately completes the existing hold-for-SOS gesture.
3. The app requests When In Use authorization if its status is not determined.
4. With full-accuracy authorization, Core Location requests one foreground fix.
5. The client validates the fix before it creates an event ID, reserves a
   sequence, persists an idempotency envelope, or calls the backend.
6. A valid fix becomes the event's bounded `location`; its native
   `CLLocation.timestamp` becomes `captured_at`.
7. The deliberate hold completion remains the event's `observed_at`. After any
   first-time authorization decision, the location fix has a 12-second timeout.
8. The existing authenticated `POST /v1/wearable/events` sends the exact
   `simulated: false`, `manual_sos`, iPhone-button envelope.
9. The server returns the authoritative escalating incident and the app renders
   the existing Getting Help experience.

The UI says “Getting current location and sending SOS…” while the owned action
is in flight. Permission, service, accuracy, freshness, timeout, and invalid-fix
failures use actionable native error copy and leave the app in monitoring.

## Core Location boundary

The adapter is a main-actor-confined `CLLocationManagerDelegate`. This keeps the
manager, authorization changes, timeout, and checked continuation on the run
loop where the manager was created. Only one acquisition can be active.

The adapter requests:

- When In Use authorization only;
- full/precise accuracy;
- one `requestLocation()` fix after authorization;
- no Always authorization;
- no background location mode; and
- no continuous wearer tracking.

`NSLocationWhenInUseUsageDescription` explains that location is used only when
the user activates SOS so authorized responders can reach the incident. No
location entitlement is needed for this foreground behavior.

## Fail-closed quality policy

The broad wire contract still permits any finite coordinate and horizontal
accuracy up to 10 km. The first-party product path now applies a stricter manual
SOS policy before transport:

| Check | Product bound |
|---|---:|
| Maximum location age | 15 seconds |
| Maximum horizontal uncertainty | 100 meters |
| Allowed future clock skew | 5 seconds |
| Location-fix timeout after authorization | 12 seconds |

The client also rejects:

- non-finite or out-of-range coordinates;
- negative or non-finite horizontal accuracy;
- denied or restricted authorization;
- disabled Location Services;
- reduced/approximate accuracy;
- software-simulated Core Location samples; and
- provider failures or cancellation.

Reduced accuracy is not relabeled as a precise fix, stale cached coordinates are
not refreshed by changing their timestamp, and explicit QA venue coordinates
are never selected by normal authenticated composition.

## Cancellation and retry boundary

The monitoring view now starts a model-owned submission task. Back navigation,
logout, profile switching, or session invalidation cancels that task and the
pending Core Location continuation.

If cancellation happens before HTTP transport begins, the newly persisted
request and its sensitive coordinate are removed because the backend could not
have accepted it. If cancellation or a transport error occurs after ingest
begins, the existing pending envelope is retained: the outcome is ambiguous,
and a later retry must reuse the exact event ID, sequence, coordinate, location
capture time, and event observation time rather than create a second SOS.

Back navigation and access-token recovery preserve that session-scoped retry.
Explicit logout/profile switching intentionally clears all artifacts for the
departing session, including a pending exact coordinate. If an in-flight request
was accepted during that race, later authenticated discovery recovers the
authoritative incident; if it was not accepted, a newly enrolled session
requires another deliberate SOS.

The logout sweep uses a durable registry of namespaced incident stores and
removes their UserDefaults keys synchronously. Each live store is then
actor-invalidated so a racing old provider cannot recreate cleared keys. The
registry lets a later cold launch sweep again if the process ended before that
actor cleanup completed. Async graph composition also captures the expected
session ID and router generation; a stale task cannot resurrect a departed
credential graph after its authorization await.

A definitive `400`, `403`, `404`, `409`, `410`, or `422` ingest response clears
the rejected pending envelope so an exact coordinate is not retried forever.
Cancellation, transport failure, `401` pending token recovery, throttling,
server failure, or an unreadable success stays retryable/ambiguous and retains
the exact request.

This preserves the incident core's established at-most-one logical event
behavior while preventing an old persona graph from continuing a location
request after teardown.

## Deliberate QA separation

`FixedDemoIncidentLocationProvider` remains available only to the explicit
`--live-api --demo-venue-location` QA launch. The normal enrolled community
experience never falls back to that provider. `UnavailableIncidentLocationProvider`
also remains the default seam so any composition that forgets to provide a
real source still fails closed.

Software-simulated Core Location is rejected in the real provider. Simulator
builds therefore verify API and composition correctness but do not constitute
evidence that a real manual SOS was sent.

## Verification

Automated verification completed for this slice:

```text
Swift package:                 113 tests in 21 suites passed
Location policy focus:          4 tests passed
Session cancellation focus:     1 test passed
Terminal retry/privacy focus:   2 tests passed
Generic unsigned iOS build:     succeeded for arm64 iOS 18
Info.plist validation:          passed
Diff whitespace validation:     passed
```

The focused tests verify preservation of the device capture time, rejection of
stale/inaccurate/software-simulated samples, and cancellation of a model-owned
SOS when the session is invalidated. Existing tests continue to verify that an
unavailable location causes zero event ingests and that an ambiguous transport
retry reuses the exact persisted event.

## Primary files

- `apps/apple/Sources/VitalRelayFeature/CoreLocationIncidentLocationProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentLocationQualityPolicy.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentRequestStore.swift`
- `apps/apple/Sources/VitalRelayFeature/LiveVitalRelayDataProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/AppModel.swift`
- `apps/apple/Sources/VitalRelayFeature/MonitoringView.swift`
- `apps/apple/Sources/VitalRelayFeature/VitalRelayRootView.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/VitalRelay-Info.plist`
- `apps/apple/Tests/VitalRelayFeatureTests/IncidentLocationQualityPolicyTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/AppModelTests.swift`

## Known limitations and external evidence

- A signed physical iPhone run has not yet verified the authorization prompt,
  actual GPS source metadata, or the end-to-end enrolled-device POST. The code
  and unsigned iOS build are complete; device signing, a consenting user, and a
  reachable HTTPS backend are external inputs.
- This slice captures only the incident-opening iPhone location. It does not
  continuously update wearer location or implement live responder routing.
- A user who disabled Precise Location must enable it in Settings before SOS;
  this slice does not request temporary full-accuracy authorization.
- Manual-SOS idempotency still uses the existing namespaced UserDefaults store.
  It is synchronously swept on logout and cleared before unambiguous
  cancellation, but device-only encrypted storage for the pending exact
  coordinate remains a hardening item.
- The backend enforces the broad coordinate contract, while the tighter
  15-second/100-meter policy is enforced by the authenticated first-party app.
- HealthKit, Core Motion, WatchConnectivity, and the genuine entitled Apple
  fall callback remain unimplemented.
- Vital Relay is a hackathon prototype and does not contact emergency services.

## Handoff to Slice 11B

The authenticated community graph and stable installation ID are now ready for
real HealthKit capability discovery and scalar ingestion. Slice 11B should
request only supported allowlisted types, preserve each visible sample's real
unit/source/observation time, report unsupported or requested-with-no-visible-
sample states honestly, and keep every health value context-only. It must not
change the manual SOS/fall/check-in/timeout escalation authority.
