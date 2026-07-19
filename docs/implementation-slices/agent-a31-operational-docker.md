# Agent A3.1: operator-selected operational Docker sandbox

## Outcome

This slice adds a real Docker execution substrate behind the same Agent A3
request, policy snapshot, durable lease, short-lived capability, bounded tool
gateway, and normalized result boundary used by NemoClaw. NemoClaw remains the
preferred operator setting. `ProcessSandboxSelection` represents exactly one
of NemoClaw or Docker and `ProcessSandboxAgentRunner.selected()` constructs
only that choice; it contains no probing, exception catch, or fallback branch
that can execute the other substrate.

The Docker runner seals `SandboxKind.DOCKER`, the fixed vLLM gateway route, and
the fixed tool-proxy gateway route into the existing versioned worker envelope.
The envelope cross-checks run, incident, state version, policy hash, capability
tool subset, zero retry/temperature, and runtime routes before the worker can
start. The worker chooses the Docker HTTP client solely from that sealed kind.
The client disables redirects and ambient proxy discovery. A Docker failure is
never handed to the NemoClaw client or CLI.

## Real Docker path

`infrastructure/docker-agent/Dockerfile` installs the committed Python 3.14
dependency lock with `pip --require-hashes --only-binary=:all:` and runs the
captured package source with `python -m vital_relay.agent.worker` as the final
unprivileged entrypoint. There is no build-time dependency resolution outside
the exact versions and artifact hashes in `requirements.lock`. The one-shot
compose command receives the existing bounded JSON envelope on stdin and emits
only `AgentRunResult` JSON on stdout.

The compose profile gives the worker:

- UID/GID 65532, a read-only root, and no host mounts or environment;
- a bounded `noexec,nosuid,nodev` temporary filesystem;
- no Linux capabilities, no privilege escalation, no privileged/device/port
  access, and bounded CPU, memory, PIDs, and file descriptors; and
- only an internal Docker network with no direct host or public egress.

The agent can reach two dedicated gateways on that internal network. The vLLM
gateway forwards only model listing and chat completions to the reviewed
`host.docker.internal:8001` upstream. The tool gateway forwards only the fixed
Agent A3 POST path to the reviewed `host.docker.internal:8000` upstream, rejects
a missing or malformed bounded capability before forwarding, copies only the
capability and JSON transport headers/body, and holds no signing key. The host
tool proxy remains the authentication, authorization, application-state, tool
budget, idempotency, and append-only audit authority.

## Startup and normalized evidence

The Docker constructor accepts only the canonical committed compose file before
it can claim `SandboxKind.DOCKER`; an identical arbitrary-path copy is rejected.
It strictly validates the complete top-level/service key sets and exact values,
including services/networks, build context/Dockerfile/targets, internal worker
network, real worker entrypoint, read-only roots, capability drops, namespace
settings, no host resources, and resource limits. Unknown additions such as
`cap_add`, configs/secrets, `volumes_from`, devices, external resources,
privileged/host namespace modes, or an image override fail closed.

The host also verifies content locks for Compose, Dockerfile, its dedicated
build-context allowlist, the empty Compose environment, the exact dependency
lock, both gateways, and every Python/JSON worker source input. The full source
digest includes the active `sandbox.py`. Files are opened through no-follow
directory descriptors, captured once, and staged into a digest-addressed,
read-only build context, closing source/build TOCTOU and intermediate-symlink
substitution. The Dockerfile pins all stages to the reviewed multi-platform
digest of the official Python 3.14.6 slim base image.

`validate_startup()` explicitly builds all three reviewed targets from that
snapshot with no cache, records their final image IDs, and generates a fixed
digest-derived project/profile whose services reference only those exact IDs.
Compose runs with the reviewed empty `--env-file` and
`COMPOSE_DISABLE_ENV_FILE=1`. Its full JSON rendering is compared to the
host-constructed expected graph before gateway startup. Gateway startup uses
`--no-build --pull never`; image tags, running gateway image IDs, graph, and
health are checked again before every worker launch, which uses `--pull never`
against a graph in which `build` is forbidden. (Compose `run` has no
`--no-build` option; absence of every build stanza is the fail-closed no-build
semantic.) A replaced local tag, changed source, changed lock,
or rendered graph drift fails closed before the envelope is sent.

The builder's single quiet `sha256:` result is the authoritative ID for each
target. Tag inspection may only confirm that ID; it can never replace it.
Current and previously built tags are rechecked between targets, and all tags
are checked again before the runtime graph is generated, so build-to-inspect
or between-target substitution cannot become the accepted baseline.

Staged build/profile roots and the digest-derived project remain provisional
until post-start verification succeeds. Any failure after staging performs
best-effort cleanup of those narrowly validated roots and, once `up` may have
created resources, runs `down` using the exact immutable profile, environment,
and project. Cleanup failure is host-authored evidence and fences retry without
replacing the primary startup failure. A successful runner owns those
resources and exposes an idempotent `close()`: it fences future runs first,
brings down only its exact project, and removes only its recorded temp roots.
A failed `down` retains the exact runtime graph, executor, and every staged
root needed to retry that same `down`; those roots are not deleted out from
under the retained Compose command. A failed root removal likewise retains
only that validated launcher-owned root. `close()` retries unresolved custody,
clears each identity only after confirmed success, and is safe to call again.
Cleanup attempt history is append-only: later success can mark the resources
resolved, but aggregate evidence preserves every earlier failed check and
never reports `cleanup_complete` while a project or root remains unresolved.
Startup and execution stay fenced during cleanup and whenever unresolved
custody exists.

The tool-proxy healthcheck deliberately validates its own reviewed
configuration, listener, and fixed denial behavior without connecting to the
Vital Relay API. This avoids a cold-start cycle when application composition
calls `validate_startup()` before Uvicorn begins listening. Tool forwarding is
not treated as ready by that local check: until the API listener exists, an
otherwise valid request receives the gateway's bounded
`503/application_failed` denial. vLLM gateway readiness remains tied to its
separately operated model upstream. NemoClaw uses the same startup API for its
fixed `status` and `doctor` checks. Only check names and the selected kind are
returned; subprocess diagnostics are not copied into evidence.

At run time, host-observed timeout becomes `manual_required/model_timeout`.
Non-zero exit, crash, output overflow, malformed JSON, or mismatched normalized
identity becomes `manual_required/runner_error`. The result always retains the
operator-selected sandbox. The launcher runs once, and tests assert that a
Docker failure never emits a NemoClaw command. Each worker also receives a
run-specific bounded container name; an exceptional launcher exit triggers a
best-effort force-removal of that exact name while the durable host authority
still fences late tool calls if cleanup itself is unavailable.

Tool denial authority remains host-authored: the authenticated host route
appends the request/result hashes, invocation identity, effect, and bounded
denial code; persistence reconciles worker traces against those audits. A
sandbox's narrative about a tool denial is not accepted as authoritative.
Protected-file and unlisted-egress denials must likewise be captured from a
real Docker deployment and corroborated with host `docker inspect`/network
configuration; fixture or worker-asserted text is not live evidence.

## Focused verification

The focused runner/transport/profile tests cover:

- one normalized envelope and result shape for both runners;
- explicit, mutually exclusive operator selection;
- fixed runtime-specific model and tool routes;
- Docker worker transport with no NemoClaw probe;
- startup health validation for both gateways;
- API-cold-start-compatible local tool-gateway readiness with request-time
  upstream failure still closed;
- normalized Docker timeout/crash behavior with no cross-sandbox retry;
- explicit no-ambient-proxy Docker HTTP construction; and
- static containment gates for entrypoint, identity, mounts, networks,
  privilege/resource restrictions, exact gateway routes, adversarial
  `SYS_ADMIN`/host-file/namespace injection, provenance tampering, and content
  locks, implicit `.env` redirects, cached-image substitution, image-ID drift,
  rendered graph drift, dependency-lock tampering, and post-construction source
  mutation; and
- authoritative builder-ID parsing, build-to-inspect/between-target tag swaps,
  post-up and partial-start cleanup, exact-project close, repeated/early close,
  startup-failure-to-close custody, failed-down and failed-root retry,
  monotonic/no-false-clean evidence, retry hygiene, and rejection of broad
  cleanup targets.

## External evidence status

On the implementation host, `docker`/Docker Compose, `nemo-deepagents`,
OpenShell, and a vLLM service on `127.0.0.1:8001` were unavailable. Therefore
this slice does not claim an image build, live Docker containment probe, live
NemoClaw/TLS run, model result, or host-audit denial capture. The executable
paths and acceptance commands are documented in the infrastructure READMEs so
an environment with those runtimes can capture the required evidence without
substituting a fake provider or sandbox.

## Integrated runtime wiring

The shared composition now carries a `ProcessSandboxSelection` and accepts only
the exact `nemoclaw` or `docker` selector. NemoClaw remains the checked-in
example and preferred deployment, but there is no implicit default. Docker
always resolves to the canonical committed Compose file; profile paths,
project names, gateway routes, and upstream addresses are not configurable.
Settings belonging to the unselected runtime are rejected even when present
with an empty value.

Both paths use the same model ID, host-only capability signing key, bounded
timeout, and pinned policy. NemoClaw additionally requires its sandbox name,
managed inference URL, and exact TLS tool route. Docker instead requires only
its vLLM API key and seals the fixed HTTP gateway routes into the worker
envelope. The Docker key remains redacted and no signing, database, persona, or
provider credential enters the container graph.

Application composition calls
`ProcessSandboxAgentRunner.selected(ProcessSandboxSelection(...))`, validates
that one runtime exactly once, and constructs `AgentRunService` only after
readiness succeeds. A failure is propagated without probing the other runtime.
Lifespan teardown drains the bounded tool pool before closing the retained
runner, then continues through notification, routing, and database cleanup
without masking a primary failure. Startup and cleanup evidence are published
on application state. If cleanup remains unresolved, application state retains
an `agent_sandbox_cleanup_retry` callable bound to that exact runner; a
composition exception likewise retains the same callable on its
`agent_sandbox_cleanup_retry` attribute. A retry refreshes the monotonic
evidence/history and clears the application-state callable only after custody
is resolved.

For Docker, vLLM must be reachable through `host.docker.internal:8001`, and the
API must bind a host interface reachable through
`host.docker.internal:8000`—typically `0.0.0.0` with host firewall exposure
limited to the contained demo environment. The exact launch command is in the
Docker infrastructure README. This integration changes only configuration and
composition; it adds no persistence, migration, evolution, promotion, or
application-service behavior.
