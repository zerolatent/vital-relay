# Wave 1 — Apple Watch Foundation

## Outcome

Vital Relay now contains a real embedded watchOS companion target and a shared
`VitalRelayWatchTransport` module backed by Apple's `WCSession`. Both the iPhone
and Watch activate the singleton session during their SwiftUI app initialization,
before any feature view needs the connection.

This slice establishes transport and capability boundaries only. It does not
create a `CMFallDetectionManager`, request fall authorization, start a workout,
query HealthKit, manufacture a fall, or publish a placeholder health value.
Those producers must provide genuine system callbacks or samples in later
slices.

## Target and capability configuration

`VitalRelayWatchApp` is a source-bearing watchOS 11 application with the stable
bundle identifier `com.vitalrelay.app.watchkitapp` and companion identifier
`com.vitalrelay.app`. The iOS target embeds the built Watch application in its
Watch content directory and depends on the Watch target.

The Watch plist contains the required fall-detection and read-only HealthKit
usage descriptions. Its entitlements declare:

- `com.apple.developer.health.fall-detection`;
- `com.apple.developer.healthkit`; and
- `com.apple.developer.healthkit.background-delivery`.

The fall-detection entitlement is restricted. Declaring it is the correct
product capability boundary, but it does not substitute for approval and a
matching provisioning profile from Apple. Apple documents both the
[entitlement key](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.health.fall-detection)
and the requirement to configure the manager early in the app lifecycle in
[`CMFallDetectionManager`](https://developer.apple.com/documentation/coremotion/cmfalldetectionmanager).

## Versioned transport contract

Every message uses a `WatchMessageEnvelope` with:

- schema version `1`;
- a unique message ID;
- a non-optional correlation ID;
- a stable idempotency key;
- an envelope creation time; and
- one typed payload: critical event, telemetry snapshot, or acknowledgement.

Decoding rejects unsupported schema versions and empty idempotency keys. The
critical-event payload currently defines the contract needed for a genuine fall
callback: event ID, event time, event kind, and the system-reported user
resolution. It does not contain a fall detector or a fallback event generator.

Telemetry uses explicit metric and unit enums. Samples require a caller-supplied
sample ID and measurement timestamp, and non-finite values are rejected at the
contract boundary. Each metric also requires its canonical unit, preventing a
numerically valid sample from being mislabeled on the wire.

## Critical-event delivery

Critical messages use their own durable path:

1. `sendCriticalEvent` writes the envelope to an atomic JSON outbox before it
   asks WatchConnectivity to transfer anything.
2. `transferUserInfo` provides the system-managed background transfer, so
   temporary lack of reachability does not discard the event.
3. A restart compares the durable outbox with
   `outstandingUserInfoTransfers`; an already-owned system transfer is not
   submitted again.
4. The receiver atomically stores a critical envelope in its durable inbox
   before returning a transport acknowledgement.
5. The inbox deduplicates both pending and previously handled idempotency keys.
   A duplicate transfer is acknowledged again but is not emitted to the feature
   handler again.
6. The sender removes its outbox entry only after receiving that acknowledgement.
7. A future consumer removes a received event with
   `markReceivedCriticalEventHandled` only after its own durable product action
   succeeds.
8. A failed critical `WCSessionUserInfoTransfer` schedules a single retry for
   its idempotency key. Retries use capped exponential backoff (1, 2, 4, 8, 16,
   32, then 60 seconds), continue while the durable envelope is pending, and
   cancel on acknowledgement. A failed transfer therefore cannot strand the
   outbox or create a hot retry loop.

Corrupt or unwritable critical storage fails closed and downgrades readiness;
the implementation never replaces unreadable critical state with an empty
queue.

## Coalesced telemetry

Telemetry is deliberately separate from critical delivery. The buffer keeps at
most one latest sample per allowlisted metric and is bounded by the metric
registry size. A newer sample for the same metric replaces the old one; a stale
sample cannot overwrite a newer observation. If capacity is constrained, the
oldest metric is evicted.

The latest bounded snapshot uses `updateApplicationContext`, whose replacement
semantics match this last-known telemetry channel. Telemetry is not copied into
the durable critical queue and does not inherit critical-event retry semantics.

## Honest readiness

`WatchConnectivityReadiness` reports observed session and storage facts rather
than treating `isReachable` as a generic connected flag.

| State | Meaning |
|---|---|
| `canQueueCriticalEvents` | The local durable inbox and outbox are readable and writable so far |
| `canTransferTelemetry` | The session is supported and activated, and the platform-observable pairing/install checks pass |
| `canTransferCriticalEvents` | Telemetry transfer conditions pass and durable critical storage is available |
| `canSendImmediately` | The counterpart is currently reachable for an interactive transfer |
| `Background delivery ready` | Durable system transfer is available even though the counterpart is not currently reachable |

On iPhone, the state uses the real `isPaired` and `isWatchAppInstalled` values.
On watchOS, it uses the real `isCompanionAppInstalled` value; only pairing stays
unknown because watchOS does not expose the iPhone-side pairing property. The
Watch therefore cannot report either transfer channel ready when its companion
app is absent.

## Verification

Automated verification completed for this slice:

```text
Focused Watch transport tests: 10 tests passed
Generic unsigned watchOS build: succeeded for a generic watchOS device target
Generic unsigned iOS build:     succeeded with embedded Watch application validation
Plist/project validation:       passed
Diff whitespace validation:     passed
```

The focused tests cover envelope versioning, correlation/idempotency identity,
durable outbox restart, inbox deduplication before and after handling, corrupt
storage fail-closed behavior, bounded latest-per-metric telemetry, non-finite
value and metric/unit rejection, and the distinction between background
transfer and immediate reachability. They also verify that duplicate failure
notifications create only one retry task, an unavailable retry reschedules with
the next backoff delay, and the later attempt reaches submission.

## Primary files

- `apps/apple/VitalRelay.xcodeproj/project.pbxproj`
- `apps/apple/Package.swift`
- `apps/apple/VitalRelayWatchApp/VitalRelayWatchApp.swift`
- `apps/apple/VitalRelayWatchApp/VitalRelayWatch-Info.plist`
- `apps/apple/VitalRelayWatchApp/VitalRelayWatch.entitlements`
- `apps/apple/Sources/VitalRelayWatchTransport/WatchMessageEnvelope.swift`
- `apps/apple/Sources/VitalRelayWatchTransport/CriticalTransferRetryScheduler.swift`
- `apps/apple/Sources/VitalRelayWatchTransport/DurableCriticalEventStore.swift`
- `apps/apple/Sources/VitalRelayWatchTransport/CoalescedTelemetryBuffer.swift`
- `apps/apple/Sources/VitalRelayWatchTransport/WatchConnectivityReadiness.swift`
- `apps/apple/Sources/VitalRelayWatchTransport/WatchConnectivityTransport.swift`
- `apps/apple/Tests/VitalRelayWatchTransportTests/`
- `apps/apple/VitalRelayApp/VitalRelayApp.swift`

## External evidence not obtained

- No signed physical iPhone/Apple Watch pair was available to record installation,
  activation, temporary-unreachability delivery, background wake, or real
  HealthKit telemetry.
- Apple approval for the restricted fall-detection entitlement and a matching
  development/distribution profile were not available in this worktree.
- No genuine `CMFallDetectionEvent` was requested or observed, and no fall was
  simulated as a substitute.
- No workout or HealthKit producer was implemented, so the telemetry channel
  has compile-time and unit evidence but no claim of live physiological data.

## Integration handoff

The fall-ingestion slice should retain one `CMFallDetectionManager` from the
earliest Watch lifecycle point, request authorization only from presented UI,
and map only a genuine callback into `WatchCriticalEvent`. It must persist a
stable event ID for callback redelivery, call `sendCriticalEvent`, and invoke
Core Motion's completion handler only after the transport has durably queued
the event.

The live-health slice should request the intended read authorization and pass
only actual, timestamped HealthKit samples into `WatchTelemetrySample`. It
should not use a timer, fixture, or default numeric value when a sample is
missing.

The iPhone community composition should register `onCriticalEvent` and
`onTelemetry` handlers, then drain `pendingReceivedCriticalEvents()` on startup.
It should map a genuine received fall into the existing authenticated wearable
event API with `simulated: false`, preserve the Watch identifiers for backend
idempotency/correlation, and call `markReceivedCriticalEventHandled` only after
the backend has durably accepted or deduplicated the event. Telemetry remains
context-only and must not become incident escalation authority.
