# Docker containment live evidence

This lane collects live, content-addressed evidence for the operator-selected
Docker agent path. It does not start Compose directly as an alternative product
launcher. The collector constructs the real application; application
composition selects `ProcessSandboxAgentRunner`, calls `validate_startup()`, and
retains exact cleanup custody before the command API can run.

Fixture tests validate parsers and privacy rules only. Their bundles are
permanently `test_only` and unsigned, cannot be published by the live writer,
and are not live evidence. A passing bundle requires Docker, the configured
vLLM model, a real PostgreSQL scope, an enrolled command session, and an active
incident at the same time. Missing external prerequisites produce an immutable,
trusted-host-signed `blocked` bundle and exit status 1. They never produce a
passing bundle.

## Prerequisites

Use a dedicated, disposable, locally addressed PostgreSQL database whose name
contains `test`, `evidence`, or `demo`. It must already have current Vital Relay
migrations, exactly one active demo scope, the pinned active agent policy, an
enrolled command session, and one active `escalating` or `response_active`
incident. Do not use a production database or captured health data. The
collector does not create, migrate, reset, or delete a database.

Docker or Colima must be running. A real vLLM-compatible server must listen on
host port 8001 and list the exact configured model from `GET /v1/models`. The
server must be reachable from `host.docker.internal`. Host port 8000 must be
free; the collector temporarily serves the real Vital Relay API there so the
reviewed tool gateway can reach it.

Set these values in the collector process environment:

- `VITAL_RELAY_DATABASE_URL`
- `VITAL_RELAY_DEMO_SCOPE_ID`
- `VITAL_RELAY_AGENT_SANDBOX=docker`
- `VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY`
- `VITAL_RELAY_DOCKER_VLLM_API_KEY`
- `VITAL_RELAY_VLLM_MODEL`
- `VITAL_RELAY_VLLM_MODEL_REVISION`
- `VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256`
- `VITAL_RELAY_AGENT_TIMEOUT_SECONDS` between 10 and 30; 10 is recommended for
  the deliberate timeout probe
- `VITAL_RELAY_EVIDENCE_COMMAND_ACCESS_TOKEN` for the enrolled command session
- `VITAL_RELAY_EVIDENCE_INCIDENT_ID`
- `VITAL_RELAY_EVIDENCE_INCIDENT_STATE_VERSION`
- `VITAL_RELAY_EVIDENCE_ENVIRONMENT=nonproduction`
- `VITAL_RELAY_EVIDENCE_ISSUER`, identifying the trusted host or evidence
  authority
- `VITAL_RELAY_EVIDENCE_KEY_ID`, identifying the independently custodied HMAC
  key
- `VITAL_RELAY_EVIDENCE_SIGNING_KEY`, a base64url-encoded high-entropy HMAC key

The normal absolute policy and digest overrides may be set when needed. Do not
set NemoClaw-only endpoint or sandbox-name variables for this Docker selection.
Never place any credential in the repository, a command argument, the evidence
output directory, or a committed shell file. The output directory must be an
absolute path outside the repository and must not traverse a symlink.

## Run

From the repository root, with the project environment activated:

```bash
python infrastructure/docker-agent/evidence/run_live_evidence.py \
  --output-directory /tmp/vital-relay-docker-live-evidence
```

Exit status meanings:

- `0`: every required live probe passed;
- `1`: the canonical bundle is non-passing because an external prerequisite is
  blocked;
- `2`: the harness, fixed-probe integrity, cleanup, privacy, or publication
  boundary failed. No plausible bundle is synthesized for this case.

If exact-project cleanup is initially incomplete, the same process performs at
most two retries through the retained application owner. It never reconstructs
a project or issues broad cleanup. The CLI emits one canonical, privacy-safe
cleanup-attempt record and still exits 2 even if a retry proves host absence.
If custody remains unresolved, the record uses
`docker_cleanup_unresolved`. No bundle from that failed attempt is published.

On bundle-producing runs, the CLI prints only the bundle digest, output path,
closed status, and blocker codes. A bundle is canonical JSON named
`<content_sha256>.json`, written with mode `0400`, and never replaced. Keep
generated bundles outside the repository. Do not commit captured live evidence.

Authenticity uses HMAC-SHA-256 over the domain-separated message
`vital-relay/docker-live-evidence/v1\0` followed by a canonical envelope that
contains the content, its digest, the live evidence class, issuer, and key ID. A
self-hash alone is not accepted. Verify a received bundle on a trusted host with
the same issuer, key ID, and signing key:

```bash
python infrastructure/docker-agent/evidence/run_live_evidence.py \
  --verify /absolute/path/to/<content_sha256>.json
```

Verification rejects non-canonical bytes, filename/content digest mismatch,
the wrong issuer or key ID, an unsigned or `test_only` bundle, a malformed
signature, and any content modification.

## What a passing bundle proves

The fixed manifest requires every probe below; omission is invalid:

1. Probe scripts match the manifest hashes.
2. External Docker, model, PostgreSQL, enrolled-session, incident, and port
   prerequisites are live.
3. Application startup completed `ProcessSandboxAgentRunner.validate_startup()`.
4. The exact closed worker manifest produced by the product boundary, its
   observed source digest and separate expected policy pin, the complete
   startup-snapshot digest, the fixed probe manifest and assets, the host
   harness, product sandbox/worker/gateway sources, exact Compose graph and
   project, immutable image IDs, image-config hashes, and root-filesystem graph
   hashes were captured. The observed and expected worker digests must match
   before Docker starts and again before signing.
5. `docker inspect` proves UID/GID 65532, read-only root, all capabilities
   dropped, no privilege escalation, no mounts or published ports, bounded
   memory/CPU/PIDs/file descriptors, bounded no-exec tmpfs, and only the exact
   internal network.
6. A fixed container based on the exact reviewed agent image proves protected
   paths absent, protected read and write denial, worker-tree and root write
   denial, unlisted egress denial, and allowed fixed vLLM/tool-gateway routing.
7. Wrong gateway paths and missing, expired, stale, cross-run, and unknown-tool
   authority are denied with exact closed codes.
8. A real command-authenticated `POST /v1/incidents/{id}/agent-runs` completes
   through Docker and the selected model. An exact retry returns the stored
   terminal record with exactly one Docker worker-create event.
9. Host PostgreSQL audit rows—not worker prose—correlate the allowed and denied
   tool requests by bounded status/error metadata and request/result hashes.
10. An exact worker kill and a paused-model timeout settle durable runs to
    normalized closed failures, and the exact named workers are absent
    afterward.
11. A fixed malformed-output process in the same immutable image and containment
    profile is rejected by the real `AgentRunResult` parser. This is fault
    evidence only and can never satisfy the successful command-API probe.
12. Application lifespan cleanup reports `docker_project_down` and
    `cleanup_complete` for the exact retained Compose project with no unresolved
    custody.

The signed content also binds dependency versions, transport settings, active
policy/model/runner identities, every inspected image and container ID, and the
attempt/project/run/invocation/audit identities used for correlation. Each run
has a fresh trusted-host challenge. Container events, API runs and retries,
faults, leases, kills, audit rows, and cleanup must match that challenge and the
exact attempt, Compose project, container, and run. Pre-existing containers,
stale rows or events, generic process-name matches, and cross-attempt evidence
are rejected.

Probe stdout is untrusted and cannot prove containment or a product outcome by
itself. The host collector corroborates it with exact attempt/project labels,
immutable Docker inspection, clean host-observed exit, an empty container
filesystem diff, fresh Docker events, exact container absence, durable database
state, and bounded host audit records. Probe authority is delivered to the
fixed container only over stdin. Docker configuration and inspection therefore
cannot contain it. The collector parses raw Docker/API/provider material in
memory and persists only closed booleans, counts, statuses, failure codes, and
SHA-256 identities. It omits bearer values, capabilities, signing material, raw
health or coordinates, tool/provider payloads, subprocess diagnostics, model
prose, prompts, and hidden reasoning.

## Blocker reporting

A blocked bundle reports only stable codes. In particular:

- `docker_cli_unavailable` or `docker_daemon_unavailable` means no Docker live
  claim was attempted;
- `model_configuration_absent`, `model_upstream_unavailable`, or
  `model_identity_mismatch` means no live model claim was made;
- `postgres_configuration_absent` or `postgres_unavailable` means no durable
  control-plane or host-audit claim was made;
- `enrolled_command_session_absent` means no command-authenticated API claim was
  made;
- `incident_prerequisite_absent` means the configured active incident identity
  or state version was unavailable;
- `api_port_unavailable` means the reviewed tool-gateway route could not be
  bound to the product API.

If a run is interrupted, use the application-reported cleanup custody from the
same process. Never reconstruct a project name and never issue broad Compose,
container, network, or filesystem cleanup.

Signer configuration is an evidence-system prerequisite, not an external
product blocker. If it is absent or invalid, the process exits 2 and publishes
nothing because an unsigned live or blocked artifact would not be authentic.
