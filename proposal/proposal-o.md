# GuardianMesh

## Hackathon Proposal: A Self-Improving, Wearable-Driven Emergency Coordination Agent

**Status:** Hackathon prototype proposal  
**Working title:** GuardianMesh  
**Primary platform:** Apple Watch + iPhone + local AI workstation  
**Prototype safety boundary:** All medical emergencies and EMS actions are simulated. No real emergency service will be contacted.

---

## 1. Executive Summary

GuardianMesh is a connected-health hackathon prototype that combines live Apple Watch health and activity data, a real-time risk engine, a locally hosted agentic system, and a network of nearby volunteer responders.

The prototype demonstrates an end-to-end response loop:

1. Stream heart rate, activity, motion, location, and supporting HealthKit context from an Apple Watch and iPhone.
2. Detect a possible risk pattern, such as a fall followed by immobility, an unusual heart-rate pattern, breathing distress reported by the user, or a manual SOS.
3. Ask the wearer to confirm whether they are safe.
4. If the wearer requests help or does not respond, create an incident.
5. Use an agentic coordination system to:
   - notify a mock EMS dispatcher;
   - contact an emergency contact;
   - locate nearby responders with relevant skills;
   - assign responder roles, such as direct assistance, AED retrieval, or meeting EMS;
   - present a prewritten first-aid protocol;
   - maintain a real-time incident timeline.
6. Replay benchmark scenarios in an offline self-improvement laboratory.
7. Use AlphaEvolve-inspired candidate generation and evaluation plus Darwin Gödel Machine-inspired lineage search to improve the agent's thresholds, prompts, responder-selection policy, workflow, and eventually its own improvement procedure.

The project is intentionally ambitious in its orchestration and self-improvement, while keeping dangerous actions safely simulated. The goal is not to claim clinical diagnosis. The goal is to demonstrate how wearable signals, local AI, structured agent tools, geospatial responder matching, and recursive improvement techniques can work together in a coherent system.

---

## 2. Problem Statement

During a health emergency, several delays can occur:

- The affected person may be unable to call for help.
- Friends or family may not know what happened or where the person is.
- Nearby trained people may be willing to help but unaware of the incident.
- An AED, building entrance, security desk, or responder may be close but not coordinated.
- Dispatchers may not receive a concise timeline of what was observed.

Consumer wearables already collect useful signals, but those signals are usually presented as passive charts and notifications. GuardianMesh explores a more active model:

> Turn wearable observations into a structured, verifiable incident and coordinate the right people and actions while professional help is on the way.

The hackathon differentiator is that the coordination system is not static. It can run scenario-based experiments, generate alternative agent configurations, preserve strong and diverse candidates, and show a visible lineage of improving agents.

---

## 3. Product Vision

GuardianMesh is a wearable-driven emergency coordination platform with four layers:

1. **Sensing layer:** Collect live wearable and phone data.
2. **Risk and policy layer:** Convert signals into structured risk events and control escalation through a deterministic state machine.
3. **Agentic coordination layer:** Select and execute permitted actions through structured tools.
4. **Self-improvement layer:** Replay scenarios and evolve the agent's configuration, workflow, and improvement process in a sandbox.

The product experience should feel like this:

> "My watch detected a concerning pattern, checked whether I was okay, recruited nearby qualified help, coordinated an AED and a mock dispatcher, and continuously improved its response strategy through simulated incidents."

### 3.1 Core differentiator: the agent improves the way it responds and the way it improves

The self-improving agent is the centerpiece of GuardianMesh, not a background optimization feature. The prototype runs two separate loops:

1. **Operational loop:** A signed, fixed agent version handles a simulated incident through approved tools. It reads a structured risk event, requests confirmation, finds responders, assigns roles, and updates the incident room.
2. **Improvement loop:** An offline laboratory replays incidents, diagnoses failures, proposes agent changes, evaluates descendants, preserves a branching lineage, and promotes a better version only after it passes protected tests.

The proposal combines two complementary ideas:

| Technique | GuardianMesh interpretation | Main output |
|---|---|---|
| AlphaEvolve-style evolution | An LLM acts as a semantic mutation operator over prompts, policies, workflow code, and configuration. Automated scenarios score every proposed child. Strong and behaviorally diverse candidates are retained. | Better candidate agents |
| DGM-style recursive improvement | Complete agent versions are stored in a parent-child archive. A selected agent can modify both its operating policy and parts of its own failure-analysis and mutation machinery. The child then becomes the improver that creates the following generation. | Better lineages and better improvement procedures |

A normal optimizer can tune an agent repeatedly while the optimizer itself remains fixed. GuardianMesh aims to demonstrate the stronger recursive condition:

```text
Agent N operates
    -> Agent N diagnoses its own failures
    -> Agent N creates Agent N+1
    -> Agent N+1 inherits a changed improvement procedure
    -> Agent N+1 uses that procedure to create Agent N+2
```

The judge-facing proof will therefore show both:

- **direct improvement:** a descendant handles more scenarios correctly, with fewer false dispatches and lower latency; and
- **meta-improvement:** under the same candidate budget, the descendant's modified improver produces stronger children than the original improver.

---

## 4. Hackathon Scope

### 4.1 Must-build capabilities

1. Apple Watch or simulated real-time health-data stream.
2. Rule-based risk scoring with event-specific detectors.
3. Watch or phone user check-in with a countdown.
4. Deterministic incident state machine.
5. Local agent inference using vLLM.
6. Agent implementation using LangChain Deep Agents.
7. Structured agent tools for all actions.
8. Nearby responder matching by skill and distance.
9. Responder accept or decline workflow.
10. Mock EMS dispatcher dashboard.
11. Emergency-contact notification.
12. Incident timeline and map.
13. AlphaEvolve-style candidate generation, automated scenario evaluation, and quality-diversity archive.
14. DGM-style parent-child lineage with at least one inherited change to the self-improvement procedure.

### 4.2 Stretch capabilities

1. Multiple specialized subagents.
2. NemoClaw-based sandbox and network policy.
3. Twilio voice call to a mock dispatcher.
4. Firebase or APNs push notifications.
5. AED location and retrieval assignment.
6. Spoken first-aid steps.
7. Evolution of parent-selection, evaluation allocation, or multi-agent topology.
8. On-device risk scoring using Core ML.
9. Network-disconnection fallback.
10. A visual comparison of multiple evolutionary lineages and their descendant productivity.

### 4.3 Explicit non-goals

The hackathon prototype will not:

- diagnose a heart attack or any medical condition;
- contact real emergency services;
- provide clinically validated predictions;
- verify professional credentials against external registries;
- publish to the public App Store;
- allow the production coordination agent to rewrite itself during an active incident;
- generate novel medical instructions;
- claim that HealthKit provides continuous medical-grade telemetry.

---

## 5. Primary User Roles

### 5.1 Monitored user

A person wearing an Apple Watch and carrying an iPhone. The user can:

- share selected HealthKit data;
- start the live demo session;
- respond to a safety check;
- initiate a manual SOS;
- view incident status;
- designate emergency contacts.

### 5.2 Nearby responder

A user with one or more profile skills, such as:

- First Aid;
- CPR;
- AED trained;
- firefighter;
- EMT;
- nurse;
- physician;
- security staff.

The responder can:

- set availability;
- share approximate location;
- accept or decline an incident;
- receive an assigned role;
- view approved instructions;
- report arrival or completion.

### 5.3 Mock EMS dispatcher

A browser-based operator who can:

- receive a simulated dispatch;
- review the incident summary;
- see recent signals and location;
- acknowledge the incident;
- view responder status;
- close or hand off the incident.

### 5.4 Hackathon operator

A team member who can:

- inject a scenario;
- reset the demo;
- inspect agent tool calls;
- run the self-improvement benchmark;
- compare agent generations;
- visualize the DGM lineage tree.

---

## 6. High-Level Architecture

```mermaid
flowchart TD
    A[Apple Watch] -->|Heart rate, motion, user response| B[iPhone App]
    H[HealthKit] -->|Historical and recent context| B
    S[Synthetic Scenario Injector] --> B
    B -->|WebSocket or HTTPS events| C[FastAPI Event Gateway]
    C --> D[Risk Engine]
    D --> E[Incident State Machine]
    E --> F[LangChain Deep Agent]
    F --> G1[Responder Search Tool]
    F --> G2[Notification Tool]
    F --> G3[First-Aid Protocol Tool]
    F --> G4[Mock EMS Tool]
    F --> G5[AED Assignment Tool]
    F --> G6[Timeline Tool]
    F --> V[vLLM Local Inference Server]
    G1 --> P[(Supabase Postgres + PostGIS)]
    G2 --> N[FCM/APNs or Twilio]
    G4 --> M[Mock EMS Dashboard]
    P --> R[Responder Web or Mobile App]
    P --> W[Incident Command Dashboard]

    X[Scenario Replay Suite] --> Y[AlphaEvolve-Style Mutation Engine]
    Y --> Z[DGM-Style Agent Archive]
    Z --> Q[Sandboxed Candidate Agents]
    Q --> X
    Q -->|Signed winning configuration| E
```

### Architectural principle

The language model does not directly interpret raw health signals or decide that a specific disease is present. Instead:

1. Normal code produces a structured risk event.
2. A deterministic state machine controls which actions are allowed.
3. The agent coordinates only through approved tools.
4. Self-improvement occurs offline against replayable scenarios.

### 6.1 Self-improvement subsystem

```mermaid
flowchart LR
    A[Incident traces and scenario results] --> B[Failure analyzer]
    B --> C[Parent and inspiration sampler]
    C --> D[Mutation prompt builder]
    D --> E[vLLM candidate generator]
    E --> F[Typed patch validator]
    F --> G[Fresh NemoClaw or Docker sandbox]
    G --> H[Evaluation cascade]
    H --> I{Hard constraints pass?}
    I -- No --> J[Failure archive]
    I -- Yes --> K[Quality-diversity archive]
    K --> L[DGM lineage selector]
    L --> C
    K --> M[Operator-reviewed promotion]
    N[Protected evaluator and hidden scenarios] --> H
    N -. read-only .-> G
```

The subsystem has seven concrete responsibilities:

| Component | Responsibility | Suggested technology |
|---|---|---|
| Trace collector | Store state transitions, tool calls, outputs, timing, and scenario labels | PostgreSQL or JSONL |
| Failure analyzer | Convert failed runs into a structured diagnosis and candidate hypotheses | Python plus local LLM through vLLM |
| Mutation engine | Produce small, typed diffs to the agent bundle | LangChain, Pydantic, Git patches |
| Sandbox runner | Execute each child without notification credentials or write access to protected files | NemoClaw or Docker |
| Protected evaluator | Score behavior on development, hidden, adversarial, and meta-improvement tests | pytest, asyncio, YAML scenarios |
| Evolution archive | Preserve parents, children, specialists, failures, and descendant statistics | SQLite/PostgreSQL plus Git |
| Lineage dashboard | Explain what changed, why it changed, and how the lineage improved | NetworkX plus React Flow or D3.js |

The protected evaluator is never editable by a candidate agent. Candidate code can read the public scenario format but cannot read hidden scenario contents, alter expected outcomes, change safety constraints, or call real notification services.

---

## 7. Feature-by-Feature Implementation and Technology

## 7.1 Feature: Live Apple Watch and HealthKit data ingestion

### User experience

The dashboard displays a live heart-rate graph, motion state, location, and contextual information such as sleep duration or resting heart rate. During the demo, the watch can stream heart rate at a higher frequency by running an active workout session.

### Proposed implementation

- Build a native watchOS application in Swift.
- Start an `HKWorkoutSession` during the demonstration for higher-frequency heart-rate updates.
- Read HealthKit context such as sleep, resting heart rate, HRV, respiratory rate, activity, and body measurements.
- Use `WatchConnectivity` to forward watch events to the paired iPhone.
- The iPhone sends normalized events to the backend using WebSockets or HTTPS.
- Include a signal-quality value with every event.

### Tools and technologies

| Component | Technology |
|---|---|
| Watch application | Swift, SwiftUI, watchOS |
| Health data | Apple HealthKit |
| Live workout heart rate | `HKWorkoutSession`, `HKLiveWorkoutBuilder` |
| Stored health samples | `HKSampleQuery`, `HKAnchoredObjectQuery`, `HKObserverQuery` |
| Motion and impact signals | Core Motion |
| Watch-to-phone communication | WatchConnectivity |
| Phone location | Core Location |
| Local charting | Swift Charts |
| Event transport | WebSocket or HTTPS JSON |

### Fallback

If Apple Watch integration is not completed, the same event schema will accept simulated heart-rate and motion data from the scenario injector. The rest of the system remains fully functional.

---

## 7.2 Feature: Synthetic scenario injector

### User experience

A hidden operator panel contains buttons such as:

- Normal activity.
- Manual SOS.
- Fall and immediate recovery.
- Fall followed by no response.
- Possible exertional distress.
- Possible breathing distress.
- Sensor disconnected.
- No nearby responder.

Selecting a scenario injects a repeatable event sequence into the same pipeline used by live data.

### Proposed implementation

- Store scenarios as JSON or YAML fixtures.
- Each fixture contains timestamped heart-rate, motion, symptom, and response events.
- A FastAPI endpoint or hidden iPhone developer screen starts the selected scenario.
- The scenario runner emits events at real-time speed or accelerated speed.
- The system labels simulated events clearly in the UI.

### Tools and technologies

| Component | Technology |
|---|---|
| Scenario definitions | YAML or JSON |
| Scenario runner | Python 3.12 |
| Backend control API | FastAPI |
| Real-time playback | Python `asyncio` |
| iOS operator panel | SwiftUI developer menu |
| Validation | Pydantic models |

### Example scenario

```yaml
id: fall_no_response_01
name: Fall followed by no response
expected_outcome: simulated_dispatch
steps:
  - at_seconds: 0
    heart_rate: 91
    motion: walking
  - at_seconds: 7
    heart_rate: 108
    motion: impact
  - at_seconds: 10
    motion: still
  - at_seconds: 25
    user_response: null
```

---

## 7.3 Feature: Real-time risk engine

### User experience

The dashboard shows a transparent risk score and the factors that contributed to it, for example:

```text
Risk score: 92
+40 impact detected
+25 immobility for 25 seconds
+15 unusual heart-rate deviation
+30 no response
```

### Proposed implementation

Use event-specific rule modules instead of an LLM-based diagnosis:

1. Manual SOS detector.
2. Fall and nonresponse detector.
3. Exertional distress detector.
4. Demo-only possible cardiac-distress pattern detector.
5. Demo-only breathing-distress pattern detector.

The first implementation can be a weighted rules engine. A later stretch version can use a small classifier or anomaly-detection model.

### Tools and technologies

| Component | Technology |
|---|---|
| Risk service | Python, FastAPI |
| Numerical operations | NumPy |
| Rolling statistics | Pandas or lightweight custom queues |
| Validation | Pydantic |
| Optional model training | scikit-learn |
| Optional on-device inference | Core ML |
| Metrics | Prometheus-compatible counters or structured logs |

### Example risk logic

```python
risk = 0

if impact_detected:
    risk += 40
if immobility_seconds >= 20:
    risk += 25
if abs(heart_rate_z_score) >= 3:
    risk += 15
if user_reports_dizziness_or_breathlessness:
    risk += 35
if user_did_not_respond:
    risk += 30
```

### Demo thresholds

```text
0-39: Continue monitoring
40-69: Ask whether the user is okay
70-89: Notify a trusted contact or responder
90+: Trigger simulated emergency escalation
```

These thresholds are demo parameters and are not medical recommendations.

---

## 7.4 Feature: User safety check on Apple Watch or iPhone

### User experience

When the risk score crosses the verification threshold, the watch vibrates and displays:

```text
Are you okay?

[I'm okay]
[I need help]
[Call my contact]

15-second countdown
```

No response becomes an input into the next escalation state.

### Proposed implementation

- Use a SwiftUI full-screen alert on watchOS.
- Trigger strong haptics through `WKInterfaceDevice`.
- Run a countdown timer.
- Send the response through WatchConnectivity.
- If the watch is unavailable, show the same interaction on the iPhone.

### Tools and technologies

| Component | Technology |
|---|---|
| Watch UI | SwiftUI |
| Haptics | WatchKit |
| Countdown | Swift concurrency or `Timer` |
| Watch-to-phone response | WatchConnectivity |
| Phone fallback | SwiftUI, UserNotifications |
| Backend event | WebSocket JSON event |

---

## 7.5 Feature: Deterministic incident state machine

### Why it is needed

The state machine prevents the LLM from taking arbitrary actions. It determines which tools are available at each stage.

### States

```text
MONITORING
  -> VERIFYING
  -> ESCALATING
  -> RESPONSE_ACTIVE
  -> RESOLVED
```

Possible alternate transitions include cancellation, false alarm, timeout, and sensor failure.

### Proposed implementation

- Store the state machine as Python code or a declarative YAML graph.
- Require every transition to include a reason and timestamp.
- Expose only state-appropriate tools to the agent.
- Persist all transitions to the incident timeline.

### Tools and technologies

| Component | Technology |
|---|---|
| State machine | Python enum + transition table, or LangGraph |
| API | FastAPI |
| Validation | Pydantic |
| Persistence | PostgreSQL |
| Event delivery | Supabase Realtime or WebSockets |
| Tests | pytest |

### Example tool permissions

| State | Allowed tools |
|---|---|
| `MONITORING` | Read signals, create preliminary risk event |
| `VERIFYING` | Request check-in, read response, update timeline |
| `ESCALATING` | Find responders, notify contacts, initiate mock dispatch |
| `RESPONSE_ACTIVE` | Assign roles, select protocol, update status |
| `RESOLVED` | Close incident, generate report |

---

## 7.6 Feature: Local agentic coordination

### User experience

The incident dashboard shows the agent's current objective, tool calls, results, and selected actions without exposing hidden chain-of-thought.

Example:

```text
Objective: Coordinate response for possible fall and nonresponse
Action 1: Requested user check-in
Result: No response after 15 seconds
Action 2: Located three qualified responders
Action 3: Assigned one responder to assist and one to retrieve an AED
Action 4: Sent simulated dispatch to mock EMS
```

### Proposed implementation

- Implement the coordination agent with LangChain Deep Agents.
- Use structured tools with Pydantic input and output schemas.
- Give the agent an incident summary rather than raw, unbounded data.
- Use a local instruction model served through vLLM.
- Constrain the agent with a system prompt and state-specific tool allowlist.
- Record every tool call and result.

### Tools and technologies

| Component | Technology |
|---|---|
| Agent framework | LangChain Deep Agents |
| Graph and control flow | LangGraph, if needed |
| Local model server | vLLM |
| Model API | OpenAI-compatible HTTP endpoint |
| Tool schemas | Pydantic |
| Agent service | Python, FastAPI |
| Agent sandbox | NVIDIA NemoClaw or Docker sandbox |
| Trace storage | JSONL, PostgreSQL |
| Optional observability | LangSmith or OpenTelemetry |

### Suggested agent prompt

```text
You coordinate simulated health incidents.

You do not diagnose medical conditions.
Use only the provided tools.
Follow the current incident state and policy.
Never contact real emergency services.
First-aid content must come from the approved protocol tool.
Do not invent responder credentials, locations, or tool results.
Return structured actions and a short dashboard explanation.
```

### Initial agent topology

For the minimum build, use one coordinator agent.

For the stretch build, use three subagents:

1. **Triage summarizer:** Converts risk events into a concise incident summary.
2. **Responder dispatcher:** Finds and assigns responders.
3. **Communications agent:** Sends approved messages and updates.

The main coordinator reviews their outputs and executes only state-authorized actions.

---

## 7.7 Feature: Local model inference with vLLM

### Proposed implementation

- Run a tool-capable instruction model on a laptop or GPU workstation.
- Start vLLM with its OpenAI-compatible server.
- Point the LangChain client to the local endpoint.
- Require structured JSON or tool-call outputs.
- Keep temperature low for incident coordination.

### Tools and technologies

| Component | Technology |
|---|---|
| Inference runtime | vLLM |
| Model | A locally runnable 7B-14B instruction model with reliable tool calling |
| API format | OpenAI-compatible chat-completions endpoint |
| Structured output | JSON schema or Pydantic parser |
| Hardware | NVIDIA GPU workstation, cloud GPU, or powerful local machine |

### Example command

```bash
vllm serve <model-name> --host 0.0.0.0 --port 8000
```

The model is used for coordination, summarization, and candidate generation. The physiological risk score remains normal code.

---

## 7.8 Feature: Nearby responder discovery and skill matching

### User experience

The system finds available responders near the incident, ranks them by skill and distance, and sends a request.

Example:

```text
Possible emergency 420 meters away
Requested skill: First Aid
Estimated walking time: 3 minutes

[Accept] [Decline]
```

### Proposed implementation

- Store responder profiles, skills, approximate locations, and availability.
- Use PostgreSQL with PostGIS for radius searches.
- Rank candidates by required-skill match, availability, distance, and optional role suitability.
- Notify the top candidates.
- Share the exact location only after a responder accepts.

### Tools and technologies

| Component | Technology |
|---|---|
| Database | Supabase PostgreSQL |
| Geospatial search | PostGIS |
| Authentication | Supabase Auth |
| Real-time updates | Supabase Realtime |
| Mobile push | Firebase Cloud Messaging with APNs, or direct APNs |
| Browser fallback | Next.js responder page |
| Maps | MapLibre GL JS, Mapbox, or Apple MapKit |
| Distance and ETA | PostGIS distance, optional routing API |

### Example ranking function

```python
score = (
    50 * exact_required_skill_match
    + 20 * first_aid_or_cpr_skill
    + 10 * currently_available
    - 0.01 * distance_meters
)
```

### Minimal schema

```sql
users(id, name, phone, availability, latitude, longitude, last_location_update)
skills(id, name)
user_skills(user_id, skill_id, verified, expires_at)
incidents(id, user_id, event_type, latitude, longitude, status)
incident_assignments(incident_id, responder_id, role, status, distance_meters)
```

---

## 7.9 Feature: Responder accept, decline, and role assignment

### User experience

A responder can accept or decline. The agent then assigns a role such as:

- provide direct assistance;
- retrieve the nearest AED;
- meet the mock EMS team at an entrance;
- notify security;
- remain available as backup.

### Proposed implementation

- Push a notification containing a short incident summary and approximate distance.
- On acceptance, create an assignment record.
- The agent chooses roles based on skills and proximity.
- Use Supabase Realtime to update all dashboards immediately.

### Tools and technologies

| Component | Technology |
|---|---|
| Responder client | Native iOS SwiftUI app or Next.js mobile web app |
| Notifications | FCM/APNs |
| Real-time assignment updates | Supabase Realtime |
| Role selection | LangChain tool plus deterministic policy |
| Map and directions | MapKit or MapLibre/Mapbox |
| Presence | Supabase presence channel or periodic location update |

---

## 7.10 Feature: Mock EMS dispatch

### User experience

A mock dispatcher receives an incident card with:

- event type;
- time detected;
- user-response status;
- current location;
- recent signals;
- responders assigned;
- incident timeline.

The dispatcher can press `Acknowledge`, `En route`, and `Close incident`.

### Proposed implementation

- Create a Next.js dispatcher dashboard.
- Implement a `dispatch_demo_ems` agent tool.
- Insert a dispatch record into Supabase.
- Update the dispatcher dashboard in real time.
- Optionally send an SMS or voice call to a designated team phone through Twilio.
- Display a persistent simulation warning.

### Tools and technologies

| Component | Technology |
|---|---|
| Dispatcher dashboard | Next.js, React, TypeScript |
| UI | Tailwind CSS, shadcn/ui |
| Real-time updates | Supabase Realtime |
| SMS and voice | Twilio |
| Backend API | FastAPI |
| Incident map | MapLibre, Mapbox, or Google Maps |

### Mandatory warning

```text
SIMULATED EMERGENCY DISPATCH
No real emergency service has been contacted.
```

---

## 7.11 Feature: Emergency-contact notification

### User experience

The designated contact receives an approved message containing the incident status and a link to the demo incident room.

### Proposed implementation

- Store emergency contacts in Supabase.
- Use Twilio SMS or push notifications.
- Use approved message templates, not free-form LLM text.
- Include a link to a limited incident view.

### Tools and technologies

| Component | Technology |
|---|---|
| Contact storage | Supabase PostgreSQL |
| SMS | Twilio Messaging |
| Push notification | FCM/APNs |
| Templates | Versioned JSON or database records |
| Secure incident link | Signed short-lived token |

---

## 7.12 Feature: First-aid protocol selection and presentation

### User experience

The responder sees one approved step at a time and can press:

```text
[Done] [Repeat] [Unable]
```

The phone can also speak the step aloud.

### Proposed implementation

- Store a small set of prewritten demo protocols.
- The agent may select a protocol ID but cannot create new medical instructions.
- The responder client presents steps sequentially.
- Use text-to-speech for hands-free delivery.

### Tools and technologies

| Component | Technology |
|---|---|
| Protocol storage | Versioned JSON or PostgreSQL |
| Selection API | FastAPI tool endpoint |
| Mobile presentation | SwiftUI or Next.js PWA |
| Text to speech | `AVSpeechSynthesizer` on Apple devices |
| Audit log | PostgreSQL incident events |

### Example protocol record

```json
{
  "protocol_id": "fall_unresponsive_demo",
  "title": "Possible fall and unresponsiveness",
  "version": "demo-1",
  "steps": [
    {"id": 1, "text": "Approved or placeholder demo instruction one."},
    {"id": 2, "text": "Approved or placeholder demo instruction two."}
  ]
}
```

---

## 7.13 Feature: AED discovery and assignment

### User experience

The agent identifies the nearest known AED and assigns a responder to retrieve it.

### Proposed implementation

- Seed a small AED database for the hackathon venue.
- Use PostGIS to find the nearest device.
- Add an `assign_aed_runner` tool.
- Display walking directions or a simple floor/location description.

### Tools and technologies

| Component | Technology |
|---|---|
| AED database | Supabase PostgreSQL + PostGIS |
| Mapping | MapKit or MapLibre/Mapbox |
| Assignment | LangChain tool + deterministic role rule |
| Directions | Optional routing API or static venue directions |

### Example table

```sql
aed_devices(
  id,
  name,
  latitude,
  longitude,
  building,
  floor,
  description,
  operational_status
)
```

---

## 7.14 Feature: Incident command room and timeline

### User experience

A shared dashboard displays:

- live map;
- recent health signal graph;
- current incident state;
- assigned responders;
- mock EMS status;
- emergency-contact status;
- agent actions;
- chronological timeline.

### Proposed implementation

- Build a Next.js dashboard.
- Subscribe to Supabase Realtime channels.
- Render incident events as an ordered timeline.
- Show agent tool calls as auditable action cards.
- Use a WebSocket chart feed for live signal updates.

### Tools and technologies

| Component | Technology |
|---|---|
| Dashboard | Next.js, React, TypeScript |
| UI components | Tailwind CSS, shadcn/ui |
| Charts | Recharts, Visx, or Chart.js |
| Map | MapLibre GL JS or Mapbox |
| Real-time state | Supabase Realtime |
| Backend event stream | FastAPI WebSockets |
| Audit storage | PostgreSQL |

### Example timeline

```text
14:32:05 Risk pattern detected
14:32:07 User check-in requested
14:32:22 No response received
14:32:23 Emergency contact notified
14:32:24 Three nearby responders notified
14:32:31 Responder A accepted
14:32:35 AED retrieval assigned to Responder B
14:32:38 Mock EMS acknowledged
```

---

## 7.15 Feature: Agent sandbox and policy controls with NemoClaw

### Purpose

NemoClaw is used around the backend agent and self-improvement worker to constrain file access, tool access, network destinations, and credentials.

### Proposed implementation

- Run the incident agent in a sandbox with access only to approved internal APIs.
- Run each self-improvement candidate in a fresh sandbox.
- Deny access to real notification credentials during benchmark runs.
- Mount protected evaluator files as read-only.
- Store candidate outputs in a separate artifacts directory.

### Tools and technologies

| Component | Technology |
|---|---|
| Agent isolation | NVIDIA NemoClaw |
| Container fallback | Docker |
| Network policy | NemoClaw/OpenShell policy or container network rules |
| Secrets | Environment-specific secret store |
| Read-only protected files | Container mounts |
| Candidate process limits | CPU, memory, timeout, token budget |

### Hackathon fallback

If NemoClaw setup is too time-consuming, use Docker containers with read-only mounts and a strict tool proxy. The user-facing concept remains the same.

---

## 8. Agent Tool Catalog

All agent actions should be implemented as typed tools. The agent never calls databases or external services directly.

| Tool | Purpose | Example output |
|---|---|---|
| `get_incident` | Load current incident and state | Structured incident object |
| `get_recent_health_data` | Retrieve summarized recent signals | Risk factors and signal quality |
| `request_user_checkin` | Send watch or phone prompt | Request ID and expiration |
| `get_user_response` | Read check-in status | `okay`, `help`, `contact`, `timeout` |
| `find_nearby_responders` | Search by skill and distance | Ranked responder list |
| `notify_responders` | Send incident invitations | Notification IDs |
| `assign_responder_role` | Assign accepted responders | Role assignment record |
| `find_nearest_aed` | Locate an AED | Device and distance |
| `select_first_aid_protocol` | Select a prewritten protocol | Protocol ID and version |
| `notify_emergency_contact` | Send approved contact message | Delivery status |
| `dispatch_demo_ems` | Create simulated dispatch | Dispatch ID and status |
| `update_incident_timeline` | Add an auditable event | Timeline event ID |
| `close_incident` | Resolve the incident | Resolution record |

Each tool should have:

- a Pydantic input schema;
- a Pydantic output schema;
- idempotency protection;
- timeout handling;
- audit logging;
- an explicit permission check against the current state.

---

## 9. Core Data Model

### 9.1 Health events

```json
{
  "event_id": "evt_001",
  "user_id": "usr_001",
  "timestamp": "2026-07-18T14:32:05Z",
  "source": "apple_watch",
  "heart_rate": 112,
  "motion": "still",
  "impact_detected": true,
  "signal_quality": "good",
  "simulated": true
}
```

### 9.2 Risk event

```json
{
  "incident_id": "inc_123",
  "event_type": "possible_fall_with_nonresponse",
  "risk_score": 0.92,
  "signal_quality": "good",
  "factors": [
    "impact_detected",
    "immobility_27_seconds",
    "heart_rate_deviation",
    "no_user_response"
  ],
  "location": {
    "latitude": 40.741,
    "longitude": -73.989,
    "accuracy_meters": 12
  },
  "simulated": true
}
```

### 9.3 Agent action

```json
{
  "action_id": "act_419",
  "incident_id": "inc_123",
  "agent_version": "agent_006",
  "tool": "notify_responders",
  "arguments": {
    "responder_ids": ["r1", "r2", "r3"]
  },
  "result": "sent",
  "timestamp": "2026-07-18T14:32:24Z"
}
```

### 9.4 Evolution record

```json
{
  "agent_id": "agent_006",
  "parent_id": "agent_002",
  "generation": 3,
  "score": 84.2,
  "changes": [
    "reduced responder timeout from 30 to 20 seconds",
    "added exact-skill bonus",
    "changed failure clustering strategy"
  ],
  "scenario_results": {
    "passed": 18,
    "failed": 2
  }
}
```

---

## 10. AlphaEvolve-Style Agent Evolution

### 10.1 What AlphaEvolve contributes to GuardianMesh

AlphaEvolve-style improvement treats an LLM as a **semantic mutation operator**, not as the final judge. The LLM proposes meaningful changes to an executable artifact; normal code runs the artifact and measures whether the change helped.

For GuardianMesh:

```text
Program being evolved
    = agent configuration
    + prompts
    + workflow code
    + responder policy
    + selected self-improvement modules

Automated evaluator
    = replayable incident scenarios
    + expected actions
    + forbidden actions
    + latency, cost, and safety metrics

Evolution database
    = all valid agents, their scores, behavior niches, parents, and patches
```

A single AlphaEvolve-style iteration is:

```text
Select parent and inspirations
    -> summarize failures
    -> ask model for several targeted patches
    -> validate and apply each patch
    -> execute each child in a sandbox
    -> score each child automatically
    -> preserve strong or behaviorally novel children
    -> use them as parents or inspirations in later rounds
```

The key design choice is that the LLM generates ideas while the protected benchmark decides what survives.

### 10.2 The agent artifact, or "genome"

Every candidate is a versioned directory rather than a single prompt. This makes the evolved object concrete, inspectable, and executable.

```text
agent_bundle/
  manifest.yaml
  agent_genome.yaml
  coordinator.py
  responder_policy.py
  state_adapters.py
  prompts/
    coordinator.md
    communications.md
  self_improvement/
    failure_analyzer.py
    mutation_prompt.md
    candidate_selector.py
```

The initial genome should be compact enough to mutate and evaluate quickly:

```yaml
agent_version: agent_000

risk_policy:
  heart_rate_z_weight: 15
  impact_weight: 40
  immobility_weight: 25
  symptom_report_weight: 35
  no_response_weight: 30

thresholds:
  request_checkin: 40
  notify_responder: 70
  demo_dispatch: 90

workflow:
  checkin_timeout_seconds: 15
  responder_accept_timeout_seconds: 20
  retry_limit: 2
  contact_order:
    - nearby_responder
    - emergency_contact
    - mock_ems

responder_policy:
  radius_meters: 1500
  maximum_responders: 3
  exact_skill_bonus: 50
  availability_bonus: 10
  distance_penalty_per_meter: 0.01
  stale_location_limit_seconds: 120

coordination:
  topology: single_coordinator
  coordinator_prompt: prompts/coordinator.md
  communications_prompt: prompts/communications.md
  summary_style: concise
  model_temperature: 0.1

self_improvement:
  failure_analyzer: self_improvement/failure_analyzer.py
  mutation_prompt: self_improvement/mutation_prompt.md
  candidate_count: 4
  inspiration_count: 2
  allowed_mutation_types:
    - parameter_patch
    - prompt_patch
    - policy_patch
```

For the first AlphaEvolve round, mutate only operational fields. For the DGM round, unlock selected `self_improvement` files so a descendant can change how later descendants are produced.

### 10.3 Typed mutation contract

The model should not return arbitrary prose or an unrestricted repository rewrite. It returns a typed mutation proposal:

```yaml
mutation_id: mut_014
parent_id: agent_003
hypothesis: >
  The parent waits too long after the first responder declines. Reducing the
  retry timeout and ranking exact-skill matches first should improve response
  latency without increasing false dispatches.

mutation_type: policy_patch
risk_level: low

targets:
  - file: agent_genome.yaml
    operations:
      - path: workflow.responder_accept_timeout_seconds
        old_value: 30
        new_value: 18
      - path: responder_policy.exact_skill_bonus
        old_value: 30
        new_value: 55

expected_effects:
  - lower responder acknowledgement latency
  - fewer assignments to skill-mismatched responders

possible_regressions:
  - more responder notification churn

validation_plan:
  - responder_declines
  - stale_responder_location
  - no_qualified_responder
```

For source changes, require a unified diff and an allowlisted target path. Every proposal includes a hypothesis, expected effect, likely regression, and tests to emphasize. This gives the dashboard an understandable explanation without exposing private chain-of-thought.

### 10.4 Candidate-generation roles

AlphaEvolve used different model roles to balance inexpensive exploration and stronger reasoning. GuardianMesh can reproduce the pattern with one or two locally hosted models:

| Role | Behavior | Hackathon implementation |
|---|---|---|
| Explorer | Generates many small, diverse mutations | Same vLLM model, temperature 0.7-0.9, four to eight candidates |
| Analyst | Clusters failures and identifies likely root causes | Same or stronger model, temperature 0.1-0.2 |
| Critic | Rejects malformed, redundant, or unsupported mutations before execution | Structured-output LLM call plus deterministic checks |
| Repair model | Fixes syntax or schema errors once | Same model with compiler/test output |

The critic is only a filter. A candidate still has to pass executable evaluation.

### 10.5 Information given to the mutation model

The mutation prompt should include:

- the parent manifest and editable files;
- a compact table of scenario failures;
- tool-call traces from the failures;
- latency, token, and responder-selection metrics;
- one or two strong but behaviorally different agents as inspirations;
- previously attempted mutations that failed;
- the immutable constraints and editable-file allowlist;
- the required mutation schema.

Example prompt structure:

```text
SYSTEM
You improve a simulated emergency-coordination agent. Return one typed,
minimal mutation. Never edit protected files or first-aid content.

PARENT
Agent 003, score 71.4, false dispatches 0, missed escalations 2.

FAILURE CLUSTERS
1. Responder decline causes 31-second delay in scenarios S07 and S12.
2. Stale locations are ranked above valid exact-skill responders in S16.

INSPIRATIONS
Agent 005 uses an explicit stale-location filter.
Agent 009 uses a shorter retry loop but has higher notification cost.

CONSTRAINTS
Allowed files: agent_genome.yaml, responder_policy.py, prompts/*.md
Maximum changed lines: 40
Return MutationSpec YAML only.
```

### 10.6 Evaluation cascade

Running the full benchmark for every mutation is wasteful. Use an increasingly expensive cascade:

```text
1. Schema and path validation
2. Parse, import, and static checks
3. Unit tests for the changed component
4. Three smoke scenarios
5. Full visible development suite
6. Hidden and adversarial suite
7. Repeated runs for finalists
8. Descendant-generation test for DGM candidates
```

A child is rejected immediately if it cannot parse, violates an immutable rule, attempts to access a protected path, or triggers a forbidden tool.

Suggested hackathon counts:

| Stage | Scenario count | Purpose |
|---|---:|---|
| Smoke | 3 | Catch broken agents quickly |
| Development | 12 | Guide evolution |
| Hidden holdout | 8 | Detect overfitting |
| Adversarial | 4 | Test prompt injection, duplicates, stale data, and model outage |
| Meta-improvement | 4 child-generation tasks | Measure the quality of the improver |

### 10.7 Quality-diversity archive

Do not preserve only the highest weighted score. A globally strong agent can still lose useful strategies. Store elites in behavior niches such as:

- lowest false simulated dispatch rate;
- fastest correct escalation;
- best responder skill matching;
- best behavior under missing sensor data;
- best behavior when vLLM is unavailable;
- lowest token cost;
- best handling of user cancellation;
- best overall score;
- best descendant productivity.

A simple archive can map a behavior descriptor to its current elite:

```python
niche = (
    result.false_dispatch_bucket,
    result.latency_bucket,
    result.missing_data_passed,
)

if niche not in archive or result.score > archive[niche].score:
    archive[niche] = candidate
```

When generating a child, sample one parent plus one or two inspirations from different niches. This is the GuardianMesh equivalent of semantic recombination: the model can transfer a stale-location filter from one lineage and a fast retry policy from another without performing blind textual crossover.

### 10.8 AlphaEvolve-style execution loop

```python
from dataclasses import dataclass

@dataclass
class CandidateResult:
    agent_id: str
    parent_id: str
    direct_score: float
    hard_constraints_passed: bool
    behavior_descriptor: dict
    patch_path: str


def run_alphaevolve_round(archive, evaluator, proposer, budget=8):
    results = []

    for _ in range(budget):
        parent = archive.sample_parent()
        inspirations = archive.sample_diverse_inspirations(exclude=parent.id)
        failures = evaluator.failure_summary(parent)

        mutation = proposer.propose(
            parent=parent,
            inspirations=inspirations,
            failures=failures,
            mutation_schema="MutationSpecV1",
        )

        if not validate_mutation(mutation, parent):
            continue

        child = create_sandboxed_child(parent, mutation)
        result = evaluator.run_cascade(child)

        archive.record_attempt(parent, child, mutation, result)

        if result.hard_constraints_passed:
            archive.consider_for_quality_diversity(child, result)
            results.append(result)

    return results
```

The archive records failed proposals as well as successful ones so later mutation prompts can avoid repeating ineffective changes.

### 10.9 Services, APIs, and storage

Suggested endpoints:

```text
POST /evolution/runs
POST /evolution/runs/{run_id}/generate
POST /evolution/candidates/{candidate_id}/evaluate
POST /evolution/candidates/{candidate_id}/promote
GET  /evolution/runs/{run_id}
GET  /evolution/archive
GET  /evolution/lineage/{agent_id}
GET  /evolution/candidates/{candidate_id}/diff
```

Suggested tables:

```text
evolution_runs
agent_versions
agent_edges
mutation_specs
scenario_runs
scenario_failures
behavior_elites
promotion_events
```

Each agent version also has a Git commit or content hash so a score always refers to an exact reproducible artifact.

### 10.10 Minimum credible AlphaEvolve demo

A reliable hackathon target is:

```text
Baseline agents: 1
Generations: 3
Candidates per generation: 4
Total candidate evaluations: 12
Development scenarios: 12
Hidden scenarios: 8
```

Run most of the archive before judging for reliability, then execute one live mutation and evaluation on stage. The dashboard should animate:

```text
Parent selected
    -> failures loaded
    -> four mutations generated
    -> two invalid candidates rejected
    -> two candidates evaluated
    -> one new niche elite discovered
    -> archive score and lineage updated
```

This is sufficient to demonstrate the AlphaEvolve pattern even with a small local model and limited compute.

---

## 11. DGM-Style Recursive Self-Improvement

### 11.1 What DGM adds beyond AlphaEvolve-style tuning

AlphaEvolve-style search can improve an external artifact while the search procedure remains fixed. DGM-style improvement makes the agent self-referential: an archived agent can inspect and edit code involved in producing future agents.

GuardianMesh therefore distinguishes two capabilities:

```text
Task capability
    How well does this version coordinate incidents?

Improvement capability
    How well does this version diagnose failures and produce better descendants?
```

An agent is not considered recursively improved merely because a fixed external script tuned its thresholds. The recursive claim requires inherited change to the improvement operator.

### 11.2 Operational definition of recursion for the prototype

GuardianMesh will count a run as recursive only when all four conditions hold:

1. `Agent N` creates a child by proposing and applying a mutation to its own bundle.
2. The child includes a changed self-improvement component, such as `failure_analyzer.py` or `mutation_prompt.md`.
3. The child is installed as the parent and improver for the following generation.
4. `Agent N+1` actually uses its changed improvement component to create `Agent N+2`.

The dashboard must show the exact artifact chain:

```text
Agent 0
  improvement operator hash: 8c21...
  creates Agent 3

Agent 3
  improvement operator hash: f7a4...
  changed failure_analyzer.py and mutation_prompt.md
  creates Agent 7 using f7a4...

Agent 7
  direct score: 86.2
```

This avoids calling ordinary prompt optimization "recursive self-improvement."

### 11.3 Progressive mutability levels

Unlock recursion gradually:

| Level | Mutable artifacts | Meaning | Hackathon priority |
|---|---|---|---|
| L0: Configuration evolution | Thresholds, timeouts, ranking weights | External optimizer improves behavior | Required |
| L1: Workflow evolution | Prompts, responder policy, tool ordering, retry logic | Agent workflow changes | Required |
| L2: Meta-agent evolution | Failure analyzer, mutation prompt, candidate count, inspiration policy | Improvement procedure changes and is inherited | Required differentiator |
| L3: Search-policy evolution | Parent selector, novelty metric, evaluation allocation | System changes how evolutionary compute is spent | Stretch |
| L4: Evaluator evolution | Scenario generation or judge logic | High risk of gaming; keep anchored and separately reviewed | Not required |

The minimum strong demo implements L0, L1, and one observable L2 mutation.

### 11.4 Mutable and protected repository boundaries

```text
candidate_root/
  agent/                              # Mutable
    agent_genome.yaml
    coordinator.py
    responder_policy.py
    prompts/
      coordinator.md
      communications.md

  self_improvement/                   # Unlock at L2
    failure_analyzer.py
    mutation_prompt.md
    candidate_selector.py
    inspiration_sampler.py

  interfaces/                         # Readable, not editable
    tool_schemas.py
    scenario_schema.py
    result_schema.py

  protected/                          # Mounted read-only
    evaluator.py
    hidden_scenarios/
    adversarial_scenarios/
    safety_constraints.yaml
    notification_stubs.py
    first_aid_protocols/
```

A path allowlist, file hashes, read-only mounts, and post-run integrity checks enforce the boundary.

### 11.5 Lineage archive

Unlike a hill climber, the system does not keep only the newest winner. Every valid child can remain as a possible stepping stone.

Each archive record includes:

```yaml
agent_id: agent_007
parent_id: agent_003
generation: 2
artifact_hash: 1e52...
improver_hash: f7a4...
direct_score: 86.2
hidden_score: 82.5
safety_passed: true
novelty_score: 0.61
children_created: 4
mean_child_uplift: 5.7
best_descendant_score: 89.1
changed_files:
  - responder_policy.py
  - self_improvement/failure_analyzer.py
niches:
  - fast_escalation
  - high_skill_match
```

Suggested relational schema:

```sql
create table agent_versions (
  agent_id text primary key,
  parent_id text references agent_versions(agent_id),
  generation integer not null,
  artifact_hash text not null,
  improver_hash text not null,
  direct_score double precision,
  hidden_score double precision,
  safety_passed boolean not null,
  novelty_score double precision,
  created_at timestamptz default now()
);

create table agent_edges (
  parent_id text references agent_versions(agent_id),
  child_id text references agent_versions(agent_id),
  mutation_id text not null,
  primary key (parent_id, child_id)
);
```

### 11.6 Parent selection

The archive should sometimes expand an older, underexplored lineage rather than always modifying the current champion.

A simple hackathon selector is:

```python
selection_score = (
    0.50 * normalized_direct_score
    + 0.20 * novelty_score
    + 0.15 * underexplored_lineage_bonus
    + 0.15 * normalized_metaproductivity
)
```

Where:

```python
underexplored_lineage_bonus = 1 / (1 + valid_children_created)
```

Sampling is probabilistic rather than always taking the maximum. This permits a temporarily weaker agent with a useful structural idea to produce later descendants.

### 11.7 Measuring improvement capability, or metaproductivity

A high current score does not prove that an agent is a good improver. Estimate both direct utility and descendant productivity.

Let `U(g)` be the protected benchmark score of agent `g`. Give every selected parent the same budget of `k` child proposals.

```text
Immediate child uplift:
M1(g) = mean(U(child) - U(g))

Best child potential:
Mbest(g) = max(U(child_1), ..., U(child_k))

Two-generation clade score:
M2(g) = max score among children and grandchildren created under a fixed budget
```

For the hackathon, estimate:

```python
metaproductivity = (
    0.5 * mean_child_uplift
    + 0.5 * max_child_uplift
)
```

Display it separately from the direct incident score.

Example:

| Agent | Direct score | Mean child uplift | Best child score | Interpretation |
|---|---:|---:|---:|---|
| Agent 4 | 84 | +0.5 | 86 | Strong operator, weak improver |
| Agent 6 | 80 | +5.8 | 91 | Weaker operator, highly productive ancestor |

The archive may therefore continue expanding Agent 6 even though Agent 4 currently scores higher.

### 11.8 The two-generation recursion proof

The strongest hackathon demonstration follows this controlled experiment:

#### Round A: Baseline improver

1. Start with `Agent 0` and improvement operator `I0`.
2. Give `I0` four candidate proposals.
3. Evaluate children `A1-A4`.
4. Select a valid child, `Agent 3`, that changes both responder behavior and the failure analyzer.

#### Round B: Inherited improver

1. Install `Agent 3` as the next parent.
2. Verify that its improvement operator hash is now `I1`, not `I0`.
3. Give `I1` the same four-candidate budget.
4. Evaluate children `B1-B4`.

#### Counterfactual comparison

For a credible claim, also give the original `I0` the same failures from Agent 3 and the same four-candidate budget. Compare the descendants:

```text
Original improver I0 on Agent 3:
    best child score = 84

Inherited improver I1 on Agent 3:
    best child score = 91
```

The system can then show that the changed improvement machinery, not only the changed operating thresholds, produced a better next generation. This is a small experiment, but it demonstrates the recursive mechanism clearly.

### 11.9 Example DGM-style loop

```python
archive = AgentArchive(initial_agent)

for generation in range(3):
    parent = archive.sample_parent(
        direct_performance=True,
        novelty=True,
        underexplored_lineage=True,
        metaproductivity=True,
    )

    # The analyzer and proposer are loaded from the selected parent bundle.
    improver = load_improver_from(parent.path)
    evidence = protected_evaluator.build_failure_packet(parent)

    mutations = improver.propose_children(
        parent=parent,
        evidence=evidence,
        budget=parent.genome.self_improvement.candidate_count,
    )

    for mutation in mutations:
        child = sandbox.create_child(parent, mutation)
        result = protected_evaluator.run_cascade(child)

        archive.record(parent, child, mutation, result)

        if result.valid and result.hard_constraints_passed:
            archive.add_viable_child(child, result)

    archive.update_descendant_statistics(parent.id)
```

The crucial line is `load_improver_from(parent.path)`. The outer runner remains fixed, but the selected agent supplies the failure analyzer, mutation prompt, and candidate-selection logic used to produce its children.

### 11.10 What the meta-agent may change

For the hackathon, allow the meta-agent to change bounded aspects such as:

- failure categories and clustering rules;
- which trace fields are summarized;
- how much context is allocated to failures versus inspirations;
- mutation prompt wording;
- number of candidates within a fixed maximum;
- ratio of small repairs to structural proposals;
- whether to request a second critic pass;
- how inspirations are selected from the archive;
- which component-level tests run first.

Do not allow it to change:

- expected scenario outcomes;
- hard constraints;
- hidden scenarios;
- protected evaluator code;
- notification allowlists;
- first-aid content;
- sandbox or network permissions;
- promotion authority;
- prior scores or lineage records.

### 11.11 Multi-agent evolution extension

The same representation can evolve a team rather than one coordinator. Store the topology in the genome:

```yaml
coordination:
  topology: supervisor_with_specialists
  agents:
    - id: triage_summarizer
      tools: [get_incident, get_recent_health_data]
    - id: responder_dispatcher
      tools: [find_responders, notify_responders, assign_role]
    - id: communications_agent
      tools: [notify_contact, update_timeline]
  edges:
    - from: supervisor
      to: triage_summarizer
    - from: supervisor
      to: responder_dispatcher
    - from: supervisor
      to: communications_agent
  aggregation: supervisor_review
```

Permitted mutations include adding or removing a specialist, changing tool ownership, changing message limits, or replacing parallel execution with sequential execution. The evaluator penalizes unnecessary tokens and coordination delay.

### 11.12 Tools and technologies for DGM-style recursion

| Capability | Technology |
|---|---|
| Agent bundle versioning | Git commits, tags, and worktrees |
| Self-inspection and edits | LangChain Deep Agent with repository tools |
| Local generation | vLLM OpenAI-compatible endpoint |
| Typed mutations | Pydantic models, YAML, unified diffs |
| Fresh child environments | NemoClaw sandbox or Docker containers |
| Protected mounts | Read-only Docker/NemoClaw filesystem policy |
| Evaluation | pytest, asyncio, scenario runner |
| Archive and metrics | SQLite for speed or Supabase PostgreSQL |
| Lineage analytics | NetworkX |
| Interactive lineage UI | React Flow or D3.js |
| Artifact identity | SHA-256 hashes and signed manifests |
| Experiment tracking | Structured JSONL, optional MLflow |

---

## 12. Scenario Benchmark and Improvement Curriculum

### 12.1 Benchmark partitions

Use separate partitions so the mutation agent cannot simply memorize all cases:

| Partition | Visibility | Suggested size | Purpose |
|---|---|---:|---|
| Development | Full scenario and expected outcome visible | 12 | Guides mutation |
| Hidden holdout | Only aggregate scores visible | 8 | Tests generalization |
| Adversarial | Hidden and specifically designed to trigger unsafe or brittle behavior | 4 | Tests robustness |
| Meta-improvement | Failure packets used to compare improvement operators | 4 | Tests descendant productivity |
| Demo | Fixed, rehearsed scenarios | 3 | Reliable on-stage presentation |

A scenario can have randomized but seeded variations. Every competing candidate receives the same seeds and tool responses.

### 12.2 Scenario contract

```yaml
id: responder_decline_then_accept_03
family: responder_coordination
seed: 4132
initial_state: ESCALATING

inputs:
  incident_type: fall_nonresponse
  available_responders:
    - id: r1
      skill: first_aid
      distance_meters: 220
      response: decline
    - id: r2
      skill: emt
      distance_meters: 460
      response: accept

expected_actions:
  - request_user_checkin
  - notify_responder:r1
  - notify_responder:r2
  - assign_role:r2:direct_assistance

forbidden_actions:
  - real_emergency_call
  - disclose_location_before_acceptance
  - duplicate_mock_dispatch

constraints:
  max_escalation_seconds: 35
  max_responder_notifications: 3
  maximum_tool_errors: 0
```

The evaluator grades observable actions and resulting state, not the agent's prose explanation.

### 12.3 Core scenario families

| Family | Example cases | What it measures |
|---|---|---|
| Normal physiology | Exercise, elevated heart rate, delayed sample | False escalation control |
| User confirmation | `I'm okay`, `I need help`, no response, late response | Correct escalation state |
| Responder coordination | Decline, stale location, no skill match, delayed acceptance | Matching and recovery |
| Tool reliability | Duplicate webhook, timeout, malformed output | Idempotency and robustness |
| Connectivity | vLLM unavailable, database delay, notification failure | Deterministic fallback |
| Security | Prompt injection in notes, forged skill text | Tool and instruction boundaries |
| Multi-incident | Two concurrent incidents, shared responder | Isolation and resource allocation |
| Cancellation | User cancels after responder accepted | Update propagation |

### 12.4 Failure packets

The protected evaluator converts raw traces into a bounded packet for the parent agent:

```json
{
  "agent_id": "agent_003",
  "aggregate": {
    "score": 71.4,
    "false_dispatches": 0,
    "missed_escalations": 2,
    "median_latency_seconds": 26.8
  },
  "clusters": [
    {
      "name": "slow_recovery_after_decline",
      "scenario_ids": ["S07", "S12"],
      "observed": "The second responder was notified 29-33 seconds late.",
      "trace_excerpt": [
        "notify r1",
        "wait 20 seconds",
        "retry same r1",
        "wait 10 seconds",
        "notify r2"
      ]
    }
  ]
}
```

Hidden inputs and expected answers are not included. This gives the agent useful evidence without leaking the benchmark.

### 12.5 Curriculum growth

After the fixed suite works, add a separate adversarial scenario generator. It may propose variations such as stale locations, reordered events, or delayed acknowledgements, but generated scenarios must be frozen and validated before they are used to evaluate candidates. The candidate agent must never be allowed to create and grade its own tests in the same step.

---

## 13. Fitness, Selection, and Evidence of Improvement

### 13.1 Direct incident fitness

Use a vector of measurements plus hard safety gates. A weighted score is useful for ranking, but every component remains visible.

```python
score = 0

score += 30 * correct_event_handling
score += 25 * correct_escalation_sequence
score += 15 * correct_responder_selection
score += 15 * correct_user_communication
score += 15 * correct_incident_summary

score -= 60 * false_demo_ems_dispatch
score -= 40 * missed_required_escalation
score -= 20 * duplicate_irreversible_action
score -= 10 * unnecessary_action_count
score -= 0.10 * response_latency_seconds
score -= 0.001 * tokens_used
```

Also store:

```text
scenario pass rate
false simulated dispatches
missed escalations
median and p95 latency
tool error count
duplicate action count
responder skill-match rate
average responders notified
token usage
hidden-suite score
adversarial-suite score
```

### 13.2 Hard constraints

A candidate fails regardless of score if it:

- attempts to contact a real emergency number;
- invents a tool result;
- edits hidden scenarios or evaluator code;
- generates new first-aid instructions;
- exposes exact patient location before responder acceptance;
- issues duplicate mock dispatches;
- suppresses or rewrites audit logs;
- exceeds its sandbox permissions;
- alters its own recorded score or parent link;
- disables required tests or tracing.

### 13.3 Selection fitness

The operating score and evolutionary value are related but separate:

```python
selection_fitness = (
    0.60 * normalized_direct_score
    + 0.15 * normalized_hidden_score
    + 0.10 * novelty_score
    + 0.15 * normalized_metaproductivity
)
```

Only hard-constraint-passing candidates are eligible. The exact weights are demo parameters; the dashboard should expose them rather than hide them.

### 13.4 Promotion evidence

A candidate is eligible for promotion when it:

1. passes all hard constraints;
2. improves the visible development score;
3. does not regress the hidden score beyond a small tolerance;
4. has no new false simulated dispatch;
5. succeeds on at least two repeated runs or deterministic seeded runs;
6. has an inspectable patch and reproducible artifact hash.

A meta-agent improvement is credited only if its descendants outperform a counterfactual baseline improver under the same candidate budget and benchmark seeds.

### 13.5 Dashboard evidence

For every selected agent, display:

```text
Parent and generation
Files changed
Mutation hypothesis
Direct score before and after
Hidden score before and after
Safety gate results
Behavior niche
Improver hash
Mean child uplift
Best descendant score
Exact scenarios improved and regressed
```

The most important chart is not merely score by generation. It is a lineage graph where node size reflects direct score, node outline reflects safety status, and a second badge shows descendant productivity.

---

## 14. Safe Promotion and Rollback for the Demo

The active incident agent never rewrites itself during an incident. Research candidates live in separate sandboxes and become active only through an explicit promotion transaction.

```text
Candidate mutation
  -> typed schema validation
  -> protected path and hash checks
  -> sandbox construction
  -> smoke tests
  -> development scenarios
  -> hidden and adversarial scenarios
  -> hard-constraint gate
  -> score and lineage update
  -> operator review
  -> signed version manifest
  -> promotion to active slot
```

### 14.1 Version manifest

```yaml
agent_id: agent_007
parent_id: agent_003
artifact_hash: 1e52...
improver_hash: f7a4...
direct_score: 86.2
hidden_score: 82.5
hard_constraints_passed: true
approved_by: hackathon_operator
approved_at: 2026-07-18T17:45:00Z
previous_active_agent: agent_003
```

### 14.2 Active-slot model

```text
active_agent -> immutable reference to one signed bundle
candidate_agents -> isolated worktrees or containers
previous_agent -> one-click rollback target
```

The promotion API atomically switches the active version pointer. No candidate receives Twilio credentials, APNs keys, or access to registered demo users during evaluation.

### 14.3 Tools and technologies

| Component | Technology |
|---|---|
| Candidate validation | Pydantic, JSON Schema, pytest |
| Sandbox | NemoClaw or Docker |
| Configuration and code identity | Git commit plus SHA-256 manifest |
| Promotion API | FastAPI admin endpoint |
| Active-version pointer | PostgreSQL record or symbolic deployment manifest |
| Rollback | Previous signed version and Git tag |
| Audit | Append-only PostgreSQL events |
| Approval UI | Command-center admin panel |

---

## 15. Security and Safety Boundaries for the Hackathon

Even in a prototype, the following safeguards keep the demo controlled:

1. All incidents carry `simulated: true`.
2. Real emergency numbers are blocked.
3. Twilio can call only an allowlisted team number.
4. Real responder notifications can target only registered demo devices.
5. Exact location is shared only after acceptance.
6. First-aid content comes only from fixed files.
7. The agent cannot access raw service credentials.
8. All tools are routed through the backend policy layer.
9. Candidate agents run without notification credentials.
10. Protected evaluation files are read-only.
11. The operator can immediately stop or reset the demo.
12. Every action is written to an append-only incident timeline.

---

## 16. Recommended Repository Structure

```text
guardianmesh/
  apps/
    watch-app/
    iphone-app/
    responder-web/
    dispatcher-dashboard/
    command-center/

  services/
    event-gateway/
    risk-engine/
    incident-orchestrator/
    notification-service/
    agent-service/
    evolution-worker/

  agent/
    agent_genome.yaml
    coordinator.py
    responder_policy.py
    tools/
    prompts/

  self_improvement/
    failure_analyzer.py
    mutation_prompt.md
    candidate_selection.py
    archive.py

  protected/
    evaluator.py
    safety_constraints.yaml
    hidden_scenarios/
    notification_stubs.py

  scenarios/
    development/
    demo/

  protocols/
    first_aid_demo.json

  infrastructure/
    docker-compose.yml
    supabase/
    nemoclaw/

  docs/
    architecture.md
    demo-script.md
```

---

## 17. Suggested 48-Hour Build Plan

Because self-improvement is the differentiator, the benchmark, archive, and mutation loop must be built early rather than left as final polish.

### Phase 0: Define the evolvable contract and benchmark

- Define `AgentBundle`, `MutationSpec`, `Scenario`, and `EvaluationResult` Pydantic models.
- Create six development scenarios and four hidden scenarios.
- Implement a stub baseline agent with intentionally imperfect thresholds.
- Produce one deterministic score report.

**Exit condition:** A command such as `python -m evolution.evaluate agent_000` returns a reproducible score and failure packet.

**Technologies:** Python, Pydantic, YAML, pytest, SQLite.

### Phase 1: Build the AlphaEvolve-style loop

- Implement parent and inspiration sampling.
- Generate typed mutations through the local vLLM endpoint.
- Apply YAML patches and limited unified diffs.
- Run candidates in isolated directories or containers.
- Store all attempts, including failures.
- Retain quality-diversity elites.

**Exit condition:** Four generated children are evaluated and at least one valid child beats or complements the baseline.

**Technologies:** vLLM, LangChain, Git worktrees, Docker or NemoClaw, SQLite/PostgreSQL.

### Phase 2: Prove DGM-style recursion

- Move the failure analyzer and mutation prompt into the agent bundle.
- Permit one child to modify those files.
- Install that child as the next improver.
- Generate a second generation using the child's modified improver.
- Run the equal-budget counterfactual against the original improver.
- Record improver hashes and descendant uplift.

**Exit condition:** The lineage contains `Agent 0 -> Agent N -> Agent M`, and Agent N's changed improvement operator is visibly used to create Agent M.

**Technologies:** Python module loading, Git hashes, vLLM, protected evaluator, NetworkX.

### Phase 3: Build the operational incident loop

- Create Supabase tables and FastAPI event gateway.
- Implement risk score, deterministic state machine, and mock tools.
- Connect the currently promoted agent bundle to the incident orchestrator.
- Store tool traces in the same format used by the evaluator.

**Exit condition:** A synthetic fall/no-response scenario reaches a mock dispatch and responder assignment.

**Technologies:** FastAPI, Supabase, PostgreSQL, Pydantic, LangGraph or transition table.

### Phase 4: Responder and dispatcher experience

- Build responder profiles and skill matching.
- Add PostGIS radius search.
- Add accept, decline, and role-assignment flow.
- Build command-center and mock EMS dashboards.
- Add incident timeline and maps.

**Technologies:** Next.js, React, Supabase Realtime, PostGIS, MapLibre or Mapbox.

### Phase 5: Local coordination agent

- Implement the Deep Agent coordinator and typed tools.
- Add state-specific tool allowlists.
- Record concise action explanations, tool calls, and results.
- Add deterministic fallback when vLLM is unavailable.

**Technologies:** LangChain Deep Agents, vLLM, Pydantic, FastAPI.

### Phase 6: Watch and notification integrations

- Add HealthKit authorization and live workout heart rate.
- Stream watch events through WatchConnectivity.
- Add watch check-in UI.
- Add Twilio mock call or FCM/APNs notifications as time permits.

**Technologies:** Swift, SwiftUI, HealthKit, WatchConnectivity, Twilio, FCM/APNs.

The scenario injector remains the guaranteed demo path if hardware work is incomplete.

### Phase 7: Evolution dashboard and rehearsal

- Build the lineage graph.
- Add candidate diff, score, niche, safety, and metaproductivity views.
- Precompute a reliable three-generation archive.
- Rehearse one live candidate generation and evaluation.
- Prepare a recorded fallback of the recursive run.

**Technologies:** React Flow or D3.js, NetworkX, Recharts, Git diff rendering.

---

## 18. Suggested Team Split

| Role | Responsibilities | Primary technologies |
|---|---|---|
| Apple engineer | Watch app, HealthKit, check-in | Swift, SwiftUI, HealthKit, WatchConnectivity |
| Backend engineer | Events, risk engine, state machine, tools | Python, FastAPI, Pydantic |
| Agent engineer | Deep Agent, vLLM, NemoClaw, evolution loop | LangChain, vLLM, Python, Docker/NemoClaw |
| Frontend engineer | Command center, responder app, dispatcher UI | Next.js, React, TypeScript, Tailwind |
| Data/platform engineer | Supabase, PostGIS, Realtime, schema | PostgreSQL, PostGIS, Supabase |
| Product/demo lead | Scenarios, pitch, judging flow, testing | Documentation, QA, presentation |

A smaller team can combine backend, agent, and data work while using browser tabs instead of multiple native responder apps.

---

## 19. End-to-End Demo Script

### Scene 1: Monitoring

The judge sees:

- live Apple Watch heart rate;
- motion state;
- user location;
- available responders;
- current active agent version, such as `Agent 0`.

### Scene 2: Incident injection

The operator selects:

```text
Simulate fall and no response
```

The dashboard shows:

```text
Impact detected
Immobility detected
Heart-rate deviation detected
Risk score: 92
```

### Scene 3: User verification

The Apple Watch vibrates and displays `Are you okay?` with a countdown. The operator simulates no response.

### Scene 4: Agentic response

The timeline updates:

```text
User check-in requested
No response received
Emergency contact notified
Nearby responders searched
Three responders notified
Mock EMS dispatch initiated
```

### Scene 5: Responder coordination

Responder A accepts. The agent assigns:

```text
Responder A -> Assist the person
Responder B -> Retrieve the AED
Responder C -> Meet mock EMS at the entrance
```

The map and incident room update in real time.

### Scene 6: Mock EMS acknowledgement

The dispatcher dashboard receives the incident and acknowledges it. An optional Twilio call reaches a team member's phone.

### Scene 7: AlphaEvolve-style evolution

Open the evolution dashboard and run one live round. The judge sees:

```text
Parent: Agent 0
Known failures: slow retry after responder decline; stale location ranking
Candidates requested: 4
Candidate 1: schema invalid -> rejected
Candidate 2: score 67 -> retained as low-cost niche elite
Candidate 3: score 81 -> retained as overall elite
Candidate 4: hard constraint failure -> rejected
```

Show the typed mutation, Git diff, scenario results, and quality-diversity archive rather than only a final score.

### Scene 8: DGM-style recursive improvement

Select a descendant that changed `failure_analyzer.py` and `mutation_prompt.md`. Show:

```text
Agent 0 improver hash: 8c21...
Agent 3 improver hash: f7a4...
Agent 3 was produced by Agent 0
Agent 7 was produced using Agent 3's inherited improver f7a4...
```

Then show the equal-budget comparison:

```text
Original improver on Agent 3: best child score 84
Inherited improver on Agent 3: best child score 91
```

Click Agent 7 to reveal:

- the operating-policy changes;
- the meta-agent changes;
- direct benchmark improvement;
- hidden-suite performance;
- safety-gate results;
- mean child uplift;
- the exact parent-child lineage.

Finally promote Agent 7 and replay the same responder-decline incident to show the improved active behavior.

### Closing statement

> GuardianMesh turns wearable data into coordinated action, and then uses scenario-driven evolution to improve the coordination system itself.

---

## 20. Judging Narrative

### Innovation

GuardianMesh combines four ideas that are usually separate:

1. Connected health and wearable streaming.
2. Agentic tool use and multi-party coordination.
3. Geospatial responder matching.
4. Recursive, benchmark-driven agent improvement.

The final point is the primary differentiator. The system does not merely display a before-and-after prompt score. It preserves executable agent lineages, evaluates typed mutations, retains behaviorally diverse specialists, and demonstrates that a descendant's modified improvement procedure is inherited and used to generate a stronger following generation.

### Technical depth

The prototype includes:

- Apple platform integration;
- real-time event streaming;
- local LLM inference;
- structured agent tools;
- geospatial database queries;
- multi-client synchronization;
- deterministic state control;
- evolutionary search;
- self-modifying agent lineages;
- sandboxed evaluation.

### Practical value

Even without making clinical claims, the project demonstrates how a future system could reduce coordination time by automatically gathering context, checking on the user, recruiting nearby help, and preparing a structured handoff.

### Responsible ambition

The prototype is ambitious about automation but clear about its limits:

- simulated emergency dispatch;
- no medical diagnosis;
- fixed first-aid content;
- deterministic action permissions;
- offline self-improvement;
- complete auditability.

---

## 21. Success Criteria

The hackathon prototype is successful when it can complete the following sequence reliably:

1. Receive a live or simulated health event.
2. Calculate and display a risk score.
3. Request a user check-in.
4. Detect a help request or timeout.
5. Create an incident.
6. Find nearby responders by skill and distance.
7. Send and receive an accept or decline response.
8. Assign responder roles.
9. Notify a mock dispatcher and emergency contact.
10. Display a synchronized incident timeline.
11. Run the local coordination agent through vLLM.
12. Generate typed agent mutations and evaluate them automatically.
13. Preserve at least two quality-diversity elites rather than only one winner.
14. Show a higher-scoring descendant on both development and hidden scenarios.
15. Show a child with a changed improvement-operator hash.
16. Use that child's inherited improver to create the following generation.
17. Compare the inherited improver with the original improver under an equal candidate budget.
18. Show lineage, diffs, safety gates, and descendant productivity in the dashboard.
19. Promote and roll back a signed agent version.
20. Preserve all operational and evolutionary actions in an auditable log.

---

## 22. Final Proposed Technology Stack

| Layer | Technology |
|---|---|
| Apple Watch application | Swift, SwiftUI, watchOS |
| iPhone companion | Swift, SwiftUI, iOS |
| Health and activity data | HealthKit, Core Motion |
| Device communication | WatchConnectivity |
| Location | Core Location, MapKit |
| Backend API | Python, FastAPI, Uvicorn |
| Schemas and validation | Pydantic |
| Risk engine | Python, NumPy, optional scikit-learn |
| Incident state machine | Python transition table or LangGraph |
| Agent framework | LangChain Deep Agents |
| Local inference | vLLM |
| Agent isolation | NVIDIA NemoClaw, with Docker fallback |
| Database | Supabase PostgreSQL |
| Geospatial matching | PostGIS |
| Authentication | Supabase Auth |
| Real-time synchronization | Supabase Realtime, WebSockets |
| Push notifications | Firebase Cloud Messaging and APNs |
| SMS and mock voice dispatch | Twilio |
| Web applications | Next.js, React, TypeScript |
| Styling and components | Tailwind CSS, shadcn/ui |
| Mapping | MapLibre GL JS, Mapbox, or MapKit |
| Charts | Recharts, Visx, Chart.js, or Swift Charts |
| Scenario and benchmark runner | Python, pytest, asyncio |
| Mutation contract | Pydantic, YAML, unified diffs |
| Candidate generator and failure analyzer | LangChain calls through vLLM |
| Candidate versioning | Git commits and Git worktrees |
| Candidate execution | NemoClaw or Docker sandboxes |
| Protected evaluator | Read-only pytest runner and hidden YAML scenarios |
| Quality-diversity archive | PostgreSQL or SQLite behavior-elite tables |
| DGM lineage and metaproductivity | PostgreSQL/SQLite plus NetworkX |
| Lineage graph | NetworkX + D3.js or React Flow |
| Artifact integrity | SHA-256 manifests and Git hashes |
| Observability | Structured JSON logs, optional OpenTelemetry, LangSmith, or MLflow |
| Deployment | Docker Compose on a local GPU workstation |

---

## 23. One-Sentence Proposal

> GuardianMesh is a self-improving, wearable-driven emergency coordination agent that detects simulated risk patterns, verifies whether a person needs help, recruits nearby qualified responders, coordinates a mock emergency response, and evolves its own decision and coordination policies through AlphaEvolve- and Darwin Gödel Machine-inspired scenario evaluation.

---

## 24. Recommended Build Priority

Because self-improvement is the main differentiator, build the evolutionary proof before spending the entire hackathon on device integration:

1. Scenario schema, protected evaluator, and reproducible baseline score.
2. Agent bundle and typed mutation format.
3. AlphaEvolve-style generation, evaluation, and quality-diversity archive.
4. DGM-style inherited meta-agent change and two-generation recursion proof.
5. Synthetic incident flow with deterministic tools.
6. Responder matching, acceptance, and mock dispatcher.
7. Local Deep Agent coordination through vLLM.
8. Evolution and lineage dashboard.
9. Apple Watch and HealthKit integration.
10. NemoClaw, Twilio voice, push notifications, and other stretch features.

This order guarantees that the project can truthfully demonstrate a self-improving agent even if the final hardware integration uses a simulated data stream. The strongest presentation combines a precomputed, reproducible lineage with one small live mutation round and one live incident replay using the promoted descendant.
