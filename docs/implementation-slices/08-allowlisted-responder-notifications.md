# Slice 08 — Allowlisted Responder Notifications

## Outcome

NOTIFY-01 turns a persisted responder invitation into one privacy-minimal APNs
alert and routes a tap into the existing authenticated native responder graph:

```text
invitation transaction
└── one durable notification intent
    └── leased outbox worker
        └── APNs HTTP/2 request
            └── incident + invitation pointer
                └── Keychain responder profile
                    └── authenticated responder API read
```

The notification is an entry pointer, never authorization. It contains no
responder credential, device token, health value, wearer or responder
coordinate, AED, route, protocol, or ETA. Exact incident data remains behind
the responder-token boundaries completed in Slices 05–07.

The backend package and API version are `0.7.0`. `/healthz` reports
`allowlisted_responder_notifications` when the application's configured
dependencies are healthy.

## Exact product boundary

Slice 08 implements:

- responder-token-authenticated APNs device registration and revocation;
- a server-owned responder UUID allowlist, APNs environment, and bundle topic;
- encrypted device-token persistence with no token echo in API responses;
- one deterministic logical notification record for each invitation while the
  APNs composition is enabled;
- atomic invitation and outbox creation in the same PostgreSQL transaction;
- a leased PostgreSQL worker safe for concurrent application instances;
- a real HTTP/2 APNs adapter using ES256 provider-token authentication;
- bounded, append-only delivery attempts and responder-authenticated receipts;
- a strict, non-authorizing APNs payload and deep-link contract;
- native permission, APNs registration, Keychain access-profile restoration,
  and routing into the existing responder experience;
- binding of a notification's invitation ID to the invitation returned by the
  authenticated responder API.

It does not claim that an APNs-accepted request reached or displayed on a
device. It does not implement production responder enrollment, background
location, critical alerts, SMS fallback, broad fan-out, a command notification
dashboard, or a universal-link deployment.

## Trust and privacy boundary

Three identities remain deliberately separate:

| Value | Purpose | Authority |
|---|---|---|
| responder token | Authenticate one configured responder | Server-verified bearer credential restored from the iOS Keychain |
| APNs device token | Select one APNs installation | Write-only delivery secret; never responder authorization |
| incident + invitation IDs | Bind the notification to one invitation | Opaque pointer only; insufficient to read incident data |

The registration and receipt endpoints authenticate the responder token first,
then require that responder to be in the explicit server allowlist. The client
cannot select the APNs environment, topic, or bundle ID. A registration is also
ineligible after the responder record is rotated or updated until that
responder authenticates and registers again.

The APNs custom object is exactly:

```json
{
  "schema_version": 1,
  "kind": "responder_invitation",
  "incident_id": "66666666-6666-4666-8666-666666666666",
  "invitation_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
}
```

It is stored beneath the top-level `vital_relay` key beside Apple's `aps`
dictionary. The alert copy is generic. The payload omits even the responder ID
and notification ID, and both backend and native decoders reject unknown custom
fields.

APNs device tokens are normalized lowercase hex, accepted as secret values,
encrypted with Fernet before PostgreSQL persistence, and represented by a
SHA-256 digest only for uniqueness checks. The raw token is decrypted only
while constructing one provider request. API responses, receipts, provider
results, error bodies, and object descriptions do not expose it.
Notification validation failures return one bounded error code instead of
FastAPI's input-echoing default. HTTPX's APNs request URL is narrowly redacted
at the logging boundary, while unrelated HTTP client logs remain enabled.
Provider/receipt error codes come from a closed enum, and provider message IDs
must be UUIDs equal to the logical notification ID in Python and PostgreSQL.

## HTTP surface

| Endpoint | Authentication | Result |
|---|---|---|
| `PUT /v1/responders/{responder_id}/push-registrations/{installation_id}` | `X-Vital-Relay-Responder-Token` | Create, rotate, reactivate, or idempotently confirm one APNs registration |
| `DELETE /v1/responders/{responder_id}/push-registrations/{installation_id}` | `X-Vital-Relay-Responder-Token` | Idempotently revoke the responder's existing installation |
| `GET /v1/responders/{responder_id}/invitations/{invitation_id}/notification` | `X-Vital-Relay-Responder-Token` | Read only that responder's truthful logical delivery receipt |

The registration request contains only `schema_version`, `platform: "apns"`,
and `device_token`. Its response contains registration, installation, responder,
platform, server-owned environment, lifecycle status, and update time, but no
raw token or token fingerprint.

A missing or invalid responder token returns `401`; an authenticated responder
outside the notification allowlist returns `403`; an absent scoped resource
returns `404`; and unavailable persistence or disabled notification composition
returns `503`. A malformed path or registration body returns the bounded
`422 invalid_notification_request` response without echoing request input. The
receipt never grants access to the incident itself.

## Durable PostgreSQL boundary

Alembic revision `0005_responder_notifications` adds:

| Table | Purpose |
|---|---|
| `responder_push_registrations` | Encrypted, consented APNs destination and registration lifecycle |
| `notification_outbox` | One durable logical notification projection per invitation |
| `notification_delivery_attempts` | Append-only audit of each provider submission outcome |

Only one active registration is allowed per responder in this demo. The same
installation cannot be reassigned to another responder, and one active APNs
destination cannot be shared across responders in the same scope and
environment. Database checks reject simulated rows, invalid lifecycle states,
malformed token digests, inconsistent terminal metadata, and notification
payloads outside the frozen v1 shape.

When the APNs composition is enabled, the dispatch repository calls the
notification enqueuer immediately after creating a responder invitation and
before committing the existing dispatch transaction. The notification ID is
deterministically derived from scope and invitation identity; database
uniqueness permits one `apns` /
`responder_invitation_v1` intent per invitation. An exact dispatch retry cannot
create another logical alert.

Workers claim due rows with row locking and `SKIP LOCKED`, increment the attempt
number, and assign a short lease before leaving the transaction for the network
call. Before a destination can be leased, the repository rechecks that:

1. the invitation is still pending;
2. the incident is still escalating;
3. the responder is explicitly notification-allowlisted;
4. the responder is active and has a current active registration;
5. the registration environment matches the server configuration;
6. the registration was authorized no earlier than the current responder
   record; and
7. the encrypted token can be decrypted and revalidated.

Failure of one of these checks finalizes the intent as `unavailable` without
calling APNs. Provider failure never rolls back or changes the incident state.

## Truthful APNs states and retry behavior

The public receipt uses only these states:

| State | Meaning | Retry |
|---|---|---|
| `pending` | Durable intent exists or an explicitly safe retry is scheduled | Worker may claim when due |
| `provider_accepted` | APNs returned `200` with the exact correlated `apns-id` | Terminal; not a delivery receipt |
| `permanent_failed` | APNs explicitly rejected the request or destination | Terminal; invalid destinations are revoked |
| `unavailable` | Local authorization, incident, registration, or token prerequisites failed before send | Terminal |
| `unknown` | The request may have reached APNs but no trustworthy result exists | Terminal; never resent |

The provider sends over APNs HTTP/2 with:

- an ES256 JWT made from the configured Apple Team ID, Key ID, and `.p8` key;
- `apns-topic` from server configuration;
- `apns-push-type: alert`, `apns-priority: 10`, and `apns-expiration: 0`;
- the stable logical notification UUID as `apns-id`; and
- the invitation UUID as `apns-collapse-id`.

An expired provider token is refreshed and retried once only after APNs
explicitly rejects the first request. Explicit connection establishment errors,
HTTP `429`, HTTP `5xx`, and APNs `IdleTimeout` remain `pending` with bounded
backoff. Server-delay failures wait 900 seconds; other safe retries use
exponential backoff capped at 900 seconds.

A read/write timeout, generic transport ambiguity, unexpected provider
exception, malformed success response, or expired worker lease becomes terminal
`unknown`. This deliberately prefers a visible unknown outcome to a duplicate
emergency alert. A `410`/`Unregistered` or other explicit client rejection is
terminal `permanent_failed`; invalid destinations also revoke the active
registration.

This is an exactly-once **logical intent**, not exactly-once physical delivery.
APNs does not provide ordinary-alert displayed/read receipts. The implementation
therefore never exposes `sent` or `delivered` and never translates
`provider_accepted` into either term.

## Native iOS entry flow

The app uses `UIApplicationDelegateAdaptor` and
`UNUserNotificationCenterDelegate` to complete this sequence:

1. A responder launch profile is configured once with
   `--persona-responder`. The API base URL, responder ID, responder token, and
   account metadata are stored in a Keychain generic-password item using
   `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`.
2. Only when that profile exists does the app ask for alert/badge/sound
   permission and call `registerForRemoteNotifications()`.
3. The current APNs device token is forwarded through the responder-token-only
   registration client. It is not cached by the app. A stable installation UUID
   is stored separately in the same device-bound Keychain service.
4. A cold launch or notification tap strictly decodes the `vital_relay` custom
   object. The push supplies only incident and invitation IDs.
5. The router restores the Keychain profile and builds a fresh responder-only
   API/model graph. If no profile exists, the app fails closed with responder
   access unavailable and makes no incident request.
6. `ResponderFeatureConfiguration.expectedInvitationID` requires the
   authenticated API's returned invitation to match the pushed invitation
   before rendering it.

The local handoff parser also supports this strict custom scheme:

```text
vitalrelay://responder/incidents/{incident_id}/invitations/{invitation_id}
```

It rejects credentials, ports, query strings, fragments, encoded path
components, malformed UUIDs, unexpected hosts, and extra path segments. An
HTTPS form is parsed only for exact runtime-allowlisted hosts, but production
universal-link association is not part of this slice.

The Xcode target now carries the `aps-environment` entitlement, using
`development` for Debug and `production` for Release, and registers the
`vitalrelay` URL scheme. Notification registration failures appear as a bounded
in-app message and do not substitute a fixture or another persona.

## Exact configuration

Apply the migration and create an explicit retained demo scope if one does not
already exist, then seed its response network before enabling delivery:

```bash
export VITAL_RELAY_DATABASE_URL='postgresql+psycopg://postgres@localhost/vital_relay'
export VITAL_RELAY_DEMO_SCOPE_ID='11111111-1111-4111-8111-111111111111'
export VITAL_RELAY_DEVICE_TOKEN='<long-random-demo-device-token>'

make migrate

.venv/bin/vital-relay-db create-scope \
  --scope "$VITAL_RELAY_DEMO_SCOPE_ID" \
  --retention-hours 24

.venv/bin/vital-relay-db seed-response-network \
  --scope "$VITAL_RELAY_DEMO_SCOPE_ID" \
  --confirm "$VITAL_RELAY_DEMO_SCOPE_ID"
```

Retain the one-time printed token for the seeded responder that will own the
test installation. Configure every server-owned value below. When
`VITAL_RELAY_APNS_ENABLED=true`, startup fails if any required value is absent,
the allowlist is empty or malformed, the signing key is unreadable/invalid, or
PostgreSQL is not configured.

```bash
export VITAL_RELAY_APNS_ENABLED=true
export VITAL_RELAY_APNS_ENVIRONMENT=sandbox
export VITAL_RELAY_APNS_TEAM_ID='<10-character-Apple-Team-ID>'
export VITAL_RELAY_APNS_KEY_ID='<10-character-Apple-Key-ID>'
export VITAL_RELAY_APNS_TOPIC='com.vitalrelay.app'
export VITAL_RELAY_APNS_PRIVATE_KEY_PATH='/absolute/path/to/AuthKey_<KEY_ID>.p8'
export VITAL_RELAY_NOTIFICATION_RESPONDER_ALLOWLIST='<seeded-responder-UUID>'
export VITAL_RELAY_NOTIFICATION_TOKEN_ENCRYPTION_KEY='<stable-Fernet-key>'
export VITAL_RELAY_NOTIFICATION_POLL_SECONDS='1.0'
export VITAL_RELAY_APNS_TIMEOUT_SECONDS='10.0'

make dev
```

Generate the Fernet key once, store it in the server secret manager, and keep it
stable across restarts:

```bash
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Do not commit the Fernet key or Apple `.p8` file. `sandbox` must be paired with
a development-signed app/device token; `production` must be paired with a
production entitlement and corresponding token. The APNs topic must exactly
match the signed app bundle ID.

For the native responder profile, set these Xcode scheme environment values and
launch once with `--persona-responder`:

```text
VITAL_RELAY_API_BASE_URL=https://<device-reachable-api-host>
VITAL_RELAY_INCIDENT_ID=<existing-demo-incident-UUID>
VITAL_RELAY_RESPONDER_ID=<allowlisted-seeded-responder-UUID>
VITAL_RELAY_RESPONDER_TOKEN=<one-time-seeded-responder-token>
VITAL_RELAY_ACCOUNT_NAME=Local demo profile
```

In the `VitalRelay` target, choose the Apple Developer Team, use a unique App ID
with Push Notifications enabled, and regenerate/select a matching development
provisioning profile. Keep the signed bundle ID and
`VITAL_RELAY_APNS_TOPIC` identical. Use the Debug/development entitlement with
the server's `sandbox` environment for this gate; do not mix a sandbox token
with the production gateway.

A physical device cannot reach backend loopback. Its API URL must be a
device-reachable HTTPS endpoint. The app permits cleartext HTTP only for the
local loopback development seam.

Apple's relevant setup references are:

- [Registering your app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns)
- [Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)
- [Setting up a remote notification server](https://developer.apple.com/documentation/usernotifications/setting-up-a-remote-notification-server)

## Verification

The implementation test matrix covers strict Python and Swift contracts,
allowlist/authentication failures, encrypted registration rotation/revocation,
transactional invitation enqueue, concurrent lease behavior, append-only
attempts, every APNs outcome class, ES256 JWT construction, privacy-minimal
payloads, device-token log/validation redaction, closed receipt metadata,
strict deep links, responder-only registration headers, and pushed invitation
identity binding.

```text
Backend fast suite:                  141 passed, 38 deselected
Full real-PostgreSQL suite:           38 passed
Focused PostgreSQL notification:       3 passed
APNs provider unit suite:              13 passed
Swift package:                         89 tests in 15 suites passed
Generic iOS Simulator app build:       succeeded with code signing disabled
```

The APNs adapter tests use a controlled HTTP transport to prove request shape,
JWT signing, stable identifiers, correlation, and failure mapping. They do not
prove that Apple's production service accepted an actual request or that a
device displayed an alert.

## Physical-device and provider evidence gate

The code path is complete, but real delivery remains externally gated. This
workspace contains no Apple Developer Team provisioning, push-enabled signed
profile, APNs `.p8` credential, deployed HTTPS backend, registered physical
device, or Apple response evidence. None is fabricated or checked in.

To close the gate, run one allowlisted physical-device demonstration and retain
all of the following privacy-safe evidence:

1. the signed app obtains a current token and the authenticated `PUT`
   registration returns `active` in the matching environment;
2. dispatch creates one real invitation and one outbox row in the same commit;
3. the worker receives APNs `200` with the exact stable `apns-id`, producing a
   `provider_accepted` receipt;
4. the physical device visibly receives the generic alert;
5. tapping it opens the same incident/invitation in the authenticated responder
   graph; and
6. exact wearer location, AED route, and protocol remain unavailable until the
   responder accepts through the existing server boundary.

Record provider acceptance and device observation as separate facts. A screen
recording of the device is evidence of that demonstration, not a new backend
`delivered` receipt.

## Known limitations and next handoff

- Only one active APNs installation per responder is supported in the demo.
- The iOS app does not yet expose account enrollment/logout or automatically
  call the revocation endpoint during a production sign-out flow.
- HTTPS handoffs have a strict host parser, but Associated Domains entitlement,
  `apple-app-site-association`, and a real link domain are not deployed.
- No SMS fallback, notification preference center, quiet-hours policy, critical
  alert entitlement, command delivery dashboard, or device-open acknowledgment
  exists.
- Notification failure is isolated from incident coordination; operators still
  need a future escalation/fallback policy when a responder is unreachable.

The next product handoff should close the physical APNs evidence gate, then add
an authenticated active-incident inbox or command delivery view before broader
fan-out or production account work.
