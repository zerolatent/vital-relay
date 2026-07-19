# Contract status

The scalar transport and immutable scalar-context contracts are frozen:

- `HealthMetric` v1
- `HealthMetricBatch` v1
- `HealthMetricBatchResult` v1
- `HealthCapability` v1
- `HealthCapabilityBatch` v1
- `HealthCapabilityBatchResult` v1
- `HealthSnapshotCreateRequest` v1
- `HealthSnapshot` v1
- `HealthSnapshotView` v1

The first real safety-event and deterministic-incident contracts are also frozen:

- `GeoLocation` v1
- `WearableEventRequest` v1
- `WearableEventResult` v1
- `IncidentView` v1
- `IncidentTransition` v1
- `CheckInRequest` v1
- `CheckInResult` v1
- `IncidentTimelineEntry` v1
- `IncidentTimeline` v1
- `IncidentResolutionRequest` v1
- `IncidentResolutionReceipt` v1

The PostGIS responder-discovery and accepted-dispatch contracts are frozen:

- `Coordinate` v1
- `ResponderCandidateView` v1
- `AEDSiteView` v1
- `ResponderInvitationView` v1
- `ResponderIncidentView` v1
- `DispatchCoordinationView` v1
- `ResponderDecisionRequest` v1
- `ResponderDecisionResult` v1
- `ResponderDecisionReceiptView` v1
- `StaticRouteLeg` v1
- `StaticRoutePlan` v1
- `AcceptedDispatchView` v1

The fixed first-aid protocol-presentation contracts are frozen:

- `FirstAidProtocolSource` v1
- `FirstAidProtocolStep` v1
- `FixedFirstAidProtocol` v1
- `ProtocolPresentationView` v1

The allowlisted responder-notification contracts are frozen:

- `PushRegistrationRequest` v1
- `PushRegistrationView` v1
- `ResponderInvitationNotificationPayload` v1
- `NotificationReceiptView` v1

The authenticated persona-session and active-incident discovery contracts are
frozen:

- `PersonaAccountView` v1
- `PersonaSessionCreateRequest` v1
- `PersonaSessionReceipt` v1
- `PersonaSessionView` v1
- `PersonaSessionRotateRequest` v1
- `PersonaSessionRotationReceipt` v1
- `PersonaSessionRevocationReceipt` v1
- `ActiveIncidentSummary` v1
- `ActiveIncidentList` v1

Only real `apple_fall` and deliberate `manual_sos` sources are accepted by this
slice. They require bounded wearer coordinates and `simulated: false`. Apple fall
requests additionally prove runtime availability, entitlement presence, authorized
status, and an exact match between the callback's `fall_date` and `observed_at`.
The server assigns receipt time, incident ID, snapshot ID, and verification expiry.

Incident creation captures a health snapshot with the `incident_created` reason,
but health values remain context only. The pure transition policy accepts only
typed fall, SOS, check-in, timeout, responder, cancellation, close, and handoff
triggers; it has no health-data input.

`IncidentResolutionRequest` authorizes only `close` or `handoff` from
`response_active`; it contains a stable idempotency identity but no client-owned
transition time. The server receipt contains the resolved incident and exact
authorized transition. Assignment revocation is an internal privacy/audit effect
of the same transaction rather than a client-selectable field.

Responder discovery starts only from a real incident in `escalating`. The
command coordination contract contains ranked eligible responders, coarse
distance bands, durable invitations, and public AED data. It has no wearer
location field, so an exact location cannot accidentally appear as a nullable
or empty pre-acceptance value. `ResponderIncidentView` is an independently
authenticated projection containing the current incident kind, state/version,
and only the requesting responder's invitation. It contains no user identifier,
wearer location, health snapshot, AED, route, protocol, or other responder. A
decline can still advance the internal command workflow to the next invitation,
but that `ResponderDecisionResult` coordination is never serialized to the
responder. The responder HTTP boundary maps every decision to a
`ResponderDecisionReceiptView`, whose schema cannot represent command
coordination or another responder profile. A decline receipt contains null
transition and accepted dispatch fields. An acceptance receipt contains the
authorized transition plus exactly one `AcceptedDispatchView`, advances the
incident to `response_active`, and contains the accepted responder's exact wearer
`GeoLocation` plus a persisted two-leg static walking route from responder to AED
to wearer. Dispatch contracts contain neither health values nor simulated
records.

Fixed protocol selection accepts exactly one input: the persisted
`IncidentKind`. `fall` maps to `fall-response` `1.0.0`; `manual_sos` maps to
`manual-sos-response` `1.0.0`. Protocols expose fixed contiguous steps, an
emergency-guidance disclaimer, source organization/title/HTTPS links, and the
SHA-256 digest of the exact packaged JSON bytes. The raw packaged JSON does not
self-declare that digest; expected values live in an append-only ID/version
catalog separate from both the content and active `IncidentKind` mapping. This
preserves exact loading of stored versions after a future active mapping changes.

`ProtocolPresentationView` binds the complete protocol snapshot to an exact
incident, accepted assignment, and responder with an authoritative presentation
time. It contains no health values, diagnosis fields, responder-observation
input, prompt/model metadata, or generated advice. Missing, modified, unknown,
or identity-mismatched content fails closed rather than producing a presentation.
An accepted assignment without its required presentation is an integrity error,
not an ordinary missing presentation.

Push registration accepts a normalized APNs device token as a write-only secret;
the environment and topic remain server-owned. Neither registration responses
nor receipts can represent the raw token. The notification payload carries only
schema/kind plus incident and invitation UUIDs and grants no authority. Receipt
error codes come from a closed privacy-safe set, and a provider-accepted receipt
requires its UUID to equal the logical notification UUID. `provider_accepted`
means APNs accepted a correlated request, not that a device received, displayed,
or opened it.

Persona selection is now a server-authorized identity boundary, not a client
label. An operator-issued enrollment bootstrap creates an opaque, role-bound
session tied to one installation and remains reusable until the operator
rotates it. The server persists only SHA-256 secret hashes. A short-lived access
secret authenticates product APIs, while a
separate refresh secret can replace only that access secret and can revoke the
session idempotently. Neither `PersonaSessionView` nor logout rediscloses either
secret, and account contracts enforce exactly one subject shape: community has
only `user_id`, responder has only `responder_id`, and command has neither.

Active-incident discovery returns privacy-minimal locators, not full incident
data. Community discovery is owner-scoped, responder discovery contains only
the caller's pending invitation or accepted assignment, and command discovery
contains the active scope-wide set. Invitation identity/status is forbidden in
community and command lists. Exact location, health context, route, protocol,
user identity, and other responder details remain absent; selecting a locator
still requires the corresponding existing API to reauthorize its own complete
projection. Resolved incidents are intentionally excluded from discovery and
remain available only through the existing command audit paths.

This is **not** completion of worktree Gate W0. Typed structured health records,
tools, scenarios, and evolution results still need reviewed contracts. Protocol
and notification contracts are frozen through Slice 08, incident resolution is
frozen through Slice 09, and persona sessions/discovery are frozen through Slice
10. Future
evaluator/sandbox enforcement that makes protocol content read-only to mutation
candidates remains unimplemented.

Scalar metrics intentionally cannot represent raw ECG voltage or raw
high-frequency motion streams. Capability/no-visible-sample states now have
their own typed contracts. Structured records will also use separate explicit
schemas instead of being encoded as fake numeric metrics or arbitrary JSON.
