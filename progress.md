# Vital Relay Implementation Progress

**Last updated:** 2026-07-19

**Current milestone:** Wave 2 native sensing and adaptive-agent foundations

**Milestone status:** All six independently reviewed Wave 2 lanes are merged:
real Apple fall ingestion, live Watch health, ACE merge/store,
Reflector/Curator, immutable evolution candidate bundles, and bounded Generator
context. Root composition now binds those features to authenticated native
sessions, the real agent runtime, and regenerated protected/source-lock
artifacts.

**Next milestones:** Run one same-budget ACE improvement round and implement the
transactional promotion/rollback bridge. In parallel, collect signed-device
Apple evidence and real Docker/NemoClaw, model, Mapbox, and APNs evidence.

**Repository note:** The trusted-offline-host boundary was accepted before the
expanded scenario catalog entered `main`. Wave 2 merge commits are `d80bacb`
(ACE merge/store), `b716f2b` (live Watch health), `d6d103b`
(Reflector/Curator), `dc477be` (Generator context), `3b9ee8d` (candidate
bundles), and `9190554` (real Apple fall).

---

## 1. Current status at a glance

| Area | Status | Result |
|---|---|---|
| Final implementation plan | Complete | 48-hour architecture, phases, safety gates, stretch goals, and health-data scope documented |
| Git worktree plan | Complete | Eight isolated work lanes with ownership, dependencies, merge order, and handoff rules |
| Python backend foundation | Complete for Slices 01–10 | Vital Relay API 0.9.0 on FastAPI, SQLAlchemy, Alembic, psycopg, GeoAlchemy2, PostGIS, HTTPX/HTTP2, cryptography, and Python 3.14 |
| Scalar health contracts | Complete for v1 | Metric, batch, and ingestion-result JSON Schemas plus fixtures |
| Scalar health ingestion | Complete | Validated, atomic, idempotent batch endpoint |
| In-memory repositories | Complete | Fast thread-safe adapters retained for unit tests and fixture development |
| Snapshot query seam | Complete and consumed | Both metrics and capabilities support deterministic `latest_by_type(user_id, as_of)` lookup |
| Health capability contracts and ingestion | Complete for v1 | Honest support/access/no-sample state with atomic idempotent batch ingestion |
| Immutable health snapshots | Complete for v1 | Server-timestamped, retry-safe snapshots with deterministic freshness |
| Redacted snapshot API | Complete for v1 | Create, exact retry, and read endpoints expose only the incident-safe view |
| PostgreSQL migrations | Complete through Agent A3 | Scoped health/incident/dispatch/protocol/notification schema plus resolution, persona/session authority, agent runs, policy activation, tool audits, and idempotency through revision 0008 |
| PostgreSQL repository adapters | Complete | Durable parity for metric, capability, and immutable snapshot ports |
| Transactional snapshot capture | Complete | Serialized snapshot ID plus one repeatable-read transaction |
| Scoped retention and holds | Complete | Serialized holds/reset, protected-row counts, and DB-enforced preservation |
| Database CLI and configuration | Complete | Safe migration precedence, factory startup, lifecycle cleanup, PostGIS readiness, scope lifecycle, and explicit response-network seed |
| Real wearable event contracts | Complete for v1 | Authenticated, non-simulated Apple fall and first-party manual SOS envelopes with bounded location |
| PostgreSQL incident persistence | Complete | Atomic event/check-in/deadline flows plus Slice 09 resolution, transition, timeline, exact receipt, and assignment revocation |
| Verification state machine | Complete | Explicit check-in and durable restart-safe timeout transitions |
| Typed structured health records | Not started | Deferred until a demo need and representative HealthKit fixture justify each schema |
| Apple/HealthKit client | Slices 11B and 11C plus live Watch telemetry complete in source | Authenticated stored HealthKit sync, real workout/live quantity collection, bounded pedometer data, genuine `CMFallDetectionManager` callback handling, durable Watch transfer, and authenticated iPhone ingestion are composed; entitlement approval and signed-device proof remain external |
| Responder/PostGIS/durable dispatch | Live-provider capable | Fresh qualified responder ranking, nearest AED, durable invitations/assignment, accepted-only exact location, and a persisted live Mapbox-compatible route or truthfully labelled static fallback |
| Fixed first-aid protocol presentation | Complete for Slice 06 | Two source-visible v1.0.0 protocols, kind-only mapping, independently pinned digests, atomic snapshots, and bounded reads |
| Command-center/responder live views | Complete through Slice 10 | One native app restores authenticated persona authority, discovers only role-scoped active incidents, composes isolated graphs, and tears down responder exact data before rotation/logout |
| Allowlisted responder notifications | Provider-capable for Slice 08 | Responder-authenticated registration, encrypted tokens, atomic invitation outbox, APNs HTTP/2 adapter, bounded receipts, Keychain profile, and native incident/invitation handoff; physical display is not yet claimed |
| Incident resolution/assignment revocation | Complete for Slice 09 | Idempotent command close/handoff atomically resolves `response_active`, appends transition/timeline/receipt/revocation, preserves command audit, and denies responder exact reads |
| Authenticated persona sessions/discovery | Complete for Slice 10 | Hashed-only operator enrollment, installation-bound access/refresh sessions, secure native restore/rotation/revocation, and privacy-minimal community/responder/command discovery |
| Agent and sandbox | Agent A3.1 plus Wave 2 context wiring integrated | Real Deep Agent worker protocol plus explicit NemoClaw/Docker selection, fixed Docker gateways, verified bounded Generator context, startup validation, cleanup custody/evidence, durable leases/runs/audits/idempotency, and no cross-sandbox fallback; no live sandbox/model evidence claimed |
| ACE adaptation | Wave 2 foundations complete | Deterministic merge/store, redacted Reflector and typed Curator, mandatory bounded Generator context, real local model boundary, and reviewed baseline selection are composed; the same-budget improvement loop and activation bridge remain |
| AlphaEvolve-style laboratory | Candidate construction and evidence foundations complete | Offline evaluation, 12 development/6 protected/4 final cases, recursively verified immutable candidate bundles, partition-bound attestations, exact policy/improver mutation replay, archive abstractions, and inherited lineage exist under the accepted trusted-offline-host boundary; no autonomous activation is claimed |

“Complete” in this file means complete for the stated slice, not completion of the broader WT-00 foundation gate or the full proposal.

---

## 2. Step-by-step implementation log

### Step 1 — Consolidate the implementation plan

**Status:** Complete  
**Purpose:** Turn the proposal into an executable 48-hour plan before creating application code.

**Implemented**

- Defined the modular-monolith architecture.
- Split operational, improvement, and platform critical paths.
- Added deterministic fallbacks for Apple fall events, sandboxing, PostGIS, routing, notification, coordination, and protocol content.
- Defined the incident state machine, ports, APIs, database model, safety boundary, phase gates, testing strategy, risks, and proposal traceability.
- Included the requested stretch goals: real Apple fall ingestion, NemoClaw with Docker fallback, PostGIS/live routing, immutable first-aid protocol presentation, and inherited DGM-style mutation behavior.

**Health-data expansion**

- Expanded the plan beyond heart rate to all visible incident-relevant scalar, activity, mobility, respiratory, sleep, fall-history, and user-initiated data supported by the device and user authorization.
- Separated live data, recent context, and user-initiated records.
- Established that optional health values cannot create, suppress, or advance escalation.
- Established that raw ECG voltage and continuous raw motion streams remain off the backend.

**Files**

- `proposal/implementation-plan.md`
- `proposal/proposal-final.md`

**Handoff created**

The plan supplied the contracts and repository boundaries used to select the first implementation slice.

### Step 2 — Break the plan into independent Git worktree lanes

**Status:** Complete  
**Purpose:** Allow contributors to work independently without repeatedly conflicting in shared schemas, root configuration, or database migrations.

**Implemented**

- Defined WT-00 through WT-70:
  - foundation and contracts;
  - Apple health/fall;
  - backend health/incidents;
  - response services;
  - native persona experience;
  - agent/sandbox;
  - evolution/DGM;
  - integration/demo QA.
- Assigned exclusive paths, prohibited edits, independent test boundaries, and completion criteria to every lane.
- Defined a contract-freeze gate, integration branch, feature migration heads, merge revision ownership, execution waves, merge order, and a handoff template.

**File**

- `proposal/worktree-plan.md`

**Handoff created**

At this planning step no feature worktrees had been created. The repository has
since merged the operational product, agent-policy/tool-proxy foundation, and
offline evolution-laboratory foundation through `e966915`, followed by Slice
11B real HealthKit ingestion at `773a0a5`.

### Step 3 — Select the smallest useful vertical slice

**Status:** Complete  
**Purpose:** Start with a runnable seam that both the future Apple client and the next backend feature can depend on.

**Selected outcome**

> Accept a single-user batch of visible scalar health observations, validate a frozen cross-platform contract, store every immutable metric ID at most once, and return a stable result without importing or invoking incident escalation behavior.

**Scope decision**

- This slice is part of WT-00 plus the smallest usable seam from WT-20.
- It is not the whole WT-00 contract gate.
- Scalar observations are implemented first.
- Capability/no-visible-sample states are deferred to Slice 02; typed structured records remain evidence-driven follow-up work.

**File**

- `docs/implementation-slices/01-health-metric-ingestion.md`

**Handoff created**

The next slice receives a stable batch contract and a repository query that can select the latest observation of each metric type.

### Step 4 — Establish the Python backend foundation

**Status:** Complete for Slice 01  
**Purpose:** Replace the empty Python scaffold with an installable, runnable backend package.

**Implemented**

- Confirmed Python 3.14.5 is locally available and compatible with the selected dependencies.
- Configured Python `>=3.14,<3.15`.
- Added FastAPI, Pydantic, and Uvicorn runtime dependencies.
- Added pytest, HTTPX, and JSON Schema development dependencies.
- Configured setuptools for the `backend/src` package layout.
- Added pytest configuration and the `vital-relay` command entry point.
- Added `make install`, `make test`, and `make dev` targets.
- Added ignored paths for virtual environments, pytest/coverage output, generated candidates, and package metadata.

**Files**

- `pyproject.toml`
- `.python-version`
- `.gitignore`
- `Makefile`
- `main.py`
- `backend/src/vital_relay/__init__.py`
- `backend/src/vital_relay/main.py`

**Runtime behavior (advanced with Slice 03)**

- `GET /healthz` returns:

```json
{
  "status": "ok",
  "slice": "postgres_health_persistence"
}
```

**Handoff created**

Every following backend slice can add a router, application service, and adapter without replacing the application composition pattern.

### Step 5 — Freeze the scalar health transport contracts

**Status:** Complete for v1  
**Purpose:** Give Swift, Python, and future TypeScript clients an exact payload shape.

**Implemented contracts**

1. `HealthMetric` v1
2. `HealthMetricBatch` v1
3. `HealthMetricBatchResult` v1

**`HealthMetric` guarantees**

- `schema_version` is exactly `1`.
- `metric_id` is a stable UUID.
- `user_id`, canonical `metric_type`, and acquisition class are required.
- Values must be finite scalar numbers with a non-empty canonical unit.
- Observation timestamps must include a timezone and are normalized to UTC.
- Machine-readable `source` and `source_kind` are required.
- Display source, bundle, and device metadata remain nullable but explicit.
- Replay sources require `simulated: true`.
- `used_for_escalation` can only be `false`.
- Unknown fields are rejected.
- Raw ECG voltage, accelerometer, gyroscope, and magnetometer metric types are rejected.

**`HealthMetricBatch` guarantees**

- One schema version, batch UUID, user, device, and send time.
- Contains 1–100 metrics.
- Metric IDs must be unique inside the request.
- Every metric user must match the batch user.

**`HealthMetricBatchResult` guarantees**

- Reports `accepted` or `already_processed`.
- Separates accepted and duplicate IDs.
- Counts must match their corresponding ID arrays.
- Accepted and duplicate ID sets must be disjoint.
- Includes an authoritative `server_received_at` value.

**Files**

- `contracts/json-schema/health-metric.schema.json`
- `contracts/json-schema/health-metric-batch.schema.json`
- `contracts/json-schema/health-metric-batch-result.schema.json`
- `contracts/README.md`
- `backend/src/vital_relay/domain/health.py`

**Handoff created**

The Apple client can implement Codable equivalents without depending on database, incident, or snapshot implementation details.

### Step 6 — Add representative cross-platform fixtures

**Status:** Complete  
**Purpose:** Make contract discussions and client implementations concrete and testable.

**Implemented fixtures**

- Live Apple workout heart rate.
- Recent HealthKit respiratory rate.
- Recent oxygen saturation.
- Replayed pedometer step count.
- A mixed-source batch containing live, recent, Apple, and replayed observations.

**Files**

- `contracts/examples/health-metric.live-heart-rate.json`
- `contracts/examples/health-metric.recent-respiratory-rate.json`
- `contracts/examples/health-metric.recent-oxygen-saturation.json`
- `contracts/examples/health-metric.replay-step-count.json`
- `contracts/examples/health-metric-batch.json`

**Handoff created**

Apple and web work can use these exact files as local fixtures before a backend is running.

### Step 7 — Define the ingestion application boundary

**Status:** Complete  
**Purpose:** Keep HTTP and storage choices outside the health ingestion use case.

**Implemented**

- `HealthIngestionService` delegates one complete batch to a repository.
- `Clock` makes authoritative receipt time testable.
- `HealthMetricRepository` defines:

```text
ingest_batch(batch, server_received_at) -> HealthMetricBatchResult
latest_by_type(user_id, as_of) -> dict[metric_type, HealthMetric]
```

- `IdempotencyConflictError` carries stable `batch_id_conflict` or `metric_id_conflict` codes.

**File**

- `backend/src/vital_relay/application/health_ingestion.py`

**Handoff created**

Slice 02 can build snapshots through `latest_by_type` without knowing whether the underlying adapter is in-memory or PostgreSQL.

### Step 8 — Implement atomic, idempotent temporary storage

**Status:** Complete for local development  
**Purpose:** Make the complete behavior runnable before introducing database setup and migrations.

**Implemented**

- Thread-safe in-memory repository guarded by a re-entrant lock.
- Canonical SHA-256 fingerprints for batches and metrics.
- Exact retry detection by batch ID and normalized content.
- Immutable metric IDs across different batches.
- Whole-batch preflight before any write, preventing partial inserts on conflict.
- Duplicate counting for a new batch that contains already stored metrics.
- Stable original server receipt time for exact retries.
- Latest visible scalar selection by metric type and `as_of` timestamp.

**File**

- `backend/src/vital_relay/adapters/in_memory_health.py`

**Known limitation at Slice 01 completion — resolved in Slice 03**

The initial adapter lost data on process restart. Slice 03 now supplies a
durable PostgreSQL implementation of the same port while retaining this adapter
for fast tests.

**Handoff created**

The future PostgreSQL adapter implements the same repository port and preserves all tested behavior.

### Step 9 — Expose the health ingestion HTTP endpoint

**Status:** Complete  
**Purpose:** Provide the exact backend boundary the Apple transport will call.

**Endpoint**

`POST /v1/health/metrics:batch`

| Condition | HTTP | Behavior |
|---|---:|---|
| New batch | 201 | Stores unseen metrics and returns `accepted` |
| Exact batch retry | 200 | Returns `already_processed`; stores nothing again |
| New batch with identical stored metrics | 201 | Reports those IDs as duplicates |
| Same batch ID with different content | 409 | Returns `batch_id_conflict` |
| Same metric ID with different content | 409 | Returns `metric_id_conflict`; writes none of the batch |
| Invalid request | 422 | Pydantic/FastAPI validation; writes nothing |

**Implemented**

- Dependency injection through FastAPI application state.
- Dynamic 201/200 idempotency response.
- Stable 409 conflict details.
- OpenAPI documentation for 200, 201, 409, and 422 outcomes.
- No public stored-health listing/debug endpoint.

**Files**

- `backend/src/vital_relay/api/health.py`
- `backend/src/vital_relay/main.py`

**Handoff created**

WatchConnectivity/iPhone transport can submit batches now; the snapshot endpoint can be added as a separate router operation next.

### Step 10 — Add automated verification

**Status:** Complete  
**Purpose:** Freeze behavior before additional contributors or worktrees build on it.

**Contract tests**

- Validate every scalar fixture against JSON Schema and Pydantic.
- Resolve and validate the referenced batch schema.
- Check every schema as valid JSON Schema Draft 2020-12.

**Domain tests**

- Frozen model behavior.
- Escalation prohibition.
- Replay/simulation invariant.
- Raw high-frequency type rejection.
- Finite-number enforcement.
- Timezone requirement and UTC normalization.
- Single-user and unique-ID batch enforcement.
- 100-metric batch limit.

**Repository tests**

- Latest-by-type selection at two `as_of` timestamps.
- Sixteen concurrent exact submissions produce one accepted batch and fifteen idempotent replays.

**API tests**

- First ingestion and exact retry.
- Duplicate metrics under a new batch.
- Batch-ID and metric-ID conflicts.
- Atomic rejection of invalid/cross-user input.
- Response contract validation.
- Health check.
- OpenAPI response documentation.

**Files**

- `backend/tests/conftest.py`
- `backend/tests/contract/test_health_contract.py`
- `backend/tests/unit/test_health_metric.py`
- `backend/tests/unit/test_health_repository.py`
- `backend/tests/integration/test_health_api.py`

**Verification result**

```text
23 passed in 0.82s
```

Dependency verification also reports no broken requirements.

### Step 11 — Smoke-test the real HTTP process

**Status:** Complete  
**Purpose:** Verify behavior outside FastAPI's in-process test transport.

**Performed**

- Started Uvicorn on localhost.
- Submitted `contracts/examples/health-metric-batch.json`.
- Repeated the identical request.
- Stopped the server cleanly.

**Observed**

```text
First request:  HTTP 201, status=accepted, accepted_count=3
Exact retry:    HTTP 200, status=already_processed, accepted_count=3
```

The retry returned the original `server_received_at`, confirming that no second ingestion occurred.

### Step 12 — Align documentation and the implementation plan

**Status:** Complete  
**Purpose:** Prevent the code, proposal, and worktree instructions from describing different contracts.

**Implemented**

- Added the Slice 01 scope, endpoint semantics, safety boundary, exclusions, acceptance checklist, and Slice 02 connection.
- Updated the implementation plan to distinguish visible scalar observations from capability/no-visible-sample states.
- Changed structured health data from an arbitrary component dictionary to future typed record schemas.
- Added current/next slice metadata to the implementation plan.
- Updated the root README with setup, test, run, example request, current scope, and next feature.

**Files**

- `README.md`
- `docs/implementation-slices/01-health-metric-ingestion.md`
- `proposal/implementation-plan.md`

### Step 13 — Freeze health capability and snapshot contracts

**Status:** Complete for v1  
**Purpose:** Represent what each device can actually provide without inventing observations or treating missing samples as denied access.

**Implemented capability contracts**

- Added `HealthCapability`, `HealthCapabilityBatch`, and `HealthCapabilityBatchResult` schemas and Pydantic models.
- Defined five honest capability states: `unsupported`, `not_requested`, `requested_no_sample`, `available`, and `error`.
- Deliberately excluded `permission_denied`: HealthKit does not reliably reveal read denial, so the contract never claims knowledge the client cannot obtain.
- Required a non-sensitive `error_code` only for `error` states.
- Required capability-check time, acquisition class, source metadata, and replay/simulation consistency.
- Preserved the safety invariant that capability data always has `used_for_escalation: false`.
- Enforced one user/device per batch, 1–100 entries, and unique capability and metric-type entries.

**Implemented snapshot contracts**

- Added `HealthSnapshotCreateRequest`, immutable internal `HealthSnapshot`, and public `HealthSnapshotView` schemas and models.
- Defined `live`, `recent`, `historical`, and `unavailable` freshness labels.
- Required availability, age, observation time, source, simulation flag, and the exact freshness windows used for each visible metric.
- Required snapshot items to be sorted and unique by metric type so serialization is deterministic.
- Kept full audit identifiers and sensitive device metadata in the internal snapshot while excluding them from the public view.

**Fixtures**

- Added a mixed capability batch covering available live/recent data, requested-without-sample data, replay data, and unsupported data.
- Added frozen snapshot request, internal snapshot, and redacted-view fixtures.

**Files**

- `backend/src/vital_relay/domain/health_context.py`
- `contracts/json-schema/health-capability.schema.json`
- `contracts/json-schema/health-capability-batch.schema.json`
- `contracts/json-schema/health-capability-batch-result.schema.json`
- `contracts/json-schema/health-snapshot-create-request.schema.json`
- `contracts/json-schema/health-snapshot.schema.json`
- `contracts/json-schema/health-snapshot-view.schema.json`
- `contracts/examples/health-capability-batch.json`
- `contracts/examples/health-snapshot-create-request.json`
- `contracts/examples/health-snapshot.json`
- `contracts/examples/health-snapshot-view.json`

**Handoff created**

The Apple lane can report visible support/access state independently of whether a scalar sample exists, and the persistence lane now has complete immutable records to store.

### Step 14 — Implement idempotent capability ingestion

**Status:** Complete for local development  
**Purpose:** Accept capability discovery from Apple/replay clients with the same atomic retry guarantees as scalar ingestion.

**Implemented**

- Added `HealthCapabilityIngestionService` and a replaceable `HealthCapabilityRepository` port.
- Added a shared canonical Pydantic fingerprint helper for deterministic content comparison.
- Implemented a thread-safe in-memory adapter with whole-batch preflight and no partial writes.
- Exact batch retries return the original result and receipt time.
- Reusing a batch or capability ID with different content produces stable `capability_batch_id_conflict` or `capability_id_conflict` errors.
- Added deterministic latest-capability selection by metric type and `as_of`, including a stable ID tie-break for identical timestamps.
- Refactored scalar ingestion to use the shared fingerprint helper and the same deterministic tie-break rule.

**Endpoint**

`POST /v1/health/capabilities:batch`

| Condition | HTTP | Behavior |
|---|---:|---|
| New batch | 201 | Stores unseen capabilities and returns `accepted` |
| Exact retry | 200 | Returns `already_processed` without a second write |
| Conflicting ID reuse | 409 | Returns the stable capability conflict code; writes none of the batch |
| Invalid request | 422 | Rejects the complete request |

**Files**

- `backend/src/vital_relay/adapters/fingerprints.py`
- `backend/src/vital_relay/adapters/in_memory_health.py`
- `backend/src/vital_relay/adapters/in_memory_health_context.py`
- `backend/src/vital_relay/application/health_context.py`
- `backend/src/vital_relay/api/health.py`
- `backend/src/vital_relay/main.py`

**Known limitation at Slice 02 completion — resolved in Slice 03**

Capability history was process-local. Slice 03 now persists it behind the same
repository port.

**Handoff created**

Snapshot construction can query the latest visible capability state at an exact capture time without depending on HTTP or storage implementation details.

### Step 15 — Build immutable health snapshots and freshness policy

**Status:** Complete  
**Purpose:** Capture the exact health context visible at monitoring start or manual refresh without letting it influence escalation.

**Implemented**

- Added an injected `FreshnessPolicy` with explicit defaults: live data remains `live` for 15 seconds; any visible sample up to 24 hours old is `recent`; older data is `historical`.
- A metric is only labeled `live` when both its acquisition class is live and its age is inside the live window.
- Added per-metric window overrides for data types that need different display semantics.
- `HealthSnapshotService` captures authoritative server time and reads both metric and capability repositories as of that exact instant.
- Future-dated observations and capability checks are excluded.
- A capability with no visible metric produces an `unavailable` item rather than a fake numeric value.
- Visible metrics include their deterministic age and freshness plus the windows used to derive the label.
- Items are emitted in stable metric-type order.
- The internal snapshot is saved before a redacted view is returned.
- Retrying the same creation request returns the original snapshot, including its original capture time and contents, even when newer health data exists.
- Added a thread-safe immutable snapshot repository with exact-request retry and `snapshot_id_conflict` detection.

**Files**

- `backend/src/vital_relay/application/health_context.py`
- `backend/src/vital_relay/adapters/in_memory_health_context.py`
- `backend/src/vital_relay/domain/health_context.py`

**Safety boundary**

Freshness and availability are presentation context only. Every nested snapshot representation requires `used_for_escalation: false`; incident creation and transition logic remain out of scope.

**Handoff created**

The next incident slice can attach one immutable snapshot ID for audit/display while the deterministic state machine remains the only escalation authority.

### Step 16 — Expose snapshot creation and redacted retrieval

**Status:** Complete  
**Purpose:** Give monitoring and incident clients a stable API without exposing internal health identifiers or device diagnostics.

**Endpoints**

- `POST /v1/health/snapshots`
  - `201` for a newly captured snapshot;
  - `200` for an exact immutable retry;
  - `409` for conflicting snapshot-ID reuse;
  - `422` for invalid input.
- `GET /v1/health/snapshots/{snapshot_id}`
  - `200` with the redacted view;
  - `404` with `snapshot_not_found` for an unknown ID.

**Redaction implemented**

- Excludes metric IDs, capability IDs, client batch IDs, source bundle identifiers, raw device metadata, and internal error codes.
- Retains only fields needed to present availability, source, value, age, freshness, and simulation state.
- Returns no raw ECG voltage or continuous motion data because those types are prohibited at ingestion.

**Files**

- `backend/src/vital_relay/api/health.py`
- `backend/src/vital_relay/main.py`

The readiness marker reported `health_context_snapshots` for Slice 02 and now
reports `postgres_health_persistence`, making the active
completed slice visible to process checks.

**Handoff created**

Web and incident consumers can use the public view without learning or reimplementing the internal privacy boundary.

### Step 17 — Verify contracts, concurrency, APIs, and safety

**Status:** Complete  
**Purpose:** Freeze Slice 02 behavior before swapping storage adapters or adding Apple transport.

**Added verification**

- JSON Schema/Pydantic parity for all capability and snapshot fixtures.
- Capability state, error, replay, raw-data, single-user, uniqueness, and escalation invariants.
- Exact freshness-boundary and per-type override tests with a frozen clock.
- Snapshot sorting, uniqueness, unavailable-item, and unsafe-payload rejection.
- Capability batch retry, conflict, atomicity, latest-as-of, deterministic tie-break, and concurrent-ingestion tests.
- Snapshot conflict, future-input exclusion, empty-user, immutable-retry, redaction, and concurrent-creation tests.
- API status, conflict, validation, missing-snapshot, response-contract, redaction, and OpenAPI coverage.
- Recursive safety checks proving that internal and public snapshots cannot mark any health datum as escalation-authorizing.

**Files**

- `backend/tests/contract/test_health_context_contract.py`
- `backend/tests/unit/test_health_context.py`
- `backend/tests/unit/test_health_context_repository.py`
- `backend/tests/unit/test_health_snapshot_service.py`
- `backend/tests/integration/test_health_context_api.py`
- `backend/tests/safety/test_health_context_safety.py`
- `backend/tests/conftest.py`

**Verification result**

```text
52 passed in 1.19s
Python bytecode compilation passed
Dependency verification reports no broken requirements
```

All Slice 01 behavior remains green.

### Step 18 — Smoke-test the complete Slice 02 process and align documentation

**Status:** Complete  
**Purpose:** Verify the real Uvicorn boundary and leave a precise handoff for persistence work.

**Observed through localhost HTTP**

```text
Metric batch:       HTTP 201, accepted_count=3
Capability batch:   HTTP 201, accepted_count=5
Snapshot creation:  HTTP 201
Exact retry:        HTTP 200, same captured_at and items
Snapshot retrieval: HTTP 200, redacted view
```

The production-clock smoke test intentionally encountered fixture observations later than server time. The returned snapshot included only the earlier respiratory-rate observation, demonstrating that snapshot construction enforces `observed_at <= captured_at` instead of leaking future input. Frozen-clock tests cover the complete five-item example snapshot.

**Documentation updated**

- Added a complete Slice 02 design and API handoff document.
- Updated the root README, contract catalog, and implementation-plan milestone metadata.
- Selected PostgreSQL health persistence and retention as Slice 03.

**Files**

- `docs/implementation-slices/02-health-capabilities-snapshots.md`
- `README.md`
- `contracts/README.md`
- `proposal/implementation-plan.md`
- `progress.md`

**Known limitations at Slice 02 completion**

- Process-restart data loss is resolved by the optional PostgreSQL adapters in Slice 03.
- No HealthKit client or physical Apple-device behavior has been verified.
- No typed structured records such as sleep summaries, activity summaries, or ECG metadata are implemented yet; they require separate explicit schemas and representative fixtures.

**Handoff created**

Slice 03 can replace the three repository adapters transactionally while keeping every contract, service, API response, and existing test behavior stable.

### Step 19 — Establish the PostgreSQL persistence foundation

**Status:** Complete  
**Purpose:** Add durable infrastructure without coupling domain/application code to SQLAlchemy.

**Implemented**

- Added SQLAlchemy 2.0, Alembic, and psycopg 3 runtime dependencies compatible with Python 3.14.
- Kept the existing synchronous repository ports and FastAPI endpoints unchanged.
- Added PostgreSQL-only engine validation; SQLite and async-driver URLs are rejected.
- Set every database connection to UTC and enabled pool preflight checks.
- Bounded new connections and pool waits to five seconds.
- Required one explicit internal demo-scope UUID for all durable data.
- Established that configured PostgreSQL failures must fail closed and never fall back silently to process memory.
- Retained the in-memory adapters as fast contract-test implementations.

**Files**

- `pyproject.toml`
- `backend/src/vital_relay/persistence/database.py`
- `backend/src/vital_relay/persistence/__init__.py`

**Handoff created**

Migrations and adapters can use one validated engine/session factory while every higher layer continues to depend on ports.

### Step 20 — Add the scoped health database migration

**Status:** Complete for v1  
**Purpose:** Freeze a durable schema that preserves idempotency, audit immutability, and retention isolation.

**Implemented tables**

- `demo_scopes`
- `health_metric_batches`
- `health_metrics`
- `health_capability_batches`
- `health_capabilities`
- `health_snapshot_requests`
- `health_snapshots`
- `health_snapshot_items`
- `health_snapshot_holds`

**Database guarantees**

- All durable keys are scoped by an explicit `scope_id`.
- Batch receipts store ordered accepted/duplicate UUID arrays and the original server receipt time.
- Metric/capability rows store normalized query columns plus immutable content fingerprints.
- Snapshot items copy nested metric/capability payloads; they do not depend on source-row survival.
- Checks reject raw ECG/motion types, non-finite metric values, inconsistent replay/error state, invalid freshness state, and any escalation-authorizing boolean at both top-level and nested snapshot payloads.
- A shared database trigger rejects updates to every health audit/receipt/snapshot/hold table, including changes that would otherwise satisfy value checks.
- Hold foreign keys restrict deletion of protected snapshots, requests, and scopes.
- Latest-as-of indexes order by scope, user, type, event time descending, and UUID descending.
- Snapshot holds provide the future incident-link retention seam.
- Alembic supports upgrade from empty, downgrade to base, and a second upgrade.

**Files**

- `alembic.ini`
- `backend/migrations/env.py`
- `backend/migrations/script.py.mako`
- `backend/migrations/versions/0001_health_persistence.py`
- `backend/src/vital_relay/persistence/models.py`
- `backend/src/vital_relay/persistence/migrations.py`

**Handoff created**

Repository adapters can rely on composite scope keys, transactional constraints, and stable query indexes rather than recreating safety rules in each endpoint.

### Step 21 — Implement durable metric and capability adapters

**Status:** Complete  
**Purpose:** Preserve the exact in-memory contract across restarts and concurrent PostgreSQL writers.

**Implemented**

- Added scope-bound PostgreSQL metric and capability repositories.
- Used transaction advisory locks for batch IDs and sorted entity IDs.
- Performed complete fingerprint conflict preflight before any insert.
- Stored new entities and their batch receipt in one transaction.
- Preserved original accepted/duplicate ordering, counts, and receipt time for exact retries after restart.
- Preserved duplicate-only new-batch behavior.
- Returned the same stable conflict codes as the in-memory adapters.
- Implemented inclusive latest-as-of queries with PostgreSQL `DISTINCT ON` and the existing timestamp/UUID tie-break.
- Isolated identical client IDs between different demo scopes.

**File**

- `backend/src/vital_relay/adapters/postgres_health.py`

**Handoff created**

Apple and replay transports receive identical API behavior whether the application uses memory or PostgreSQL.

### Step 22 — Make snapshot capture one consistent database transaction

**Status:** Complete  
**Purpose:** Close the transaction gap between metric lookup, capability lookup, and immutable snapshot storage.

**Implemented**

- Added `HealthSnapshotUnitOfWork` and transaction-bound repository grouping to the application boundary.
- Kept the original repository ports and non-database service path intact.
- Serialized a scoped snapshot ID with a session-level advisory lock.
- Began `REPEATABLE READ` only after the lock was acquired, so concurrent exact retries observe the first committed snapshot.
- Translated the scope-close serialization race into `DemoScopeUnavailableError` after a read-committed lifecycle recheck.
- Read metrics and capabilities, derive freshness, and insert request/header/items inside one transaction.
- Ensured rollback removes every partial snapshot row.
- Reconstructed reads solely from the stored header and copied items; newer source data is never consulted.

**Files**

- `backend/src/vital_relay/application/health_context.py`
- `backend/src/vital_relay/adapters/postgres_health.py`

**Handoff created**

Future incidents can attach a snapshot knowing its complete context is durable, transactionally consistent, and immutable.

### Step 23 — Add fail-closed retention and snapshot holds

**Status:** Complete  
**Purpose:** Minimize backend health retention without allowing an implicit broad deletion.

**Implemented**

- Added immutable retention preview/count/reset result types and a repository port.
- Bound every retention repository to one scope UUID.
- Required callers to repeat the exact UUID as confirmation for preview, manual reset, and expiry purge.
- Rejected expiry purge before the authoritative scope expiration.
- Locked and closed the scope during reset so it cannot accept new writes.
- Deleted metric/capability entities and their idempotency receipts together.
- Deleted unheld snapshot requests, headers, and items together.
- Preserved held snapshots and their copied audit content after source deletion.
- Added a stable hold ID plus reason/reference seam for future incident linkage.
- Serialized hold creation and reset on the same exclusive scope lock and rejected holds on expired scopes.
- Included hold rows in protected retention previews/results.

**Files**

- `backend/src/vital_relay/application/health_retention.py`
- `backend/src/vital_relay/adapters/postgres_retention.py`
- `backend/tests/unit/test_health_retention.py`

**Known limitation**

Expiry purge is an explicit CLI/service operation; no background scheduler exists yet.

**Handoff created**

The incident state machine can create a snapshot hold before retention without changing the frozen snapshot contract.

### Step 24 — Wire PostgreSQL and add the database lifecycle CLI

**Status:** Complete  
**Purpose:** Make durable mode operable while keeping local in-memory startup simple.

**Implemented**

- `create_app` selects PostgreSQL only when a database URL is configured.
- PostgreSQL mode requires `VITAL_RELAY_DATABASE_URL` and a valid active `VITAL_RELAY_DEMO_SCOPE_ID`.
- Application startup validates the scope and database instead of falling back.
- Removed module-level application construction and switched Uvicorn to factory mode, preventing imports and test collection from touching an ambient database.
- Disposed PostgreSQL engines after failed composition and through the FastAPI lifespan.
- Made PostgreSQL readiness revalidate the configured database and active scope, returning `503` when unavailable.
- Ensured an explicit programmatic Alembic URL wins over ambient configuration.
- Snapshot services receive the PostgreSQL unit of work automatically.
- FastAPI version advanced to `0.2.0`; readiness reports `postgres_health_persistence`.
- Added `vital-relay-db` commands for migration, scope creation, preview, confirmed reset, and confirmed expiry purge.
- Added `make migrate` and `make test-postgres` targets.

**Files**

- `backend/src/vital_relay/main.py`
- `backend/src/vital_relay/persistence/cli.py`
- `Makefile`
- `pyproject.toml`
- `backend/tests/unit/test_database_configuration.py`

**Handoff created**

A contributor can provision one bounded demo scope and run the unchanged health API against durable storage from documented commands.

### Step 25 — Verify adapter parity with real PostgreSQL

**Status:** Complete  
**Purpose:** Test PostgreSQL behavior PostgreSQL actually controls instead of approximating it with SQLite.

**Implemented verification**

- Added a reusable repository contract suite for both in-memory and PostgreSQL adapters.
- Added a private Postgres.app pytest cluster using temporary storage and a Unix socket; it never touches the developer's normal cluster.
- Verified migration upgrade/downgrade/upgrade and migration/model parity.
- Inspected the required latest-as-of indexes.
- Verified ordered receipt persistence, duplicate behavior, stable conflicts, inclusive boundaries, UUID tie-breaks, and user isolation.
- Verified concurrent exact metric/capability batches produce one accepted receipt.
- Verified concurrent snapshot capture produces one complete immutable result.
- Disposed and recreated engines, then recovered original receipt and snapshot data.
- Verified direct SQL cannot enable escalation or store a forbidden raw metric type.
- Verified all eight health audit tables reject even valid-looking updates.
- Verified copied JSON payloads independently reject raw types and nested escalation flags.
- Verified confirmed retention, held snapshot survival, and cross-scope isolation.
- Verified scope closure serializes against concurrent ingestion and rejects the waiting write.
- Verified staged hold/reset and snapshot/reset races have stable domain outcomes.
- Verified held-row foreign keys block direct snapshot-request and scope deletion.
- Verified concurrent exact scope creation is idempotent and lifecycle conflicts are deterministic.
- Verified migration URL precedence, bounded engine configuration, lifecycle disposal, and database-backed readiness.
- Exercised the database CLI and unchanged FastAPI contracts against the real adapters.

**Files**

- `backend/tests/repository_contract.py`
- `backend/tests/unit/test_repository_contract.py`
- `backend/tests/postgres/conftest.py`
- `backend/tests/postgres/test_postgres_health.py`
- `backend/tests/postgres/test_runtime_hardening.py`
- `backend/tests/postgres/test_schema_hardening.py`
- `backend/tests/unit/test_persistence_schema_hardening.py`
- `docs/implementation-slices/03-postgres-health-persistence.md`
- `README.md`
- `proposal/implementation-plan.md`
- `progress.md`

**Verification result**

```text
84 passed, 31 PostgreSQL tests deselected   # make test
31 passed                                  # make test-postgres
Python bytecode compilation passed
Dependency verification reports no broken requirements
```

**Remaining limitations**

- PostgreSQL/PostGIS production deployment, backups, authentication, and role authorization are not implemented.
- PostGIS responder/AED tables intentionally remain outside this health-only migration.
- No Apple client or physical-device HealthKit/fall behavior has been verified.

**Handoff created**

Slice 04 can attach a real incident to one immutable held health snapshot without
changing the completed health contracts or retention behavior.

### Step 26 — Freeze the real wearable-event and incident boundary

**Status:** Complete for v1  
**Purpose:** Define exactly which real operational signals may create or advance
an incident before adding runtime behavior.

**Implemented**

- Added frozen Pydantic and JSON Schema contracts for:
  - bounded timestamped location;
  - Apple fall and first-party manual SOS event requests/results;
  - incident views and authorized transitions;
  - idempotent verification check-ins/results;
  - ordered presentation-safe timeline entries.
- Restricted the product event path to `simulated: false` with only
  `apple_fall` and `manual_sos` sources.
- Required an Apple fall's reported date to equal its observation time plus
  affirmative availability, entitlement, and authorization metadata.
- Required manual SOS to identify a deliberate Watch or iPhone activation.
- Added representative non-simulated Apple-fall, manual-SOS, check-in, incident,
  transition, and timeline fixtures.
- Added the pure deterministic transition table. Health snapshot inputs are not
  accepted by the transition function.

**Files**

- `backend/src/vital_relay/domain/incidents.py`
- `backend/src/vital_relay/domain/health_context.py`
- `contracts/json-schema/*.schema.json`
- `contracts/examples/*.json`
- `contracts/README.md`

**Safety boundary**

This contract accepts evidence from a first-party Apple client; it does not
obtain Apple's fall entitlement or synthesize a fall callback. No replay source
is accepted by this real incident path.

**Handoff created**

Persistence and HTTP code receive one strict, location-bearing event envelope
and cannot introduce additional escalation sources.

### Step 27 — Add the durable incident schema and atomic repository path

**Status:** Complete and verified  
**Purpose:** Ensure a safety signal cannot leave a partial or process-local
incident behind.

**Implemented**

- Alembic revision `0002_incident_core` adds immutable wearable events,
  check-in commands, state transitions, timeline entries, mutable current-state
  incident projections, and persistent verification deadlines.
- PostgreSQL constraints independently enforce non-simulated sources, exact
  source/type relationships, location bounds, the transition matrix, one active
  incident per scoped user, event/device-sequence uniqueness, and ordered audit
  sequences.
- Incident creation captures one immutable health snapshot at server receipt
  time and creates an incident hold in the same logical transaction.
- The incident references the exact `(scope, hold, snapshot)` tuple, preventing
  retention from deleting or substituting the attached display context.
- Event UUID retries, Apple fall natural-key redelivery, device sequence reuse,
  and check-in UUID retries receive stable idempotency/conflict behavior.
- The incident repository has no in-memory fallback; configured PostgreSQL and
  an active demo scope are required.

**Files**

- `backend/migrations/versions/0002_incident_core.py`
- `backend/src/vital_relay/persistence/models.py`
- `backend/src/vital_relay/adapters/postgres_incidents.py`

**Handoff created**

The service can treat event acceptance, snapshot retention, state mutation, and
audit history as one durable effect.

### Step 28 — Expose authenticated ingestion, check-in, and durable timeout

**Status:** Complete and verified  
**Purpose:** Run the incident policy through a real first-party HTTP boundary
and survive process restarts during verification.

**Implemented**

- Added application operations for event ingestion, incident reads, check-ins,
  ordered timeline reads, and due-deadline processing.
- Added device-token authentication using
  `X-Vital-Relay-Device-Token`; missing or invalid tokens fail before incident
  data is exposed.
- Added:
  - `POST /v1/wearable/events`;
  - `GET /v1/incidents/{incident_id}`;
  - `POST /v1/incidents/{incident_id}/check-in`;
  - `GET /v1/incidents/{incident_id}/timeline`.
- Fall events begin in `verifying`; manual SOS begins in `escalating`.
- `i_am_okay` resolves verification, while `i_need_help`, manual SOS during
  verification, or authoritative timeout advances the incident to
  `escalating`.
- The timeout due time and settlement live in PostgreSQL. A bounded background
  worker claims due records so restart or multiple workers cannot produce a
  second timeout transition.
- The static device token is explicitly a hackathon boundary, not production
  device attestation, identity, or responder authorization.

**Files**

- `backend/src/vital_relay/application/incident_service.py`
- `backend/src/vital_relay/api/incidents.py`
- `backend/src/vital_relay/main.py` (composition path)
- `.env.example`

**Handoff created**

Slice 05 receives a real `escalating` incident plus durable exact coordinates,
held health context, and an ordered timeline.

### Step 29 — Run the focused real-path acceptance gate

**Status:** Complete  
**Purpose:** Spend the hackathon test budget on persistence and race behavior
that cannot be established with an in-memory fake.

**Acceptance exercised**

- Revision 0002 upgrades and matches the SQLAlchemy model on a private real
  PostgreSQL cluster.
- One authenticated Apple-fall fixture atomically creates its event, incident,
  snapshot/hold, transition, deadline, and ordered timeline.
- Exact event and check-in retries return their original durable result.
- An explicit help check-in escalates once.
- A due fall verification advances once after application restart.
- Invalid authentication is rejected before incident persistence. Existing
  contract, scope-lifecycle, and schema checks remain green with revision 0002.

**Verification result**

- Fast non-database regression suite: `84 passed, 33 deselected`.
- Focused incident PostgreSQL acceptance: `2 passed`.
- Complete existing PostgreSQL suite after revision 0002: `33 passed`.
- The private cluster exercised Alembic through revision 0002, atomic
  Apple-fall ingestion, exact retry, the `incident_created` snapshot/hold,
  explicit-help escalation, ordered timeline reads, and timeout processing on
  application recreation with a controlled clock and no sleep.
- Physical-device Apple entitlement, authorization, and callback behavior are
  still intentionally unclaimed; those belong to the Swift client slice.

### Step 30 — Build the first Living Relay iPhone vertical slice

**Status:** Complete for iOS UI-01  
**Purpose:** Prove the consumer-facing visual identity and safety interaction
without waiting for HealthKit, Watch, dispatch, or live incident transport.

**Implemented**

- Added a native Xcode iPhone application and a local Swift feature package.
- Added the OLED-dark Living Relay design tokens and persistent demo/replay
  boundaries.
- Added a 720-particle SwiftUI Canvas heart, verification halo, and Reduce Motion
  static-orbit fallback.
- Added a fixture-driven `78 BPM` monitoring state with honest replay/source and
  context-only labeling.
- Added a deliberate SOS hold, deterministic timed safety check, `I'm okay`,
  `I need help`, timeout, and bounded help-request acknowledgement.
- Routed every action through a typed provider and injectable date source.
- Added direct-launch scene fixtures for simulator visual QA.

**Files**

- `apps/apple/VitalRelay.xcodeproj`
- `apps/apple/VitalRelayApp/`
- `apps/apple/Sources/VitalRelayFeature/`
- `apps/apple/Tests/VitalRelayFeatureTests/`
- `docs/implementation-slices/ios-ui-01-monitoring-safety-check.md`

**Verification result**

```text
7 Swift tests passed
arm64 iOS Simulator build passed
iPhone 17 Pro / iOS 26.2 launch passed
Monitoring, safety-check, and timeout states visually inspected
```

**Remaining limitations**

- This UI pre-slice does not complete Swift Codable parity or live incident
  transport.
- HealthKit, WatchConnectivity, haptics, physical-device performance, Apple fall
  entitlements, responder UI/backend integration, maps, and notifications remain
  unimplemented in the Apple app.
- Provider transitions are unit-tested, but an XCUITest does not yet drive the
  SOS hold and response buttons.

**Handoff created**

UI-02 can add Swift incident/check-in contracts and an authenticated live data
provider without changing the completed scene views. The fixture provider
remains the deterministic demo fallback. Responder visuals can now integrate
with Slice 05's authenticated dispatch read models.

### Step 31 — Freeze the responder-dispatch and privacy contracts

**Status:** Complete for v1  
**Purpose:** Make pre-accept coordination structurally incapable of leaking the
wearer's exact location while giving the accepted responder a separate bounded
view.

**Implemented**

- Added frozen Pydantic and JSON Schema contracts for responder candidates,
  AEDs, invitations, coordination, responder decisions, accepted dispatch, and
  static route plans/legs.
- Limited pre-accept candidates to identity, role, allowlisted skills, rank,
  location freshness, availability, and a coarse distance band.
- Kept `wearer_location` out of `DispatchCoordinationView` by design. The exact
  `GeoLocation` exists only in `AcceptedDispatchView`.
- Required contiguous candidate ranks and invitation sequences, exact candidate
  snapshots for invitations, at most one accepted responder, and exact links
  between the accepted invitation, AED, route, and incident.
- Extended the authoritative incident policy and timeline vocabulary with
  responder search, invitation, decline, acceptance, and dispatch activation.
- Added representative accept/decline and accepted-only examples under
  `contracts/examples/`.

**Files**

- `backend/src/vital_relay/domain/dispatch.py`
- `backend/src/vital_relay/domain/incidents.py`
- `contracts/json-schema/*dispatch*.schema.json`
- `contracts/json-schema/responder-*.schema.json`
- `contracts/json-schema/aed-site-view.schema.json`
- `contracts/json-schema/static-route-*.schema.json`
- `contracts/examples/*dispatch*.json`
- `contracts/examples/responder-*.json`
- `contracts/examples/aed-site-view.json`
- `contracts/examples/static-route-*.json`

**Handoff created**

The persistence and HTTP layers share one explicit disclosure boundary instead
of relying on endpoint code to remember which coordinates to remove.

### Step 32 — Add real PostGIS persistence and an explicit venue seed

**Status:** Complete and verified  
**Purpose:** Replace conceptual proximity with durable, index-backed responder
and AED discovery while retaining the existing scope and audit guarantees.

**Implemented**

- Added GeoAlchemy2 and Alembic revision `0003_postgis_dispatch`, including
  `CREATE EXTENSION IF NOT EXISTS postgis`.
- Added scope-bound responders, skills, mutable availability, append-only
  geography location history, static AEDs, invitations, immutable decisions,
  and immutable accepted assignments.
- Stored responder/AED points as `geography(POINT, 4326)` and added explicit
  GiST indexes for both spatial columns.
- Added database constraints for qualification values, non-simulated data,
  exact scope/incident/responder relationships, one pending invitation, one
  accepted invitation, one assignment, immutable decisions/assignments, and
  accepted-response assignment integrity.
- Made data-bearing downgrade explicit: a latest responder acceptance is
  removed and its incident returns to `escalating` with recalculated audit
  counters; downgrade refuses to erase an assignment after any later state
  transition.
- Added `seed-response-network --scope UUID --confirm UUID`. It persists two
  responders and two AEDs at explicit Chicago Loop demo-venue coordinates,
  refreshes availability/location, and rotates high-entropy responder tokens.
  Plaintext tokens are emitted once; PostgreSQL stores only SHA-256 hashes.
- Extended `/healthz` to verify PostGIS availability in PostgreSQL mode and
  report the `postgis_dispatch` slice.

**Files**

- `backend/migrations/versions/0003_postgis_dispatch.py`
- `backend/src/vital_relay/persistence/models.py`
- `backend/src/vital_relay/persistence/cli.py`
- `backend/src/vital_relay/adapters/postgres_dispatch.py`
- `backend/src/vital_relay/main.py`
- `pyproject.toml`
- `.env.example`

**Handoff created**

An operator can create an explicit demo scope, seed real persisted coordinates,
capture the printed responder credentials, and run discovery without a process-
local or simulated dispatch substitute.

### Step 33 — Build durable invitation, acceptance, and static-route APIs

**Status:** Complete for Slice 05  
**Purpose:** Turn an existing `escalating` incident into exactly one authenticated
accepted responder assignment.

**Implemented**

- Added indexed PostGIS eligibility queries that require an active responder,
  current availability, valid `first_aid` qualification, a fresh latest
  location, and the configured radius. Results are ordered by exact internal
  meter distance and disclosed only as coarse bands.
- Added nearest-active-AED selection using the incident's durable coordinates.
- Added incident-locked coordination that records search once and creates at
  most one pending invitation for the next never-invited eligible responder.
- Preserved each invitation's redacted candidate snapshot so history remains
  valid after live eligibility changes.
- Added responder-token authentication with constant-time hash comparison.
  Authentication occurs before invitation/assignment existence is disclosed,
  and an inactive responder identity cannot retain exact-location access.
- Added immutable decision UUID/fingerprint receipts. An exact decline or accept
  retry returns the stored result; conflicting reuse is rejected.
- A decline and its next invitation commit together. Acceptance revalidates
  eligibility and atomically commits `escalating -> response_active`, the
  decision, assignment, and ordered timeline events.
- Added the `RoutingProvider` port and real `StaticVenueRoutingProvider`. The
  persisted route has responder-to-AED and AED-to-wearer legs using PostGIS
  straight-line distances and a simple walking estimate.
- Added device-authenticated coordination endpoints and responder-authenticated
  decision/accepted-dispatch endpoints. Exact wearer location is released only
  to the token bound to the accepted responder.
- Served accepted-dispatch reads from the immutable accepted decision snapshot
  and validated it against the assignment, so later AED edits cannot rewrite or
  invalidate the persisted route.

**HTTP endpoints**

- `POST /v1/incidents/{incident_id}/dispatch`
- `GET /v1/incidents/{incident_id}/dispatch`
- `POST /v1/incidents/{incident_id}/responders/{responder_id}/response`
- `GET /v1/incidents/{incident_id}/responders/{responder_id}/dispatch`

**Files**

- `backend/src/vital_relay/application/dispatch_service.py`
- `backend/src/vital_relay/application/routing.py`
- `backend/src/vital_relay/api/dispatch.py`
- `backend/src/vital_relay/adapters/postgres_dispatch.py`
- `backend/src/vital_relay/adapters/static_routing.py`
- `backend/src/vital_relay/main.py`

**Known limitations**

- `static_venue` is not live, turn-by-turn, indoor, or traffic-aware routing and
  does not provide a live ETA.
- No notification provider, APNs/Twilio delivery, responder device registration,
  or externally delivered invitation exists yet.
- Authentication is a bounded hackathon token design, not production responder
  enrollment, certification, attestation, rotation, or authorization.

**Handoff created**

Slice 06 receives one durable `response_active` incident, its accepted responder,
assigned AED, exact accepted-only wearer location, and persisted static route.

### Step 34 — Run the focused PostGIS dispatch acceptance gate

**Status:** Complete  
**Purpose:** Spend the hackathon test budget on the spatial, privacy, idempotency,
and state-transition boundaries that cannot be proven with an in-memory fake.

**Acceptance exercised**

- Migrated a private real PostgreSQL database through revision 0003 and verified
  the PostGIS extension plus responder/AED GiST indexes.
- Opened a real non-simulated manual SOS and ranked only fresh, available,
  first-aid-qualified responders while excluding closer stale, unavailable, and
  unqualified rows.
- Confirmed the pre-accept response has no wearer location or exact responder
  point.
- Declined the first responder, invited the second once, and replayed the same
  decision without a duplicate invitation, response, or timeline effect.
- Accepted the second responder and verified one `response_active` transition,
  exact accepted-only wearer location, one immutable assignment, the persisted
  two-leg static route, token isolation, and ordered timeline events.
- Edited and deactivated the assigned AED after acceptance and confirmed the
  stored accepted snapshot remained byte-for-byte stable; then deactivated the
  responder and confirmed its exact-location access was revoked.
- Downgraded the populated database to revision 0002, verified the incident was
  reconciled to `escalating` without dispatch-only audit rows, and upgraded to
  head again.

**Verification commands and results**

```text
make test
84 passed, 35 deselected

make test-postgres
35 passed

.venv/bin/python -m pytest -m postgres \
  backend/tests/postgres/test_postgis_dispatch.py
2 passed
```

The two focused dispatch checks are included in the `35`-test full PostgreSQL/
PostGIS result; they are not additional tests. Testing remains deliberately
narrow for the hackathon.

**Files**

- `backend/tests/postgres/test_postgis_dispatch.py`
- `docs/implementation-slices/05-postgis-dispatch.md`
- `README.md`
- `proposal/implementation-plan.md`
- `progress.md`

**Handoff created**

The next slice can attach a fixed, versioned, source-visible first-aid protocol
to the accepted dispatch without reopening discovery, privacy, or route storage.

### Step 35 — Connect the iPhone scenes to the real incident boundary

**Status:** Complete for iOS UI-02  
**Purpose:** Replace the fixture-only action boundary with authenticated,
restart-safe incident transport while leaving the backend as the only incident
state authority.

**Implemented**

- Added Swift Codable/Sendable parity for wearable events, incidents,
  transitions, check-ins, and timeline entries, including strict schema,
  simulation, location, Apple proof, pairing, and RFC 3339 handling.
- Added the device-token-authenticated URLSession client for event ingestion,
  incident reads, check-ins, and timeline reads. Non-loopback hosts require
  HTTPS before any token or location-bearing request can be sent.
- Corrected the monitoring hold semantics: deliberate manual SOS now posts one
  real `manual_sos` envelope and maps server `escalating` directly to Getting
  Help. It never fabricates a fall safety check.
- Made `verification_expires_at` the countdown authority. Local expiry performs
  only a read; the client remains verifying at zero until the backend advances.
- Added bounded polling, state-version rewind protection, and `409` timeout-race
  reconciliation.
- Persisted the exact pending SOS/check-in, active incident ID, and monotonic
  device sequence in a namespaced request store before network delivery.
- Retried a pending check-in during cold-start recovery before enabling actions,
  blocked a second SOS when active recovery fails, and guarded late polls by
  incident lifecycle generation.
- Added in-app retry for failed initial recovery, configured-user validation on
  every authoritative incident, and support for manual SOS attaching to an
  already-active fall incident.
- Kept fixture mode as the default and required explicit live configuration.
  Live manual SOS fails closed without an injected current location; fixed
  venue coordinates require an explicit demo-only launch argument.
- Added honest live/replay badges, live Getting Help copy, and a minimal
  gradient Resolved scene without inventing responder identity, route, or ETA.

**Files**

- `apps/apple/Sources/VitalRelayFeature/IncidentContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentRequestStore.swift`
- `apps/apple/Sources/VitalRelayFeature/LiveVitalRelayDataProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/ResolvedView.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/IncidentContractCodableTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/IncidentAPIClientTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/LiveVitalRelayDataProviderTests.swift`
- `docs/implementation-slices/ios-ui-02-live-incident-client.md`

**Verification result**

```text
38 Swift tests in 6 suites passed
arm64 iOS Simulator build passed
iPhone 17 Pro / iOS 26.2 Getting Help and Resolved scenes inspected
Simulator live mode reached loopback FastAPI and rendered its typed 503
2 focused real-PostgreSQL incident acceptance tests passed
```

**Remaining limitations**

- Core Location, HealthKit, WatchConnectivity, Apple fall entitlement/callback,
  physical-device proof, and long-press XCUITest coverage remain unimplemented.
- Polling exists, but WebSocket/SSE delivery and a visible offline indicator do
  not.
- The iPhone app does not yet consume Slice 05 dispatch read models.

**Handoff created**

UI-03 can consume the redacted coordination and accepted-only static-route
contracts from Slice 05. The active live incident, durable retry identity, and
state-version ordering are now available without reopening the SOS/check-in
authority boundary.

**Status update:** UI-03 completed the redacted coordination portion of this
handoff. The accepted static route remains intentionally outside the wearer app
and requires a separate responder-authenticated entry surface.

### Step 36 — Freeze the fixed protocol and presentation contracts

**Status:** Complete for v1  
**Purpose:** Give command and responder clients one exact source-visible
presentation shape without introducing diagnosis or generated emergency advice.

**Implemented**

- Added frozen `FirstAidProtocolSource`, `FirstAidProtocolStep`,
  `FixedFirstAidProtocol`, and `ProtocolPresentationView` Pydantic/JSON Schema
  contracts plus representative fixtures.
- Required exact semantic versions, lowercase SHA-256 digests, unique HTTPS
  source links, contiguous ordered steps, an emergency-guidance disclaimer, and
  an explicit non-simulated presentation bound to incident/assignment/responder.
- Added `fall-response` `1.0.0` and `manual-sos-response` `1.0.0`, each with six
  fixed steps.
- Made the mapping key exactly `IncidentKind`. Contracts have no health-value,
  diagnosis, responder-observation, prompt/model, or generated-text field.
- Made review provenance visible through official American Red Cross First Aid
  Steps and unresponsive/breathing pages, NHS Falls, and American Heart
  Association emergency cardiac-arrest links.

**Files**

- `backend/src/vital_relay/domain/protocols.py`
- `backend/src/vital_relay/protocols/content/fall-response-v1.json`
- `backend/src/vital_relay/protocols/content/manual-sos-v1.json`
- `contracts/json-schema/first-aid-protocol-source.schema.json`
- `contracts/json-schema/first-aid-protocol-step.schema.json`
- `contracts/json-schema/fixed-first-aid-protocol.schema.json`
- `contracts/json-schema/protocol-presentation-view.schema.json`
- `contracts/examples/fixed-first-aid-protocol.fall.json`
- `contracts/examples/fixed-first-aid-protocol.manual-sos.json`
- `contracts/examples/protocol-presentation-view.json`

**Handoff created**

Backend, web, and iOS consumers can render the same fixed source/version/hash
record without receiving a safety-content generation interface.

### Step 37 — Build the independently pinned fail-closed registry

**Status:** Complete and verified  
**Purpose:** Prevent a missing or edited local JSON file from silently becoming
authorized emergency guidance.

**Implemented**

- Pinned each expected raw-file SHA-256 in an append-only ID/version catalog
  separate from the packaged JSON. The JSON cannot declare or update its own
  digest.
- Kept that version catalog separate from the active
  `IncidentKind -> (protocol ID, version)` mapping. A future active-version
  change can preserve exact loading of older persisted presentations.
- Added exact active mappings for `fall` and `manual_sos`; unknown active or
  exact catalog identities fail closed.
- Reread the raw bytes, recomputed their digest, decoded UTF-8 JSON, validated
  the contract, and checked ID/version/kind identity at all three boundaries:
  startup, acceptance-time selection, and every presentation read.
- Avoided an in-memory content cache so later missing/modified content is
  detected rather than hidden by an earlier successful read.
- Validated all content during application creation and `/healthz`; later
  integrity failure produces `protocol_content_unavailable` instead of stale or
  generated advice.
- Bumped the backend/package version to `0.5.0` and readiness marker to
  `fixed_protocol_presentation`.

**Files**

- `backend/src/vital_relay/protocols/registry.py`
- `backend/src/vital_relay/protocols/__init__.py`
- `backend/src/vital_relay/main.py`
- `backend/src/vital_relay/__init__.py`
- `pyproject.toml`

**Honesty boundary**

Digest-pinned fail-closed product integrity is implemented. The evaluator and
sandbox do not exist yet, so read-only mount enforcement that prevents mutation
candidates from writing protocol files remains future NemoClaw/evolution work.

**Handoff created**

Any current product path can request only a known, byte-verified protocol;
candidate isolation can later protect the same directory without changing its
registry contract.

### Step 38 — Persist and expose the exact accepted-assignment presentation

**Status:** Complete for Slice 06  
**Purpose:** Ensure accepted dispatch and fixed instructions cannot diverge
across partial commits, mutable files, or application restart.

**Implemented**

- Added Alembic revision `0004_protocol_presentations` and the
  `protocol_presentations` ORM model.
- Added an exact unique responder-assignment link and an append-only presentation
  table with `ON DELETE RESTRICT`, one presentation per incident/assignment,
  allowlisted incident kind, valid digest, JSON snapshot, and `simulated: false`.
- Added an insert trigger that requires the presentation's exact assignment,
  responder, incident kind, and presentation/acceptance timestamp to agree.
- Backfilled every accepted assignment already present at revision 0003 by
  rereading its protected exact protocol and inserting the deterministic
  presentation during upgrade.
- Selected/validated the protocol and inserted its complete snapshot in the same
  PostgreSQL transaction as responder decision, transition, assignment, and
  route. Any protocol error aborts the complete acceptance transaction.
- Added the device-authenticated
  `GET /v1/incidents/{incident_id}/protocol` endpoint.
- Added the responder-token-authenticated
  `GET /v1/incidents/{incident_id}/responders/{responder_id}/protocol` endpoint;
  only the active assigned responder can read it, and deactivation revokes access.
- Reloaded the registered exact ID/version/digest and compared every stored
  metadata field plus the complete snapshot, incident kind, assignment/responder
  identity, and acceptance timestamp before presentation.
- Treated an accepted assignment without its required presentation as integrity
  failure instead of an ordinary `404`.
- Kept pre-accept reads at `404` and returned the identical typed presentation
  after application recreation.

**Files**

- `backend/migrations/versions/0004_fixed_protocol_presentations.py`
- `backend/src/vital_relay/persistence/models.py`
- `backend/src/vital_relay/adapters/postgres_dispatch.py`
- `backend/src/vital_relay/adapters/postgres_protocols.py`
- `backend/src/vital_relay/application/protocol_service.py`
- `backend/src/vital_relay/api/protocols.py`
- `backend/src/vital_relay/main.py`

**Known limitations**

- The content is fixed emergency presentation, not diagnosis, medical
  interpretation, professional-care replacement, or generated advice.
- Notification registration/delivery and live routing remain unimplemented.
- Command-center, responder web, and iOS UI-03 consumption remain the next slice.

**Handoff created**

Slice 07 receives stable authenticated protocol endpoints tied to the same
accepted assignment and state authority as dispatch.

### Step 39 — Run the focused fixed-protocol acceptance gate

**Status:** Complete  
**Purpose:** Verify the integrity, atomicity, authorization, and restart behavior
without expanding the hackathon test budget broadly.

**Acceptance exercised**

- Validated both fixed protocols and source/step/presentation JSON Schemas.
- Proved deterministic `IncidentKind`-only mapping and ordered steps.
- Modified copied content and requested unknown/mismatched identity/digest values
  to confirm fail-closed behavior.
- Accepted a real manual-SOS invitation and verified exactly one protocol
  presentation was inserted with the exact accepted assignment.
- Confirmed command and active assigned-responder reads return the same source-
  visible `manual-sos-response` `1.0.0` snapshot; pre-accept reads return `404`
  and responder deactivation returns `401`.
- Recreated the application and received the identical persisted presentation.
- Downgraded the populated database to revision 0003, upgraded to head, and
  verified the protected backfill recreated the identical presentation.
- Kept all existing PostGIS discovery, invitation, dispatch, static-route,
  immutable accepted snapshot, and data-bearing downgrade checks green.

**Verification commands and results**

```text
make test
88 passed, 35 deselected

.venv/bin/python -m pytest -m postgres \
  backend/tests/postgres/test_postgis_dispatch.py
2 passed

make test-postgres
35 passed
```

The focused `2` tests are included in the full `35`-test PostgreSQL/PostGIS
result. An initial sandboxed PostgreSQL start could not allocate shared memory;
the identical isolated-cluster run outside the sandbox passed, confirming an
environment-only limitation rather than a product failure.

**Files**

- `backend/tests/unit/test_protocol_registry.py`
- `backend/tests/postgres/test_postgis_dispatch.py`
- `docs/implementation-slices/06-fixed-protocol-presentation.md`
- `contracts/README.md`
- `README.md`
- `proposal/implementation-plan.md`
- `progress.md`

**Handoff created**

The next product slice can render authoritative command/responder live views and
iOS UI-03 from the existing incident, dispatch, and protocol contracts. External
notification delivery remains a separate future provider.

### Step 40 — Connect the wearer app to privacy-redacted dispatch

**Status:** Complete for iOS UI-03  
**Purpose:** Render real responder/AED coordination in the Getting Help scene
without crossing the Slice 05 responder-token privacy boundary.

**Implemented**

- Added strict Swift device-view contracts for responder candidates,
  invitation history, coordination state, accepted-responder linkage, and the
  public AED.
- Deliberately omitted accepted dispatch, responder/wearer coordinates, route,
  meter distance, ETA, and responder-token models from the wearer app.
- Added device-token `POST` and `GET /v1/incidents/{incident_id}/dispatch`
  transport with the existing HTTPS-or-loopback policy.
- Started dispatch on a best-effort basis for escalating incidents and polled
  incident and coordination independently so invitation changes do not depend
  on an incident `state_version` change.
- Preserved incident authority and the last acknowledged scene across dispatch
  failures; dispatch presentation cannot advance or rewind incident state.
- Distinguished a server-reported unavailable public AED from transient
  coordination failure and retained any last confirmed privacy-safe details.
- Added timestamp, request-ordinal, incident identity/generation, nonshrinking
  history, immutable terminal invitation, and sticky-acceptance safeguards for
  delayed or out-of-order responses.
- Added a dark violet-blue-aqua relay constellation and readable searching,
  invitation-pending, and accepted states with coarse responder and public AED
  details.
- Added static Reduce Motion presentation, one VoiceOver summary, Dynamic Type
  scrolling, and copy that labels pending invitations as recorded without
  claiming phone notification, travel, route, arrival, or ETA.
- Added direct visual-QA launch arguments:
  `--fixture-dispatch-invited` and `--fixture-response-active`.

**Files**

- `apps/apple/Sources/VitalRelayFeature/DispatchContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/DispatchPresentationMapper.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/LiveVitalRelayDataProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/RelayConstellationView.swift`
- `apps/apple/Sources/VitalRelayFeature/HelpRequestedView.swift`
- `apps/apple/Sources/VitalRelayFeature/VitalRelayScene.swift`
- `apps/apple/VitalRelayApp/VitalRelayApp.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/DispatchContractCodableTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/DispatchAPIClientTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/DispatchPresentationMapperTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/LiveDispatchIntegrationTests.swift`
- `docs/implementation-slices/ios-ui-03-dispatch-coordination.md`

**Verification result**

```text
68 Swift tests in 10 suites passed
generic arm64 iOS Simulator build passed
```

Tests cover strict privacy/contract decoding, exact transport shape, all role
and distance-band mappings, startup and same-version polling, dispatch failure
isolation, incident authority, foreign-incident rejection, and delayed
equal-timestamp race handling.

**Remaining limitations and handoff**

- Notification registration/delivery, signed or universal-link entry,
  responder credential bootstrap, and a responder client are not implemented.
- The wearer app cannot and must not fetch exact accepted dispatch, route,
  protocol, or ETA.
- HealthKit, Core Location, WatchConnectivity, a genuine Apple fall callback,
  WebSocket/SSE, and physical-device proof remain unimplemented.

The next frontend work is a separate responder-authenticated entry surface.
Only after invitation/deep-link or notification bootstrap and authenticated
acceptance should that surface fetch the exact static dispatch and fixed
protocol.

### Step 41 — Freeze the one-app native persona boundary

**Status:** Complete  
**Purpose:** Correct Slice 07 before implementation so command and responder are
native experiences in the one Vital Relay app, not a separate browser product.

**Implemented**

- Removed the generated web starter and package cache before product code was
  written there.
- Added community, responder, and command access profiles with shared native
  persona chrome.
- Kept three separate feature graphs in one app binary:
  - existing wearer client remains device-authenticated and redacted;
  - responder owns only its responder credential and accepted-only models;
  - command owns its device credential and command projections.
- Updated the implementation/worktree plans from a Next.js lane to a single-app
  native persona lane.
- Made invalid command/responder configuration fail closed instead of
  substituting fixture mode or another persona.

**Honesty boundary**

This is a credential-scoped hackathon access-profile seam, not a production
account system. Account creation, sign-in, switching, attestation, rotation,
and secure provisioning remain unimplemented.

**Files**

- `apps/apple/Sources/VitalRelayFeature/PersonaSession.swift`
- `apps/apple/VitalRelayApp/VitalRelayApp.swift`
- `proposal/implementation-plan.md`
- `proposal/worktree-plan.md`

### Step 42 — Add a responder-authenticated pre-accept read

**Status:** Complete  
**Purpose:** Let the native responder read its own invitation without receiving
the command/device token or other candidates.

**Implemented**

- Added frozen `ResponderIncidentView` with incident kind/state/version/update
  time and exactly one linked responder invitation.
- Added
  `GET /v1/incidents/{incident_id}/responders/{responder_id}/invitation`.
- Authenticates responder ID/token before invitation lookup.
- Returns only that responder's coarse candidate snapshot; no user, health,
  wearer location, AED, route, protocol, or other responder data.
- Returns decisions through a separate privacy-bounded receipt. Declines expose
  no command coordination or other responder, while acceptance adds only the
  authenticated responder's authorized transition and accepted dispatch.
- Added service/repository plumbing plus JSON Schema/example documentation.
- Covered cross-token `401`, valid-not-invited `404`, decline/accept projection
  updates, state-version advancement, and inactive-token revocation in the
  focused real PostGIS flow.

**Verification**

```text
make test:                         88 passed, 35 deselected
Focused PostgreSQL/PostGIS flow:  passed
Schema + Pydantic example:        passed
Python compileall:                passed
```

**Files**

- `backend/src/vital_relay/domain/dispatch.py`
- `backend/src/vital_relay/application/dispatch_service.py`
- `backend/src/vital_relay/adapters/postgres_dispatch.py`
- `backend/src/vital_relay/api/dispatch.py`
- `contracts/json-schema/responder-incident-view.schema.json`
- `contracts/examples/responder-incident-view.pending.json`
- `contracts/README.md`

### Step 43 — Build the native command feature

**Status:** Complete for Slice 07  
**Purpose:** Give command a live, source-visible operational view without
creating a second incident authority or borrowing responder-only data.

**Implemented**

- Added a command/device-credential-only client for incident, timeline,
  redacted coordination, explicit dispatch start/retry, and protocol reads.
- Added bounded polling with live/stale/unavailable presentation and monotonic
  incident-version checks.
- Kept `POST /dispatch` as an explicit operator command; background polling uses
  `GET`. Empty discovery can be retried when no invitation is pending/accepted.
- Added native SwiftUI panels for incident state/version/location, candidates,
  invitations, nearest available AED, ordered timeline, and the exact fixed
  disclaimer/version/digest/sources/steps.
- Validates incident/coordination state plus accepted responder/protocol kind
  identity before rendering protocol content.
- Labels the accepted route static and responder-only; command never decodes
  the accepted route.
- Surfaces action/refresh failures and never claims notification delivery or a
  live ETA.

**Files**

- `apps/apple/Sources/VitalRelayFeature/CommandFeature.swift`
- `apps/apple/Sources/VitalRelayFeature/CommandFeatureView.swift`

### Step 44 — Build the native responder feature and acceptance gate

**Status:** Complete for Slice 07  
**Purpose:** Complete the real responder handoff from coarse invitation through
authenticated acceptance to exact static route and fixed protocol.

**Implemented**

- Added strict Swift contracts for redacted responder incident, decisions,
  accepted dispatch, static route, and fixed presentation.
- Made the responder decision decoder reject command `coordination`, including
  an explicitly null field, and consume the privacy-bounded receipt fixtures.
- Added a responder-token-only client with no device/command methods.
- Persisted the exact decision identity before sending and reused it for retry;
  no token or accepted-only location is stored with that retry.
- Added authoritative polling, offline/unavailable state, identity checks, and
  accepted-data removal after authorization loss or resolution.
- Added coarse pre-accept UI and accepted-only exact wearer/AED details,
  persisted MapKit route, and verbatim protocol metadata/content.
- Labeled the map `STATIC VENUE ROUTE`, `NOT LIVE NAVIGATION`, and
  `NO LIVE ETA`.
- Composed community, responder, and command profiles in the one native target;
  no browser product remains.

**Verification**

```text
Swift package:                    76 tests in 13 suites passed
Generic iOS Simulator app build: succeeded
```

Simulator services were unavailable for interactive launch in the sandbox, so
this gate proves compilation and focused behavior but not visual or
physical-device QA.

**Files**

- `apps/apple/Sources/VitalRelayFeature/FixedProtocolContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderDecisionStore.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderFeatureModel.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderFeatureView.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/ResponderContractCodableTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/ResponderAPIClientTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/ResponderFeatureModelTests.swift`
- `contracts/json-schema/responder-decision-receipt-view.schema.json`
- `contracts/examples/responder-decision-receipt.accept.json`
- `contracts/examples/responder-decision-receipt.decline.json`
- `docs/implementation-slices/07-native-persona-live-views.md`

**Handoff created**

The next slice can deliver an allowlisted, idempotent notification into the
existing native responder graph. Active-incident discovery, production persona
accounts, server-side assignment revocation, Apple live data/fall, and
NemoClaw/evolution remain future work.

### Step 45 — Add allowlisted responder notification delivery

**Status:** Provider-capable implementation complete for Slice 08; signed
physical-device delivery not yet verified  
**Purpose:** Carry a durable responder invitation into the existing native graph
without making APNs, a notification payload, or a deep link an authority.

**Implemented**

- Added strict push-registration and bounded notification-receipt contracts plus
  matching JSON Schemas and examples.
- Added revision 0005 with encrypted APNs registrations, one logical
  notification per invitation/channel/template, leased outbox state, and
  append-only provider attempts.
- Requires both the seeded responder credential and an explicit responder UUID
  allowlist. APNs device tokens are Fernet-encrypted at rest; a SHA-256
  fingerprint supports safe rotation without becoming a bearer credential.
- Commits the invitation and logical notification in the same dispatch
  transaction, including the next invitation after an authenticated decline.
- Added a real HTTP/2 APNs adapter with ES256 token authentication, server-owned
  topic/environment, 4 KB payload enforcement, zero expiration, and a stable
  correlation ID. The custom payload contains only schema version, kind,
  incident ID, and invitation ID.
- Retries only known pre-send/transient outcomes. Read/write timeouts, malformed
  success correlation, and abandoned leases settle as terminal `unknown` so an
  uncertain request cannot automatically produce a second visible alert.
- Separates APNs provider acceptance from display/delivery. Provider failure
  never changes authoritative incident or invitation state.
- Added responder-authenticated registration/revocation/receipt APIs, strict
  fail-closed startup configuration, readiness integration, and backend version
  0.7.0.
- Added native permission/registration handling, a device-bound Keychain access
  profile, strict notification/deep-link parsing, and exact invitation binding
  before the existing responder graph can render.

**Verification**

```text
Fast non-PostgreSQL suite:          141 passed, 38 deselected
Full real-PostgreSQL suite:          38 passed
Swift package:                       89 tests in 15 suites passed
Generic iOS Simulator app build:     succeeded with code signing disabled
Physical APNs device delivery:       not run; Apple signing/device inputs absent
```

**Files**

- `backend/migrations/versions/0005_responder_notifications.py`
- `backend/src/vital_relay/domain/notifications.py`
- `backend/src/vital_relay/application/notification_service.py`
- `backend/src/vital_relay/adapters/postgres_notifications.py`
- `backend/src/vital_relay/adapters/apns.py`
- `backend/src/vital_relay/api/notifications.py`
- `apps/apple/Sources/VitalRelayFeature/ResponderNotificationHandoff.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderNotificationRegistrationContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderNotificationAPIClient.swift`
- `apps/apple/VitalRelayApp/VitalRelayPushAppDelegate.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/VitalRelayApp/ResponderAccessProfileStore.swift`
- `docs/implementation-slices/08-allowlisted-responder-notifications.md`

**Handoff created**

The signed-device gate can now supply real Apple team/key/provisioning inputs,
register one consenting allowlisted responder installation, create one
invitation, compare the APNs correlation receipt, and open the exact native
graph. The next product code can add authenticated incident discovery and
assignment revocation without redesigning notification authority. Step 46 now
completes the revocation half of that handoff; discovery remains next.

### Step 46 — Add incident resolution and assignment revocation

**Status:** Complete for Slice 09

**Purpose:** Let command finish a real active response while ending the accepted
responder's exact-data authorization in the same durable transaction.

**Frozen command contract**

- Added `POST /v1/incidents/{incident_id}/resolution`, authenticated by the
  existing `X-Vital-Relay-Device-Token` boundary.
- The strict request is exactly `schema_version`, `resolution_id`, and
  `action`; the only actions are `close` and `handoff`.
- The client supplies no clock, device ID, assignment ID, responder ID, note,
  or state. The server owns one receipt/transition/resolution timestamp.
- The receipt is exactly schema version, resolution ID, accepted or
  already-processed status, action, resulting `IncidentView`,
  `IncidentTransition`, and server receipt time.
- An exact retry returns HTTP `200` with the original accepted snapshot and
  timestamp. Conflicting resolution-ID reuse and a new losing resolution return
  deterministic `409` outcomes without another state change.

**Durable transaction and privacy boundary**

- Added Alembic revision `0006_incident_resolution` with append-only resolution
  receipts, transition-to-resolution linkage, and append-only responder
  assignment revocations.
- One serialized PostgreSQL transaction accepts only `response_active`, updates
  the incident to `resolved`, increments its state version, appends the typed
  transition and ordered timeline entry, records the exact receipt, and links
  one revocation to the accepted assignment.
- Exact retries, content conflicts, losing races, and restart behavior use the
  durable receipt rather than process memory.
- The concurrent close-versus-handoff gate exposed and fixed a pooled psycopg
  setup transaction: the UTC connection hook now commits its own `SET TIME
  ZONE` work before callers apply an isolation level on a fresh connection.
- Command incident, timeline, and immutable protocol reads remain available for
  audit. The frozen command dispatch projection remains active-state-only and
  does not fabricate resolved coordination data.
- The revoked responder's exact accepted-dispatch and protocol endpoints return
  a privacy-preserving `404`; invalid or cross-responder credentials remain
  `401`. The responder's own redacted incident projection can report the
  authoritative resolved state.

**Native command and responder behavior**

- Added strict Swift resolution request/receipt contracts, including unknown-key
  rejection and linked incident/action/transition/sequence/timestamp checks.
- Added consequence-confirmed `Close incident` and `Record handoff` command
  actions. A failed retry reuses the same pending resolution identity for the
  same action and blocks a competing action while the result is uncertain.
- On success, command adopts the resolved incident, removes live coordination,
  and can refresh its incident/timeline/protocol audit without requiring a
  resolved dispatch projection.
- Responder accepted-only loading now rechecks the redacted incident after route
  and protocol reads before committing exact data. A resolution race therefore
  cannot briefly restore revoked coordinates.
- A revoked exact read immediately wipes exact location, static route, and
  protocol, then reconciles through the redacted responder incident; it does not
  wait for a later polling interval to remove sensitive state.

**Verification**

```text
Resolution JSON Schema/Python examples: passed
Fast non-PostgreSQL suite:          143 passed, 41 deselected
Full real-PostgreSQL suite:         41 passed
Swift package:                      93 tests in 16 suites passed
Generic iOS Simulator app build:   succeeded with code signing disabled
Physical APNs device delivery:     not run; Apple signing/device inputs absent
```

Testing remains intentionally narrow for the hackathon. The focused evidence
targets exact contract parity, concurrent idempotency/transition behavior,
append-only database linkage, post-resolution authorization, and immediate
native exact-data teardown.

**Files**

- `contracts/json-schema/incident-resolution-request.schema.json`
- `contracts/json-schema/incident-resolution-receipt.schema.json`
- `contracts/examples/incident-resolution-request.close.json`
- `contracts/examples/incident-resolution-receipt.close.json`
- `backend/migrations/versions/0006_incident_resolution.py`
- `pyproject.toml`
- `backend/src/vital_relay/__init__.py`
- `backend/src/vital_relay/domain/incidents.py`
- `backend/src/vital_relay/application/incident_service.py`
- `backend/src/vital_relay/adapters/postgres_incidents.py`
- `backend/src/vital_relay/adapters/postgres_dispatch.py`
- `backend/src/vital_relay/adapters/postgres_protocols.py`
- `backend/src/vital_relay/api/incidents.py`
- `backend/src/vital_relay/main.py`
- `backend/src/vital_relay/persistence/database.py`
- `backend/src/vital_relay/persistence/models.py`
- `backend/tests/integration/test_health_api.py`
- `backend/tests/unit/test_incident_resolution.py`
- `backend/tests/postgres/test_slice09_resolution.py`
- `apps/apple/Sources/VitalRelayFeature/IncidentResolutionContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/CommandFeature.swift`
- `apps/apple/Sources/VitalRelayFeature/CommandFeatureView.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderFeatureModel.swift`
- `apps/apple/Sources/VitalRelayFeature/ResponderFeatureView.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/CommandResolutionAPIClientTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/ResponderFeatureModelTests.swift`
- `contracts/README.md`
- `README.md`
- `proposal/implementation-plan.md`
- `proposal/ios-frontend-implementation-plan.md`
- `docs/implementation-slices/09-incident-resolution-assignment-revocation.md`

**Known limitations**

- `handoff` is an audited terminal action, not proof of external recipient
  acknowledgment or transfer.
- Resolution is terminal; reopening, reassignment, arrival, and correction are
  outside v1.
- Native live profiles still require manually configured incident IDs and seeded
  credentials.
- Signed physical-device APNs display/open remains unverified because Apple
  provisioning, provider keys, a device, and a reachable HTTPS environment are
  external inputs.

**Handoff created**

The next code slice can replace seeded launch profiles with authenticated
persona/session enrollment and role-scoped active-incident discovery. Apple
live health/fall work and NemoClaw sandboxing with Docker fallback can proceed
in parallel without changing the frozen resolution/revocation boundary.

Step 47 completes this handoff. Manual incident IDs and seeded raw credentials
remain only in explicit QA launch paths, not the normal app entry flow.

### Step 47 — Add authenticated persona sessions and role-scoped discovery

**Status:** Complete for Slice 10

**Purpose:** Replace launch-time persona labels and manual incident IDs with a
durable server identity, secure native session lifecycle, and the minimum
authorized incident locator set for each role.

**Frozen contracts and HTTP boundary**

- Added strict shared contracts for `PersonaAccount`, session create/current/
  rotate/revoke receipts, and privacy-minimal active-incident lists.
- Added `POST /v1/persona-sessions`,
  `GET /v1/persona-sessions/current`,
  `POST /v1/persona-sessions/{session_id}/rotation`, and
  `DELETE /v1/persona-sessions/{session_id}` with separate enrollment, access,
  and refresh credential transports.
- Added community, responder, and command discovery endpoints. Community sees
  only its own active incidents, responder sees only its pending invitation or
  unrevoked accepted assignment, and command sees active incidents in its demo
  scope.
- Discovery rows contain only incident kind/state/version/update time and the
  caller's responder invitation locator when applicable. They contain no health
  context, coordinates, route, protocol, account subject, or other responder.

**Durable authentication and authorization**

- Added Alembic revision `0007_persona_sessions`, `persona_accounts`, and
  installation-bound `persona_sessions` with subject-shape constraints,
  active-account/session indexes, and hashed-only enrollment/access/refresh
  secret storage.
- Access tokens last 15 minutes; refresh tokens last 24 hours and remain bounded
  by the active demo scope. Product APIs recheck session, account, persona,
  scope, expiry, ownership, and responder activity on every request.
- A valid wrong-role session receives `403 persona_not_authorized`; community
  cross-owner and responder cross-assignment reads preserve resource privacy
  with `404`; invalid, expired, or revoked authority returns `401`.
- The existing community/command and responder client headers now carry issued
  session access tokens. Raw configured command/responder tokens work only
  behind the explicit `create_app(legacy_persona_auth=True)` test/QA seam.
- Added operator provisioning for all three personas. The CLI reveals an
  enrollment bootstrap code once per provision/rotation; it remains usable
  until the operator rotates it. Response-network seeding creates matching
  responder accounts, and the configured command bootstrap creates the command
  account deterministically. Product startup rejects a command bootstrap that
  is outside the same 43–256-character URL-safe contract accepted by enrollment.
- PostgreSQL serializes concurrent access rotation; multiple successful callers
  may receive tokens, but only the last token remains valid and no second
  refresh/session is created.

**Native authenticated experience**

- The normal app launch now restores the one device-only Keychain session or
  presents secure enrollment for backend URL, expected persona, and operator
  code. The code is never saved; a mismatched persona receipt is revoked.
- A stable non-secret installation UUID is stored separately. Session restore
  validates current authority, rotates an expired access token once, and then
  loads role-scoped active discovery.
- Selecting an authorized row composes the existing typed community, responder,
  or command client with the session access token. Community can also enter
  monitoring with an empty incident list; real SOS still fails closed without
  a live location provider.
- Any selected-graph `401` synchronously destroys that graph and responder
  exact state before rotation, returns through authenticated discovery, and
  requires a fresh authorized selection instead of hot-swapping credentials.
- The Swift session actor shares one in-flight rotation per session so database
  last-token-wins behavior cannot leave Keychain holding an older invalid token.
- Logout/switch tears down graph artifacts, deletes the captured local session
  before awaiting network work, and then attempts best-effort server revocation.
  This avoids actor-reentrancy races that could erase a newly enrolled session.
- APNs and deep-link locators must match the active responder session's exact
  discovered incident/invitation. Legacy responder profiles are reachable only
  from the explicit QA launch argument.

**Verification**

```text
Persona contract/domain focus:       32 passed
Fast non-PostgreSQL suite:           176 passed, 43 deselected
Full real-PostgreSQL suite:           43 passed
Swift package:                        98 tests in 17 suites passed
Generic iOS Simulator app build:     succeeded with code signing disabled
Python compile and diff whitespace:  passed
Physical Keychain/APNs evidence:     not run; signing/device inputs absent
```

Testing remains intentionally focused for the hackathon. The PostgreSQL gates
exercise lifecycle, concurrent rotation, role/ownership denial, discovery, and
earlier protected surfaces; Swift gates exercise contract/header separation,
secure-store lifecycle, discovery, graph invalidation, and the integrated app
composition.

**Primary files**

- `contracts/json-schema/persona-*.schema.json`
- `contracts/json-schema/active-incident-*.schema.json`
- `contracts/examples/persona-*.json`
- `contracts/examples/active-incident-list.*.json`
- `backend/migrations/versions/0007_persona_sessions.py`
- `backend/src/vital_relay/domain/persona_sessions.py`
- `backend/src/vital_relay/application/persona_session_service.py`
- `backend/src/vital_relay/adapters/postgres_persona_sessions.py`
- `backend/src/vital_relay/api/persona_auth.py`
- `backend/src/vital_relay/api/persona_sessions.py`
- `backend/src/vital_relay/main.py`
- `backend/src/vital_relay/persistence/cli.py`
- `.env.example`
- `backend/tests/contract/test_persona_session_contract.py`
- `backend/tests/unit/test_persona_sessions.py`
- `backend/tests/postgres/test_persona_sessions_and_discovery.py`
- `apps/apple/Sources/VitalRelayFeature/PersonaSessionContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/PersonaSessionAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/PersonaAccessViews.swift`
- `apps/apple/VitalRelayApp/PersonaSessionStore.swift`
- `apps/apple/VitalRelayApp/VitalRelayApp.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/PersonaSessionTests.swift`
- `docs/implementation-slices/10-authenticated-persona-sessions-active-incident-discovery.md`

**Known limitations**

- Enrollment is operator provisioned and its bootstrap code remains reusable
  until rotated; there is no public sign-up, recovery, attestation, or remote
  device administration.
- One installation stores one persona session. Physical Keychain behavior and
  signed-device APNs display/open remain unverified.
- Best-effort logout can leave a server session active until its bounded expiry
  if the backend is unreachable. Responder logout also does not yet unregister
  a prior APNs installation, although a locator cannot authorize protected data.
- Live HealthKit/Core Motion/Core Location and the entitled fall callback are
  not implemented. Without a live or explicit QA location source, the normal
  community graph cannot create a real manual SOS.

**Handoff created**

The next product slice can use the authenticated community account and stable
installation identity for real HealthKit capability/metric ingestion, live Core
Location manual SOS, and a genuine entitled Apple fall callback. NemoClaw with
Docker fallback can proceed in parallel without receiving persona credentials
or changing deterministic incident authority.

### Step 48 — Add live Core Location to authenticated manual SOS

**Status:** Code complete for Slice 11A; signed physical-device evidence pending

**Purpose:** Replace the normal authenticated community graph's unavailable
location source with a real, foreground-only iPhone location acquisition so a
deliberate native SOS can cross the existing non-simulated incident boundary.

**Implemented**

- Added a main-actor-confined `CLLocationManagerDelegate` adapter that requests
  When In Use authorization and one precise location only after the user
  completes the SOS hold. It does not request Always access, background modes,
  or continuous tracking.
- Added the required `NSLocationWhenInUseUsageDescription` with SOS-only copy.
- Injected the Core Location provider only into authenticated community
  composition. The explicit fixed venue provider remains QA-only, and the
  unavailable provider remains the default fail-closed seam.
- Added a platform-neutral first-party quality policy: at most 15 seconds old,
  at most 100 meters horizontal uncertainty, at most 5 seconds future skew, and
  a 12-second location-fix timeout after authorization.
- Rejects disabled services, denied/restricted authorization, reduced accuracy,
  invalid or negative values, stale/future/inaccurate fixes, provider failure,
  and software-simulated Core Location. No rejected fix creates or posts an
  event.
- Preserves the deliberate hold completion as event `observed_at` and the
  independent `CLLocation.timestamp` as `location.captured_at`.
- Added visible native “Getting current location and sending SOS…” progress and
  actionable error descriptions while keeping the authoritative incident scene
  unchanged until the backend accepts the event.
- Made the manual-SOS UI action model-owned. Session invalidation, logout,
  profile switching, or graph teardown cancels pending acquisition/submission.
  Unambiguous pre-transport cancellation clears the newly persisted exact
  location. Normal ambiguous transport failures retain the exact retry
  envelope; explicit logout/switch clears all departing-session artifacts and
  later relies on authenticated incident discovery if the server accepted it.
- Added a synchronous sweep backed by a durable incident-store registry plus
  actor invalidation, so pending exact coordinates are cleared and racing old
  providers cannot recreate their keys. A cold launch can sweep again after a
  crash. Stale async composition is generation/session guarded and cannot
  resurrect a departed credential graph.
- Definitive `400`/`403`/`404`/`409`/`410`/`422` ingest rejection clears the
  doomed envelope; ambiguous cancellation/transport/server outcomes retain the
  exact idempotent request.

**Verification**

```text
Swift package:                 113 tests in 21 suites passed
Location policy focus:          4 tests passed
Session cancellation focus:     1 test passed
Terminal retry/privacy focus:   2 tests passed
Generic unsigned iOS build:     succeeded for arm64 iOS 18
Info.plist validation:          passed
Diff whitespace validation:     passed
Signed physical iPhone flow:    not run; device/signing/HTTPS inputs external
```

Testing remains intentionally focused for the hackathon. New tests cover the
pure safety policy and the session-teardown cancellation race; existing tests
continue to prove zero ingest without a valid location and exact event reuse
after an ambiguous transport failure.

**Primary files**

- `apps/apple/Sources/VitalRelayFeature/CoreLocationIncidentLocationProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentLocationQualityPolicy.swift`
- `apps/apple/Sources/VitalRelayFeature/IncidentRequestStore.swift`
- `apps/apple/Sources/VitalRelayFeature/LiveVitalRelayDataProvider.swift`
- `apps/apple/Sources/VitalRelayFeature/AppModel.swift`
- `apps/apple/Sources/VitalRelayFeature/MonitoringView.swift`
- `apps/apple/Sources/VitalRelayFeature/VitalRelayRootView.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/VitalRelay-Info.plist`
- `apps/apple/Tests/VitalRelayFeatureTests/IncidentLocationQualityPolicyTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/AppModelTests.swift`
- `docs/implementation-slices/11a-live-core-location-manual-sos.md`

**Known limitations**

- This is one incident-opening iPhone coordinate, not continuous wearer
  tracking or live responder routing.
- Reduced-accuracy users must enable Precise Location in Settings; temporary
  full-accuracy authorization is not requested in Slice 11A.
- A signed physical iPhone has not yet supplied the authorization/GPS/API
  evidence required to claim live-device operation. Simulator software
  locations are deliberately rejected as real SOS inputs.
- The tighter quality policy is first-party client enforcement; the backend
  still enforces its broader finite/bounded wire contract.
- The existing namespaced UserDefaults idempotency store temporarily holds a
  pending exact SOS envelope. It is synchronously swept on logout and clears on
  unambiguous cancellation; device-only encrypted pending-envelope storage
  remains a hardening item.
- HealthKit was not part of Slice 11A and is implemented separately in Slice
  11B below. Core Motion, WatchConnectivity, live Watch data, and the genuine
  entitled Apple fall callback remain unimplemented.

**Handoff created**

Slice 11B can use the authenticated community session and stable installation
identity to discover supported HealthKit types and ingest all visible
allowlisted scalar values plus honest capability states. Health remains
context-only and cannot create, suppress, or advance an emergency transition.

### Step 49 — Add real foreground HealthKit scalar ingestion

**Status:** Code and focused automated verification complete and merged into
`main` at `773a0a5`; signed physical-iPhone evidence pending

**Purpose:** Connect the authenticated community graph to real, read-only
HealthKit stored context without introducing fake data, a live-Watch claim, or
any dependency between health values and incident escalation.

**Implemented**

- Added a frozen 30-type scalar registry: 26 standard heart, respiratory,
  sleeping-measurement, activity, mobility, and stored-fall-count quantities,
  plus body temperature, blood glucose, body mass, and BMI behind a separate
  off-by-default connected-source toggle. The coordinator rejects expanded
  samples or selected expanded capabilities when that scope is off.
- Enabled the HealthKit target capability and added an honest
  `NSHealthShareUsageDescription`. The app requests read access only; it does
  not request write, clinical-record, background-delivery, or workout-writing
  access.
- Added a real iPhone `HKHealthStore` source that presents authorization only
  after the authenticated community user explicitly chooses Connect/Refresh,
  then queries the latest visible stored quantity sample for each selected
  type. The simulator reports every type as unsupported and produces no fake
  value.
- Normalizes values to the frozen canonical units, converts HealthKit fractions
  to wire percentages where required, and preserves the sample end time, source
  name, source bundle, and device model.
- Reports all 30 capability states as `available`, `requested_no_sample`,
  `not_requested`, `unsupported`, or bounded `error`. An empty HealthKit read is
  never labeled `permission_denied` because read denial and no visible sample
  are not distinguishable.
- Added strict native Codable contracts and an authenticated URLSession client
  for metric batches, capability batches, and snapshots. The existing community
  access token is sent only in `X-Vital-Relay-Device-Token`; user and stable
  installation identity are bound into every request.
- Derives a deterministic per-account metric UUID from registry version,
  metric kind, and HealthKit sample UUID, so exact samples deduplicate without
  exposing the raw HealthKit identifier as a global cross-account identity.
- Uploads an optional metric batch, complete capability batch, and immutable
  snapshot. The first completed sync in one coordinator session uses
  `monitoring_started`; later explicit syncs use `manual_refresh`. Ambiguous
  retries preserve the exact batches, snapshot ID, and capture reason while the
  selected consent scope is unchanged. Changing that scope abandons the pending
  client envelope and recollects instead of finishing the prior optional upload.
- Updated snapshot selection so a newer non-available capability suppresses an
  older metric from the same source/acquisition path in future snapshots. This
  honors expanded-context opt-out and visibility loss without rewriting an
  already-created immutable snapshot.
- Composes the coordinator only inside an authenticated community feature
  graph. A `401` invalidates the session; logout, persona switching, or graph
  teardown cancels the owned task, clears the view state, and prevents a late
  result from repopulating a departed account.
- Added a native Apple Health Context card with an explicit Connect/Refresh
  action, optional connected-source toggle, timestamp/source labels for every
  visible scalar, honest empty/capability summaries, and persistent context-only
  safety copy. Stored heart rate is labeled stored, never live or Watch-connected.
- Kept HealthKit sync and manual SOS as independent tasks. Health authorization,
  latency, missing samples, and ingestion failure cannot disable, delay, open,
  suppress, or advance an incident.

**Verification status**

```text
Swift package:                 125 tests in 26 suites passed
Python health context:        13 focused tests passed
Generic unsigned iOS build:   succeeded for a generic iOS device target
Plist/entitlement/project:    plutil validation passed
Diff whitespace validation:   passed
Signed physical iPhone flow:  not run; device/signing/HTTPS inputs external
```

The focused gate covers registry and contract invariants, per-user deterministic
metric identity, upload ordering, exact transient retry identity, authenticated
endpoint receipt validation, honest no-sample/unsupported presentation, and
session-teardown cancellation. The generic build compiles the real HealthKit
adapter, but automated simulator or unsigned-build success is not evidence that
real HealthKit authorization or samples were read.

**Primary files**

- `apps/apple/Sources/VitalRelayFeature/HealthMetricRegistry.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthKitScalarSource.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthIngestionContracts.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthIngestionAPIClient.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthContextCoordinator.swift`
- `apps/apple/Sources/VitalRelayFeature/HealthContextViewState.swift`
- `apps/apple/Sources/VitalRelayFeature/AppModel.swift`
- `apps/apple/Sources/VitalRelayFeature/MonitoringView.swift`
- `apps/apple/Sources/VitalRelayFeature/VitalRelayRootView.swift`
- `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`
- `apps/apple/VitalRelay-Info.plist`
- `apps/apple/VitalRelay.entitlements`
- `apps/apple/VitalRelay.xcodeproj/project.pbxproj`
- `backend/src/vital_relay/application/health_context.py`
- `backend/tests/unit/test_health_snapshot_service.py`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthMetricRegistryTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthContextCoordinatorTests.swift`
- `apps/apple/Tests/VitalRelayFeatureTests/HealthIngestionAPIClientTests.swift`
- `docs/implementation-slices/11b-live-healthkit-ingestion.md`

**Known limitations**

- There is no watchOS target, `HKWorkoutSession`, live workout stream,
  WatchConnectivity path, or claim that a Watch is connected. Slice 11B reads
  the latest stored iPhone-visible quantity sample per type.
- The Apple fall-detection entitlement/callback and genuine non-simulated fall
  event remain Slice 11C. The stored fall-count quantity is not a fall event.
- There is no anchored/observer query, background delivery, or automatic sync;
  collection is an explicit foreground action.
- Structured sleep stages, ECG/workout records, activity summaries, and paired
  blood-pressure samples remain deferred until each has a typed contract and
  representative fixture. Raw ECG voltage and continuous raw motion stay out
  of the backend.
- Core Motion/pedometer-derived context and live routing are not added here.
- A signed physical iPhone with a consenting community account, visible samples,
  Apple Developer signing, and a reachable HTTPS backend is still required for
  end-to-end device evidence.

**Handoff created**

Slice 11C can build on the authenticated community graph and frozen real
wearable-event boundary to ingest a genuine Apple fall callback. It must expose
entitlement/readiness status, deduplicate callbacks, preserve manual SOS while
approval is pending, and never manufacture a fall from stored fall history or
other health context.

---

### Step 50 — Connect the live sandboxed coordination agent

**Status:** Code and automated verification complete; external runtime evidence
open
**Purpose:** Join the Agent A2 policy/tool boundary to durable incident services
through one real LangChain Deep Agent worker without adding a deterministic
coordinator.

**Implemented**

- Added command-persona Bearer APIs to start, get, and list durable agent runs.
  A first execution returns `201`; an exact terminal retry returns `200` without
  invoking a second model.
- Added PostgreSQL revision 0008 for active policy pointers, leased agent runs,
  pinned per-tool budgets, append-only tool audits, and durable mutation
  idempotency.
- Creates and commits the `running` row before model execution, allows only one
  live run per scope/incident, and reconciles a crashed worker only after its
  durable lease.
- Bounds the tool capability by that same lease and uses host receipt time for
  completion, preventing reclaimed and late workers from concurrently holding
  mutation authority.
- Rechecks capability and database-time run authority after a per-run
  PostgreSQL fence, enforces pinned durable total/mutating/per-tool budgets, and
  holds terminalization until each full proxy invocation and audit has drained.
- Added the fixed-argv NemoClaw worker protocol with bounded stdin/stdout,
  timeout handling, normalized results, and no shell or raw stderr propagation.
- Added the private authenticated HTTP transport for the five Agent A2 tools.
  The worker validates NemoClaw's root-owned CONNECT-proxy/CA inputs, disables
  ambient proxy discovery and redirects, and bounds request/response bytes and
  schemas.
- Isolated private tool calls on a bounded executor so long-lived agent-run
  requests cannot starve their own proxy, and prevented cancelled HTTP waiters
  from leaking late worker exceptions through the event loop.
- Launches the absolute NemoClaw CLI path with a minimal allowlisted environment;
  no database URL, provider credential, signing key, or `VITAL_RELAY_*` setting
  is inherited by that child process.
- Composed the proxy from scope-bound incident, dispatch, and protocol
  application services; persona credentials and the signing key never enter
  the sandbox.
- Enforced state-specific grants: escalating can coordinate dispatch;
  response-active can read the fixed protocol; neither can resolve or hand off.
- Added canonical policy-sidecar verification and monotonic command-authorized
  activation. Startup refuses an artifact/pointer mismatch instead of silently
  replacing a promoted policy.
- Made NemoClaw the sole selectable live substrate. Docker remains an unclaimed
  alternate containment smoke profile and is never automatically invoked.
- Preserved the agentic decision boundary: every runner/model/tool/policy/
  sandbox failure terminates as durable `manual_required`; no ordered software
  fallback makes a coordination decision.
- Reconstructs terminal tool traces from authenticated, append-only host audit
  hashes inside the fenced finish transaction; sandbox-supplied evidence and
  credential-shaped output are never persisted as authority. All model-authored
  conclusion prose is replaced by a reviewed host summary derived from the
  authenticated read/mutation effects.

**Verification**

- Focused Agent A3 service/API/sandbox/transport/control-plane tests passed.
- The complete fast backend suite passed 381 tests with the real optional agent
  dependencies; all 44 PostgreSQL-marked tests passed after
  integration, including concurrent starts, restart reconciliation, late-result
  rejection, active-policy CAS, audit immutability, and replay/in-doubt paths.
- Static sandbox policy, Python compilation, and whitespace checks are part of
  the final worktree gate.

**Primary files**

- `backend/src/vital_relay/application/agent_service.py`
- `backend/src/vital_relay/application/agent_control.py`
- `backend/src/vital_relay/adapters/postgres_agent_control.py`
- `backend/src/vital_relay/agent/sandbox.py`
- `backend/src/vital_relay/agent/worker.py`
- `backend/src/vital_relay/agent/http_tools.py`
- `backend/src/vital_relay/api/agent_runs.py`
- `backend/src/vital_relay/api/agent_tools.py`
- `backend/migrations/versions/0008_agent_control_plane.py`
- `infrastructure/nemoclaw/presets/vital-relay-tool-proxy.yaml`
- `docs/implementation-slices/agent-a3-live-sandboxed-coordination.md`

**Known limitations**

- The selected machine has not yet supplied live NemoClaw onboarding, trusted
  internal TLS, a tool-capable vLLM model, or protected-file/unlisted-egress
  denial evidence.
- Deep Agents' Restricted baseline still includes GitHub/package routes. The
  complete base/effective policy replacement and post-lifecycle attestation is
  an external activation gate; the checked-in preset is only a route fragment.
- The NemoClaw managed image uses Python 3.13 while Vital Relay requires 3.14;
  a reviewed self-contained 3.14 runtime must be staged, host-manifest-attested,
  and made read-only to the sandbox process.
- The stock `managed_inference` binary list does not authorize that staged
  interpreter. The retrieved effective policy must include the exact canonical
  Python 3.14 executable in both inference and tool-proxy entries, with no broad
  Python wildcard or unrelated egress.
- The Docker profile still runs the earlier smoke entry point and has no Agent
  A3 tool-proxy route; production composition deliberately rejects it.
- WT-60 has no live candidate activation endpoint. Policy promotion remains an
  explicit durable adapter operation requiring separately reviewed evidence.
- Close, handoff, responder decisions, arbitrary notifications, exact
  locations, and generated medical instructions remain outside agent authority.

**Handoff created**

Agent A3.1 can capture real sandbox/model/denial/restart evidence without
changing the production contracts. After that gate, the next code feature is a
transactional WT-60 promotion bridge that binds a protected signed evaluation
report and operator approval to the existing active-policy CAS.

---

### Step 51 — Establish the native Apple Watch transport foundation

**Status:** Complete and merged in `1faa331` (feature `084ac53`); physical
device and entitlement evidence remain external.

**Purpose:** Add the real watchOS companion and reliable transport boundary
needed by genuine fall callbacks and live wearable telemetry.

**Implemented**

- Added and embedded a native watchOS 11 app with the fall-detection and
  read-only HealthKit capability declarations.
- Activated the singleton `WCSession` during both iPhone and Watch startup.
- Added versioned/idempotent critical-event, telemetry, and acknowledgement
  envelopes.
- Added durable critical inbox/outbox storage, receiver deduplication,
  acknowledgement-driven removal, and capped retry after transfer failure.
- Added bounded latest-per-metric telemetry coalescing through
  `updateApplicationContext` and readiness derived only from observable session,
  pairing/install, reachability, and storage facts.

**Verification:** 10 focused Watch transport tests passed; generic unsigned
watchOS and embedded iOS builds succeeded; plist/project validation passed.

**Primary files:** `apps/apple/VitalRelayWatchApp/`,
`apps/apple/Sources/VitalRelayWatchTransport/`, and
`docs/implementation-slices/apple-watch-foundation.md`.

**Known limitations:** No Apple entitlement approval, signed device pair,
genuine fall callback, workout session, or live Watch HealthKit sample has been
claimed. The transport never manufactures a fall or placeholder value.

**Handoff created:** Wave 2 can attach genuine `CMFallDetectionManager`
callbacks to the durable critical channel and real HealthKit/Core Motion
observations to the separate telemetry channel.

---

### Step 52 — Add live responder routing with truthful static fallback

**Status:** Complete and merged in `d322bc5` (feature `7e51ee3`); external
provider evidence remains pending.

**Purpose:** Persist a real, source-labelled walking route when available while
keeping the initial venue-coordinate route useful and honestly labelled.

**Implemented**

- Expanded the durable route with source, provider, fallback reason, ordered
  geometry, distance, and ETA.
- Added a real Mapbox Directions-compatible HTTPS adapter with bounded total
  timeout, response size, schema, totals, waypoint, and geometry validation.
- Added `LiveFirstRoutingProvider`; unavailable, timed-out, or invalid provider
  responses become explicit `static_fallback` results.
- Persisted the validated route in both the decision snapshot and responder
  assignment, while retaining strict legacy static-record compatibility.
- Updated the native responder map to draw full geometry and distinguish live
  directions from `NOT LIVE NAVIGATION` static estimates.

**Verification:** 390 fast backend tests passed with 3 skips and 45 deselected;
48 focused route/contract tests, 3 PostGIS dispatch tests, and 127 Swift tests
passed in the slice worktree.

**Primary files:** `backend/src/vital_relay/adapters/live_routing.py`,
`backend/src/vital_relay/adapters/composite_routing.py`, route domain/contracts,
the native responder view, and `docs/implementation-slices/route-01-live-routing.md`.

**Known limitations:** No external Mapbox token was supplied, so live HTTP was
verified at the intercepted real request boundary. The client is a persisted
route preview, not continuously updating turn-by-turn navigation.

**Handoff created:** Shared composition can enable live routing only from
complete server-owned configuration, otherwise use the explicit fallback and
close the owned HTTP client on every shutdown/failure path.

---

### Step 53 — Freeze immutable ACE operational-playbook contracts

**Status:** Complete and merged in `aea2067` (feature `36428ec`).

**Purpose:** Establish the typed, content-addressed boundary for offline
reflection and inherited playbook mutation without admitting live, protected,
final-test, or arbitrary generated content.

**Implemented**

- Added frozen strict contracts for playbooks, items, applicability,
  provenance, review manifests, role/model identities, deltas, and selected
  Generator context.
- Added separate draft validation and canonical SHA-256 construction so every
  top-level and nested artifact revalidates its exact hash.
- Restricted operational instructions to immutable host templates and typed
  parameters; arbitrary model-authored prose has no injectable field.
- Added bounded `ADD`, `REFINE`, `TAG`, and `DEPRECATE` operations with exact
  parent/item binding and optimistic conflict detection.
- Added a detached review manifest and pinned five-item baseline playbook.
- Rejected live incidents, protected validation, and final-test evidence at the
  provenance boundary.

**Verification:** 91 focused ACE contract tests passed, covering nested
tampering, hash/context forgery, provenance partitions, review binding,
operation limits, identities, membership, applicability, and context budgets.

**Primary files:** `backend/src/vital_relay/evolution/ace/`,
`agents/playbooks/baseline/`, and
`docs/implementation-slices/ace-00-contracts.md`.

**Known limitations:** Host merge/storage, model reflection/curation, promotion,
rollback, and production Generator injection remain Wave 2 work.

**Handoff created:** Independent Wave 2 lanes can implement merge/store,
Reflector/Curator, and Generator selection without changing the frozen artifact
boundary.

---

### Step 54 — Add the operator-selected operational Docker sandbox

**Status:** Complete and merged in `6906e8f` (feature `97034b2`); live
Docker/model evidence remains external.

**Purpose:** Add a real Docker execution substrate behind Agent A3 without
turning it into an automatic retry or fallback for NemoClaw.

**Implemented**

- Added mutually exclusive `ProcessSandboxSelection` and a factory that
  constructs only the operator-selected NemoClaw or Docker runner.
- Added a content-locked, unprivileged, read-only Docker worker with fixed
  resource limits, no host mounts or ambient environment, and internal-only
  networking.
- Added dedicated fixed-route vLLM and tool gateways while retaining host
  authority for authentication, budgets, idempotency, and audit.
- Added exact Compose, Dockerfile, dependency, worker-source, image-ID, and
  rendered-graph verification with digest-addressed build staging.
- Added startup validation, normalized failures, exact-project cleanup,
  retryable custody, monotonic evidence, and explicit no-cross-sandbox retry.

**Verification:** Focused runner, transport, profile, gateway, containment,
startup, provenance, drift, cleanup, and no-fallback tests passed; static
source/profile and dependency-lock checks passed.

**Primary files:** `backend/src/vital_relay/agent/sandbox.py`, agent worker and
transport files, `infrastructure/docker-agent/`, and
`docs/implementation-slices/agent-a31-operational-docker.md`.

**Known limitations:** Docker/Compose, NemoClaw/OpenShell, and live vLLM were
unavailable on the implementation host. No image build, real model result,
live containment probe, or corroborated file/egress denial is claimed.

**Handoff created:** Shared composition can validate exactly one selected
runner, retain it for application lifetime, and publish its startup and cleanup
evidence.

---

### Step 55 — Wire live routing and explicit sandbox selection into runtime

**Status:** Implemented and merge-reviewed on
`codex/wave1-runtime-integration`; final combined gate is in progress.

**Purpose:** Activate the merged routing and Docker capabilities through one
fail-closed production composition and documented operator configuration.

**Implemented**

- Added strict optional Mapbox configuration. No token selects the truthful
  static fallback; malformed or partial configuration fails startup.
- Composed `LiveFirstRoutingProvider` into PostgreSQL dispatch and closes its
  owned client on normal teardown and failed construction.
- Added exact `nemoclaw`/`docker` selection with no default, probing, or
  automatic fallback; even blank settings for the unselected runtime fail.
- Docker uses only the canonical Compose file and fixed gateway routes;
  NemoClaw retains its exact managed routes.
- Constructs the selected runner, validates it exactly once before
  `AgentRunService`, and propagates failure without trying another sandbox.
- Drains the tool pool before runner cleanup and continues provider/database
  cleanup without masking the primary error.
- Publishes startup, cleanup, and monotonic cleanup-history evidence. Unresolved
  custody retains a retry callable bound to that exact runner, including on a
  composition exception.
- Documented the mutually exclusive environment matrix and Docker-reachable
  host bind requirements.

**Verification:** 59 focused routing-runtime tests passed before Docker
integration. The final fast backend gate passed 587 tests with 3 expected skips
and 45 PostgreSQL deselections. Apple verification passed 127 tests in 26 Swift
Testing suites plus 10 Watch transport XCTest cases. Independent review found
no merge blocker.

**Primary files:** `backend/src/vital_relay/main.py`,
`backend/src/vital_relay/adapters/live_routing.py`,
`backend/src/vital_relay/agent/sandbox.py`, the focused unit tests,
`.env.example`, `README.md`, and `infrastructure/docker-agent/README.md`.

**Known limitations:** Runtime wiring does not substitute for live Mapbox,
Docker, NemoClaw, vLLM, TLS, or physical-device evidence. Docker host services
must bind only as broadly as needed for `host.docker.internal`, with local
firewall containment.

**Handoff created:** Wave 2 can run six independent lanes: real Apple fall, live
Watch health, ACE merge/store, Reflector/Curator, candidate bundles, and
Generator context.

---

### Step 56 — Expand and merge the protected evolution scenario catalog

**Status:** Complete and merged into `main` at `4405e8a` (feature `b2ce7a6`)
after explicit acceptance of the trusted-offline-host boundary.

**Purpose:** Replace the single-case laboratory fixture with deterministic
development, protected-validation, and cadence-gated final partitions suitable
for comparing static, ACE, and typed policy candidates.

**Implemented**

- Added 12 complete development cases, 6 protected cases exposed only through
  opaque input aliases, and 4 final cases absent from the ordinary catalog.
- Added observable world/effect scoring, hard safety gates, fixed protocol hash
  checks, bounded timing/fallback expectations, and three-repeat reproducibility.
- Added a separate final-test authority with atomic cadence consumption,
  candidate-bound one-use sessions, host-recomputed observations/scores, and
  signed evidence/report issuance.
- Removed caller-supplied scores/observations from final issuance and consumes a
  final capability before scoring so failure cannot create a replay oracle.
- Restricted adaptation feedback to development reports; protected/final
  metrics remain selection evidence only.
- Added deterministic protected-asset regeneration and complete artifact digest
  binding.

**Accepted trust boundary:** The evaluator, authorities, cadence ledger,
protected assets, and signing material run only in one trusted offline host
process. Candidate/model/mutation code must run in a separate sandbox process.
Arbitrary Python already executing inside the evaluator process could inspect
closures; that is treated as trusted-host compromise. A stronger deployment
must move signing/issuance into a separate process or service.

**Verification:** 36 focused catalog tests and 47 complete evolution tests
passed in the feature worktree; the full non-PostgreSQL gate passed with 3
expected skips. Independent trusted-boundary review found no supported
candidate-reachable cadence, score-substitution, oracle, replay, or signing
bypass.

**Primary files:** `backend/src/vital_relay/evolution/evaluator.py`,
`backend/src/vital_relay/evolution/scenario.py`, `scenarios/`,
`protected/evolution/`, and
`docs/implementation-slices/evolution-scenario-catalog.md`.

**Known limitations:** The bundled cadence ledger is single-process; durable
compare-and-swap storage is required before multi-process use. The accepted
hackathon boundary is not equivalent to a separate cryptographic signing
service.

**Handoff created:** Wave 2 candidate bundles and ACE evaluation lanes can bind
to the expanded partitions without exposing protected/final feedback to
adaptation.

---

### Step 57 — Launch six isolated Wave 2 implementation lanes

**Status:** Complete; every lane was implemented in its own Git worktree,
independently reviewed, and merged with history preserved.

**Purpose:** Maximize parallel throughput without allowing concurrent edits to
shared composition, generated manifests, or the protected-worker source lock.

**Implemented:** Created separate lanes for Apple fall, live Watch health, ACE
merge/store, Reflector/Curator, candidate bundles, and Generator context. Each
lane owned a narrow source area, returned focused evidence, addressed review
findings on its feature branch, and left root configuration, generated
artifacts, and milestone documentation to the integration lane.

**Verification:** All six final feature heads received an independent
merge-safety review. The root integrated them in dependency-aware order and
resolved one additive Apple package-manifest conflict by retaining both test
targets.

**Handoff created:** Root composition could bind all six reviewed primitives
without combining partially reviewed worktree state.

---

### Step 58 — Add deterministic ACE host merge and immutable storage

**Status:** Complete and merged at `d80bacb` (feature/fix commits `7817070` and
`42b4b32`).

**Purpose:** Turn reviewed typed deltas into reproducible playbook descendants
without allowing callers to forge lineage or bypass content verification.

**Implemented:** Added deterministic delta ordering, duplicate handling,
contradiction rejection, pruning, content-addressed writes, and exact replay of
every descendant from its parent. Direct writes are restricted to roots;
descendants must be created through validated delta application. Durable writes
flush the file and parent directory before returning.

**Verification:** 104 focused ACE tests passed after independent review exposed
and the lane fixed a direct-descendant forgery path and missing directory
`fsync`.

**Primary files:** `backend/src/vital_relay/evolution/ace/merge.py`,
`backend/src/vital_relay/evolution/ace/store.py`, and focused ACE tests.

**Handoff created:** Candidate construction and Generator selection can resolve
only replayable, content-verified playbook versions.

---

### Step 59 — Add redacted ACE reflection and typed curation

**Status:** Complete and merged at `d6d103b` (feature/hardening commits
`935e461` and `567fce3`).

**Purpose:** Obtain useful failure analysis from a real local model without
exposing live operational data or protected/final evaluation content to the
adaptation loop.

**Implemented:** Added a loopback OpenAI-compatible model client with strict
JSON, temperature zero, and no retries; a redacted failure packet containing
only closed evidence codes; and a Curator that emits typed, grammar-bounded
output. Live records, protected/final reports, protected content, and arbitrary
prose are rejected before model invocation.

**Verification:** Independent review found an allowlisted-string exfiltration
path. The lane replaced free strings with closed operational code sets,
sentinels, category grammar/length limits, and evidence-subset checks. All 144
ACE tests plus adversarial path/pair/hostile-input matrices passed.

**Primary files:** `backend/src/vital_relay/evolution/ace/reflection.py`,
`backend/src/vital_relay/evolution/ace/curation.py`, model-boundary code, and
focused tests.

**Known limitation:** The configured loopback model endpoint must be operated
inside the accepted offline adaptation environment; this code does not make a
remote model safe for protected evidence.

**Handoff created:** A same-budget improvement round can produce bounded typed
proposals without turning evaluation evidence into model-visible prose.

---

### Step 60 — Build recursively verified evolution candidate bundles

**Status:** Complete and merged at `3b9ee8d` (final reviewed head `789101b`).

**Purpose:** Bind every candidate evaluation to exact playbook, policy,
improver, and mutation artifacts instead of a caller-provided label.

**Implemented:** Added complete immutable candidate manifests, exact A2 policy
mutation replay, signed improver-mutation receipts, content-addressed storage,
partition-scoped HMAC attestations, and recursive parent verification. Every
public read, resolver, and path API traverses full verification before returning
an artifact.

**Verification:** Review first found that child bytes/provenance were not
replayed exactly, then found a public manifest-read verification bypass. Both
were fixed on the lane. The final 21 focused and 32 combined bundle/archive/
lineage tests passed.

**Primary files:** `backend/src/vital_relay/evolution/bundles.py`,
`backend/src/vital_relay/evolution/bundle_store.py`, candidate/attestation
modules, and focused evolution tests.

**Handoff created:** Protected and final authorities can evaluate a precisely
identified candidate; later promotion can require the same verified bundle.

---

### Step 61 — Make selected ACE context mandatory at Generator boundaries

**Status:** Complete and merged at `dc477be`, then composed into the backend
runtime at root.

**Purpose:** Ensure the Generator receives one bounded reviewed baseline or
adapted playbook on every live, sandboxed, recorded, and scenario execution.

**Implemented:** Added mandatory `SelectedContext` to in-process, process,
worker, recorded, scenario, and sandbox-schema-v2 paths. The worker revalidates
the context; only canonical tactic titles/instructions cross the boundary; the
selection is capped at five items and 600 characters. Root composition verifies
the packaged baseline and review sidecar, pins exact model revision/artifact
hash and secret-free inference configuration identity, and supplies the
selector to `AgentRunService` and the real smoke path.

**Verification:** 79 focused runtime/configuration tests passed with 3 expected
skips. The full composed backend gate later passed 693 tests with 3 expected
skips and 45 PostgreSQL tests intentionally deselected.

**Primary files:** `backend/src/vital_relay/evolution/ace/selection.py`, agent
runner/worker/scenario boundaries, `backend/src/vital_relay/config.py`,
`backend/src/vital_relay/main.py`, and `backend/src/vital_relay/agent/smoke.py`.

**Known limitation:** Startup intentionally fails without exact local model
revision and artifact metadata; deployment still needs real model/sandbox
evidence.

**Handoff created:** The next ACE round can compare the reviewed baseline with
an adapted descendant while holding model identity, prompt budget, tools, and
scenario budget constant.

---

### Step 62 — Add real latest-only Apple Watch health telemetry

**Status:** Complete in source and merged at `b716f2b` (feature `6d5b118`).

**Purpose:** Replace placeholder Watch samples with real health data while
keeping telemetry strictly separate from durable fall-event delivery and
incident escalation.

**Implemented:** Added `HKWorkoutSession`/`HKLiveWorkoutBuilder` collection for
every allowlisted live scalar the Watch supplies, bounded `CMPedometer`
features, stable observation IDs, deterministic metric/capability batch IDs,
validated replay, latest-only WatchConnectivity coalescing, and an authenticated
iPhone ingestion consumer. The native Watch app owns the producers for its
process lifetime, starts HealthKit and pedometer collection independently so
either can remain useful when the other is unavailable, and exposes visible
start/stop/source-status controls. The iPhone
router installs the consumer only for an authenticated community persona,
generation-fences late callbacks, handles current-session `401`, and clears the
telemetry handler synchronously on graph teardown.

**Verification:** The feature lane passed 138 Swift tests in 29 suites plus 10
Watch transport XCTest cases and generic iOS/watchOS builds. After Apple root
composition, 151 Swift tests in 31 suites plus 10 XCTest cases passed; generic
unsigned iOS and watchOS builds succeeded. Final integration review found that
HealthKit startup failure could suppress an independently available pedometer
and that later producer stops could leave stale UI state; independent startup
and explicit running-state callbacks now preserve the surviving source and
refresh Start/Stop state.

**Primary files:** the Watch workout/controller/mapper sources under
`apps/apple/VitalRelayWatchApp/`, Watch transport and health consumer sources
under `apps/apple/Sources/`, `apps/apple/VitalRelayApp/VitalRelayAppRouter.swift`,
and Apple tests.

**Known limitation:** Real HealthKit availability, workout execution, paired
transfer, and backend receipt require a signed paired Watch/iPhone run with
consenting test data.

**Handoff created:** Community sessions can now contribute genuine live Watch
context to the existing scalar ingestion API without granting that context
escalation authority.

---

### Step 63 — Add genuine Apple fall-event ingestion

**Status:** Complete in source and merged at `9190554` (final reviewed head
`8e3568a`).

**Purpose:** Carry a genuine Apple fall callback through durable Watch transfer,
bounded iPhone location, authenticated backend ingestion, and restart-safe
acknowledgement.

**Implemented:** Retained a real `CMFallDetectionManager` with visible Watch
authorization/readiness, stable callback IDs, and completion only after durable
outbox storage. Dismissed/rejected events persist a local non-escalating
disposition and never request location or HTTP. Confirmed/unresponsive events
reach a process-lifetime iPhone coordinator; foreground onboarding requests
When In Use then Always, while background handling never prompts and requires
existing Always plus full-accuracy authority. The backend receipt is persisted
before Watch inbox acknowledgement, so restart skips duplicate location and
network work. Manual SOS remains compatible with precise When In Use location.

**Verification:** Independent final review passed 140 Swift tests in 28 suites
plus 10 XCTest cases, generic unsigned iOS/watchOS builds, and plist validation.
The composed Apple gate passed the larger counts recorded in Step 62.

**Primary files:** `apps/apple/VitalRelayWatchApp/WatchFallDetectionController.swift`,
`apps/apple/Sources/VitalRelayFeature/AppleFallEventCoordinator.swift`, durable
Watch transport stores, app routers/views, and both application plists.

**Known limitation:** Apple entitlement approval, provisioning, physical fall
callback behavior, background delivery, paired transfer, real Core Location,
and authenticated production-like receipt remain external evidence gates.

**Handoff created:** A signed-device run can exercise the exact operational
path without substituting replay or a simulated fall source.

---

### Step 64 — Compose Wave 2 and refresh integrity artifacts

**Status:** Complete and verified in the integrated tree.

**Purpose:** Bind independently reviewed components to the real product runtime
and update every generated digest that covers changed execution code.

**Implemented:** Wired verified baseline/model identities and selected context
into backend startup and smoke execution; composed persona-scoped Watch health
and fall lifecycles in the one native app; regenerated development, protected,
final, and recorded evaluation manifests; and refreshed the reviewed Docker
worker-tree digest from the exact composed backend source. Shared environment,
README, implementation-plan, and progress documentation now describe the new
requirements and external evidence limits.

**Verification:** The full backend selection passed 693 tests with 3 expected
skips and 45 PostgreSQL tests intentionally deselected. The combined Apple gate
passed 151 Swift tests in 31 suites plus 10 XCTest cases, generic unsigned iOS
and watchOS builds, and both plist checks. Generated artifact regeneration is
deterministic and the recomputed worker-tree digest matches its pinned value.

**Primary files:** `backend/src/vital_relay/main.py`,
`backend/src/vital_relay/config.py`, `protected/evolution/`, `scenarios/`,
`backend/src/vital_relay/agent/sandbox.py`, Apple app composition,
`.env.example`, `README.md`, `proposal/implementation-plan.md`, and this ledger.

**Accepted trust boundary:** Evaluator, authorities, cadence ledger, protected
assets, signing material, merge/review, and promotion remain in one trusted
offline host process. Candidate/model/mutation execution remains a separate
sandbox process. Arbitrary Python in the host is host compromise; stronger
deployment requires a separate signing/issuance service.

**Handoff created:** Wave 3 can run a real same-budget ACE round and build a
transactional, operator-approved promotion/rollback bridge over exact verified
candidate evidence.

---

## 3. Architectural decisions now considered stable

| Decision | Reason |
|---|---|
| Scalar observations have a small v1 contract | Apple and backend work can start without waiting for every structured HealthKit record |
| Capability states are not fake numeric metrics | `requested_no_sample` and `unsupported` describe access/support, not observations |
| Structured records receive typed schemas | Prevents sleep, ECG, and activity data from becoming unvalidated arbitrary JSON |
| Server controls receipt time | Clients control observation time; audit receipt time must be authoritative |
| IDs are immutable and content-address checked | Retries are safe and conflicting reuse is visible |
| A batch is single-user and single-device | Simplifies authorization, idempotency, audit, and future partitioning |
| Health context cannot authorize escalation | The deterministic incident state machine remains the safety authority |
| Raw ECG/motion is not transportable | Reduces privacy exposure, bandwidth, storage, and accidental misuse |
| Repository is a port, not a FastAPI detail | In-memory development and PostgreSQL production behavior can remain equivalent |
| Snapshot lookup accepts `as_of` | Deterministic scenarios and incident-time snapshots do not depend on wall-clock timing |
| Capability states report only observable facts | HealthKit read denial cannot be reliably distinguished from no visible sample, so `permission_denied` is not a valid state |
| Snapshot capture time is server-controlled | A client can request a capture but cannot backdate or future-date the audited context |
| Snapshots are immutable under exact retry | Incident evidence must not silently change when newer metrics arrive |
| New snapshots honor the newest same-source visibility state | An older accepted value cannot reappear after a newer `not_requested`, no-sample, unsupported, or error capability; prior immutable snapshots are not rewritten |
| Freshness windows are stored with each item | Labels remain reproducible after defaults or per-type policy change |
| Freshness is presentation-only | Age and availability provide context but cannot suppress, create, or advance escalation |
| Public snapshot reads use a dedicated view | Internal IDs, bundles, devices, and diagnostic errors are not needed by incident/native-command consumers |
| Durable data is always demo-scope bound | Retention and reset must never infer or target a global dataset |
| Configured PostgreSQL never silently falls back | An unavailable durable store must be visible instead of causing untracked process-local writes |
| Batch receipts are durable records | Exact retries must retain original order, counts, and receipt time after restart |
| Concurrent entity IDs use advisory locks | Different batches can safely race on the same immutable metric/capability ID without partial writes |
| Snapshot capture uses a unit of work | Latest metric/capability reads and immutable storage must share one consistent transaction |
| Snapshot items copy their audit inputs | Source-data retention cannot alter or prevent deletion around a retained snapshot |
| Snapshot holds are separate from public contracts | Future incident linkage can preserve evidence without exposing persistence policy to clients |
| Source rows and idempotency receipts share retention | Keeping only one side would make retry behavior misleading after deletion |
| Incident input is real-only | The operational path accepts only non-simulated Apple fall or deliberate manual SOS events; replay cannot masquerade as a real emergency |
| Incident persistence has no in-memory adapter | A safety transition must not disappear on process restart or silently diverge from PostgreSQL |
| Health snapshot creation is incident-atomic | The displayed context and its retention hold cannot be missing from an otherwise accepted incident |
| Verification timeouts are database records | Restart, duplicate workers, and check-in races settle one authoritative transition |
| Existing client headers are access-token transports | Product mode resolves a durable persona session and resource scope from the issued token; this is real hackathon role authorization but not a production IdP or attestation system |
| Responder/AED distance is a PostGIS concern | Geography points, meter-based `ST_DWithin`/`ST_Distance`, and GiST indexes keep proximity out of application approximations |
| Pre-accept and accepted dispatch are different contracts | Coarse bands are safe for coordination; exact wearer location is released only after authenticated acceptance |
| Invitations advance one responder at a time | One pending-invitation constraint plus incident locking prevents broadcast disclosure and duplicate assignment |
| Responder decision IDs are immutable receipts | Exact retries return their stored effect while conflicting reuse cannot mutate invitation history |
| Static venue routing is labeled honestly | Persisted straight-line distances and fixed instructions provide a demo handoff without claiming live directions or ETA |
| Live routing never erases its fallback provenance | Provider geometry is persisted only after bounded validation; unavailable, timed-out, or invalid responses become an explicit source-labelled static fallback |
| Bootstrap and session token storage is one-way server-side | Provisioning reveals high-entropy bootstrap material only to the operator, sessions reveal issued secrets only to the installation, and PostgreSQL stores SHA-256 hashes |
| The iPhone renders but does not author incident state | Manual SOS/check-ins cross authenticated endpoints; countdown expiry and polling only display the latest server projection |
| Wearer and responder dispatch clients stay separate | The community graph consumes only redacted coordination through its access token; the responder graph owns exact dispatch/protocol behind its distinct responder-persona session |
| Protocol selection is `IncidentKind`-only | Health context, observations, routes, prompts, and models cannot choose or alter emergency presentation content |
| Protocol bytes cannot self-authorize | Raw JSON omits its digest; an append-only external catalog pins exact ID/version/bytes separately from the active kind mapping |
| Protocol content is reread at every trust boundary | Startup, selection, readiness, and presentation all detect missing or modified packaged content |
| Accepted assignment and protocol presentation are atomic | A responder cannot become assigned without the exact fixed presentation committing in the same transaction |
| Stored presentations remain fail-closed | Reads compare the protected exact version, complete snapshot, incident kind, assignment identity, and acceptance timestamp |
| Product integrity is not candidate isolation | Digest checks are complete; future evaluator/sandbox read-only mounts remain unimplemented until those systems exist |
| One binary does not mean one omnipotent client | Community, responder, and command each own a credential-scoped feature graph, and accepted-only responder types never enter the wearer graph |
| Persona labels do not grant authority | The backend derives persona from the durable account/session and rechecks resource ownership; native selection only chooses which bounded client to compose |
| Responder decisions persist before transport | Exact decision ID, invitation, choice, and timestamp survive retry without storing the credential or accepted location |
| Static MapKit presentation is not live routing | The UI draws the persisted two-leg venue route and labels it static with no live navigation or ETA claim |
| Notification payloads are locators, not credentials | Incident and invitation IDs can select only an active discovery row; the authenticated responder session still authorizes every protected read and action |
| APNs device tokens are reversibly encrypted only at the provider boundary | Delivery needs the original token, while authenticated encryption prevents plaintext durable storage and logs never receive it |
| Exactly-once applies to the logical outbox row, not physical display | APNs correlation and collapse identifiers cannot prove device display or provider deduplication |
| Ambiguous notification outcomes are terminal unknown | Retrying after an uncertain write/read outcome can create a second visible emergency alert |
| Provider acceptance does not advance incidents | Notification transport remains a side effect of a committed invitation, never an incident-state input |
| Resolution time is server-owned | Close/handoff clients send only a stable UUID and typed action; incident, transition, revocation, and receipt share one authoritative instant |
| Resolution idempotency is a durable receipt | Exact retries survive restart and return the original snapshot; conflicting identity reuse cannot mutate the winner |
| Assignment revocation is append-only | Ending exact responder access must preserve the assignment, route, protocol, and command audit rather than delete history |
| Command audit and responder live access are separate | Resolved incident/timeline/protocol remain command-readable while responder exact-dispatch/protocol reads fail as absent |
| Command dispatch stays active-state-only | The frozen coordination view does not model resolved incidents; command rebuilds resolved audit without inventing a new projection |
| Native exact data is revalidated before display | A resolution race or revoked read clears responder coordinates/route/protocol immediately and reconciles the redacted incident |
| Persona authority comes from the durable account | A native label or selected screen cannot grant community, responder, or command capability |
| Enrollment, access, and refresh are separate secrets | Bootstrap provisioning, product reads, and session lifecycle cannot substitute for one another; only hashes persist server-side |
| Discovery is a locator, not authorization | Its minimal incident IDs reduce launch configuration, while every selected graph must reauthorize the resource |
| One session composes one typed feature graph | Switching, logout, or `401` destroys the old graph and responder exact state before another credential can be used |
| Database last-token-wins requires native single flight | PostgreSQL safely serializes concurrent rotations; sharing one in-flight native task keeps Keychain on the only valid access result |
| Local logout precedes best-effort network revocation | Deleting captured Keychain authority before suspension avoids Swift actor reentrancy erasing a later enrollment |
| Location is acquired only for deliberate SOS | When In Use, one-shot foreground acquisition avoids turning incident creation into background wearer tracking |
| A valid wire location is not automatically a safe SOS fix | The first-party app applies explicit freshness, accuracy, clock-skew, precision, and software-simulation gates before transport |
| Device capture time remains evidence | `CLLocation.timestamp` is preserved as `captured_at`; the client never refreshes cached data by substituting its own time |
| Cancellation stops before the authority boundary | Graph teardown cancels model-owned acquisition; pre-transport data is cleared, while a possibly delivered request keeps its exact retry envelope |
| Stored HealthKit context is not a live Watch signal | Slice 11B labels every queried value as a stored `recent_context` sample and never infers Watch connectivity from its source |
| Watch critical events and telemetry use different delivery semantics | Critical events survive restart and require acknowledgement; latest-only telemetry coalesces and can never acquire escalation authority |
| Watch telemetry is authenticated-session scoped | Only a current community session installs the iPhone health consumer; graph teardown clears its handler before late asynchronous work can publish or ingest |
| A fall disposition is an authority boundary | Dismissed/rejected callbacks remain durable local audit only; only confirmed/unresponsive callbacks may request location and cross the incident API |
| Background fall location never prompts | Background processing requires pre-existing Always and full-accuracy authorization; foreground onboarding owns the explicit two-step request, while manual SOS retains precise When In Use support |
| HealthKit read completion is not permission evidence | Empty reads become `requested_no_sample`; the app never claims per-type denial that HealthKit does not reveal |
| Health connection is an explicit per-account product action | OS authorization is app-wide, so a restored or switched persona remains disconnected until that community user chooses Connect/Refresh |
| Scalar registry scope is closed and staged | Twenty-six standard quantities are requested together; four connected-source quantities remain off until separately selected, and structured records require typed contracts |
| Health sample identity is user-scoped and deterministic | Registry version, user, type, and sample UUID are hashed so exact retries deduplicate without exposing a raw global HealthKit UUID |
| Health sync and SOS have separate lifecycles | Authorization or ingestion cannot gate SOS; session teardown cancels both owned tasks and rejects late health-state publication |
| Agent strategy is model-owned inside application authority | The model chooses observations and bounded coordination actions; identity, state, recipients, idempotency, protected content, and transitions remain enforced by services and PostgreSQL |
| A sandbox failure is not a reason to run another coordinator | The durable outcome is `manual_required`; NemoClaw failure never triggers scripted decisions or an automatic Docker execution |
| Docker is an explicit substrate, not fallback control flow | One exact operator selector determines the only runtime constructed and validated; settings for the other substrate fail startup |
| Sandbox cleanup retains exact custody | Unresolved project/root cleanup keeps the same runner and monotonic evidence reachable for retry; composition never reconstructs or broadly cleans Docker identity |
| ACE operational context is closed and content-addressed | Models may select bounded reviewed tactics and typed deltas, but arbitrary prose and live/protected/final evidence cannot enter playbook artifacts |
| Every candidate read verifies recursively | Public manifests, resolvers, and artifact paths replay lineage and validate partition attestations before returning bytes; storage presence alone is not authority |
| Reflection evidence uses a closed vocabulary | Redaction is not a string scrubber: only fixed operational codes and bounded typed curation may cross into the adaptation model |
| Generator context is mandatory and bounded | Live, process, worker, recorded, and scenario execution all receive the same validated selected-context type with a five-item/600-character ceiling |
| Final evaluation uses an explicitly trusted offline host | Candidate code is isolated in another process; arbitrary Python inside the evaluator is host compromise, and stronger deployments require a separate signer service |
| Tool authority cannot outlive execution ownership | Capability expiry is bounded by the durable run lease, and host receipt time fences late results before a run can be reclaimed |
| An agent run exists before inference | Durable `running` creation prevents invisible model work and makes exact retries/restart reconciliation observable |
| Policy bytes and activation are separate trust inputs | A canonical digest pins the artifact while a monotonic command-authorized pointer determines which hash may run or invoke tools |
| Mutation ambiguity is durable | A reserved tool operation that cannot prove completion remains `idempotency_in_doubt`; retries do not blindly repeat an irreversible action |

---

## 4. Next parallel milestones

### Goal

Run the first real same-budget improvement cycle while separate evidence lanes
exercise the already-built native, provider, and sandbox paths.

### Planned deliverables

| Wave 3 lane | Independent outcome | Dependency |
|---|---|---|
| ACE improvement round | Baseline and inherited candidate run through identical Generator/model/tool/scenario budgets; development-only failures feed reflection and typed curation | Completed Wave 2 ACE context, reflector, merge/store, and bundles |
| Promotion/rollback bridge | One transactional operator-approved CAS binds exact candidate bundle, signed protected/final evidence, active baseline, and policy pointer; rollback is equally explicit | A successful candidate with valid cadence evidence |
| Signed Apple evidence | Entitled physical Watch fall, live HealthKit/Core Motion, durable paired transfer, background location policy, authenticated receipt, and restart behavior captured with consenting demo accounts | Apple entitlement/provisioning and paired devices |
| Live sandbox evidence | Real selected Docker and NemoClaw runs with local model, scoped tool calls, protected-file/egress denial, crash/lease recovery, and exact cleanup custody | Local runtimes and model artifacts |
| Provider evidence | One real Mapbox route and APNs delivery trace without changing the truthful static/unknown fallbacks | Provider credentials and reachable test devices |

The trusted-host boundary remains fixed for Wave 3: evaluator, authorities,
cadence, protected assets, signing, merge/review, and promotion are one trusted
offline host; candidate/model/mutation execution is isolated in the selected
sandbox. The improver may propose and build a candidate but may never activate
itself.

### Deferred by design

Typed structured HealthKit records remain deferred until a concrete demo need
and a representative source fixture justify each schema. Production identity,
remote attestation, multi-process cadence storage, and a separate signing
service remain post-hackathon hardening rather than hidden claims in the demo.

---

## 5. How to update this file

Add one chronological step for each completed slice or meaningful integration. Every entry should include:

- status and purpose;
- behavior implemented;
- files changed;
- tests or evidence produced;
- known limitations;
- the exact interface handed to the next step.

Do not mark a broader milestone complete because one narrow slice is complete. Record fallback use, missing external prerequisites, and unverified physical-device behavior explicitly.
