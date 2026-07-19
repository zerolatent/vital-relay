# iOS UI-01: Living Relay Monitoring and Safety Check

**Status:** Complete and simulator-verified  
**Scope:** Fixture-driven iPhone frontend foundation  
**Does not complete:** Swift health-contract parity, HealthKit collection,
WatchConnectivity, Apple fall ingestion, or backend incident integration

## Outcome

Vital Relay now has a native iPhone application that demonstrates the first
consumer-facing vertical slice:

```text
dark monitoring
    → deliberate SOS hold
    → timed safety check
    ├── I'm okay → monitoring
    ├── I need help → bounded help-request acknowledgement
    └── timeout → bounded help-request acknowledgement
```

The flow runs entirely through a typed fixture provider and injectable date
source. Views do not advance the incident locally or depend on backend,
HealthKit, Watch, map, notification, or model availability.

## Implemented

- Native Xcode iPhone application under `apps/apple/VitalRelay.xcodeproj`.
- Local Swift package with macOS-runnable feature tests.
- OLED-dark semantic design tokens.
- Persistent `DEMO SYSTEM — NO EMERGENCY SERVICE CONTACTED` boundary.
- Explicit `REPLAYED DATA` status.
- Deterministic replay monitoring state with:
  - `78 BPM` context value;
  - Apple Watch replay source;
  - connection state;
  - context-only label.
- A 720-particle SwiftUI `Canvas` heart with a restrained sample-derived pulse.
- A particle verification halo with a textual countdown.
- Static orbit/SF Symbol fallback when Reduce Motion is enabled.
- Deliberate 0.8-second SOS hold with a VoiceOver activation alternative.
- Large `I'm okay` and `I need help` controls.
- Provider-owned intent validation and state transitions.
- A bounded help-request acknowledgement that explicitly stops before responder
  coordination.
- Direct-launch fixture arguments for visual QA:
  - `--fixture-safety-check`;
  - `--fixture-help-requested`.

## Safety boundary

- Health rate is display context only and cannot alter provider transitions.
- Replay status comes from immutable fixture state rather than a view-local
  presentation toggle.
- The particle visual receives only a bounded presentation state.
- `I need help` and timeout record a fixture intent; they do not claim that a
  responder, contact, dispatcher, or emergency service was reached.
- No ECG waveform, diagnostic label, or generated medical content appears.
- No exact wearer location exists in this slice.

## Verification

```text
Swift package build: passed
Swift Testing:       7 tests in 2 suites passed
iOS simulator build: passed for arm64 iOS Simulator
Simulator launch:    passed on a booted iPhone 17 Pro / iOS 26.2
Visual QA:           monitoring, safety check, and timeout acknowledgement
```

Tests cover:

- deterministic 20-second expiry;
- countdown rounding and lower bounds;
- `I'm okay` returning to monitoring;
- `I need help` recording the bounded help-request state;
- timeout using the same bounded path;
- invalid intents leaving provider state unchanged;
- direct-launch safety-check fixture initialization.

## Run locally

```bash
cd apps/apple
swift test
open VitalRelay.xcodeproj
```

Run the `VitalRelay` scheme on an iPhone simulator. The default launch opens
monitoring. Add `--fixture-safety-check` or `--fixture-help-requested` under the
scheme's launch arguments to inspect an individual scene.

## Known limitations

- The fixture models are not yet Codable equivalents of the frozen backend
  health and incident contracts.
- The SOS hold and response controls have not yet been driven by an XCUITest;
  provider transitions are unit-tested and all states were simulator-launched.
- The particle renderer has not been profiled on a physical iPhone or under Low
  Power Mode.
- Haptics are not implemented in this first slice.
- The iPhone app does not call backend health, event, incident, check-in, or
  timeline endpoints.
- The Watch app target remains future work.

## Handoff to UI-02

The next frontend feature should connect the existing monitoring and safety
check views to the frozen real incident contracts:

1. add Swift Codable models for wearable events, incidents, transitions,
   check-ins, and timeline entries;
2. add authenticated incident/check-in HTTP transport behind
   `VitalRelayDataProvider`;
3. use backend `verification_expires_at` as countdown authority;
4. preserve the fixture provider as the required replay/demo fallback;
5. map `escalating` to a minimal Getting Help scene without claiming responder
   coordination until Slice 05 dispatch data exists.

Responder constellation and route UI should begin only after the Slice 05
dispatch read model freezes.

**Status update:** This handoff is complete in
[iOS UI-02: Live Incident Client](ios-ui-02-live-incident-client.md). Slice 05's
dispatch read model is also frozen; its consumer UI remains the UI-03 boundary.
