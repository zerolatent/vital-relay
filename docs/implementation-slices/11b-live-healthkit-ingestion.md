# Slice 11B — Real HealthKit Scalar Ingestion

## Outcome

An authenticated community user can now explicitly connect Apple Health from
the native Vital Relay monitoring screen, authorize read-only access, and sync
the latest visible stored scalar sample for every selected allowlisted type.
The app sends real HealthKit observations, honest per-type capability states,
and an immutable snapshot through the existing authenticated health APIs.

This slice adds recent stored context. It does not claim a live Apple Watch
stream, a connected Watch, or a real Apple fall-detection event. Health context
is informational only and has no reference to the SOS or incident-transition
authority.

## Product flow

1. The authenticated community graph composes a session-scoped
   `HealthContextCoordinator` with the account user ID, stable installation ID,
   community access token, and real `HKHealthStore` source.
2. The user chooses whether to include the separately labeled connected-source
   group. That group is off by default; changing the toggle does not request
   access or upload data.
3. The user taps **Connect Apple Health** or **Refresh Health Context**. Vital
   Relay never silently syncs health data merely because a persona session was
   restored or selected.
4. The source requests read-only access to the selected types and queries the
   latest visible stored `HKQuantitySample` for each requested type.
5. Each visible sample is normalized to the frozen v1 canonical unit while
   preserving its HealthKit end time, source name, bundle identifier, device
   model, and non-simulated Apple Health source.
6. The coordinator posts an optional metric batch, a complete capability batch,
   and an immutable snapshot through the authenticated community endpoints. The
   first completed sync in that coordinator session uses `monitoring_started`;
   later explicit syncs use `manual_refresh`.
7. The monitoring screen renders every visible scalar with its stored-sample
   timestamp and source. It also reports requested types with no visible sample,
   unsupported types, and bounded read errors without inferring permission
   denial.

The HealthKit task is independent of the manual-SOS task. Syncing, failure, or
missing health data does not disable or delay the hold-for-SOS control.

## Scalar registry

The frozen Slice 11B registry contains 30 quantity types. Twenty-six comprise
the standard incident-context request:

| Group | Standard scalar types |
|---|---|
| Heart | Heart rate, resting heart rate, walking heart-rate average, HRV SDNN, one-minute heart-rate recovery, AFib burden |
| Respiratory and sleeping measurements | Respiratory rate, oxygen saturation, sleeping wrist temperature, sleeping breathing disturbances |
| Activity | Step-count sample, active energy, basal energy, walking/running distance, cycling distance, swimming distance, wheelchair distance, flights climbed |
| Mobility and fall history | Walking speed, walking step length, walking asymmetry, walking double-support time, stair ascent speed, stair descent speed, walking steadiness, stored number-of-times-fallen sample |

Four connected-source quantities require the separate opt-in toggle:

- body temperature;
- blood glucose;
- body mass; and
- body mass index.

The upload boundary rejects an off-scope collection if it contains any of these
four samples or reports them as selected; this invariant does not rely only on
the concrete HealthKit adapter behaving correctly.

The stored fall-count quantity is historical context only. It is not an Apple
fall callback and cannot create an incident. Percentage-valued HealthKit
fractions are converted to the frozen `%` wire convention; all other values use
the registry's canonical unit.

## Authorization and capability truthfulness

The target enables the HealthKit capability and includes
`NSHealthShareUsageDescription`. It requests no write types, so this slice does
not add `NSHealthUpdateUsageDescription`, workout-writing access, clinical
records access, or background delivery.

HealthKit intentionally does not reveal whether read permission was denied for
an individual type. Vital Relay therefore reports only facts it can observe:

| Capability status | Meaning in Slice 11B |
|---|---|
| `available` | A visible stored quantity sample was returned and normalized |
| `requested_no_sample` | The type was requested but no sample was visible |
| `not_requested` | The expanded type was not selected for this sync |
| `unsupported` | HealthKit is unavailable, including the simulator product path |
| `error` | Authorization presentation, query, time, unit, or value normalization failed |

`requested_no_sample` never becomes `permission_denied`, and the UI explains
that an empty result can mean no stored data, limited access, or a choice not to
share. The simulator returns all types as unsupported and uploads no fabricated
health values.

## Contract, identity, and retry boundary

All observations use:

- `acquisition_class: recent_context`;
- `source: apple_healthkit` and `source_kind: apple_healthkit`;
- `simulated: false`; and
- `used_for_escalation: false`.

The client has strict Codable equivalents of the existing metric, capability,
batch-result, and snapshot contracts. It rejects unknown fields and inconsistent
success receipts. Requests use the existing
`X-Vital-Relay-Device-Token` community-session header; the credential never
enters a request body.

A metric ID is deterministic for the account, registry version, metric kind,
and HealthKit sample UUID. The client hashes those inputs into a UUID-shaped
identifier instead of sending the raw HealthKit UUID as a cross-account global
identifier. Re-reading the same stored sample for the same account therefore
reaches backend idempotency with the same metric identity.

One collection prepares exact metric/capability batches, snapshot identity, and
capture reason before transport. Ambiguous transport or response failures
retain that pending upload for an exact retry while the selected standard or
expanded scope is unchanged, including the original capture reason. If the user
changes that consent scope after a failure, the client abandons the old pending
envelope and recollects the newly selected scope instead of intentionally
finishing the previous optional upload. Stable metric IDs safely deduplicate
anything already accepted.

The backend also treats a newer non-available capability as the current
visibility boundary for an older metric from the same source and acquisition
class. New snapshots therefore omit a previously shared optional value after
opt-out or loss of visibility; already-created immutable snapshots remain
unchanged. Definitive client errors clear pending work, and a `401` invalidates
the persona session. This slice adds no persistent health-sample or
pending-upload store; the access token remains in the existing secure
persona-session boundary.

Logout, profile switching, session invalidation, or graph teardown cancels the
owned sync task, clears the native presentation, and prevents a late result
from repopulating the departed account's graph. Because HealthKit permission is
application-wide at the OS layer, the explicit per-account Connect/Refresh
action is the product consent boundary after a persona switch.

## Native presentation

The monitoring screen now provides a dedicated Apple Health Context card with:

- an explicit Connect/Refresh action;
- an off-by-default connected-source toggle;
- stored-sample rather than live/Watch labeling;
- timestamp, canonical value/unit, and source for every visible scalar;
- counts for no-visible-sample, unsupported, and read-error states; and
- persistent copy that health context never triggers or suppresses escalation.

When a stored heart-rate sample is visible, it can replace the fixture heart
rate presentation, but it remains labeled as a stored Apple Health sample. Its
presence never marks a Watch connected or a value live.

## Verification boundary

The integrated implementation passed:

```text
Swift package:                 125 tests in 26 suites passed
Python health context:        13 focused tests passed
Generic unsigned iOS build:   succeeded for a generic iOS device target
Plist/entitlement/project:    plutil validation passed
Diff whitespace validation:   passed
```

The focused Swift coverage includes registry/contract invariants, deterministic
per-user metric IDs, coordinator upload order, exact same-scope retry identity,
consent-scope changes, backend opt-out suppression,
authenticated API receipt validation, honest unsupported/no-sample state, and
session-teardown cancellation. A signed physical-iPhone run remains required
even after these automated gates pass.

A physical verification should record the real authorization sheet, visible
sample/source metadata, authenticated metric/capability receipts, and snapshot
creation for a consenting community account. The simulator is suitable for
compile and UI verification only and is deliberately not evidence of real
HealthKit ingestion.

## Primary files

- `apps/apple/Sources/VitalRelayFeature/HealthMetricRegistry.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthKitScalarSource.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthIngestionContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthIngestionAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthContextCoordinator.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthContextViewState.swift`
- `apps/apple/Sources/VitalRelayFeature/AppModel.swift`
- `apps/apple/Sources/VitalRelayFeature/MonitoringView.swift`
- `apps/apple/Sources/VitalRelayFeature/VitalRelayRootView.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/VitalRelay-Info.plist`
- `apps/apple/VitalRelay.entitlements`
- `apps/apple/VitalRelay.xcodeproj/project.pbxproj`
- `backend/src/vital_relay/application/health_context.py`
- `backend/tests/unit/test_health_snapshot_service.py`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthMetricRegistryTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthContextCoordinatorTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthIngestionAPIClientTests.swift`

## Known limitations and external evidence

- There is no watchOS target, `HKWorkoutSession`, live-workout stream,
  WatchConnectivity transport, or live Watch connection state. Slice 11B reads
  the latest stored iPhone-visible quantity sample per type.
- The Apple fall-detection entitlement/callback and genuine non-simulated fall
  event remain Slice 11C. The stored fall-count quantity is not a substitute.
- There is no anchored/observer query, background delivery, or automatic
  refresh. Every collection is an explicit foreground user action.
- Structured sleep stages, ECG records, workout records, activity summaries,
  and paired blood-pressure samples remain deferred until each has a typed
  cross-platform contract and representative fixture. Raw ECG voltage and raw
  continuous motion remain out of scope.
- This slice does not add Core Motion/pedometer-derived features or live routing.
- A signed physical iPhone, Apple Developer signing, a consenting account with
  visible samples, and a reachable HTTPS backend are still required to prove
  the real device path end to end.
- Vital Relay is a hackathon prototype and does not contact emergency services.

## Handoff to Slice 11C

The authenticated community graph now has real Apple capability wiring,
session-scoped cancellation, and the frozen non-simulated wearable-event API
from Slice 04. Slice 11C should add the Apple fall-detection entitlement/readiness
state and map a genuine callback into that event boundary without manufacturing
a fall, using stored health context only for presentation and audit.
