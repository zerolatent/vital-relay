# Vital Relay Frontend Concept

> **Architecture update (Slice 07):** The route-based web shell described below
> is no longer a product target. Its information hierarchy and truth-labeling
> principles are being reused inside one native Vital Relay app with isolated
> community, responder, and command personas. See
> [07-native-persona-live-views.md](implementation-slices/07-native-persona-live-views.md)
> and [ios-app-design-proposal.md](ios-app-design-proposal.md). The remaining
> route sketches are historical design input, not an implementation plan.

**Direction:** Signal Flow  
**Surfaces:** Command center, responder link, simulated dispatcher, evolution lab  
**Design goal:** Make a complex emergency-coordination system feel calm, legible, and trustworthy without making it look clinical or alarmist.

## Product experience principles

1. **Calm until action is required.** Monitoring is quiet and spacious. Urgency appears only around an active incident or a destructive operator action.
2. **One dominant story per screen.** The command center answers “what is happening now?” The responder view answers “what do I need to do next?” The evolution view answers “is this candidate safer and better?”
3. **Show the relay, not a wall of telemetry.** Wearable context supports the incident but does not dominate it. The visual focus is the handoff from wearer to responder, AED, contact, and simulated dispatcher.
4. **Label truth explicitly.** Replay, simulation, fallback, freshness, sandbox, and protocol-version status are persistent text labels, never color-only hints.
5. **Progressive disclosure.** The default view shows current state and next action. Raw hashes, capability details, health context, and audit metadata open in drawers or inspectors.
6. **Motion explains state.** Fluid graphics should show progress or connection, not decorate every surface. Reduced-motion mode replaces animation with static gradients and state changes.

## Concept directions considered

| Concept | Visual idea | Strength | Tradeoff |
|---|---|---|---|
| **Signal Flow — recommended** | Soft pearl canvas, translucent cards, a violet-to-aqua relay ribbon, coral reserved for escalation | Most approachable; makes coordination visible; strong on desktop and mobile | Requires disciplined motion so it does not feel playful during escalation |
| **Nightwatch** | Deep navy command center, luminous cyan paths, compact operational panels | Dramatic on a demo screen; excellent map contrast | Feels more like emergency dispatch software and less like a humane connected-health product |
| **Human Relay** | Warm off-white canvas, large people/role cards, softer apricot and plum gradients | Friendly and mobile-first; emphasizes helpers over infrastructure | Less effective for dense operator evidence and the evolution lab |

Signal Flow should be the base system. A darker map can be used inside it without turning the entire product into Nightwatch.

## Visual language

### Brand metaphor

The core graphic is a **relay ribbon**: one continuous, softly glowing path that passes through small nodes representing the wearer, responder, AED, contact, and dispatcher. It can appear as:

- the incident progress indicator;
- the route overlay on the map;
- a subtle empty-state illustration;
- the lineage connector in the evolution lab;
- a compact animated mark in the product header.

The ribbon becomes tighter and more directional as an incident advances. It should never resemble an ECG trace, because the product is not making medical judgments.

### Color tokens

| Token | Value | Use |
|---|---:|---|
| Canvas | `#F5F8FA` | Main background |
| Surface | `rgba(255,255,255,0.82)` | Cards with subtle backdrop blur |
| Ink | `#102A36` | Primary text |
| Muted ink | `#607781` | Secondary text |
| Border | `#DCE7EA` | Dividers and inactive controls |
| Relay violet | `#695CFF` | Primary gradient start and verifying state |
| Relay aqua | `#2ED6C4` | Primary gradient end and response-active state |
| Live blue | `#4E9CFF` | Monitoring and live wearable data |
| Escalation coral | `#FF6B61` | Escalating state and urgent action only |
| Warning amber | `#F7B955` | Stale, fallback, or degraded provider state |
| Resolved green | `#4FB88A` | Resolved and passed safety gates |

Primary gradient:

```css
linear-gradient(120deg, #695CFF 0%, #4E9CFF 48%, #2ED6C4 100%)
```

Ambient background gradient:

```css
radial-gradient(circle at 18% 12%, rgba(105,92,255,.13), transparent 34%),
radial-gradient(circle at 88% 6%, rgba(46,214,196,.12), transparent 32%),
#F5F8FA
```

Escalation gradients use coral locally; they never recolor the whole application red.

### Typography and geometry

- **Interface:** Geist Sans or Inter, with tabular numerals for time, distance, heart rate, and scores.
- **Evidence/code:** Geist Mono for hashes, policy paths, versions, and diffs.
- **Headings:** medium weight, slightly tight letter spacing; avoid oversized marketing typography inside the product.
- **Cards:** 20–24 px corner radius, 1 px cool-gray border, very soft shadow.
- **Controls:** minimum 44 px touch target; responder primary actions use 52–56 px height.
- **Spacing:** 4 px base scale with 12, 16, 24, 32, and 48 px as the common rhythm.

## Information architecture

```text
Vital Relay
├── Command                 /command
│   ├── Monitoring overview
│   ├── Active incident
│   └── Incident history
├── Responder invite        /respond/{token}
├── Simulated dispatcher    /dispatcher
└── Evolution lab           /evolution
```

The desktop shell uses a slim left rail. On smaller widths it becomes a bottom navigation bar. `DEMO SYSTEM — NO EMERGENCY SERVICE CONTACTED` remains pinned along the top of every route.

## Screen design

### 1. Command center: monitoring

The default screen should feel spacious, not like an ICU monitor.

```text
┌ DEMO SYSTEM — NO EMERGENCY SERVICE CONTACTED ───────────────────────┐
│ rail │ Good morning                         Live • Apple Watch       │
│      │ ┌ Wearer status ─────────────────┐  ┌ System readiness ────┐ │
│      │ │ Connected       78 BPM         │  │ 8 / 8 checks ready   │ │
│      │ │ soft live sparkline            │  │ sandbox • route • db │ │
│      │ └─────────────────────────────────┘  └───────────────────────┘ │
│      │                                                               │
│      │ ┌ Recent activity ──────────────────────────────────────────┐ │
│      │ │ Timeline with source, freshness, replay, and fallback     │ │
│      │ └────────────────────────────────────────────────────────────┘ │
└──────┴───────────────────────────────────────────────────────────────┘
```

Only live, allowlisted workout metrics receive prominent cards. Recent or historical health context stays in a right-side drawer and always shows age, source, acquisition class, and availability.

### 2. Command center: active incident

When an incident starts, the page smoothly reorganizes around one large **incident canvas** rather than opening many alert cards.

```text
┌ REPLAYED INCIDENT • DEMO SYSTEM ────────────────────────────────────┐
│ rail │ Fall + no response                         00:18 elapsed      │
│      │ [VERIFYING] Wearer check timed out                            │
│      │                                                               │
│      │ ┌ relay path / map ─────────────┐ ┌ response team ─────────┐ │
│      │ │ Wearer ●──● Responder ──● AED │ │ Maya    accepted       │ │
│      │ │ route, distance, fallback     │ │ Jordan  retrieving AED │ │
│      │ └───────────────────────────────┘ └─────────────────────────┘ │
│      │                                                               │
│      │ ┌ next action ──────────────┐ ┌ live timeline ─────────────┐ │
│      │ │ Awaiting observation      │ │ timestamped state/actions │ │
│      │ │ [Open responder view]     │ │ source + simulated labels │ │
│      │ └───────────────────────────┘ └─────────────────────────────┘ │
└──────┴───────────────────────────────────────────────────────────────┘
```

The state pill is always text plus icon. The five-state progress path is visible but secondary:

`Monitoring → Verifying → Escalating → Response active → Resolved`

Health context collapses to a compact “Context only — not used for escalation” strip. Operator kill and reset controls live in a clearly separated utility menu and require confirmation.

### 3. Responder mobile view

The invitation is designed as a focused mobile task, not a compressed dashboard.

Before acceptance:

- approximate distance and estimated walking time;
- requested skill and high-level incident type;
- explicit note that exact location is hidden until acceptance;
- two large actions: `Accept` and `Decline`;
- persistent replay/demo label.

After acceptance:

- exact route takes the upper half of the screen;
- a sticky bottom task sheet shows assignment and the single next action;
- responder observations use large segmented controls;
- fixed protocol steps appear one at a time, with source and version always visible;
- no generative chat surface.

### 4. Simulated dispatcher

This route is intentionally plain and unmistakably simulated:

- large `SIMULATED DISPATCHER` header and striped simulation banner;
- incident summary and coordination status;
- three allowed actions: acknowledge, mark en route, close;
- no phone keypad, real emergency-service branding, or ambiguous “call” affordance.

### 5. Evolution lab

The evolution lab uses the same shell but shifts to an evidence-first split view:

- baseline and candidate metric cards at top;
- exact typed policy diff in a central inspector;
- protected safety gates as a pass/fail checklist;
- compact lineage graph using the relay ribbon as its connector;
- improved/regressed scenarios shown side by side;
- promote and rollback in a separate approval panel with hash and active-version summary.

The primary comparison should emphasize behavior, for example “qualified responder accepted 12 s sooner,” rather than presenting one opaque score.

## Interaction and motion

- **Page transitions:** 180–240 ms opacity and 8 px translation.
- **Relay ribbon:** very slow 6–8 s gradient drift while monitoring; 1.5–2 s directional flow during an active handoff.
- **Incident transition:** cards reflow into the incident canvas; avoid flashes, shaking, or continuous pulsing.
- **Timeline:** new items slide in once and then remain still.
- **Map route:** draw once when a route arrives; fallback switches use a short crossfade and a visible `STATIC ROUTE` label.
- **Reduced motion:** remove drift and route drawing; use static gradient fills and immediate layout changes.

## Reusable component set

- `SafetyBanner`
- `StatusPill`
- `SourceBadge`
- `FreshnessBadge`
- `IncidentStatePath`
- `RelayRibbon`
- `MetricCard`
- `ReadinessCard`
- `IncidentCanvas`
- `ResponderCard`
- `RouteMap`
- `TimelineEvent`
- `ProtocolStepCard`
- `EvidenceGate`
- `PolicyDiff`
- `LineageGraph`
- `OperatorActionPanel`

## Demo-first responsive targets

- **Primary:** 1440 × 900 desktop/laptop for the command center and evolution lab.
- **Responder:** 390 × 844 mobile viewport.
- **Minimum desktop:** 1180 px before the rail collapses.
- **Tablet:** command center becomes one main column with a collapsible timeline drawer.

## Accessibility and safety checks

- Meet WCAG AA contrast on every text and control state.
- Never use gradient color alone to communicate incident state or pass/fail.
- Pause nonessential motion when the tab is unfocused and honor `prefers-reduced-motion`.
- Keep urgent coral distinct from replay amber and active-response aqua.
- Require confirmation for kill, reset, promote, rollback, and resolve.
- Keep exact location absent from both the DOM payload and UI before responder acceptance.
- Announce new incident state and responder assignments through an ARIA live region without repeatedly announcing heart-rate updates.

## Recommended first implementation slice

Build the shell and four routes against fixtures before connecting real APIs:

1. Shared responsive shell, tokens, safety banner, and badges.
2. `/command` in monitoring and replayed-active-incident states.
3. `/respond/{token}` before and after acceptance, proving location redaction.
4. `/dispatcher` simulation state.
5. `/evolution` with baseline/candidate fixtures and a visible safe diff.
6. Mocked WebSocket timeline behind the same transport interface as the future backend.

This produces the complete demo narrative early while keeping the real API integration an environment switch.
