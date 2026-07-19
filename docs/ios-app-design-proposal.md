# Vital Relay iOS App Design Proposal

**Recommended direction:** Living Relay  
**Primary platform:** iPhone + Apple Watch  
**Visual character:** OLED-dark, minimal, cinematic, fluid, humane  
**Core rule:** One scene, one state, one primary action.

See [the iOS frontend implementation plan](../proposal/ios-frontend-implementation-plan.md)
for the fixture-first build sequence, architecture, risk gates, and acceptance
criteria.

**Implementation update (Slice 07):** The wearer experience uses the relay
constellation for searching, invitation-pending, and responder-accepted states.
It consumes only device-token-redacted responder/AED coordination. The same
native app now contains isolated responder and command graphs: only the
accepted responder graph renders the exact MapKit static route, while the
wearer graph still has no exact route, coordinates, ETA, or responder token.

## 1. Why the direction changes

The web concept is useful for an operator, but it presents the product as a
dashboard. That is the wrong mental model for the person wearing the Watch or
carrying the iPhone.

The iOS experience should feel like a quiet safety companion. Most of the time
it shows a single living signal and almost no controls. During an incident, the
same signal transforms into a safety check, then a visible relay between the
wearer and the people responding. The interface does not add more panels as the
situation becomes urgent; it removes everything that is not the current state
or next action.

Command is now a separate credential-scoped persona inside the one native app.
Policy evolution, protected-test evidence, raw audit events, and promotion
controls can extend that command graph without entering the calm wearer scene.

## 2. Design concepts explored

### Concept A — Living Signal

A cloud of luminous particles forms a soft anatomical-abstract heart. It feels
alive without looking like a clinical scan. The center slowly folds inward and
releases a soft pulse, similar in spirit to the supplied Apple Watch reference.

- Best for the calm monitoring/home state.
- Immediately communicates Watch connection and live presence.
- Creates a memorable demo image with almost no interface chrome.
- Risks feeling like a health diagnosis if paired with medical copy, ECG lines,
  or red alert styling; those elements must be avoided.

### Concept B — Relay Constellation

A small point cloud surrounds the wearer. When help is needed, particles travel
outward and settle into the responder and public AED nodes. Future contact or
dispatcher nodes appear only when an implemented delivery result supports them.
A luminous strand connects only the active handoff.

- Makes the product's real differentiator—coordination—visible.
- Naturally carries the experience from verification to response.
- Can become a map route without a hard visual cut in the future
  responder-authenticated surface.
- Is less emotionally recognizable than a heart in the idle state.

### Concept C — Silent Orbit

An extremely minimal ring surrounds the current status. The ring breathes,
counts down, opens into an accepted relay arc, and closes when the incident is
resolved. A responder-only variant can extend into a route arc.

- Clearest and easiest to implement accessibly.
- Works especially well on Apple Watch.
- Has the least visual risk during an emergency.
- Is less distinctive for a hackathon demo and does not create the same
  emotional presence as the particle concepts.

## 3. Recommendation: Living Relay

Combine Concepts A and B into one continuous visual language:

```text
particle heart
    → verification halo
    → outward particle stream
    → relay constellation
    → accepted constellation (wearer) / route strand (responder only)
    → calm resolved glow
```

The transition itself explains what Vital Relay does: a wearable signal becomes
a coordinated human response. Concept C's orbit becomes the accessibility and
low-power fallback.

The heart is not a medical diagnostic visualization. Its rhythm is a restrained
representation of connection and the latest displayed heart-rate sample. Heart
rate never changes the incident state, the animation never claims clinical
continuity, and the UI continues to label health data as context only.

## 4. Experience architecture

The app is state-driven rather than tab-driven.

```text
MONITORING
├── Watch connected / live context
├── Hold for SOS
└── Settings and demo details in a sheet

VERIFYING
├── “Are you okay?”
├── I’m okay
├── I need help
└── visible timeout

ESCALATING
├── “Getting help”
├── responder and public-AED handoffs
└── cancel if state machine permits

RESPONSE_ACTIVE
├── accepted responder name/role
├── coarse distance band and public AED
├── no route, exact coordinate, or ETA
└── one next action

RESOLVED
├── clear outcome
└── return to monitoring
```

There is no permanent tab bar in the core incident flow. A compact top control
opens history, settings, Watch connection, permissions, and demo-source details
as sheets. The app returns to one full-screen scene when those sheets close.

## 5. Screen proposal

### Screen 1 — Monitoring

```text
┌──────────────────────────────┐
│ DEMO • NO 911 CONTACT        │
│                              │
│         particle heart       │
│           78                 │
│          BPM                 │
│                              │
│       Watch connected        │
│   Context only • Live now    │
│                              │
│        Hold for SOS          │
└──────────────────────────────┘
```

- Pure black/near-black field with no card grid.
- Particle heart occupies roughly 44% of the usable height.
- `78 BPM` uses large tabular numerals, but is secondary to connection status.
- One low-emphasis capsule provides Watch/source details.
- `Hold for SOS` is anchored above the home indicator and requires a deliberate
  hold rather than a casual tap.

### Screen 2 — Safety check

The heart expands into a wide halo and becomes quieter so the question owns the
screen.

```text
Are you okay?
Respond within 18 seconds

[ I'm okay ]
[ I need help ]
```

- A large, explicit countdown remains textual; the animated ring is redundant.
- `I'm okay` and `I need help` are equal-size controls with distinct labels and
  symbols. Color is not the only differentiator.
- Haptics mark the state change and the final countdown seconds; there is no
  flashing, shaking, or alarm-like screen animation.

### Screen 3 — Getting help

The heart dissolves. Particles travel toward the responder and public AED nodes.
The active invitation remains visually distinct from accepted status.

```text
Getting help

● Searching nearby      SEARCHING
● Main Hall AED         AED SITE

REPLAYED INCIDENT
```

- Each coordination result appears as a short human outcome, not agent
  reasoning.
- The current handoff glows; completed handoffs remain still.
- A pending server record is described as an invitation recorded and waiting
  for confirmation; the UI explicitly says the demo sends no phone notification.
- The persistent demo/replay boundary is concise but unmistakable.

### Screen 4 — Response active

The wearer constellation settles into an accepted state. It shows only the
accepted responder's display name, role, coarse distance band, and public AED
description. It does not become a wearer/responder map.

```text
Response active

Jordan K.               ACCEPTED
Trained volunteer • 100–250 m band

Main Hall AED           AED SITE
East wall beside the information desk

[ Refresh status ]
```

Acceptance means only that the responder accepted the invitation. The wearer UI
does not say `on the way`, show an ETA, infer arrival, or contain exact wearer or
responder coordinates. A separate responder-authenticated surface may render
the persisted static route after acceptance.

### Screen 5 — Resolved

The accepted relay strand contracts into a soft aqua halo. The outcome appears
in plain language: `You marked yourself safe`, `Responder arrived`, or another
exact state-machine result. A single `Done` action returns to monitoring.

### Responder mode

The responder invite may open through an authenticated signed universal link in
the same app or an App Clip-style focused surface. Notification/deep-link
delivery and responder credential bootstrap must exist before this flow exposes
decisions. It remains a separate task flow:

1. Before acceptance: approximate distance, requested role, accept, decline.
2. After acceptance: exact location and route.
3. On arrival: large observable-condition controls.
4. Fixed protocol: one immutable sourced step at a time.

There is no chat interface and no generated medical instruction.

## 6. Dark visual system

### Palette

| Token | Value | Use |
|---|---:|---|
| OLED canvas | `#030405` | Main scene |
| Elevated canvas | `#0B0D10` | Sheets and transient surfaces |
| Primary text | `#F4F7FA` | Headings and active values |
| Secondary text | `#8B929B` | Metadata and descriptions |
| Hairline | `#23272E` | Separation without card borders |
| Heart ember | `#FF3F62` | Particle heart core |
| Heart warmth | `#FF795B` | Particle-heart gradient edge |
| Relay violet | `#7657FF` | Verification / relay start |
| Relay blue | `#3E8BFF` | Active handoff |
| Relay aqua | `#27D9C2` | Accepted / response active |
| Replay amber | `#F2AD4A` | Replay and fallback labels |

Only one saturated gradient should dominate a screen. The monitoring heart uses
ember-to-warmth. Coordination uses violet-to-blue-to-aqua. Urgency comes from
copy, haptics, and action hierarchy—not by flooding the screen red.

### Type and symbols

- SF Pro / system text styles with Dynamic Type.
- Rounded numerals only for the central live value; standard text elsewhere.
- SF Symbols for connection, responder, location, AED, and confirmation.
- Avoid ultra-light font weights on black.
- Minimum 44 × 44 pt hit area; primary safety actions target 56 pt height.

### Materials

Use the current platform glass treatment only for navigation and important
interactive controls. Do not turn every content surface into glass. In content,
use true black, subtle elevation, and thin separators. If Reduce Transparency is
enabled, every translucent control becomes an opaque elevated surface.

## 7. Motion language

The reference feels like a GIF, but the production visual should be rendered
natively. A GIF has fixed timing, limited adaptation, weak accessibility control,
and unnecessary decode/battery cost. The design can still be prototyped and
shared as a short looping movie.

### Living Signal motion

- 4,000–8,000 particles form a soft heart-shaped field.
- Three depth bands move at slightly different speeds; movement stays near the
  center of the screen rather than the periphery.
- A slow 6–8 second ambient fold prevents the visual from feeling frozen.
- A restrained pulse can align with the currently displayed heart-rate sample,
  capped to a comfortable visual cadence and never treated as continuous
  medical-grade telemetry.
- A small percentage of particles trail for 200–350 ms, producing the soft
  drifting texture in the reference.
- State changes are morphs, not crossfades between unrelated GIFs.

### State transitions

| Transition | Motion | Duration |
|---|---|---:|
| Monitoring → verifying | Heart expands into a halo; question fades in | 450–600 ms |
| Verifying → escalating | Halo opens; particles stream to first relay node | 650–900 ms |
| Escalating → active | Responder node settles into an accepted aqua state | 700–1000 ms |
| Active → resolved | Relay strand contracts into a calm aqua glow | 500–700 ms |
| Any → reduced motion | Static state illustration plus crossfade | 150–200 ms |

No animation flashes, shakes, bounces repeatedly, or blocks an action.

## 8. Native implementation proposal

### Recommended stack

- **SwiftUI** for screens, state transitions, typography, controls, sheets, and
  accessibility.
- **SwiftUI `Canvas` + `TimelineView`** for the first deterministic particle
  prototype.
- **A small Metal shader or `MTKView` renderer** only if the Canvas version
  cannot sustain the target device frame rate or morph complexity.
- **Phase/keyframe animation** for the semantic transitions between incident
  states; particle simulation timing remains independent from business state.
- **MapKit** or the selected MapLibre surface only in the future
  responder-authenticated route screen, with the existing routing adapter
  labeling static versus live route data honestly.

### State boundary

```swift
enum LivingSignalState {
    case monitoring(sampleAge: Duration?)
    case verifying(secondsRemaining: Int)
    case escalating(activeHandoff: Handoff)
    case responseActive(responderAccepted: Bool)
    case resolved(outcome: Resolution)
}
```

This is a rendering state derived from the authoritative incident state machine.
It does not open, escalate, or resolve incidents by itself.

### Performance and fallbacks

- Use deterministic particle seeds so snapshot tests remain stable.
- Target 60 fps on the demo device, fall to 30 fps in Low Power Mode, and pause
  the simulation when the scene is inactive.
- Reduce particle count before lowering text or control responsiveness.
- Provide the Silent Orbit vector fallback when Metal is unavailable or the
  animation budget is missed.
- Avoid bundling multiple large GIF assets.

## 9. Accessibility and safety

- Respect Reduce Motion by replacing the particle simulation with a static
  heart/orbit and using crossfades for state changes.
- Respect the animated-images preference if any prototype loops are embedded.
- Respect Reduce Transparency with opaque elevated controls.
- Support Dynamic Type without placing critical copy inside the particle field.
- Give the visual a single VoiceOver summary such as `Watch connected, latest
  heart rate 78 beats per minute, live sample`; do not expose every particle.
- Use text and symbols in addition to all state colors.
- Keep the `DEMO SYSTEM — NO EMERGENCY SERVICE CONTACTED` boundary persistently
  visible. Add `REPLAYED INCIDENT` whenever the replay source is active.
- Do not animate raw ECG lines or imply rhythm analysis.

## 10. Proposed prototype sequence

### Design proof

1. Create three 5–8 second motion studies: Living Signal, Relay Constellation,
   and Silent Orbit.
2. Evaluate them on an actual iPhone at normal brightness and in Reduce Motion.
3. Lock the Living Relay morph and color system.

### Fixture-driven SwiftUI slice

1. Monitoring screen with a replayed `78 BPM` sample.
2. Safety check with deterministic 20-second countdown.
3. Getting-help state with scripted responder/contact tool outcomes.
4. Response-active redacted constellation and one bottom action.
5. Resolved state and reset.
6. Separately authenticated responder universal-link flow before/after location
   reveal, only after its invitation/credential bootstrap exists.

### Integration

Connect the visual state adapter to frozen incident and health contracts. Keep
the fixture provider available for demo rehearsal and visual regression tests.

## 11. Current Apple design basis

This proposal follows the current Apple guidance that motion should convey
status and feedback, that Reduce Motion should remove automatic/repetitive and
depth-heavy effects, and that Liquid Glass belongs primarily to navigation and
interactive chrome rather than the content layer.

- [Apple Human Interface Guidelines: Motion](https://developer.apple.com/design/human-interface-guidelines/motion)
- [Apple Human Interface Guidelines: Materials](https://developer.apple.com/design/human-interface-guidelines/materials)
- [Apple Human Interface Guidelines: Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode)
- [Apple Human Interface Guidelines: Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility)
- [SwiftUI Canvas](https://developer.apple.com/documentation/swiftui/canvas)
- [SwiftUI TimelineView](https://developer.apple.com/documentation/swiftui/timelineview)
- [SwiftUI Reduce Motion environment value](https://developer.apple.com/documentation/swiftui/environmentvalues/accessibilityreducemotion)

Dark is the primary hackathon and demo appearance. The tokens should still be
semantic so a system-respecting light appearance can be added without rewriting
the hierarchy or interaction model.
