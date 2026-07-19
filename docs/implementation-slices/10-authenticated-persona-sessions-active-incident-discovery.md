# Slice 10: Authenticated Persona Sessions and Active-Incident Discovery

## Outcome

Slice 10 replaces launch-time persona labels and manually supplied incident IDs
with a real authenticated entry flow inside the single native Vital Relay app:

```text
operator-issued enrollment secret
└── one role-bound account and installation
    └── opaque access + refresh session
        ├── restore or rotate
        ├── role-scoped active-incident discovery
        │   └── select one privacy-minimal locator
        │       └── compose the existing community, responder, or command graph
        └── logout / switch persona
            ├── tear down the current graph and exact responder state
            ├── revoke the server session
            └── delete the local session credentials
```

The app still has three distinct capability graphs. A persona is now derived
from an authenticated server account rather than a Swift launch flag, and every
selected product API reauthorizes the session principal and resource ownership.
Discovery is a locator mechanism, not an authorization grant.

The backend package and API version are `0.9.0`. `/healthz` reports
`persona_sessions` when PostgreSQL, the active demo scope, fixed protocols, and
the session system are ready.

## Exact product boundary

Slice 10 implements:

- durable community, responder, and command accounts bound to one demo scope;
- exchange of an operator-issued enrollment bootstrap secret for an opaque,
  installation-bound access/refresh session; the bootstrap remains reusable
  until the operator rotates it;
- server-stored SHA-256 hashes rather than plaintext enrollment, access, or
  refresh secrets;
- 15-minute access credentials and 24-hour refresh credentials bounded by the
  active demo scope's lifetime;
- current-session lookup, access rotation, and idempotent logout;
- role- and ownership-scoped authentication on the existing product APIs;
- separate active-incident discovery endpoints for community, responder, and
  command;
- device-only Keychain persistence of exactly one secret persona session plus a
  separately stored stable, non-secret installation identity;
- native restore, enrollment, discovery, incident selection, logout, and
  persona-switch behavior; and
- immediate feature-graph and responder exact-data teardown before logout waits
  for network completion.

It does not implement public self-sign-up, email/password login, account
recovery, multi-scope membership, long-lived production identity, remote device
administration, background refresh, or a historical incident inbox. It does not
put health data, exact wearer location, routes, protocols, or user identity into
the discovery response.

## Frozen account and session contracts

Every account has exactly one persona and one valid subject shape:

| Persona | `user_id` | `responder_id` |
|---|---|---|
| Community | Required | `null` |
| Responder | `null` | Required |
| Command | `null` | `null` |

The account display name is presentation metadata. It does not select the role
or resource scope. The database account row and authenticated session principal
do that.

### Enrollment

```text
POST /v1/persona-sessions
X-Vital-Relay-Enrollment-Token: <operator-issued secret>
Content-Type: application/json
```

The body contains exactly:

```json
{
  "schema_version": 1,
  "installation_id": "50505050-5050-4050-8050-505050505050"
}
```

HTTP `201` returns one `PersonaSessionReceipt`: account/session metadata, a
short-lived access token, and a separate refresh token. These are the only
plaintext copies the server returns. Enrollment locks the account and
installation, revokes a prior active session for that same pair, and creates a
new session atomically.

The enrollment bootstrap is revealed once per operator provisioning/rotation,
but it is not consumed by a successful exchange. Reusing it for the same
account/installation replaces that installation's active session; rotating the
account bootstrap invalidates future exchanges with the prior value.

The native app never persists the enrollment secret. It verifies that the
receipt's authenticated persona is the one the user selected before saving the
session or composing a feature graph.

### Current session

```text
GET /v1/persona-sessions/current
Authorization: Bearer <access token>
```

The response is `PersonaSessionView`, which includes the account, installation,
issue/rotation times, and expirations but neither credential. A valid token is
resolved against the durable session, account, scope, and role on every read.

### Access rotation

```text
POST /v1/persona-sessions/{session_id}/rotation
X-Vital-Relay-Refresh-Token: <refresh token>
Content-Type: application/json
```

The exact body repeats the schema and installation identity. A successful
rotation replaces only the access-token hash and returns the new plaintext
access token plus server-owned rotation/expiry times. The refresh token remains
stable, is never echoed, and cannot be used for product reads.

Rotation has no client idempotency key because replaying a stored result would
require retaining or redisclosing an earlier plaintext access secret. The
repository serializes concurrent calls on the session row. Both callers may
receive a successful rotation receipt, but only the last-issued access token
remains valid; no second session or refresh credential is created. The native
session actor prevents concurrent rotation during normal app operation.

### Logout

```text
DELETE /v1/persona-sessions/{session_id}
X-Vital-Relay-Refresh-Token: <refresh token>
```

Logout is idempotent for that authenticated session. The receipt status is
`revoked` or `already_revoked` and includes one server-owned `revoked_at`.
Expired, revoked, cross-session, and invalid credentials cannot become a valid
principal.

## Role-scoped active-incident discovery

Discovery uses the access token as a standard bearer credential:

```text
GET /v1/community/incidents/active
GET /v1/responders/me/incidents/active
GET /v1/command/incidents/active
Authorization: Bearer <access token>
```

The authenticated persona must match the requested endpoint. A valid session
for a different role returns `403 persona_not_authorized`; it is never silently
reinterpreted as the requested persona.

| Endpoint | Authorized set | Invitation fields |
|---|---|---|
| Community | Active incidents owned by the account's `user_id` | Always `null` |
| Responder | The responder's pending invitation or unrevoked accepted assignment | Required for every row |
| Command | All active incidents in the authenticated demo scope | Always `null` |

Each summary contains only incident ID, kind, active state, state version,
updated time, and—only for responder discovery—the caller's invitation ID and
`pending`/`accepted` status. Pending invitations correspond to `escalating`;
accepted invitations correspond to `response_active`. Resolved, cancelled, and
otherwise terminal incidents are not active discovery results.

The response is ordered deterministically and server-timed. It cannot represent
wearer coordinates, health context, AEDs, route legs, fixed first-aid content,
account subjects, another responder, notification credentials, or internal
audit records.

## Selected-graph authorization

The discovery row supplies identifiers only. Selecting it composes one of the
already implemented feature graphs, whose existing API boundary performs a new
authorization check:

| Selected graph | Native identity input | Existing product header | Required principal |
|---|---|---|---|
| Community | Account `user_id`, installation ID, incident ID | `X-Vital-Relay-Device-Token` | Owning community session |
| Responder | Account `responder_id`, incident ID, invitation ID | `X-Vital-Relay-Responder-Token` | Matching responder session |
| Command | Incident ID | `X-Vital-Relay-Device-Token` | Command session |

The value transported in those existing headers is the session access token,
not the enrollment or refresh secret. The community/device, responder, and
command clients remain separate types; no union client conditionally attaches
both credential headers.

Resource checks use privacy-preserving outcomes. A valid wrong-role principal
receives `403 persona_not_authorized`. Community cross-owner incident access and
responder cross-assignment/exact-data access use `404` so resource existence is
not disclosed. Invalid, expired, or revoked session access returns `401`.

## Durable security boundary

Alembic revision `0007_persona_sessions` adds:

| Database change | Purpose |
|---|---|
| `persona_accounts` | One active role and exact subject shape with a hashed enrollment credential |
| `persona_sessions` | Installation binding, separate hashed access/refresh credentials, expirations, and revocation state |
| Active-account/subject indexes | Prevent duplicate community/responder identities inside one scope |
| Active-session index | Prevent concurrent active sessions for the same account/installation |
| Responder discovery index | Resolve only the caller's pending/accepted invitation set efficiently |

Session operations lock the rows whose credential or installation state they
replace. The server checks active account status, active session status, token
hash, installation binding, scope status, scope expiry, and credential expiry
inside the durable boundary. Product mode has no raw configured-token bypass;
the old command/device and responder seed secrets are enrollment bootstrap
material only. A narrowly explicit legacy-auth composition exists solely to
keep pre-Slice-10 tests exercising earlier behavior and cannot be enabled by a
production environment setting. Product startup rejects a configured command
bootstrap outside the same 43–256-character URL-safe token contract enforced by
the enrollment endpoint.

## Native session lifecycle

The default app launch is no longer a fixture or command-line persona:

1. Load the stable installation UUID and the single Keychain session.
2. If no session exists, show enrollment for server URL, expected persona, and
   secure enrollment code.
3. If an access token is current, validate it through the current-session API.
4. If access expired but refresh remains usable, rotate access and retry the
   current-session read once.
5. Fetch the authenticated role's active-incident list.
6. Show a privacy-minimal empty, retry, or selectable discovery state.
7. Compose an incident-bound graph only after an authorized row is selected.
   A community account with no row may instead enter its empty monitoring graph;
   creating a real SOS there still requires the next slice's live location.

If a selected graph later receives `401`, it does not hot-swap a credential
inside that graph. It first tears the graph down (including responder exact
state), validates or rotates the session, reloads role-scoped discovery, and
then requires an authorized selection to compose a fresh typed client. This
keeps credential refresh from weakening the community/responder/command client
separation.

Exactly one role-tagged session is stored with device-only Keychain
accessibility. A transient network failure or temporarily locked Keychain does
not erase otherwise valid credentials. Tokens are never printed or copied into
view diagnostics.

Logout and persona switching reverse the composition in safety-first order:

1. discard the selected graph immediately;
2. clear any responder exact wearer location, route, protocol, and pending
   decision state;
3. delete local Keychain authority before any network suspension point; and
4. attempt best-effort server revocation with the old refresh credential.

Deleting first prevents Swift actor reentrancy from erasing a newly enrolled
session if revocation is slow. If the server is unreachable, the still-active
remote session remains bounded by its access/refresh expiration deadlines.

Notification payloads and deep links remain untrusted locators. They can select
only an incident/invitation already authorized to the active matching responder
session; they cannot enroll a persona, switch accounts, restore an expired
credential, or bypass discovery.

## Verification

Testing for this hackathon slice is intentionally focused on credential
separation, role/ownership enforcement, durable lifecycle operations, frozen
contract parity, and native state teardown. Physical-device APNs delivery is a
separate external gate and is not evidence for session correctness.

```text
Persona contract/domain focus:       32 passed
Fast non-PostgreSQL backend suite:   176 passed, 43 deselected
Real PostgreSQL suite:                43 passed
Swift package:                        98 tests in 17 suites passed
Generic iOS Simulator app build:     succeeded with code signing disabled
Python compile / diff whitespace:    passed
Physical Keychain and APNs proof:    not run; signing/device inputs absent
```

The PostgreSQL checks include real session lifecycle and concurrent rotation,
persona/ownership denial, discovery filtering, and regression coverage across
the previously protected product surfaces. Native tests cover strict Codable
parity, endpoint/header separation, secure-store lifecycle, discovery behavior,
and selected-graph teardown. Coverage is deliberately focused rather than
exhaustive for the hackathon.

## Known limitations and next handoff

- Enrollment is operator provisioned and its bootstrap remains reusable until
  the operator rotates it; there is no consumer sign-up or recovery workflow.
- One installation keeps exactly one persona session at a time. Concurrent
  multi-persona tabs are intentionally unsupported.
- Active discovery is a current locator list, not history or search. Resolved
  audit remains in the existing command paths after an incident is selected.
- The 15-minute/24-hour session lifetimes suit the hackathon and do not replace
  a production identity provider, attestation, policy administration, or remote
  device revocation system.
- Signed physical-device APNs display/open remains unverified without Apple
  provisioning, a provider key, a device, and a reachable HTTPS environment.
- Logout does not yet unregister an existing APNs installation. A signed-out
  device may still receive a privacy-minimal locator until the registration is
  explicitly replaced/revoked or expires, but it cannot open protected data
  without a valid responder session.
- The community graph can monitor an authorized existing incident, but creating
  a new real manual SOS still fails closed until the next slice supplies live
  Core Location. HealthKit and the entitled Apple fall callback also remain
  unimplemented.

The next product slice can now use a real community session to collect live
HealthKit context and ingest a genuine entitled Apple fall callback without
launch flags or a global product credential. NemoClaw isolation with Docker
fallback remains the parallel platform slice; neither runner receives persona
credentials or emergency-response authority.
