# Aegis — a self-improving connected-health emergency-response agent

*Hackathon proposal. Working name "Aegis" (shield); rename freely.*

**One-liner:** A wrist-to-responder emergency system that detects a medical crisis from live wearable data in real time, orchestrates the right response through an on-device + agentic pipeline, and — the differentiator — **improves its own detection and triage overnight from the episodes it just handled**, with the improvement measured on a locked set of real physiological recordings.

**What makes it a winner:** most health-hackathon projects stop at "watch → alert." Ours closes the loop: it gets measurably better at telling real emergencies from false alarms every time it runs, using the exact AlphaEvolve/DGM techniques — offline, bounded, and quantified live on stage.

---

## Guiding principle: everything is real

No mocked data, no fake services. Concretely:

- **Real health data** — live Apple Watch stream + real open physiological datasets (named below).
- **Real inference** — a real open model served locally via vLLM.
- **Real detection** — real signal-processing/ML on real signals.
- **Real notifications** — real phone calls, SMS, and push via Twilio and APNs.
- **Real map + responders** — real geolocation, a real responder app, real public AED locations.
- **Real self-improvement** — real optimizer runs (OpenEvolve / GEPA / DSPy) on real labeled data, producing real, measured metric gains that we hot-swap in live.

**The one scoped component — the EMS call — is real, but points at a designated recipient, not 911.** The emergency alert is a *real* Twilio voice call + SMS to *real*, pre-designated emergency contacts (teammates/family) who consented to receive it. We do **not** connect to the 911 system: direct PSAP dispatch requires certified emergency infrastructure (e.g., a RapidSOS partnership) that a hackathon team can't obtain, and placing test calls to real 911 is illegal and diverts responders from actual emergencies. Routing to designated contacts is not a mock — it is the same real call pipeline the production system uses, and it is literally how consumer medical-alert (PERS) devices already work. Swap the recipient for a certified PSAP feed later and nothing else in the system changes.

---

## Architecture at a glance

Two tiers plus an offline improver. The rule that keeps it safe *and* fast: **no LLM in the time-critical path.**

```
   Apple Watch (live) ──┐
                        ├─▶  TIER 1: DETECTOR (on-device / edge, deterministic, <10ms)
   Public datasets      │     rolling personal baseline + rules → graded signal
   (for the offline     │            │  normal / watch / ALERT  + which vitals fired
    improver only)      │            ▼
                        │     CONFIRMATION ("Are you OK? escalating in 10s…")
                        │            │ (times out / user confirms real)
                        │            ▼
                        └─▶  TIER 2: AGENT (deep agents on vLLM, wakes only on ALERT)
                                     interprets → picks response plan → explains
                                     tools: call_ems · first_aid · notify_responders
                                            · notify_contact · generate_summary
                                     │
                                     ▼
                        Real actions (Twilio call/SMS, push, map, AED routing)

   ───────────────────────────────────────────────────────────────────────────
   OFFLINE, between runs:  SELF-IMPROVEMENT LOOP
   locked labeled episodes → optimizer mutates {detector config, triage prompt}
   → re-score → keep if better (archive) → human approve → hot-swap
```

Tier 1 is plain, audited code so the demo never hangs on inference. Tier 2 is where the LLM reasons. The improver never touches the live decision during an event — it runs between runs on recorded data.

---

## The 8 build points — real implementations

### 1. Medical premise — detect specific events + short-horizon early warning
Don't claim heart-attack prediction (wrist PPG can't do it). Demo three **detectable** events, each producible from real signals: a **cardiac anomaly** (arrhythmia / VF-VT-like or AFib pattern), a **fall**, and **breathing distress** (SpO2 drop + rising respiratory rate). Turn "prediction" into an honest **short-horizon early warning**: a rolling-baseline detector raises a *graded* alert (watch → alert) as a deterioration trend worsens over ~30–60s. Simplest real detector: EWMA + z-score against a personal baseline, plus per-condition rules. Stretch: a small model (logistic / tiny 1-D CNN or LSTM) outputting "event within next N seconds," with the rules as the always-on fallback.

### 2. Data layer — real live stream + real public data
- **Live, real:** stream ~1 Hz heart rate from the Apple Watch via `HKWorkoutSession` + `HKLiveWorkoutBuilder` (a genuine real-time stream), plus real **fall-detection** and **crash** events and real SpO2/HRV/respiratory-rate samples from HealthKit. This is your "it's actually reading my body right now" moment.
- **Real, for the events you can't safely reproduce on stage** (and for the offline improver): open physiological databases (see the datasets list in the self-improvement section). These are real patient/subject recordings, not synthetic.
- Don't fight HealthKit's sync limits — HealthKit is a periodic store, not a firehose; the low-latency signals come from the watch sensor/event APIs, so build around that.

### 3. Intelligence layer — two-tier split (correct *and* demos beautifully)
Tier 1 runs on every tick, deterministic, returns a graded signal + which vitals fired. Tier 2 wakes only on ALERT: the agent interprets the situation, selects a response plan, and writes the explanation. A **confirmation countdown** before escalation crushes false alarms and lets you demo a false alarm *correctly standing down* and a real one *escalating*. **Personal baseline:** a short calibration window at session start → per-user mean/σ thresholds, so "abnormal" means abnormal *for this person*.

### 4. Action layer — all real
- **Call EMS →** real Twilio voice call + SMS to pre-designated emergency contacts, TTS reading the incident packet ("automated alert, suspected cardiac event, HR 176, unresponsive, location…"). Real call, real number, real service; recipient is a designated contact, not 911 (see principle above).
- **First aid →** curated, guideline-based protocols (AHA/Red Cross) stored as versioned JSON (CPR, choking, fainting, bleeding). The agent *selects* and *adapts tone*; it does not invent steps. UI shows steps + a CPR metronome at 100–120 bpm.
- **Notify nearby responders →** a real responder app: profiles carry verified skills (first-aid, nurse, firefighter) + real geolocation. On ALERT, match skill within a radius, push a real notification (APNs / Twilio), show accept → en-route with a real map route to the patient and the **nearest real public AED** (OpenStreetMap `emergency=defibrillator` data / an AED registry). Model it on PulsePoint (dispatch-triggered, verified, willing responders, routed to AEDs); for the demo, "dispatch" is our Tier-2 agent and the responder pool is consented teammates.
- **Extra real actions:** live location + vitals link to a contact (SMS), phone siren + flashlight + full-screen message, a Medical ID card, a smart-bulb/lock "access granted" via a real Home/IoT API, and an LLM-generated **incident summary** (great closing artifact).

### 5. Self-improvement — the core (full section below)
Bounded, offline, measured. See ★ section.

### 6. Tech stack — real, LLM kept out of the fast path
- **vLLM:** run locally, serve a real open instruct model (e.g., Qwen or Llama) through vLLM's OpenAI-compatible server; point Tier 2 at `http://localhost:8000/v1`. Pick a model small enough to stay responsive.
- **NemoClaw:** the always-on daemon — subscribes to the stream, holds session state/memory, runs Tier 1, triggers Tier 2, executes tools. Its "always-on agent runtime" nature fits the monitoring role exactly.
- **LangChain deep agents:** the Tier-2 reasoner — planner + subagents (triage / dispatch / comms) + a virtual filesystem for the first-aid playbooks. Tools: `call_ems`, `get_first_aid`, `notify_responders`, `notify_contact`, `generate_summary`.
- **Guardrail rule:** the LLM *proposes* an action; a small deterministic authorization layer (confirmation state + rate limit + severity check) *disposes*. Keep Tier 1 as plain code and keep a manual trigger for stage safety.

### 7. Privacy — real, cheap, credible
Use only teammates' and open data, and say so. Run Tier 1 **on-device** so you can honestly claim raw health data never leaves the phone for the detection path. Minimize what responders see (location + skill-relevant context, not full history). One "privacy by design" slide.

### 8. Wedge — one tight, real scripted scenario
For a demo the wedge is a single end-to-end story in a bounded community (a co-working space / campus). Someone collapses → watch flags a cardiac anomaly + fall → confirmation times out → EMS-contact is called (real Twilio) and the nurse two desks over + a first-aid-certified colleague are pinged → they get CPR guidance with a metronome while help is "en route" → an incident summary is generated → **overnight, the system self-improves from that episode and we show the numbers move.** Stretch: run a false alarm that correctly stands down *and* a real event that escalates, back to back, to show off base-rate handling.

---

## ★ Self-improvement — how it works, with real examples and real metrics

This is the part to spend your polish on. It is a direct, honest port of AlphaEvolve and the Darwin Gödel Machine: an **offline** loop that proposes changes to **bounded artifacts**, **empirically validates** each change against a **locked set of real labeled episodes**, keeps what improves in an **archive**, and hot-swaps the winner after a human OK. We deliberately use the *AlphaEvolve* stance (an external optimizer over a bounded artifact) for the safety-relevant detector, and a *DGM-flavored* self-modification for the ambitious reveal — both offline, never rewriting the live decision mid-event.

### What we improve (the "genome")
Two bounded artifacts only:
1. **The detector config** — thresholds and rules (HR/SpO2/RR cutoffs, EWMA windows, z-score limits, duration/persistence gates, signal-combination logic, personal-baseline flags).
2. **The Tier-2 triage prompt** — the instructions that decide, given an alert, *which* response plan to run and *whether* to escalate.

The underlying model weights are **frozen**. Improvement lives in config + prompt, which is why it's safe, cheap (API calls only, no GPU training), model-agnostic, and reversible.

### The three real mechanisms (pick one to demo, mention the others)
1. **OpenEvolve (AlphaEvolve-style) over the detector config.** Wrap the config in `EVOLVE-BLOCK` markers; OpenEvolve's LLM mutates it, scores each variant with your `evaluate()` against the locked set, and keeps a MAP-Elites archive (niches = e.g. detection-latency × false-alarm-rate) so it explores genuinely different operating points, not one local optimum.
2. **GEPA / DSPy over the triage prompt.** Define the triage step as a DSPy `Signature` + a `Metric` (correct action), or hand GEPA the prompt. GEPA's edge: it **reads the execution traces to learn *why* a decision was wrong** and proposes targeted edits — the same "learn from your own failures" idea DGM uses. ~$2–10 per run, no GPU.
3. **DGM-flavored self-modification (the ambitious reveal).** After an eval run, the agent is handed its *own* confusion matrix and its *own* misclassified episodes. It diagnoses the failure pattern, proposes a change to its own detector config *or* triage prompt, re-scores on the locked set, and keeps it if it improves — building an archive of self-improved variants (tree; parent selection ∝ score × novelty). The recursion twist: it also rewrites the *prompt it uses to analyze its own failures*, improving its own improvement operator. Safe because it's offline, bounded to two artifacts, validated on locked data, and human-approved before hot-swap.

The two styles share one substrate — the locked set of labeled episodes and the `evaluate()` function below. Build that first; both loops plug into it.

The unit of data both loops consume is a **labeled episode** (a window of real vitals + ground truth):
```python
episode = {
  "id": "mitbih_207_seg14",
  "source": "MIT-BIH #207",
  "signals": {                        # time series resampled to a common rate (e.g. 1 Hz)
     "hr":      [72, 73, 75, ...],    # bpm
     "spo2":    [98, 98, 97, ...],    # %
     "rr":      [16, 16, 17, ...],    # breaths/min
     "acc_mag": [1.0, 1.0, 3.2, ...], # g (for falls / motion artifacts)
     "active":  [0, 0, 0, ...],       # workout flag
  },
  "label": "cardiac",                 # none | cardiac | fall | breathing
  "onset_idx": 88,                    # sample where the event begins (for latency scoring)
}
```

And the shared **evaluator** — this is the fitness function, the single most important piece:
```python
def evaluate(detect) -> dict:
    episodes = load_locked_set()            # real labeled episodes (validation split)
    tp = fp = fn = tn = 0; latencies = []
    for ep in episodes:
        fired_idx = detect(ep["signals"])   # None, or the sample index it alerted at
        true, pred = ep["label"] != "none", fired_idx is not None
        if   true and pred: tp += 1; latencies.append(max(0, fired_idx - ep["onset_idx"]))
        elif true:          fn += 1
        elif pred:          fp += 1
        else:               tn += 1
    recall    = tp / (tp + fn + 1e-9)
    precision = tp / (tp + fp + 1e-9)
    f1        = 2 * precision * recall / (precision + recall + 1e-9)
    far       = fp / (total_episode_seconds() / 3600)     # false alarms per hour
    latency   = median(latencies) if latencies else 999
    # reward F1, punish false alarms — but ONLY if recall stays high (the safety floor)
    fitness   = f1 - 0.5 * far / (1 + far) if recall >= 0.90 else -1.0
    return {"combined_score": fitness, "recall": recall, "precision": precision,
            "f1": f1, "false_alarms_per_hr": far, "median_latency_s": latency}
```
The `recall >= 0.90` floor is the line that makes the whole thing safe: a change is only rewarded if it reduces false alarms *while still catching real events*. Never let the optimizer trade recall for a prettier number.

### Deep dive A — the AlphaEvolve-style loop (evolving the detector config)
An **external** optimizer proposes edits to a bounded artifact and keeps a diverse archive. Mapped to us, the four AlphaEvolve components are:

- **Program database → the config archive.** Not "the best config" but a MAP-Elites grid. Two behavioral axes that matter to us: median detection latency and false-alarm rate. The archive keeps the best config in each `(latency, far)` cell, so you end up with a *family* of operating points ("fast but chattier," "slower but very clean") and pick the cell that fits deployment. This is why it beats hill-climbing — it won't collapse into one local optimum.
- **Prompt sampler → build the mutation prompt** from a parent config + a couple of diverse "inspiration" configs + their scores.
- **LLM ensemble → the mutation engine** (your local vLLM model). It returns a *diff*, not a rewrite, so working structure is preserved.
- **Evaluators → `evaluate()` above**, ideally cascaded (score on a cheap 50-episode subset first; only promising variants get the full set — more generations per weekend).

The prompt the sampler assembles each iteration:
```
You are improving a real-time health-emergency detector. Below is the current
detector (code between the EVOLVE-BLOCK markers) and its scores on real labeled
recordings, plus two high-scoring variants for inspiration.

CURRENT:
[parent config]
scores: recall 0.94, precision 0.55, false_alarms_per_hr 0.90, median_latency 6s

INSPIRATION:
[variant 1]  (precision 0.71, far 0.30)
[variant 2]  (latency 3s, far 0.80)

Goal: reduce false alarms while keeping recall >= 0.90 and latency low.
Propose ONE targeted change as a diff. Add a comment with the physiological
reason. Do not touch code outside the EVOLVE-BLOCK.
```

The loop (what OpenEvolve runs for you, conceptually):
```
seed the archive with the initial config
for i in range(N_iterations):
    parent       = archive.sample()                 # biased to high score + diversity
    inspirations = archive.sample_diverse(k=2)
    diff         = LLM(build_prompt(parent, inspirations, parent.scores))
    child        = apply_diff(parent, diff)
    scores       = evaluate(child.detect)           # your evaluate(), cascaded
    archive.add_if_earns_niche(child, scores)       # MAP-Elites
best = archive.best_on("combined_score")            # load this into Tier 1
```

To run it with **OpenEvolve** you write three files: `initial_program.py` (your `detect()` with the config inside `# EVOLVE-BLOCK-START/END`), `evaluator.py` (the `evaluate()` above, returning that metrics dict), and `config.yaml` (points at your local vLLM `/v1` endpoint, sets iterations / population / islands, and declares the two MAP-Elites feature dimensions). Then run it and load the winning config file into Tier 1. Budget: a few hundred iterations over a weekend is plenty to produce visible gains.

### Deep dive B — the DGM-style loop (the self-diagnosing agent)
The decisive difference from A: there is **no external optimizer proposing generic mutations. The agent reads its own results, diagnoses its own failures in natural language, writes its own fix, tests it, keeps it if it helps — and can improve the very procedure it uses to diagnose itself.** The agent is its own mutation operator, and better agents become better self-improvers.

The agent's **"self"** is three editable artifacts (its codebase):
1. `config` — the detector thresholds/rules.
2. `triage_prompt` — the Tier-2 decision prompt.
3. `improve_prompt` — the prompt it uses to analyze its own failures and propose fixes. **Editing this is what makes it recursive.**

One DGM iteration:
```
1. EVALUATE self on the locked set → confusion matrix + list of misclassified episodes.
2. DIAGNOSE: feed {confusion matrix, worst mistakes, current config, triage_prompt}
   through improve_prompt → agent returns (a) a one-sentence diagnosis of the dominant
   failure mode, and (b) a concrete edit to config OR triage_prompt, as a diff.
3. APPLY the edit → candidate self.
4. RE-EVALUATE the candidate on the locked set.
5. VALIDITY GATE — admit only if it (a) still runs, (b) recall >= 0.90 (still catches
   real events), and (c) still contains a working improve step (can self-modify again).
6. ADMIT to the archive if combined_score improved; branch a new node in the tree.
7. RECURSION — every few iterations, also let it rewrite improve_prompt itself, judged
   by whether its later proposals raise scores in fewer iterations.
```

Step 5 is the DGM-critical piece translated to health: a child is admitted only if it still detects real events, still produces valid actions, and still retains a functioning self-improvement step. This stops a self-edit from silently breaking the safety behavior or the ability to keep improving. The **archive** is a tree; pick the next parent with probability ∝ `score × 1/(1+children)` so strong agents are favored but under-explored branches still get tried (a slightly-worse variant can be the ancestor of the best one).

The self-diagnosis prompt (`improve_prompt`, the concrete artifact the agent both uses and edits):
```
You are a detector that just evaluated itself on real labeled recordings.
Your confusion matrix and your worst mistakes (with signals) are below.

false alarms: 12  |  misses: 2
- fired "breathing" at t=41; SpO2 dipped to 90 for 3s while acc_mag=2.8 (movement); label=none
- did NOT fire; HR rose 92 -> 148 over 40s; label=cardiac
- ... (6 more)

CURRENT config: [config]
CURRENT triage prompt: [triage_prompt]

1. Name the single dominant failure pattern in one sentence.
2. Propose ONE concrete edit (to config OR triage prompt) that fixes it WITHOUT
   hurting recall. Give it as a diff, with the physiological reason.
3. State which metric you expect to move, and in which direction.
```
A real answer might be: *"Dominant pattern: transient SpO2 dips during movement counted as breathing distress. Edit config: `spo2_min_duration_s: 0 -> 15` and add `rr_threshold: 24`. Expect false_alarms_per_hr to fall; recall unchanged."* The **recursion twist** is when it edits `improve_prompt` itself — e.g., adding *"prefer duration/concurrency gates over lowering a single threshold, and always check whether a false alarm co-occurs with a second abnormal signal or is transient."* Now every future diagnosis is sharper; you measure this as a steeper improvement curve (scores rising in fewer iterations).

The loop:
```
seed archive with the initial agent (config, triage_prompt, improve_prompt)
for i in range(N):
    parent          = archive.sample_tree()                  # score x novelty
    cm, mistakes    = evaluate_with_logs(parent, locked_set)
    proposal        = parent.run(parent.improve_prompt, cm, mistakes)   # SELF-diagnosis
    child           = apply(parent, proposal.diff)
    scores          = evaluate(child.detect)
    if valid(child) and scores["combined_score"] > parent.scores["combined_score"]:
        archive.add_child(parent, child, scores)
    if i % 5 == 0:                                            # recursion
        meta_child = improve_the_improver(parent)             # edits improve_prompt
        if converges_faster(meta_child): archive.add_child(parent, meta_child, ...)
best = archive.best()
```
Safe because it is offline, bounded to those three artifacts, validated on locked real data behind the recall floor, and human-approved before anything is hot-swapped into Tier 1 — it is **not** the agent rewriting its live decision mid-event.

### Which to use, and how they compose
- **Detector config → AlphaEvolve-style (OpenEvolve).** A numeric/structural genome with a huge, cheap-to-score search space is exactly where evolutionary search shines. This is your workhorse — run it to get the real metric gains on stage.
- **Triage prompt → GEPA/DSPy (an AlphaEvolve-style optimizer over text).** GEPA already reads failure traces and proposes targeted edits, so it gets most of the value with the least code.
- **DGM-style self-diagnosis → the showstopper + the recursion claim.** Same locked set, same validity gate. It produces the on-screen "watch the agent read its own mistakes and fix itself" narrative, and the meta-edit to `improve_prompt` is the "recursively self-improving" line judges remember.

Recommended split for the weekend: use OpenEvolve to push the detector numbers up, and run the DGM-style self-diagnosis (with the recursion twist) as the live, narratable reveal.

### Real worked example 1 — evolving the detector config
`detectors.py` (the evolvable artifact):
```python
# EVOLVE-BLOCK-START
BREATHING_DISTRESS = {
    "spo2_threshold": 92,        # flag if SpO2 below this…
    "spo2_min_duration_s": 0,    # …for at least this long
    "rr_threshold": 0,           # …AND respiratory rate above this
}
FALL = {
    "impact_g": 2.5,
    "post_impact_stillness_s": 0,
}
CARDIAC = {
    "hr_abs_threshold": 150,
    "use_personal_baseline": False,
    "baseline_sigma": 3,
    "suppress_if_active": False,
}
# EVOLVE-BLOCK-END
```
After ~50 generations against the locked set, the optimizer discovers (real, interpretable changes):
```python
BREATHING_DISTRESS = {
    "spo2_threshold": 91,
    "spo2_min_duration_s": 15,   # learned: brief dips are motion artifacts, require it to persist
    "rr_threshold": 24,          # learned: require a concurrent breathing-rate spike
}
FALL = {
    "impact_g": 3.0,
    "post_impact_stillness_s": 8, # learned: require stillness to separate a real fall from "sat down hard"
}
CARDIAC = {
    "hr_abs_threshold": 150,
    "use_personal_baseline": True, # learned: a fixed 150 bpm over-fires; personalize
    "baseline_sigma": 3,
    "suppress_if_active": True,    # learned: don't flag high HR during a workout
}
```
Every one of these is a real false-alarm-reduction move a human tuner would be proud of — and the system found them on its own.

### Real worked example 2 — evolving the triage prompt
Before (over-escalates on transient anomalies that self-resolve):
> You are an emergency triage agent. Given the vitals and the detected event, choose the response: call EMS, give first aid, notify a contact, or stand down.

After (GEPA, having read the failure traces where it called EMS on a spike that resolved and the user was fine):
> You are an emergency triage agent… **Before escalating to EMS, check two things: (1) is the signal trending *worse* or already *resolving* over the last 30 seconds? (2) did the user fail to respond to the confirmation prompt?** Escalate to EMS only if the event is severe **and** (worsening **or** unconfirmed). If a transient anomaly is resolving and the user confirmed they're fine, stand down and log it.

### Real worked example 3 — DGM-flavored self-diagnosis
The agent reads its own results and reasons (real, logged text you can put on a slide):
> On the locked set I produced 12 false alarms and 2 misses. 8 of the 12 false alarms were breathing-distress flags from brief SpO2 dips *while the accelerometer showed movement*. Both misses were slow-onset cardiac events where HR crept up but stayed under the fixed 150 threshold. **Proposed self-edit:** require SpO2 drop to persist ≥15s and co-occur with elevated respiratory rate; switch cardiac detection to a personal baseline. **Meta-edit:** add "look for a concurrent-signal or duration gate" to my failure-analysis checklist so I catch this class of error faster next time.

It applies the edit, re-scores, sees improvement, keeps the variant, archives it.

### The real data behind it (named, open, labeled)
The offline loop needs real labeled episodes. We use real recordings, no synthetic events required:
- **Cardiac (arrhythmia / VF-VT / AFib):** MIT-BIH Arrhythmia Database, MIT-BIH Malignant Ventricular Arrhythmia, Creighton University Ventricular Tachyarrhythmia (CUDB), Long-Term AF Database — all on PhysioNet, with beat/rhythm annotations.
- **PPG (what the watch actually senses) + motion artifacts:** the Pulse Transit Time PPG dataset (ECG+PPG+accelerometry during sitting/walking/running — perfect for false-alarm cases) and BIDMC (synchronized ECG+PPG).
- **Breathing / respiratory:** BIDMC and MIMIC waveforms (include respiration); PhysioNet respiratory/aeration recordings with breath-holds.
- **Falls (with everyday activities to test false alarms):** KFall (32 subjects, 15 simulated falls + 21 activities of daily living, with temporal fall labels); SisFall as an alternative.
- **Real live capture:** teammates wearing Apple Watches, plus safely reproducible near-event conditions (jumping jacks → HR spike, stair climbs / breath-holds → SpO2 & RR change, controlled falls onto a mat → real fall events). This becomes your own held-out real test set.

Split it three ways: **train** (optimizer tunes on it), **validation** (optimizer scores/selects on it — the "locked" set it sees), **test** (a real held-out set the optimizer never sees — proves it learned rather than overfit).

### How we measure self-improvement
Two families of metrics. Report all with the scaffold fixed so before/after is honest.

Detection & triage quality (on the held-out test set):

| Metric | What it captures | Why it matters |
|---|---|---|
| Recall / sensitivity | fraction of real events caught | you cannot miss a real emergency |
| Precision | fraction of alerts that are real | drives user trust |
| F1 (per condition) | balance of the two | headline quality number |
| **False-alarm rate at fixed recall** | e.g. false alarms per 24h *at 95% recall* | the honest tradeoff metric; the base-rate killer |
| Detection latency | event onset → alert (seconds) | speed saves lives |
| Triage action accuracy | did it pick the right response plan | correctness of the agent's decision |
| Over-/under-escalation rate | EMS-called-when-unneeded / not-called-when-needed | the two asymmetric failure costs |

Improvement-of-the-improvement (the AlphaEvolve/DGM-specific metrics):

| Metric | What it captures |
|---|---|
| Improvement trajectory | the chosen metric vs. generation/iteration (the curve going up) |
| Held-out generalization | gain on the *test* split the optimizer never saw (learned, not overfit) |
| Regression rate | previously-correct cases that the new variant breaks |
| Archive diversity | how many genuinely different strategies are being explored |
| Sample / compute efficiency | metric gain per iteration and per dollar |
| Human-approval rate | proposals accepted vs. rejected at the gate |

**The demo moment:** run the loop as a visible batch step ("learning from the last 200 episodes…"), show the trajectory curve, then hot-swap the improved config and re-run the scenario so the false alarm from earlier now *correctly stands down*. Illustrative targets to aim for (example, not results): false-alarm rate at 95% recall **0.9/hr → 0.15/hr**, F1 **0.71 → 0.88**, triage action accuracy **0.74 → 0.91** — plus one concrete discovered rule shown on screen ("learned to require SpO2 drop *and* RR spike before flagging breathing distress").

---

## Demo script (≈4 min, hits all 8 + the reveal)
1. **Setup (20s):** teammate wearing an Apple Watch; live HR streaming on the dashboard (points 2, 6).
2. **Baseline (15s):** 10-second personal calibration; thresholds adapt (points 1, 3).
3. **False alarm handled right (30s):** teammate does jumping jacks → HR spikes → "watch" then a confirmation prompt → they tap "I'm fine" → stands down and logs. (Point 3 — the base-rate story.)
4. **Real event (60s):** simulated cardiac anomaly (fed from real MIT-BIH data through the same pipeline) + a controlled fall → ALERT → confirmation times out → **real Twilio call/SMS** to the designated EMS contact + push to the nurse and first-aid-certified colleague nearby, with a map route + nearest AED (point 4).
5. **Response (30s):** responder's phone shows CPR steps + metronome; contact gets live location + vitals (point 4).
6. **Wrap of the incident (15s):** LLM-generated incident summary card (point 4).
7. **The reveal (60s):** "Overnight, Aegis learns from that episode." Run the offline loop, show the trajectory curve and the discovered rule, hot-swap, re-run step 3's stimulus → now cleaner. Show the metrics table moving (point 5). **This is the mic-drop.**

---

## Build plan (weekend)
- **Person A — data + detector (Tier 1):** live HealthKit stream, dataset loaders, EWMA/z-score + rules, personal baseline, the `evaluate()` harness. *Owns points 1, 2, 3.*
- **Person B — agent + actions (Tier 2):** NemoClaw daemon, deep-agent triage on vLLM, tools, Twilio/APNs, first-aid JSON, map + AEDs, guardrail authorization layer. *Owns points 4, 6.*
- **Person C — self-improvement + demo:** locked-set construction + splits, OpenEvolve/GEPA/DSPy loops, metrics + trajectory dashboard, the hot-swap, the demo script and slides. *Owns points 5, 7, 8.*
- **Shared:** the event/message schema between Tier 1 → Tier 2 → tools (define this first).

Suggested order: schema → live HR stream + a hardcoded-threshold detector end-to-end (watch → alert → real Twilio call) → agentic Tier 2 + real responder/first-aid actions → offline improver + metrics → polish the two-scenario demo.

---

## Honest notes
- **The one non-real thing is the 911 link, by necessity** (legal + safety); everything else is real, and the EMS action uses a real call service to a real recipient.
- **Keep the LLM out of the detection critical path** — determinism and speed on stage, and it's the correct design.
- **The self-improvement must stay offline and bounded** — it improves config + prompt on recorded data, validated before hot-swap. That's exactly why it's safe to be ambitious with it, and it's the part judges will remember.
- **Watch for overfitting to the demo:** always report the held-out test number, not the set the optimizer tuned on — being able to say "it generalized to data it never saw" is far more convincing than a big in-sample jump.
