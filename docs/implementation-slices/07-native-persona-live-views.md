# Slice 07: Native Persona Live Views

## Outcome

Slice 07 keeps one native Vital Relay app and composes three isolated feature
graphs inside it:

```text
VitalRelayApp
├── community/wearer → existing device-auth incident + redacted dispatch graph
├── responder        → responder-token invitation/decision/accepted graph
└── command          → device-auth incident/timeline/coordination/protocol graph
```

The app does not centralize these APIs in an omnipotent client. A responder
client cannot send a device token or ingest an event. The wearer client cannot
decode an accepted dispatch, exact route, or responder protocol. Persona labels
select a graph but never grant server authority locally.

## Backend responder entry

The responder previously needed a known invitation ID but had no safe way to
read it. Slice 07 adds:

```text
GET /v1/incidents/{incident_id}/responders/{responder_id}/invitation
X-Vital-Relay-Responder-Token: <responder credential>
```

Authentication happens before invitation lookup. The frozen
`ResponderIncidentView` contains only:

- incident ID, kind, current state, state version, and update time;
- that authenticated responder's existing invitation;
- the already-redacted responder snapshot with role, skills, rank, and coarse
  distance band.

It has no user ID, wearer location, trigger event, health snapshot, other
candidate, AED, route, or protocol. A different token receives `401`; a valid
but non-invited responder receives `404`; deactivation revokes access.

Responder decisions return a separate `ResponderDecisionReceiptView`. A
decline receipt contains only the responder's own updated invitation and null
transition/accepted-dispatch fields; command coordination is never serialized
to the responder. Acceptance adds only the authorized transition and that
responder's accepted dispatch. The native decoder rejects a `coordination`
field even when it is explicitly null.

## Native command experience

`URLSessionCommandAPIClient` owns only the command/device credential and reads
the existing authoritative endpoints. `CommandFeatureModel` polls with `GET`
and never authors incident state. The native command view displays:

- incident kind, state, `state_version`, exact command-authorized location, and
  last server update;
- qualified candidates, persisted invitations, nearest available AED, and
  accepted responder status from the redacted coordination contract;
- the ordered append-only incident timeline;
- the exact fixed protocol title, disclaimer, version, emergency kind,
  SHA-256, source links, and backend-ordered steps.

`POST /dispatch` remains an explicit command. It is not used as polling. The
operator can start or retry discovery when an escalating incident has no
pending/accepted invitation; all background refreshes use `GET`.

Before rendering protocol content, the model requires the incident,
coordination, accepted responder, presentation responder, and emergency kind to
describe the same assignment. An older incident version marks the view stale
instead of silently retaining a `LIVE` label.

The command view does not decode the accepted responder's route. After
acceptance it honestly reports that a static route is assigned and remains
responder-only.

## Native responder experience

The responder graph consists of strict contracts, a responder-token-only
client, durable decision store, polling model, and SwiftUI/MapKit view.

Before acceptance it presents only:

- incident kind and authoritative state version;
- the responder's role and skills;
- coarse proximity;
- accept and decline actions.

A decision envelope is persisted before transport. Retries reuse the exact
decision ID, invitation ID, decision, and timestamp until the backend
acknowledges it. No credential or accepted location is stored in that retry
record.

After server-confirmed acceptance, the same responder credential retrieves:

- exact wearer location and recorded accuracy;
- the assigned AED coordinate and access instructions;
- the immutable two-leg `static_venue` route;
- the identical persisted fixed protocol presentation.

The MapKit route is labeled `STATIC VENUE ROUTE`, `NOT LIVE NAVIGATION`, and
`NO LIVE ETA`. Persisted instructions, protocol steps, source names/links,
version, and SHA-256 are displayed without generation, summarization, or
reordering. Authorization loss, assignment removal, or resolution removes
accepted-only data from the responder state.

## One-app persona composition

The iOS composition root accepts these launch modes:

- default / `--live-api`: community/wearer graph;
- `--persona-responder`: responder graph;
- `--persona-command`: command graph.

Each mode builds a new credential-scoped client and model. Invalid command or
responder configuration fails closed in the native app and does not substitute
fixtures or another persona.

This is a hackathon access-profile seam, not a production identity system. The
backend still uses one scope device credential for wearer/command calls and
seeded one-way-hashed responder credentials. Account creation, sign-in,
switching, attestation, rotation, revocation UX, and secure provisioning remain
future work.

## Verification

Focused evidence for this slice:

```text
Backend fast suite:              88 passed, 35 deselected
Focused PostgreSQL responder:    passed
Swift package:                   76 tests in 13 suites passed
Generic iOS Simulator app build: succeeded
```

The Swift checks focus on strict contract decoding, responder-only headers,
durable exact decision retry, accepted-only disclosure, authorization loss,
and protocol ordering. The product path remains the priority; no broad UI test
matrix was added.

## Known limitations and next handoff

- Notification registration/delivery is not implemented. Responder mode
  currently receives its incident/responder identity through the configured
  hackathon launch profile rather than a notification or universal link.
- There is no active-incident list endpoint. Command mode requires a retained or
  deep-linked incident ID.
- Full production persona accounts do not exist yet.
- The command view deliberately omits health snapshot values until those reads
  have an incident-scoped authenticated boundary.
- The stored accepted route is not yet revoked by incident resolution at the
  repository read boundary. The responder UI removes it when the redacted
  incident projection reports resolution; server-side assignment revocation
  should land before close/handoff is added.
- WebSocket/SSE, live routing, external delivery, Apple fall entitlement, and
  physical-device proof remain separate slices.

The next product slice is an allowlisted, idempotent notification provider that
delivers an incident-bound responder entry into this existing native flow.
