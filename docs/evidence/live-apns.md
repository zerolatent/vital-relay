# Live APNs provider-acceptance evidence

This lane captures one real Vital Relay product path:

```text
authenticated responder session -> durable push registration
  -> atomic responder invitation/outbox -> leased worker
  -> APNs HTTP/2 provider -> persisted attempt and receipt
```

There is no replay, stub, simulation, dry-run, or fixture mode in the CLI. A
missing or invalid external prerequisite fails nonzero. Unit fixtures only test
the harness's orchestration and privacy rules and are not live evidence. Their
controlled-adapter capability is always marked `test_only` and unsigned; the
live artifact writer rejects test-only or unsigned input.

## Claim boundary

A successful artifact proves that Apple Push Notification service accepted the
request and returned the exact correlated `apns-id`, and that Vital Relay
persisted that result in its unsimulated durable attempt and receipt rows.

It does **not** prove that an Apple device received, displayed, or opened the
notification. The artifact sets `signed_device_display_open_verified` to
`false` and `apple_evidence_correlation_required` to `true`. Final end-to-end
completeness requires correlation with the signed-device Apple evidence lane.
The harness attestation authenticates the APNs-lane artifact described here; it
does not expand that claim to device receipt, display, or open.

## Required isolated setup

Use a dedicated, non-production evidence scope that is not being served by an
application notification worker or modified by another operator. The harness
fails closed unless all of these conditions hold before registration:

- the configured PostgreSQL/PostGIS database is reachable, migrated, and has an
  active unexpired `VITAL_RELAY_DEMO_SCOPE_ID`;
- the entire scope has zero responder push registrations, zero responder
  invitations, zero notification outbox rows, and zero notification attempts;
- the configured incident already exists in `escalating` state;
- the confirmed responder is the incident's first eligible candidate under the
  configured radius/freshness bounds, with current availability, qualification,
  and location already established by the normal product setup;
- `VITAL_RELAY_LIVE_APNS_SESSION_ACCESS_TOKEN` is an active, unexpired responder
  persona access token in that scope;
- that session is bound to the exact installation passed to
  `--confirm-installation-id`, its responder is the exact recipient passed to
  `--confirm-responder-id`, and the responder is in
  `VITAL_RELAY_NOTIFICATION_RESPONDER_ALLOWLIST`;
- `VITAL_RELAY_LIVE_APNS_DEVICE_TOKEN` is a current token from that confirmed
  non-production physical installation;
- APNs is explicitly enabled and configured for `sandbox`, with the matching
  topic, Apple team/key identifiers, an absolute readable P-256 `.p8` key path,
  token-encryption
  Fernet key, and outbound APNs connectivity;
- the exact certifi CA-bundle SHA-256 matches an expected digest reviewed and
  distributed out of band;
- a dedicated live-evidence HMAC key and its public opaque issuer/key identity
  are configured as described below;
- the process has no proxy or custom-CA transport override described below; and
- the absolute output directory already exists, is writable, and is outside the
  repository.

Production APNs is unconditionally rejected. Never reuse a scope after a run,
including a rejected, unknown, or transient provider outcome: the durable rows
are intentionally retained as truthful audit state. A transient result remains
pending under the product retry policy, but this one-shot harness does not make
a second provider submission.

## Configuration

Use the application's normal server configuration for PostgreSQL and APNs:

- `VITAL_RELAY_DATABASE_URL`
- `VITAL_RELAY_DEMO_SCOPE_ID`
- `VITAL_RELAY_APNS_ENABLED=true`
- `VITAL_RELAY_APNS_ENVIRONMENT=sandbox`
- `VITAL_RELAY_APNS_TEAM_ID`
- `VITAL_RELAY_APNS_KEY_ID`
- `VITAL_RELAY_APNS_TOPIC`
- `VITAL_RELAY_APNS_PRIVATE_KEY_PATH` (absolute path)
- `VITAL_RELAY_APNS_TIMEOUT_SECONDS` (optional; defaults to `10`)
- `VITAL_RELAY_NOTIFICATION_RESPONDER_ALLOWLIST`
- `VITAL_RELAY_NOTIFICATION_TOKEN_ENCRYPTION_KEY`
- `VITAL_RELAY_RESPONDER_RADIUS_M` (optional; defaults to `1000`)
- `VITAL_RELAY_RESPONDER_STALE_SECONDS` (optional; defaults to `120`)
- `VITAL_RELAY_LIVE_APNS_EXPECTED_CERTIFI_SHA256`
- `VITAL_RELAY_LIVE_APNS_ATTESTATION_ISSUER`
- `VITAL_RELAY_LIVE_APNS_ATTESTATION_KEY_ID`

`VITAL_RELAY_LIVE_APNS_EXPECTED_CERTIFI_SHA256` must be the exact lowercase
SHA-256 of the reviewed certifi PEM bytes for the intended runtime. Obtain and
approve it independently of this evidence invocation. A digest calculated from
the bundle by the same invocation is an observed runtime hash, not an
out-of-band expected value, and cannot satisfy this trust pin by itself.

The attestation issuer and key ID are public signed metadata. Use stable opaque
service/release identifiers, not a person's name, email address, host name,
tenant name, secret-manager path, or other operational detail. Both values are
strict clean bounded ASCII inputs: they are not silently trimmed or normalized.

Supply the three live secrets through the operator's secret manager or process
environment, never as command arguments or committed files:

- `VITAL_RELAY_LIVE_APNS_SESSION_ACCESS_TOKEN`
- `VITAL_RELAY_LIVE_APNS_DEVICE_TOKEN`
- `VITAL_RELAY_LIVE_APNS_ATTESTATION_HMAC_KEY`

The attestation key is exactly 32 random bytes encoded as 43-character
unpadded canonical base64url. Do not reuse an APNs signing key, Fernet key,
session credential, application key, or another evidence lane's key.

### Direct network and trust boundary

The harness rejects the **presence**, including a blank value, of every proxy
or custom-CA variable below before it can create a registration or provider:

- `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `http_proxy`, `https_proxy`, and
  `all_proxy`;
- `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE`;
  and
- lowercase spellings of those custom-CA names.

`NO_PROXY` and `no_proxy` do not make a configured proxy acceptable. If either
is present alone it is inert: the explicit transport does not read environment
transport configuration. If a proxy variable is also present, the proxy still
causes `environment_transport_override_forbidden` even when `NO_PROXY` names
Apple's APNs host.

The provider receives an explicit `httpx.HTTPTransport` configured with
`trust_env=False`, `proxy=None`, HTTP/1 disabled, HTTP/2 enabled, and `retries=0`.
It requires hostname and certificate verification with TLS 1.2 or newer. The
TLS context loads the exact in-memory certifi/Mozilla CA bundle bytes. Before
PostgreSQL is opened, an APNs provider is created, or any durable registration
is written, the harness hashes those exact bytes and requires an exact match to
`VITAL_RELAY_LIVE_APNS_EXPECTED_CERTIFI_SHA256`. A missing, malformed, or
mismatched expected digest fails nonzero. The expected value is never derived
from the runtime bundle as a fallback.

The configuration digest binds both the reviewed expected digest and observed
matching bundle digest, certifi version, HTTPX and HTTP Core versions, Python
OpenSSL/TLS backend identity, HTTP/2 stack versions, cryptography, SQLAlchemy,
psycopg, Pydantic, and GeoAlchemy versions, hostname/certificate policy, HTTP
protocol selection, connection limits, retry policy, and proxy/environment
policy. The executed dependency/source manifest is bound as well. System or
environment-selected CA stores are not used.

The installed project exposes the module entry point directly:

```sh
python -m vital_relay.adapters.apns_live_evidence \
  --incident-id "$NONPROD_INCIDENT_ID" \
  --confirm-installation-id "$NONPROD_INSTALLATION_ID" \
  --confirm-responder-id "$NONPROD_RESPONDER_ID" \
  --output-dir /absolute/operator-controlled/live-apns-evidence \
  --confirm-non-production
```

The three UUID arguments are confirmations and routing inputs, not evidence
output. Review them against the non-production installation and intended test
recipient before invoking the command.

### Programmatic safety boundary

The concrete live runner revalidates the core safety configuration even when a
configuration object was constructed programmatically instead of by the CLI
parser. Production APNs, unconfirmed or mismatched responder/installation
identity, unsafe output placement, an invalid allowlist or timing bound, and an
unreviewed network/trust setting fail before the database or APNs send path can
start. Direct construction cannot bypass those checks.

The concrete live runner constructs the real PostgreSQL repositories, leased
worker, direct HTTP/2 provider, durable evidence reader, and system clock
internally. It has no injectable clock or controlled provider/repository seam.
Dependency injection remains confined to the separate unsigned `test_only`
orchestration capability used by focused unit tests.

Concrete orchestration, evidence construction, HMAC authentication,
self-verification, and the live content-addressed write are sealed into one
runner path. That path accepts no caller-supplied observation, candidate,
payload, encoded envelope, clock, or authority token. Controlled observations
therefore have no live attestation or live-writer interface; their only payload
builder fixes the mode to `test_only`, the claim to `none`, and authentication
to false.

## Success and failure behavior

Exit status `0` means all of the following were reread from PostgreSQL and
validated:

- one active unsimulated registration was newly persisted;
- two coordination calls produced the same single invitation and single atomic
  outbox row;
- one leased worker pass made exactly one real logical APNs provider call with
  zero transport retries (the provider may refresh an explicitly rejected
  expired JWT and make its single bounded follow-up POST);
- one unsimulated attempt recorded `provider_accepted`, attempt number `1`, and
  an APNs message identifier exactly equal to the logical notification ID;
- the durable receipt is terminal `provider_accepted`; and
- a second worker pass processed zero rows and did not create another attempt.

The harness also exhaustively checks the in-memory provider request against the
authenticated principal and newly persisted path: scope, responder,
installation, registration, incident, invitation, notification, attempt
number, sandbox environment, and minimal invitation-payload identities must
agree. It then checks the bounded provider outcome and correlated APNs ID
against the exact unsimulated attempt/outbox/receipt rows, row counts, states,
and timestamps read back from PostgreSQL. These comparisons happen in memory;
the device token, request destination, provider authorization, raw notification
payload, and raw provider body are never copied into evidence.

Exit status `3` means a real provider attempt was durably recorded but APNs did
not produce acceptance. The content-addressed artifact truthfully records only
the bounded outcome/error and durable state; it never upgrades that result to
success.

Exit status `2` means configuration, session, PostgreSQL, local APNs credentials,
network/trust isolation, scope isolation, orchestration, artifact storage, or
another prerequisite failed. CLI errors contain only a bounded `error_code`;
arbitrary database and HTTP exception text is suppressed because it can contain
sensitive endpoints. No pre-provider fixture or fabricated acceptance artifact
is emitted.

## Artifact authentication, integrity, and privacy

The privacy-checked evidence claims are wrapped in a domain-separated,
canonical HMAC-SHA256 authenticated envelope. The signed material binds the
versioned Vital Relay live-APNs provider-acceptance domain, algorithm, public
issuer, public key ID, evidence digest, and complete evidence claims. The HMAC
is computed over a fixed binary domain prefix followed by the canonical compact
UTF-8 JSON bytes. Verification reconstructs the same canonical material and
uses a constant-time signature comparison; a changed claim, issuer, key ID,
domain, algorithm, or evidence digest fails verification.

The final signed envelope, not an unsigned intermediate payload, is canonical
compact UTF-8 JSON. Its exact byte SHA-256 is the content address:

`<output-dir>/<first two digest characters>/<remaining digest characters>.json`

An existing object at that address must have identical bytes or the run fails.
The live writer verifies the authenticated envelope immediately before its
atomic content-addressed write. It rejects a missing signature, a failed
signature, `test_only=true`, any non-live capability, and noncanonical input.
Controlled adapters and fixture observations therefore cannot be promoted into
live evidence by calling the writer.

The authenticated artifact binds:

- domain-separated SHA-256 hashes of scope, incident, responder, installation,
  registration, invitation, notification, and correlated APNs identifiers;
- attempt number and bounded provider outcome/error;
- final durable invitation rank/status/responded state, receipt status,
  finalization, row counts, and unsimulated state;
- invitation/outbox replay and terminal worker duplicate-suppression results;
- a source-bundle hash and embedded manifest covering this harness, every
  directly executed service and adapter, notification/dispatch/incident/session
  domain schemas, persistence database/models, canonical hashing, and validated
  fixed-protocol registry/content sources;
- a canonical non-secret configuration hash that also commits the adapter hash
  and the complete direct-network/reviewed-trust and attestation metadata
  described above; and
- UTC registration, invitation, outbox, provider, receipt, and run timing.

It never serializes the `.p8` key or path, provider JWT, device token or token
fingerprint, session/access/refresh credential, database URL, APNs destination
URL, raw notification/provider payload, provider response body, HMAC key or key
fingerprint, raw UUID, health data, coordinates, or hidden reasoning. Privacy
validation runs before signing and again over the final envelope. Keep captured
artifacts in the operator-controlled directory; do not copy or commit them into
this repository.

HMAC uses a symmetric secret. Anyone who can verify with the shared key can
also forge an envelope, so this attestation is not public non-repudiation. Use a
dedicated, access-controlled APNs-lane key, distribute it only to the concrete
issuer and authorized verifier, and rotate it under a new opaque key ID. The
HMAC authenticates Vital Relay evidence provenance and detects modification; it
does not authenticate Apple, a device, or a notification display/open event.
