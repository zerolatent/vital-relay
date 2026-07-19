# NemoClaw/OpenShell live policy-attestation evidence

This lane is a fail-closed acceptance harness for the real Vital Relay product
path. It contains no captured evidence and makes no claim that NemoClaw,
OpenShell, a model, PostgreSQL, or the private TLS route is available on a
developer machine.

Run the fixed, argument-free entry point on the selected Linux host:

```bash
python3 infrastructure/nemoclaw/assert_effective_policy.py \
  > /operator-owned/private/path/live-nemoclaw.json
```

Use the reviewed host-side Vital Relay Python environment, including its normal
application dependencies. Those dependencies remain on the trusted host and
are not installed in Nemo. If they are missing, the wrapper emits a canonical
`host_harness_dependencies_unavailable` failure and exits nonzero.

Exit `0` means every member of the closed probe enum passed and stdout contains
one canonical, HMAC-authenticated, content-addressed artifact. Missing external
prerequisites, client/DNS/TLS failures without matching OpenShell evidence,
schema drift, a pin mismatch, duplicate/missing probes, or unresolved cleanup
exit nonzero with a closed failure receipt. A failure receipt is never live
evidence.

Never commit the redirected artifact, credentials, raw policies, OCSF logs,
model/provider bodies, subprocess diagnostics, health data, coordinates,
tokens, or hidden reasoning.

## Dedicated deployment boundary

Use a dedicated Deep Agents acceptance sandbox named
`vital-relay-acceptance`. It must not be an interactive or shared development
sandbox. The lane inspects the Docker-driver container through the local Docker
API because the selected deployment uses the OpenShell Docker driver; it never
starts Docker and never falls back from NemoClaw to Docker or another runner.

The host must provide:

- exact `/usr/local/bin/nemo-deepagents` and `/usr/local/bin/openshell`
  versions;
- the one running `vital-relay-acceptance` OpenShell sandbox, its immutable
  image digest, sandbox ID, and current policy revision;
- the reviewed OCSF JSONL export enabled at
  `/var/log/openshell-ocsf.YYYY-MM-DD.log` inside the sandbox, with the schema
  and vendor values pinned for the exact OpenShell version;
- the reviewed, read-only `/sandbox/vital-relay-runtime`, including exact
  `/sandbox/vital-relay-runtime/bin/python3.14` and
  `/sandbox/vital-relay-runtime/bin/vital-relay-agent-worker` paths;
- the reviewed single-file sandbox probe at
  `/sandbox/vital-relay-runtime/nemoclaw_probe.py`;
- the exact tool-capable model through `https://inference.local/v1`;
- a live PostgreSQL scope, active coordination policy, eligible incident, and
  active command-persona session;
- the backend capability key and a distinct evidence-authentication key; and
- the reviewed private CA and TLS terminator for the one tool route, forwarding
  to the harness-owned backend at `127.0.0.1:8017`.

Port `8017` and all command vectors are fixed. Stop any competing acceptance
backend before running the lane. The harness owns the exact
`multiprocessing.Process` it crashes; it does not kill an unrelated backend.

### Minimal sandbox bundle

Do not install the ordinary Vital Relay wheel or full application package to
make acceptance helpers importable. Stage the reviewed source bytes from
`backend/src/vital_relay/agent/nemoclaw_probe.py` as the exact
`/sandbox/vital-relay-runtime/nemoclaw_probe.py` file. The helper is
standard-library only and has no imports from the application, PostgreSQL,
capability authority, persistence, evaluator, model runner, signing code, or
evidence orchestrator. Its exact bytes have a separate source pin and are
included in the immutable runtime manifest; it is not added to the agent worker
source manifest.

The host invokes it only as:

```text
/usr/local/lib/nemoclaw/dcode-managed-exec
/sandbox/vital-relay-runtime/bin/python3.14
/sandbox/vital-relay-runtime/nemoclaw_probe.py
<one fixed subcommand>
```

The product worker remains the real staged executable. The probe is not a
worker replay, stub, or replacement and cannot produce a live artifact.

The sandbox needs `/usr/bin/curl` solely for the unlisted-binary denial. It does
not need `pgrep`, `pkill`, a shell, a package manager, or a global cleanup
utility.

## Required environment

Normal product configuration must select the dedicated sandbox and exact
routes:

```text
VITAL_RELAY_AGENT_ENABLED=true
VITAL_RELAY_AGENT_SANDBOX=nemoclaw
VITAL_RELAY_AGENT_SANDBOX_NAME=vital-relay-acceptance
VITAL_RELAY_AGENT_TOOL_PROXY_ENDPOINT=https://vital-relay.internal:8443/internal/v1/agent/tools/invoke
VITAL_RELAY_VLLM_BASE_URL=https://inference.local/v1
VITAL_RELAY_VLLM_MODEL=<exact reviewed model ID>
VITAL_RELAY_DATABASE_URL=<live PostgreSQL URL>
VITAL_RELAY_DEMO_SCOPE_ID=<live scope UUID>
VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY=<unpadded base64url; 32-64 decoded bytes>
```

Add the live-evidence inputs:

```text
VITAL_RELAY_LIVE_EVIDENCE_NEMOCLAW_VERSION=<reviewed semver>
VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_VERSION=<reviewed semver>
VITAL_RELAY_LIVE_EVIDENCE_OCSF_SCHEMA_VERSION=<schema emitted by that OpenShell version>
VITAL_RELAY_LIVE_EVIDENCE_OCSF_VENDOR=<vendor emitted by that OpenShell version>
VITAL_RELAY_LIVE_EVIDENCE_OCSF_EXPORT_PATH=/var/log
VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_DRIVER=docker
VITAL_RELAY_LIVE_EVIDENCE_OPENSHELL_POLICY_REVISION=<positive integer>
VITAL_RELAY_LIVE_EVIDENCE_IMAGE_DIGEST=sha256:<64 lowercase hex>
VITAL_RELAY_LIVE_EVIDENCE_RUNTIME_SHA256=<64 lowercase hex>
VITAL_RELAY_LIVE_EVIDENCE_BASE_POLICY_SHA256=<64 lowercase hex>
VITAL_RELAY_LIVE_EVIDENCE_EFFECTIVE_POLICY_SHA256=<64 lowercase hex>
VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_FILE=<absolute host-side reviewed CA path>
VITAL_RELAY_LIVE_EVIDENCE_TLS_CA_SHA256=<64 lowercase hex>
VITAL_RELAY_LIVE_EVIDENCE_TRANSPORT_IDENTITY_SHA256=<sandbox proxy/CA identity>
VITAL_RELAY_LIVE_EVIDENCE_WORKER_TRANSPORT_IDENTITY_SHA256=<actual worker proxy/CA identity>
VITAL_RELAY_LIVE_EVIDENCE_WORKER_COMMAND_SHA256=<raw /proc PID cmdline SHA-256>
VITAL_RELAY_LIVE_EVIDENCE_HARNESS_SOURCE_SHA256=<reviewed harness graph hash>
VITAL_RELAY_LIVE_EVIDENCE_SANDBOX_PROBE_SOURCE_SHA256=<reviewed probe-file hash>
VITAL_RELAY_LIVE_EVIDENCE_AGENT_SOURCE_SNAPSHOT_SHA256=<reviewed NEMOCLAW_AGENT_SOURCE_MANIFEST snapshot digest>
VITAL_RELAY_LIVE_EVIDENCE_HMAC_KEY=<distinct unpadded base64url key; 32-64 bytes>
VITAL_RELAY_LIVE_EVIDENCE_ISSUER=<reviewed evidence issuer>
VITAL_RELAY_LIVE_EVIDENCE_KEY_ID=<reviewed evidence key ID>
VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_ID=<live incident UUID>
VITAL_RELAY_LIVE_EVIDENCE_INCIDENT_STATE_VERSION=<positive integer>
VITAL_RELAY_LIVE_EVIDENCE_RUN_ID=<new product-run UUID>
VITAL_RELAY_LIVE_EVIDENCE_KILL_RUN_ID=<different new crash-run UUID>
VITAL_RELAY_LIVE_EVIDENCE_COMMAND_TOKEN=<opaque command access token only>
```

Both run IDs must be fresh. In one serializable PostgreSQL transaction, the
harness locks the scope, requires both IDs absent, resolves the token-hash to
the active session, and snapshots the active coordination-policy
ID/version/hash/revision and database time. It never deletes prior rows to
manufacture success.

The capability and evidence keys must differ. No secret-valued input is put in
argv, an artifact field, a blocker, or a subprocess diagnostic.

## Pin and signature boundary

Pins come from the reviewed deployment record, not from an unreviewed harness
attempt. The signed inventory binds:

- NemoClaw/OpenShell versions, the pinned OCSF schema/vendor/export contract,
  exact Deep Agents identity/runtime, sandbox ID, OpenShell revision, immutable
  image ID, and exact model;
- base and effective OpenShell policy hashes;
- runtime, sandbox probe, and harness graph hashes;
- the exact in-tree `NEMOCLAW_AGENT_SOURCE_MANIFEST` name, entrypoint, ordered
  path-contract hash, captured `ReviewedSourceSnapshot` digest, and source
  count;
- the complete fixed command graph and `runner=nemoclaw`;
- the active coordination-policy hash and revision; and
- sandbox and actual worker proxy/CA identities.

The evidence envelope uses HMAC-SHA256 with the domain
`vital-relay:nemoclaw-openshell-live-evidence:v2`, issuer, and key ID. The HMAC
covers the issuer, key ID, canonical body, and body digest. The harness verifies
its own envelope before emission; verifier fixtures alter both body and digest,
re-label the issuer, and prove tampering fails. HMAC custody remains a
trusted-host responsibility.

The harness imports the reviewed `NEMOCLAW_AGENT_SOURCE_MANIFEST` authority and
calls `capture_reviewed_source_snapshot` against the trusted checkout. That
capture enforces the exact dependency-closed worker paths, rejects host-only
imports and unsafe source entries, and computes the one content digest. The
digest must match the separately reviewed deployment pin before any live
probe. Capture errors, a missing pin, or a digest mismatch fail closed without
emitting source bytes. The harness does not create a second dependency closure
or install the ordinary Vital Relay wheel in Nemo.

The harness digest covers the host orchestrator, minimal probe source, and
fixed entry point. The separate probe pin is the SHA-256 of the exact
single-file source staged into Nemo.

## Immutable runtime proof

The sandbox probe opens the runtime root with `O_DIRECTORY|O_NOFOLLOW` and
walks it with descriptor-relative `openat`/`stat` operations. Symlinks and all
non-regular/non-directory entries fail. Its canonical, path-sorted manifest
includes every directory's sorted child-name list plus device, inode, mode,
UID, GID, link count, mtime, and ctime; file entries additionally include size
and content SHA-256. `fstat` is compared before and after reads and recursion,
and the complete manifest is identical before and after managed inference.

For every directory it attempts an exclusive descriptor-relative create. For
every file it attempts `O_WRONLY|O_NOFOLLOW`. Every attempt must fail with a
read-only/permission error. The deepest authoritative `/proc/self/mountinfo`
entry covering the runtime must contain the VFS `ro` flag, and `statvfs` must
also report `ST_RDONLY`. A root-only check, writable nested path, traversal,
unstable metadata, or a matching hash on a writable tree fails acceptance.

## Closed health and policy schemas

NemoClaw status and doctor are not searched recursively for convenient scalar
values. Status must match the complete supported schema version exactly,
including the dedicated sandbox identity, Deep Agents terminal runtime, model,
provider routes, OpenShell driver/version, `Ready` phase, healthy recursively
probed inference route, zero OOM kills, no route drift/RPC/failure layer, and
no paused container. Doctor must match its complete supported schema, report
`ok`, have zero failures/warnings, and include the exact OpenShell, live
sandbox, and gateway-inference checks. Unknown, fatal, warning, degraded, or
future-schema fields fail closed.

Both base and full effective policies must contain only:

| Host | Port | Allowed rules |
|---|---:|---|
| `inference.local` | `443` | `GET /v1/models`, `POST /v1/chat/completions`; optional exact `POST /v1/embeddings` |
| `vital-relay.internal` | `8443` | only `POST /internal/v1/agent/tools/invoke` |

Every entry uses `protocol: rest` and `enforcement: enforce`. GitHub, PyPI,
PythonHosted, package, search, messaging, observability, public providers,
shell, curl, wildcard hosts/paths/binaries, and all extra tool routes are
mechanically rejected.

## Cursor-correlated OCSF evidence

The harness makes no claim that sandbox `/var/log` is mounted on the host. It
uses only the fixed, signed command that runs the reviewed minimal probe inside
`vital-relay-acceptance`:

```text
/usr/local/bin/nemo-deepagents vital-relay-acceptance exec
--no-tty --timeout 30 --stdin --
/sandbox/vital-relay-runtime/bin/python3.14
/sandbox/vital-relay-runtime/nemoclaw_probe.py ocsf-export
```

The exporter accepts no path from argv and permits only the reviewed sandbox
path `/var/log`. Before each Python network denial and the curl denial, it
opens the durable JSONL directory descriptor and captures every current file's
device/inode/size/mtime and SHA-256 of all prefix bytes. Files must be regular,
complete newline-terminated JSONL. After the attempt it reopens with
descriptor-relative, no-follow operations; rotation may append a new dated
file, but a missing/replaced/truncated/rewritten cursor prefix or partial line
fails.

Every exported event must match the configured OCSF schema, configured vendor,
`OpenShell Sandbox Supervisor` product, and exact pinned OpenShell version. The
current export must already contain at least one event with that provenance.
An unavailable export, a stale release's event, a vendor/schema mismatch, or a
future undocumented shape fails with
`openshell_ocsf_version_export_schema_invalid`. No host-side `/var/log`
visibility is assumed.

Each attempt receives a domain-separated HMAC-derived 128-bit challenge. The
unlisted-host challenge is embedded in a unique `*.github.com` hostname; the
wrong-route challenge is embedded in the exact GET path; curl receives it in
the fixed request URL and returns its actual child PID. A passing denial is
exactly one new pinned-schema OpenShell Sandbox Supervisor event for the dedicated
sandbox, exact host/port, exact binary/PID or challenge path, and structured
Denied/Blocked policy result with an authoritative no-match reason. Zero or
multiple events, a stale pre-cursor event, wrong nonce/PID/sandbox, DNS error,
TLS failure, proxy error, curl exit code, or Python client exception never
passes by itself.

## Closed live probe graph

The signed artifact requires exactly one successful observation for every
closed enum member:

1. pinned NemoClaw/OpenShell versions, closed status/doctor, sandbox/image
   inventory, and base/effective policy allowlists;
2. immutable runtime inventory, managed model, and actual sandbox transport;
3. unlisted-host, wrong-tool-route, protected-file, and unlisted-binary
   denials;
4. one real product run with a new OCSF worker launch, managed inference, exact
   `POST /internal/v1/agent/tools/invoke`, terminal durable record, and exact
   host-audit correlation; both required HTTP POST events must name the exact
   staged-Python actor and worker PID from that launch;
5. exact retry with the byte-equivalent canonical record and a two-second OCSF
   settling window containing no new worker/inference/tool event;
6. expired, cross-run, cross-scope, stale-state, revoked/non-active-policy, and
   unknown-tool denials, each with the exact closed response code and exactly
   one append-only PostgreSQL audit row; and
7. crash/lease reconciliation plus exact cleanup custody.

Before launching the crash request, the killed run ID was already proven
absent transactionally. Its new `running` row must match this attempt's exact
run/incident/state, schema/objective, requester account/session, active
policy ID/version/hash, model, NemoClaw runner, creation window, and exact POST
body/path digest (the run UUID is the product idempotency input). The OCSF
cursor must yield exactly one worker launch, whose PID is inspected in the
sandbox. Inspection requires exact `/proc/PID/exe`, raw cmdline hash, start-time
ticks, actual worker environment proxy values, and the CA bytes opened through
`/proc/PID/root`.

The harness kills only its exact backend process and accepts only HTTPX's
defined `RemoteProtocolError` for the initiating request. Connect, DNS, TLS,
timeout, generic client errors, or an HTTP response fail this probe. Before the
lease expires, exact retry must remain `409 agent_run_in_progress`. At expiry,
the real backend must reconcile the row to `manual_required` at the lease
boundary. Only then may cleanup submit the previously recorded PID,
start-time, exe, cmdline, worker transport, and CA pins to the minimal probe.
If the original handle is still present, that probe revalidates the entire
handle before signaling its PID. If the PID is absent or has a different start
time, it treats the previously inspected handle as already absent and never
signals the replacement. There is no `pgrep`, `pkill -f`, global matching
process, or cross-sandbox cleanup claim.

The current product lease is approximately ten minutes. Shortening it inside
this harness would test a different product configuration.

## Honest blockers

An unprepared host normally reports closed blockers for missing NemoClaw,
OpenShell/OCSF, exact OCSF version/export schema, the reviewed agent-source
snapshot pin/closure, immutable runtime or minimal probe bundle,
image/source/policy
pins, model, PostgreSQL, command session, Docker-driver inventory, private TLS
route/CA, worker `/proc` visibility, or evidence key custody. These are real
deployment prerequisites. Unit fixtures validate only parsing, correlation,
signature tampering, redaction, command boundaries, and truthful failure; they
never count as live evidence.

Current command and structured-log semantics are based on the official
[NemoDeepAgents command reference](https://docs.nvidia.com/nemoclaw/user-guide/deepagents/reference/commands),
[OpenShell OCSF JSON export](https://docs.nvidia.com/openshell/latest/observability/ocsf-json-export),
and [OpenShell log access](https://docs.nvidia.com/openshell/observability/accessing-logs).
