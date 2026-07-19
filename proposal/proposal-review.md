# Proposal Review and Recommendation

## Executive verdict

Do not finalize either proposal unchanged.

- Use `proposal-c.md` as the narrative shell: it has the clearer pitch, stronger demo arc, and more memorable explanation of offline improvement.
- Use `proposal-o.md` as the engineering substrate: it has the safer control model, better evaluation boundaries, typed tools, auditability, and credible promotion/rollback design.
- Narrow the product from medical-event detection to wearable-triggered emergency coordination. Live heart-rate data can establish that the Watch integration is real, but the prototype should not imply that a 1 Hz heart-rate stream diagnoses arrhythmia, ventricular fibrillation, or breathing distress.
- Make one AlphaEvolve-inspired policy-improvement loop the required differentiator. Only call the system DGM-inspired or recursively self-improving if a changed improvement operator is inherited and demonstrably used to create a later generation.

The recommended merged proposal is in `proposal-final.md` under the name **Vital Relay**.

### Implementation decision after review

The final proposal promotes five advanced goals into the committed build: Apple fall-event ingestion, NemoClaw sandboxing, PostGIS with live routing, fixed source-reviewed first-aid presentation, and a DGM-inspired inherited-improver experiment. Each has a defined fallback or claim boundary so an entitlement delay, sandbox integration issue, routing outage, or inconclusive DGM comparison cannot break the core incident demo.

## At-a-glance comparison

| Dimension | Aegis (`proposal-c.md`) | GuardianMesh (`proposal-o.md`) | Recommended synthesis |
|---|---|---|---|
| Pitch clarity | Excellent | Buried in a design specification | Use Aegis's concise story |
| Safety boundary | Partly credible, but overclaims detection | Strong and explicit | Use GuardianMesh's state machine and non-goals |
| Hackathon feasibility | Too broad for three people | Far beyond a 48-hour build | One incident, one responder flow, one optimizer |
| Demo energy | Very strong | Technically rich but too long | Four-minute incident plus improvement reveal |
| Medical credibility | Weakest area | More careful, but still uses an arbitrary risk score | Coordinate a fall/no-response or manual SOS; do not diagnose |
| Self-improvement proof | Clear story, loose experimental controls | Strong experimental definition, too much machinery | Typed policy mutations, protected evaluator, one held-out test |
| Product authenticity | Strong emphasis on real components | Over-relies on simulation | Live Watch signal + labeled replay + real allowlisted notifications |
| Architecture | Simple but missing several control details | Safe but sprawling | Two device clients, one backend, one command center |

---

## Critique of `proposal-c.md` — Aegis

### What it does well

1. **The differentiator is immediately understandable.** The line “watch to alert, then improve offline” is much easier to remember than a generic health-monitoring pitch.
2. **The two-tier architecture is directionally correct.** Keeping the LLM out of the signal-processing path is safer, faster, and easier to demonstrate.
3. **The confirmation countdown is excellent product design.** It creates a natural false-alarm story and a clear state transition.
4. **The improvement loop is tangible.** The proposal names the mutable artifacts, evaluator, archive, safety floor, and before/after evidence.
5. **The four-minute demo has a compelling shape.** Live signal, incident, coordinated response, post-incident improvement, and replay is the right dramatic sequence.
6. **It is appropriately explicit about not calling 911.** A real call or SMS to a consenting demo contact is a legitimate integration test, provided it is never described as EMS dispatch.

### What must change

#### 1. The medical claim is too broad

The proposal promises cardiac anomaly, fall, and breathing-distress detection from Apple Watch data. That is the largest credibility risk in the document.

- A live workout heart-rate stream is useful context, but it is not equivalent to a diagnostic ECG or a beat-to-beat arrhythmia detector.
- MIT-BIH contains clinical ECG recordings, not Apple Watch PPG. A detector that performs well on those ECG annotations has not thereby been validated on wrist data.
- Blood-oxygen readings are intermittent, device- and region-dependent, and Apple explicitly describes them as fitness/wellness measurements rather than medical measurements.
- Respiratory rate and HRV in HealthKit should be treated as stored context, not guaranteed continuous telemetry.

**Change:** make the core event a manual SOS or fall/no-response workflow. Present heart rate as live context, not the medical trigger. A replayed fall sequence can enter through the same event schema, with a visible `REPLAYED DATA` label.

#### 2. “Everything is real” is rhetorically strong but technically brittle

The proposal mixes four different meanings of real:

- live sensor data;
- replayed real-world recordings;
- staged physical activity;
- real outbound communications to consenting teammates.

A replayed clinical signal is real recorded data, but the stage incident is still a simulation. Calling every layer “real” invites judges to look for a contradiction.

**Change:** use the more defensible phrase: **live wearable context, replayed or simulated emergency events, real software actions to allowlisted demo participants, and no real emergency dispatch.**

#### 3. Some proposed demonstrations are unsafe or unnecessary

The suggestions to use breath-holds and controlled falls on a mat add health and injury risk without strengthening the proof. Public fall datasets also contain staged falls, so staging one on stage does not solve the data-validity problem.

**Change:** use a manual SOS, Apple-authorized fall event if the entitlement is available, or a clearly labeled replay. Do not ask a teammate to induce oxygen changes or fall for the demo.

#### 4. The common “episode” schema hides incompatible datasets

The document implies that cardiac, respiratory, and fall datasets can all become episodes containing heart rate, SpO2, respiratory rate, accelerometer magnitude, and activity flags. Most named sources do not contain that complete synchronized set, and they were collected from different devices and populations.

**Change:** separate the two benchmarks:

- **Operational coordination benchmark:** synthetic but deterministic incident scenarios with labeled tool outcomes, delays, responder states, and safety constraints.
- **Optional signal benchmark:** one modality and one event family, such as fall/ADL motion data, evaluated separately and never presented as clinical validation of the Apple Watch pipeline.

#### 5. The agent is allowed too much triage authority

The proposed triage prompt decides whether to escalate. In a health workflow, that decision should not depend on a mutable LLM prompt.

**Change:** the deterministic state machine owns escalation after manual SOS, explicit “I need help,” or timeout following an accepted trigger. The agent coordinates responders, contact order, summaries, and role assignment only after the state permits those actions.

#### 6. Three optimization mechanisms are too many

OpenEvolve, GEPA/DSPy, and a DGM-style recursive loop are each substantial. Building all three will produce shallow integrations and an unreliable demo.

**Change:** require one AlphaEvolve-inspired typed-policy optimizer. Preserve diverse candidates and show a real diff. Treat prompt optimization and inherited improver mutation as stretch goals.

#### 7. The weekend scope is not credible

The proposed build includes Watch and iPhone apps, live health streaming, multiple event detectors, responder discovery, maps, AED routing, push, Twilio, first-aid UI, vLLM, Deep Agents, NemoClaw, three optimization systems, and a metrics dashboard.

**Change:** cut to one Watch/iPhone flow, one responsive web application with role-specific views, one incident type, one nearby responder, one designated contact, one seeded AED, and one policy evolution loop.

#### 8. Several lines read like conclusions before experiments

Phrases such as “what makes it a winner,” “crushes false alarms,” and illustrative jumps like `0.71 -> 0.88` can sound like fabricated results unless the team has already measured them.

**Change:** describe those numbers as acceptance targets until reproducible runs exist. On the final slide, distinguish measured results from goals.

#### 9. Promotion needs stronger controls

The proposal mentions human approval and hot-swapping, but it does not fully specify artifact identity, rollback, protected tests, or an atomic active version.

**Change:** adopt GuardianMesh's immutable candidate bundle, protected evaluator, content hash, operator approval, active-version pointer, and one-click rollback.

### Bottom line on Aegis

Aegis is the better pitch document, but not yet the safer or more credible build plan. Its strongest contribution is the story. Its weakest contribution is the claim that the prototype can detect several serious medical conditions from the proposed wearable signals.

---

## Critique of `proposal-o.md` — GuardianMesh

### What it does well

1. **It draws a clear safety boundary.** Simulation labels, blocked emergency numbers, allowlisted devices, fixed protocols, and no live self-rewriting are all appropriate.
2. **The deterministic state machine is the correct control plane.** Tool availability by incident state is stronger than relying on prompt instructions.
3. **The tool design is implementation-ready.** Typed inputs and outputs, idempotency, timeouts, permission checks, and audit logs are exactly the right requirements.
4. **The self-improvement section defines evidence instead of relying on spectacle.** Protected tests, a typed mutation contract, quality-diversity archive, artifact hashes, and lineage records are persuasive.
5. **The DGM section is unusually precise.** It correctly distinguishes a fixed optimizer from an inherited change to the improvement operator and proposes an equal-budget counterfactual.
6. **Promotion and rollback are excellent.** This is one of the strongest parts of either proposal and should survive nearly unchanged in the merged architecture.
7. **The benchmark focuses on observable behavior.** Grading tool calls and state transitions is more reliable than grading the agent's prose.

### What must change

#### 1. It is a design specification, not a hackathon proposal

At roughly 79 KB and 2,478 lines, the document makes the core idea hard to find. It is valuable as an internal architecture reference, but a judge or teammate cannot quickly identify the wedge, must-build path, or fallback.

**Change:** keep the judge-facing pitch to roughly 1,500-2,500 words. A combined proposal and implementation plan may reach roughly 5,000 words after the selected advanced goals, but endpoint catalogs, SQL, and extended methodology should remain outside the submitted pitch.

#### 2. The “must-build” list is impossible in 48 hours

Fourteen required capabilities, five client experiences, geospatial infrastructure, local inference, two evolutionary systems, and twenty success criteria are a multi-sprint project. The suggested team split assumes six specialized people.

**Change:** define six must-build outcomes and put everything else into stretch. Use a single responsive web app for command-center, responder, and dispatcher views.

#### 3. It overcorrects toward simulation

Labeling every medical emergency and EMS action as simulated is responsible, but making the Watch stream optional until phase six weakens the wearable premise.

**Change:** guarantee one live Apple Watch signal and live check-in early. Keep the dangerous event replayed and the dispatch simulated. Send a real notification only to registered demo devices or numbers.

#### 4. The risk score looks more scientific than it is

A displayed score of `92` derived from hand-selected weights can imply clinical calibration. Adding “demo thresholds” does not fully remove that impression.

**Change:** use an explainable event state rather than a pseudo-clinical score: `impact observed`, `stillness observed`, `check-in timed out`, `incident opened`. If a score is needed internally, do not present it as health risk probability.

#### 5. The mutable agent bundle is too large

Allowing mutations to prompts, policies, workflow code, topology, failure analysis, parent selection, and evaluation allocation makes attribution and safety difficult.

**Change:** for the required demo, mutate only `coordination_policy.yaml` through bounded, typed fields. Unlock one prompt only after the policy loop is reliable. Source-code mutation and topology evolution are stretch goals.

#### 6. The DGM proof can consume the entire hackathon

The two-generation proof and counterfactual are intellectually strong, but four candidates per branch may produce a noisy comparison. A local model may also generate invalid source changes frequently.

**Change:** precompute the lineage, use fixed seeds, repeat the equal-budget comparison, and label it “DGM-inspired” unless the inherited operator truly creates the next generation. Do not make this proof a dependency for the incident demo.

#### 7. The hidden suite is at risk of becoming a validation suite

If candidate selection or promotion repeatedly consults hidden scores, those cases are no longer an untouched final test even when the candidate cannot read their contents.

**Change:** use three partitions:

1. development scenarios and detailed failure packets for mutation;
2. protected validation scenarios for candidate selection;
3. a final test set run only once for the chosen candidate or at a declared limited cadence.

#### 8. The stack contains too many unresolved choices

Repeated alternatives—PostgreSQL or SQLite, MapLibre or Mapbox or MapKit, WebSocket or HTTPS, Docker or NemoClaw—make the proposal look undecided.

**Change:** name one default stack and one fallback. For example: FastAPI, SQLite, Next.js, WebSocket, Deep Agents, vLLM; NemoClaw as the preferred sandbox with Docker fallback.

#### 9. The first-aid feature needs stricter framing

Placeholder medical instructions are not suitable for a live product demonstration, and an LLM should not adapt medical content beyond presentation.

**Change:** omit first-aid instructions from the minimum demo. If included, use a small fixed, source-reviewed protocol and let the agent select only its identifier.

#### 10. The demo is too long and too technical

Eight scenes plus an archive explanation, DGM hashes, counterfactual, promotion, and incident replay will exceed most judging slots.

**Change:** use four scenes: live monitoring, incident coordination, one visible policy mutation, and improved replay with held-out evidence.

### Bottom line on GuardianMesh

GuardianMesh is the better internal engineering plan, but a weaker submission document. Its strongest contributions are the state machine, typed tools, protected evaluator, and promotion model. Its weakest contribution is uncontrolled scope.

---

## Key conflicts and how to resolve them

| Conflict | Resolution |
|---|---|
| “Everything real” vs. “everything simulated” | Live Watch context, replayed emergency, real allowlisted notifications, simulated dispatch |
| Evolve medical thresholds vs. evolve coordination policy | Evolve coordination policy; keep event opening deterministic |
| LLM can decide escalation vs. state machine controls escalation | State machine is authoritative; agent acts only through state-allowed tools |
| Three optimization systems vs. full recursive agent bundle | One typed-policy optimizer first; then one bounded inherited-improver experiment |
| Heterogeneous physiological datasets vs. scenario fixtures | Use operational scenarios for the main benchmark; keep signal research separate |
| Many native and web apps vs. one coherent demo | Watch/iPhone check-in plus one role-based responsive web app |
| Aegis vs. GuardianMesh | Use **Vital Relay**, which matches the repository and describes passing an incident to the right helper |

## Final scope recommendation

### Must build

1. Live Apple Watch heart rate or workout status plus a Watch/iPhone safety check.
2. Manual SOS or clearly labeled fall/no-response replay through one normalized event schema.
3. Deterministic incident state machine with audited transitions.
4. One local Deep Agent through vLLM using typed, state-authorized coordination tools.
5. One responder acceptance flow, one designated contact notification, a seeded AED location, and a shared timeline.
6. One offline AlphaEvolve-inspired loop that proposes typed coordination-policy patches, evaluates them on protected scenarios, and promotes a hashed winner after operator approval.

### Committed advanced goals selected after review

1. Apple fall-detection entitlement and real fall event ingestion.
2. NemoClaw sandboxing, with Docker as fallback.
3. PostGIS and live routing, with static venue coordinates and route instructions as fallback.
4. Fixed, versioned, source-reviewed first-aid protocol presentation.
5. DGM-inspired inherited mutation of the failure analyzer rules or mutation prompt.

Twilio voice remains optional; SMS, push, or an in-app notification is sufficient for the committed demo.

### Explicitly drop from the hackathon claim

- Heart-attack, VF/VT, AFib, or respiratory-distress prediction.
- Controlled falls, breath-holds, or induced physiological distress.
- Any real emergency-service call or text.
- An LLM-authored medical protocol.
- Multi-agent topology evolution.
- Five separate client applications.
- Unverified before/after metric claims.

## Final recommendation

Build **Vital Relay** from the merged proposal. It preserves the memorable self-improvement reveal while making the health claim, system authority, evaluation, and weekend scope defensible.

The central sentence should be:

> Vital Relay turns a wearable safety event into a coordinated, auditable response among trusted nearby helpers, then improves its coordination policy offline against protected replay scenarios—without diagnosing the wearer or contacting real emergency services.
