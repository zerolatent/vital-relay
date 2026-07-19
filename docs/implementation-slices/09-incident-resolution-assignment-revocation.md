# Slice 09: Incident Resolution and Assignment Revocation

## Outcome

Slice 09 gives the native command persona two real ways to finish an active
response and makes that same transaction end the responder's exact-data access:

```text
confirmed native close / handoff
└── authenticated idempotent resolution request
    └── one PostgreSQL transaction
        ├── response_active → resolved incident update
        ├── immutable state transition
        ├── ordered timeline entry
        ├── append-only assignment revocation
        └── append-only exact-retry receipt
            ├── command incident/timeline/protocol audit remains readable
            └── responder route/location/protocol reads return 404
```

`close` records that the Vital Relay response ended. `handoff` records that
responsibility moved outside Vital Relay. Both actions have the same bounded
system effect: resolve the incident and revoke the accepted responder's access
to the exact wearer location, assigned route, and fixed protocol presentation.
They do not claim that an external service accepted a handoff or that a
responder physically arrived.

The backend package and API version are `0.8.0`. `/healthz` reports
`incident_resolution` when the configured dependencies are healthy.

## Exact product boundary

Slice 09 implements:

- one device-token-authenticated command endpoint for `close` and `handoff`;
- strict Python, JSON Schema, example, and Swift request/receipt contracts;
- a server-owned resolution time and exact idempotency identity;
- one atomic `response_active` to `resolved` transition with the incident,
  transition, timeline, assignment revocation, and receipt committed together;
- an append-only revocation record linked to the exact assignment, resolution,
  and transition;
- command-authorized incident, timeline, and immutable protocol audit reads
  after resolution;
- privacy-preserving denial of responder exact-dispatch and responder protocol
  reads after assignment revocation;
- native command confirmation controls for both resolution actions; and
- native responder revalidation and immediate teardown of cached exact data
  when resolution or revoked access is observed.

It does not implement responder arrival, transfer to another Vital Relay
responder, reopening, rollback, notes, external handoff acknowledgment,
production identity, or active-incident discovery. It does not turn a
notification, client clock, persona label, or cached native projection into
state-machine authority.

## Frozen HTTP contract

```text
POST /v1/incidents/{incident_id}/resolution
X-Vital-Relay-Device-Token: <configured device credential>
Content-Type: application/json
```

The request contains exactly three fields:

```json
{
  "schema_version": 1,
  "resolution_id": "99999999-9999-4999-8999-999999999999",
  "action": "close"
}
```

`action` is exactly `close` or `handoff`. The client does not send a timestamp,
device identifier, assignment identifier, incident state, responder identity,
or free-text reason. The authenticated server scope and path select the
incident; the server supplies the authoritative receipt time.

An accepted request returns HTTP `200` with exactly:

```json
{
  "schema_version": 1,
  "resolution_id": "99999999-9999-4999-8999-999999999999",
  "status": "accepted",
  "action": "close",
  "incident": { "...": "IncidentView" },
  "transition": { "...": "IncidentTransition" },
  "server_received_at": "2026-07-18T14:34:10Z"
}
```

The nested incident is `resolved`. The transition is for the same incident,
has sequence equal to the resulting `state_version`, and is exactly
`response_active` to `resolved` with trigger equal to the requested action.
`incident.resolved_at`, `incident.updated_at`, `transition.occurred_at`, and
`server_received_at` are the same server-owned instant. Neither the request nor
the public transition adds a mutable command note or responder-supplied field.

## Idempotency and conflicts

The first valid resolution ID/action/incident tuple returns `accepted`. An
exact retry returns HTTP `200`, `status: "already_processed"`, and the original
accepted incident/transition snapshot and server receipt time. It does not
advance the incident, append another timeline entry, or revoke the assignment
again.

The database stores a request fingerprint and the exact accepted receipt. A
reused resolution ID with different content or a different incident fails with
HTTP `409` and `resolution_id_conflict`. A new resolution ID after another
request won the transition fails with HTTP `409` and
`incident_transition_not_allowed`; it reports the authoritative current state
instead of pretending the new command succeeded.

Malformed requests use the existing bounded validation response. A missing or
invalid device token returns `401`. No resolution path silently falls back to
in-memory state.

## Atomic PostgreSQL boundary

Alembic revision `0006_incident_resolution` adds:

| Database change | Purpose |
|---|---|
| `incident_resolution_receipts` | Append-only request fingerprint and exact accepted receipt for retry/conflict handling |
| `incident_state_transitions.resolution_id` | Bind a `close` or `handoff` transition to its resolution authority without changing the public transition contract |
| `responder_assignment_revocations` | Append-only end of exact responder access, linked to one assignment, incident, resolution, and transition |

The repository serializes on the incident and accepts resolution only while it
is `response_active` with its accepted assignment. One transaction uses one
server timestamp to:

1. record the resolution identity, fingerprint, and exact accepted receipt;
2. move the incident to `resolved` and increment its state version;
3. append the `close` or `handoff` transition;
4. append the existing `state_transitioned` timeline event;
5. append the assignment revocation.

Any failure rolls back the whole unit. Database foreign keys, uniqueness, state
policy checks, and append-only protections prevent a receipt without its
transition, a revocation for a different assignment, more than one revocation
for the incident/assignment/resolution, or a mutated audit row.

The slice deliberately reuses `IncidentTransition` and
`TimelineEventType.STATE_TRANSITIONED`. It does not create a second public
resolution event shape or leak internal revocation identifiers into clients.

## Access after resolution

Revocation changes authorization, not history:

| Reader | After resolution |
|---|---|
| Command/device incident read | Returns the authoritative resolved incident |
| Command/device timeline read | Retains the ordered resolution transition and prior audit |
| Command/device coordination read | Not available after resolution; the frozen dispatch view is active-state-only |
| Command/device protocol read | Retains the exact immutable protocol presentation for audit |
| Responder coarse incident/invitation read | May return the privacy-redacted resolved projection for that authenticated responder |
| Responder accepted-dispatch read | Returns privacy-preserving `404` |
| Responder protocol read | Returns privacy-preserving `404` |

Responder authentication still happens before scoped lookup. An invalid or
cross-responder credential remains `401`; the server does not reveal whether a
different responder or revoked assignment exists. Revocation does not delete
the accepted assignment, route, protocol snapshot, or audit rows because the
command persona needs the incident record after the live response ends.

## Native behavior

The command graph exposes `Close incident` and `Record handoff` only for an
authoritative `response_active` incident. Each action presents consequence copy
before transport, creates one resolution UUID, disables competing actions while
the request is in flight, and reuses the pending UUID for a retry of that same
action. The client accepts only a strict receipt that proves the matching
incident, action, state transition, sequence, and server timestamp.

After acceptance the command graph immediately adopts the resolved incident and
removes live coordination state. Subsequent command reads can reconstruct the
durable incident/timeline/protocol audit without requiring a resolved
`DispatchCoordinationView`.

The responder graph does not trust one stale `response_active` projection. It
loads accepted dispatch and protocol data, then re-reads the redacted responder
incident before committing exact data to the UI. If that authoritative recheck
is resolved, it commits the resolved projection without restoring route,
coordinates, or protocol. If an already-active graph receives a `404` or `410`
from an exact read, it immediately clears the accepted-only state and re-reads
the redacted responder incident so the resolved projection can replace it in
the same reconciliation. A `401` also wipes exact data and fails closed because
the credential can no longer authorize even the redacted read.

## Verification

Testing remains focused on the transaction, idempotency race, authorization
boundary, strict contracts, and native teardown behavior:

```text
Resolution JSON Schema/Python examples: passed
Backend fast suite:                  143 passed, 41 deselected
Full real-PostgreSQL suite:          41 passed
Swift package:                       93 tests in 16 suites passed
Generic iOS Simulator app build:     succeeded with code signing disabled
Physical APNs device delivery:       not run; Apple signing/device inputs absent
```

The physical APNs proof remains an external gate. Slice 09 neither needs nor
claims Apple provisioning, provider acceptance, device display, or notification
open evidence.

## Known limitations and next handoff

- The command and responder live profiles still require configured incident
  IDs; there is no authenticated active-incident inbox.
- Seeded device/responder credentials remain hackathon access profiles, not
  account enrollment, session rotation, persona switching, or production RBAC.
- `handoff` records a bounded state-machine outcome only; it does not identify
  or notify an external recipient.
- Resolution is terminal in v1. There is no reopen, reassignment, or correction
  workflow.
- Signed physical-device APNs delivery is still unverified because the required
  Apple team, key, provisioning, device, and reachable HTTPS environment are
  external to this workspace.

The next code slice is authenticated persona/session enrollment plus
role-scoped active-incident discovery. That removes manual incident-ID launch
configuration while keeping community, responder, and command clients inside
one native app. Apple live health/fall work and NemoClaw sandboxing with Docker
fallback can proceed in parallel because neither changes this resolution or
revocation authority boundary.
