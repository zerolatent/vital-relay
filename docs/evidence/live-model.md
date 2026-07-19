# Live local-model evidence

This lane exports trusted-host-authenticated evidence only after the real Vital
Relay product path completes one typed `get_incident` call. Model discovery
alone can never produce success evidence.

No live capture, model/API credential, command-session token, capability,
database URL, or signing key is checked into this repository. Unit fixtures
exercise schemas, transport policy, HMAC verification, Docker inspection,
product-response validation, and failure behavior; they are not live evidence.

## Two distinct isolation identities

Accepted evidence identifies two separate boundaries:

1. `model_service` is the reviewed vLLM Docker container. The host verifies its
   full container ID, image ID, operator-reviewed normalized inspect digest,
   read-only root, dropped capabilities, `no-new-privileges`, non-host
   namespaces, non-sensitive read-only mounts, and sole host mapping
   `127.0.0.1:8001 -> 8000/tcp`.
2. `agent_execution` is the product's `ProcessSandboxAgentRunner` Docker worker.
   The worker is started only by the durable `AgentRunService`, uses the sealed
   `SandboxWorkerEnvelope`, calls the fixed model and tool-proxy gateways, and
   returns `AgentRunResult.sandbox=docker`.

The exporter never constructs `DeepAgentRunner` on the trusted host. It never
uses an in-memory `BoundedToolGateway` for an accepted artifact. A Dockerized
model combined with a host `IN_PROCESS` agent result is rejected before HMAC
signing. `--sandbox in_process` remains parse-compatible and exits nonzero with
`in_process_not_live_evidence`.

## Real product path

The exporter first verifies `GET http://127.0.0.1:8001/v1/models`, then uses a
fresh run ID to call the already-running product API at exact loopback
`http://127.0.0.1:8000`:

- it reads the configured durable incident and timeline with a real command
  session;
- it reconstructs the exact privacy-bounded `AgentRunRequest` and selected ACE
  context from the pinned policy and worker inference configuration;
- it sends one `POST /v1/incidents/{incident}/agent-runs` request;
- the product service creates a durable PostgreSQL run, issues the real
  capability, invokes `ProcessSandboxAgentRunner`, and reconciles worker output
  against append-only authenticated host tool-proxy audit; and
- the exporter accepts only HTTP 201, a completed `SandboxKind.DOCKER` record,
  one `HOST_PROXY_AUDIT` `get_incident` trace, and the exact expected tool
  request/result SHA-256 values.

A replayed HTTP 200 record, runtime-authored trace, empty/incomplete trace,
wrong tool hash, wrong model, wrong state, or in-process result fails nonzero.
Raw incident, timeline, protocol, model response, and subprocess bodies remain
in memory only and are never emitted.

## Exact worker-manifest and runtime integration contract

The exporter now imports the integrated `DOCKER_AGENT_SOURCE_MANIFEST` and
captures it with `capture_reviewed_source_snapshot`. The operator-reviewed
manifest input must equal that actual source digest, the product response must
report the same digest, and the exporter recaptures it before and after the
live request. The manifest is the exact sandbox-safe worker set; no full
backend package is staged or accepted.

The integrated `ProcessSandboxAgentRunner` does not yet carry its startup,
runtime, or raw worker request/result evidence through `AgentRunService` to the
HTTP response. The exporter therefore keeps a fail-closed adapter contract. A
fresh successful agent-run response must provide these headers:

| Header | Required meaning |
| --- | --- |
| `X-Vital-Relay-Agent-Worker-Manifest-SHA256` | Exact closed set of sandbox-safe worker source/dependency bytes. |
| `X-Vital-Relay-Agent-Runtime-Snapshot-SHA256` | Exact built image IDs and validated Docker runtime graph used for the run. |
| `X-Vital-Relay-Agent-Startup-SHA256` | Host-authored successful startup-check snapshot for that runner instance. |
| `X-Vital-Relay-Agent-Worker-Request-SHA256` | Exact sealed worker-envelope bytes passed to the Docker worker. |
| `X-Vital-Relay-Agent-Worker-Result-SHA256` | Exact bounded worker result bytes received before host reconciliation. |
| `X-Vital-Relay-Agent-Boundary-HMAC-SHA256` | Run-bound HMAC over all five hashes, fresh run ID, and canonical product response hash. |

The manifest value must match the locally captured exact source digest. The
runtime and startup values must match their separately reviewed inputs. The
per-run worker request/result hashes must be well-formed. The product boundary
HMAC is verified with the reviewed tool-proxy signing key before the outer
evidence HMAC is created, so a captured header set cannot satisfy a fresh run.
A missing or mismatched header prevents signing. The response's Docker identity
and host-proxy trace remain independently validated.

Shared integration must have `ProcessSandboxAgentRunner` retain its exact
`ReviewedDockerSnapshot`, built image IDs/runtime graph, host-authored
`SandboxStartupEvidence`, serialized `SandboxWorkerEnvelope` hash, and bounded
worker stdout hash. `AgentRunService` must bind those values to the fresh run
and canonical durable response, authenticate them with the tool-proxy signing
key, and return the headers above. Until that wiring exists, the real CLI exits
nonzero with `agent_execution_snapshot_unavailable`; fixtures only validate
this adapter and never count as live evidence. The shared Docker integrity pin
must be composed by the integration owner; this lane does not change it.

## Transport boundary

Host catalog discovery uses one explicit `httpx.Client` with
`trust_env=false`, redirects disabled, and `HTTPTransport(retries=0)`. The
product API client uses the same closed transport properties and exact
`http://127.0.0.1:8000` base URL.

The model completion client is necessarily a distinct client because it runs
inside the agent worker process. The bound worker implementation applies the
same explicit no-environment, no-redirect, zero-retry construction. Its fixed
route is `http://vllm-gateway:8080/v1`; the reviewed gateway permits only
`/v1/models` and `/v1/chat/completions` and forwards to the host model service
at port 8001. The fixed tool route is
`http://tool-proxy-gateway:8080/internal/v1/agent/tools/invoke`.

Every proxy/custom-CA variable recognized by this harness is rejected before
Docker inspection or network activity. Docker inspection uses an absolute
operator-pinned CLI path and digest, the fixed local Unix socket, a nonexistent
configuration directory, and a minimal environment. No environment-derived
proxy, CA, endpoint, Docker context, or TLS setting is accepted.

## Operator-pinned model claim

The selected model ID, revision, and artifact SHA-256 are an
`operator_pinned_not_endpoint_attested` claim. The exporter verifies only that
the exact model ID appears in `/v1/models`. The OpenAI-compatible API does not
cryptographically attest weights, revision, or artifact digest. The trusted
host HMAC authenticates the operator claim together with the observed run;
reviewers must establish the model artifact and image pins separately.

## Required configuration

The product API must already be running on `127.0.0.1:8000` with PostgreSQL,
the Docker agent runtime, model gateway, and authenticated tool-proxy route
ready. Its normal reviewed Docker configuration must be present:

```bash
export VITAL_RELAY_AGENT_ENABLED='true'
export VITAL_RELAY_AGENT_SANDBOX='docker'
export VITAL_RELAY_DATABASE_URL='<secret PostgreSQL URL>'
export VITAL_RELAY_AGENT_TOOL_PROXY_SIGNING_KEY='<unpadded base64url>'
export VITAL_RELAY_VLLM_MODEL='organization/exact-model-id'
export VITAL_RELAY_VLLM_MODEL_REVISION='immutable-reviewed-revision'
export VITAL_RELAY_VLLM_MODEL_ARTIFACT_SHA256='<64 lowercase hex>'
export VITAL_RELAY_DOCKER_VLLM_API_KEY='<loopback vLLM key>'
```

Provide the evidence authority, real product input, model-service identity, and
reviewed execution assertions out of band:

```bash
export VITAL_RELAY_MODEL_EVIDENCE_ISSUER='reviewed-host-identity'
export VITAL_RELAY_MODEL_EVIDENCE_KEY_ID='reviewed-hmac-key-id'
export VITAL_RELAY_MODEL_EVIDENCE_HMAC_KEY='<unpadded base64url, 32-64 bytes>'

export VITAL_RELAY_MODEL_EVIDENCE_COMMAND_TOKEN='<real command session token>'
export VITAL_RELAY_MODEL_EVIDENCE_INCIDENT_ID='<eligible durable incident UUID>'
export VITAL_RELAY_MODEL_EVIDENCE_STATE_VERSION='<positive integer>'

export VITAL_RELAY_VLLM_SANDBOX_CONTAINER_ID='<full 64-char container ID>'
export VITAL_RELAY_VLLM_SANDBOX_IMAGE_SHA256='<64 lowercase hex>'
export VITAL_RELAY_VLLM_SANDBOX_INSPECT_SHA256='<reviewed normalized SHA-256>'
export VITAL_RELAY_DOCKER_CLI_PATH='/absolute/reviewed/docker'
export VITAL_RELAY_DOCKER_CLI_SHA256='<64 lowercase hex>'

export VITAL_RELAY_MODEL_EVIDENCE_WORKER_MANIFEST_SHA256='<64 lowercase hex>'
export VITAL_RELAY_MODEL_EVIDENCE_AGENT_RUNTIME_SNAPSHOT_SHA256='<64 lowercase hex>'
export VITAL_RELAY_MODEL_EVIDENCE_AGENT_STARTUP_SHA256='<64 lowercase hex>'

PYTHONPATH=backend/src \
  python -m vital_relay.agent.model_live_evidence --sandbox docker
```

`VITAL_RELAY_MODEL_EVIDENCE_WORKER_MANIFEST_SHA256` is not an arbitrary
operator identity. It must equal the digest captured from the active integrated
`DOCKER_AGENT_SOURCE_MANIFEST`; a stale or substituted value is rejected before
network activity.

Do not set `VITAL_RELAY_VLLM_BASE_URL` for this Docker lane. The host catalog
route and worker gateway route are fixed in reviewed code. Optional policy path
and agent-timeout settings must match those used by the running product.

Unset `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`, lowercase proxy
equivalents, `OPENAI_BASE_URL`, `OPENAI_PROXY`, `SSL_CERT_FILE`, `SSL_CERT_DIR`,
`REQUESTS_CA_BUNDLE`, and `CURL_CA_BUNDLE` before running.

The vLLM inspect pin is computed during trusted configuration review from the
exporter's privacy-safe normalization. It binds the exact process, container
configuration, host configuration, image, mount inventory, and loopback port
without exporting raw values. Recreating or changing the container requires a
new review and pin.

## Authenticated evidence boundary

Success stdout is one canonical JSON object. `evidence_sha256` addresses the
public material; `attestation_hmac_sha256` authenticates that content address
and all public fields. Consumers must call `verify_live_model_evidence` with
the trusted key and expected issuer/key ID. Checking the self-hash alone is
insufficient.

The authenticated configuration digest binds:

- host harness, configuration, HTTP clients, `ProcessSandboxAgentRunner`,
  worker, product service, durable control plane, reconciliation, tool proxy,
  context selection, Docker profile, and gateway source hashes;
- exact closed worker manifest, startup snapshot, runtime snapshot, worker
  command policy, model-service image/inspect, and transport policy;
- catalog and worker inference configuration hashes, policy, typed-tool
  definitions, selected-context IDs/hashes, retry/timeout/tool budgets; and
- exact product request, reconstructed `AgentRunRequest`, sealed worker
  envelope, raw bounded worker result, durable response, normalized result,
  and host-proxy tool request/result hashes.

The artifact omits prompts, conclusions, command tokens, API/capability/HMAC
keys, database URLs, raw incident IDs, raw health data, locations, tool bodies,
provider/subprocess output, and hidden reasoning.

## Truthful failures

Failure stdout contains only a closed canonical failure code and exits nonzero:

| Exit | Meaning |
| ---: | --- |
| 2 | Reviewed configuration, policy, signing authority, or transport is invalid. |
| 3 | Exact loopback model endpoint/catalog is unavailable or invalid. |
| 4 | Exact claimed model ID is not listed. |
| 5 | Product API, PostgreSQL-backed control plane, or Docker worker run fails. |
| 6 | Host-proxy trace or exact tool request/result binding is incomplete. |
| 7 | Source/config hashing, evidence schema, HMAC, or privacy validation fails. |
| 8 | Selected CLI sandbox is not eligible. |
| 9 | Reviewed model-service or agent-execution sandbox metadata is unavailable. |
| 10 | An unclassified exporter failure is closed without diagnostics. |

The command performs one model-catalog attempt and one product-run attempt. It
never substitutes a stub, replay, fixture, host Deep Agent, in-memory tool
gateway, proxy, custom-CA route, or non-loopback product/model endpoint.
