# Docker operational sandbox profile

Docker is an explicit operator-selected alternative to the preferred NemoClaw
sandbox. It executes the real `vital-relay-agent-worker` and the same sealed
`SandboxWorkerEnvelope`/`AgentRunResult` boundary. It is never selected by a
NemoClaw exception, timeout, non-zero exit, malformed result, or tool denial.

## Containment and routing

The one-shot `agent` container has no host mounts or environment variables. It
runs as UID/GID 65532 with a read-only root filesystem, a bounded no-exec
`/tmp`, all capabilities dropped, no privilege escalation, bounded CPU,
memory, PIDs, and file descriptors, and only the `agent-internal` network.
That Docker network is `internal: true`, so the worker has no direct host or
internet route.

Two non-privileged, read-only gateways are dual-homed:

- `vllm-gateway` forwards only `GET /v1/models` and
  `POST /v1/chat/completions` to the configured host vLLM port. Its healthcheck
  requires the exact configured model to appear in `/v1/models`.
- `tool-proxy-gateway` accepts only
  `POST /internal/v1/agent/tools/invoke`, requires the bounded
  `X-Vital-Relay-Agent-Capability` header, forwards only that header and JSON
  body to the host, and never receives the signing key. The host route still
  verifies the capability and appends authoritative audit evidence.

The tool gateway's container healthcheck probes only its local listener and
expects the reviewed 403 denial on a fixed non-tool path. It does not connect
to the Vital Relay API. This lets application startup validate Docker before
Uvicorn listens without weakening the boundary: a real tool request still
returns bounded `503/application_failed` until the API upstream is available.
The vLLM gateway healthcheck continues to require its separately operated
upstream and exact selected model.

The gateways are not general HTTP CONNECT proxies and do not accept an
operator-supplied path. Agent responses, stderr, and gateway logs cannot turn
a failure into another execution attempt.

## Build and start the selected substrate

Run a host vLLM-compatible server on reviewed port 8001. It must bind an
interface reachable through Docker's `host.docker.internal` mapping (typically
`0.0.0.0:8001`, restricted to the local machine/Docker network by the host
firewall). The Vital Relay API must likewise bind `0.0.0.0:8000` for real tool
calls; the normal loopback-only development command is intentionally
insufficient for this Docker selection. The API need not be listening while
application startup validates Docker because the tool-gateway healthcheck is
local. Start the selected backend with:

```bash
PYTHONPATH=backend/src .venv/bin/python -m uvicorn \
  vital_relay.main:create_app --factory --host 0.0.0.0 --port 8000
```

Do not expose either host port beyond the contained demo environment. Do not
invoke the committed build profile directly for product execution; it is a
content-locked input to the host runner.

Application startup must construct `ProcessSandboxSelection` with
`sandbox=SandboxKind.DOCKER`, the canonical committed compose path, these fixed
worker routes, and zero model retries/temperature. An identical profile copied
to another path is not reviewed and is rejected.

```text
http://vllm-gateway:8080/v1
http://tool-proxy-gateway:8080/internal/v1/agent/tools/invoke
```

It must call `validate_startup()` before accepting agent runs. That check
captures every build input with no-follow reads, stages a digest-addressed
read-only context, builds all reviewed targets, records their image IDs,
renders and validates an ID-only graph, and starts both gateways with
`--no-build --pull never`. Each run revalidates source, tags, image IDs,
rendered graph, and gateway health, then executes the equivalent of the
following. Compose `run` has no `--no-build` flag; the validated runtime graph
contains no `build` keys, which makes a build impossible:

```text
COMPOSE_DISABLE_ENV_FILE=1 docker compose --env-file <reviewed-empty-env> \
  --project-name vital-relay-agent-<runtime-digest> \
  -f <immutable-id-only-runtime-profile> run --rm --no-deps \
  --pull never --no-TTY \
  --name vital-relay-agent-<runtime-digest>-worker-<run-uuid> agent
```

The JSON request enters on stdin and the normalized result exits on stdout.
The host assigns a run-specific bounded container name and attempts to
force-remove that exact container if the Docker CLI times out or is
interrupted. Cleanup failure cannot change the normalized closed result; the
durable host lease/capability still fences any late tool call, and the stable
name makes external cleanup auditable.

Before constructing the runner, the host strictly validates every top-level,
service, build, network, resource, namespace, and mount-related profile key and
value. Unknown fields fail closed, including `cap_add`, `configs`, `secrets`,
`volumes_from`, `devices`, privileged/host namespace modes, external resources,
and an `image` or build path outside the reviewed targets. It also verifies
SHA-256 locks for Compose, the Dockerfile-specific context allowlist,
Dockerfile, empty Compose environment, exact hash-locked dependencies, both
gateways, and the worker source tree including `sandbox.py`. Any source/profile
change requires an explicit digest review before Docker can be selected. All
image stages also use the reviewed multi-platform digest for the official
Python 3.14.6 slim base rather than a mutable tag.

Each quiet build must return exactly one `sha256:` image ID. That builder ID is
authoritative; immediate and between-target tag inspections are confirmation
only. The ID-only runtime graph is never derived from a mutable tag.

The launcher supplies the Docker CLI only a small locale/PATH environment plus
`COMPOSE_DISABLE_ENV_FILE=1`, and always names the reviewed empty env file.
The selected model is written into the immutable host-generated graph; upstream
addresses are exact reviewed constants rather than host environment
interpolation. It passes no database, provider, persona, signing, APNs, or
operator credential, and the agent service has no environment block.

The integration layer must retain the validated runner for application
lifetime and call its idempotent `close()` from lifespan teardown before
discarding or replacing it. After successful validation, the runner—not the
operator—owns the exact digest-derived gateway project and both staged temp
roots. Startup failure cleans provisional resources; successful close fences
future runs, executes `down` with the exact retained
profile/environment/project, and removes only recorded launcher-created roots.
If `down` fails, the runner retains that exact graph/project/executor and the
staged roots required to retry it. If a root removal fails, it retains only the
narrowly validated unresolved root. Repeated `close()` retries that custody;
the runner clears each identity only after success. Cleanup attempt history and
prior failed checks are monotonic, and `cleanup_complete` is absent while any
resource remains unresolved. Retain and report that evidence. Never reconstruct
a project name or issue broad Compose or filesystem cleanup.

## Acceptance evidence

Do not call static compose inspection live containment evidence. On a host with
Docker, backend TLS/HTTP reachability, PostgreSQL, and the selected vLLM model,
capture all of the following:

1. image digests and `docker compose config` for the exact profile;
2. `validate_startup()` evidence with both gateways healthy;
3. `docker inspect` evidence for non-root identity, read-only root, dropped
   capabilities, resource limits, no mounts/ports, and only the internal agent
   network;
4. protected-file write/read denial and direct unlisted-host egress denial from
   the real worker image;
5. allowed vLLM and capability-authenticated tool calls through the fixed
   routes, plus wrong-path, missing/expired/cross-run/stale/unknown-tool
   denials;
6. the host proxy audit rows corresponding to tool denials—worker-reported
   denial text is not authoritative evidence;
7. normalized successful, crash, timeout, malformed-output, and denial results
   with one durable run lease and no second sandbox invocation; and
8. absence of capabilities, keys, prompts, hidden reasoning, and raw gateway
   diagnostics from results and logs.

No Docker, vLLM, TLS, or live denial evidence is claimed by this repository
when those external runtimes are unavailable.
