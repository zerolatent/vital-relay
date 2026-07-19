# Vital Relay NemoClaw live profile

NemoClaw is the preferred operator-selected containment substrate for Agent A3.
Docker is a separately selectable operational profile; neither runtime is an
automatic fallback or retry for the other. The repository contains the
authenticated sandbox-to-host transport and durable control plane, but this
file does not claim that a local NemoClaw, vLLM model, TLS gateway, or live
denial probe has run successfully.

## Security boundary

- Start onboarding LangChain Deep Agents Code with the **Restricted** policy
  tier, then replace and attest the complete base/effective network policy as
  described below. Restricted is not the final Vital Relay policy.
- Route inference only through NemoClaw's managed
  `https://inference.local/v1` endpoint.
- Treat `presets/vital-relay-tool-proxy.yaml` as an additive review fragment,
  not a complete containment policy. Add its route to the exported complete
  base policy only after the reviewed `vital-relay.internal:8443` TLS route
  terminates at this backend.
- Do not enable GitHub, PyPI, web search, messaging, public-reference, shell,
  or arbitrary package-install presets for the incident agent.
- Keep protocol content, evaluator files, signing keys, persona credentials,
  device/responder tokens, and APNs keys outside the sandbox.
- Do not export prompts, tool arguments, results, or hidden reasoning to
  remote observability.
- Treat any unapproved egress, tool denial, timeout, malformed result, or
  sandbox failure as `manual_required`. No alternate planner is invoked.

The host creates a durable leased run before model execution and issues one
HMAC-authenticated capability whose expiry cannot exceed that lease. The
capability binds run, scope, incident, state version, active policy hash, and a
state-specific tool subset. Only the capability—not its signing key and not an
operator session—crosses stdin into the sandbox. Tool calls return through the
single private POST path and are re-authorized against fresh application state.

## Host configuration

Set the Agent A3 variables documented in `.env.example`. The NemoClaw selection
accepts only:

- `VITAL_RELAY_AGENT_SANDBOX=nemoclaw`;
- `https://inference.local/v1` for vLLM-compatible inference;
- `https://vital-relay.internal:8443/internal/v1/agent/tools/invoke` for tools;
- an unpadded base64url signing key that decodes to 32–64 bytes; and
- an exact policy artifact with a matching canonical SHA-256 sidecar.

On first enablement the command enrollment bootstrap must be present so the
reviewed baseline policy can be activated by a durable command account. Later
startups never overwrite a promoted active-policy pointer; a configured
artifact/pointer mismatch stops composition.

Before onboarding, run the read-only model/tool-calling probe against the
host-routable upstream vLLM URL (the sandbox-only `inference.local` name is not
valid from the host):

```bash
vital-relay-agent-readiness \
  --model "$VITAL_RELAY_VLLM_MODEL" \
  --base-url http://127.0.0.1:8001/v1
```

After onboarding, make NemoClaw's in-sandbox route checks authoritative:

```bash
nemo-deepagents vital-relay status
nemo-deepagents vital-relay doctor
```

The worker uses NemoClaw's non-provider placeholder
`nemoclaw-managed-inference`; never replace it with an upstream API key.

## Stage the worker runtime

NemoClaw keeps its managed `/opt/venv` read-only. Agent A3 therefore executes
an independently staged project environment at
`/sandbox/vital-relay-runtime`. Before enabling the backend:

1. Build a self-contained, relocatable CPython 3.14 runtime and offline
   wheelhouse for the sandbox's exact architecture and libc. The managed Deep
   Agents image currently uses Python 3.13, while this project requires
   `>=3.14,<3.15`; do not derive the project environment from `/opt/venv`.
   Record and review the interpreter, standard-library, wheel, and
   native-library hash inventory on the host.
2. Build the dedicated worker-only wheel from the exact reviewed source
   manifest:

   ```bash
   python3.14 infrastructure/nemoclaw/build_worker_bundle.py build \
     --python /path/to/reviewed/python3.14 \
     --output ./vital-relay-worker-bundle
   ```

   The builder rejects missing imports, unexpected source, symlinks, and any
   archive member outside the exact worker manifest. It emits independent
   source, wheel, and dependency-lock hashes. Never substitute the ordinary
   `vital-relay` project wheel: it contains trusted-host application,
   evaluator, signing, persistence, and promotion source.
3. Upload the CPython runtime, offline dependency wheelhouse, and this reviewed
   worker bundle with
   `nemo-deepagents vital-relay upload ./vital-relay-bundle/ /sandbox/vital-relay-bundle/`.
4. Expand the reviewed CPython 3.14 runtime directly at
   `/sandbox/vital-relay-runtime`. Its site-packages directory must be fresh:
   only the reviewed `pip`, `setuptools`, and `wheel` bootstrap distributions
   may pre-exist. Do not install over a runtime that ever contained the full
   `vital-relay` wheel. Install and validate dependencies plus the worker wheel
   in one closed operation using the runtime interpreter:

   ```bash
   RUNTIME_SITE="$('/sandbox/vital-relay-runtime/bin/python3.14' \
     -I -c 'import site; print(site.getsitepackages()[0])')"
   python3.14 infrastructure/nemoclaw/build_worker_bundle.py install \
     --python /sandbox/vital-relay-runtime/bin/python3.14 \
     --bundle ./vital-relay-worker-bundle \
     --wheel-sha256 REVIEWED_BUILD_OUTPUT_WHEEL_SHA256 \
     --wheelhouse ./reviewed-worker-wheelhouse \
     --target "$RUNTIME_SITE"
   ```

   Installation uses `--no-index`, hash-required dependencies, and the
   worker-only wheel with `--no-deps`. The wheel SHA-256 must be copied from the
   separately reviewed build output. Validation binds every archive member,
   `RECORD` digest, console entry point, and installed launcher to that wheel.
   It rejects unrecorded files, unhashed bytecode, `sitecustomize.py`,
   `usercustomize.py`, every `.pth` hook, and any non-fresh environment. It also
   compares the dependency distribution inventory to the separate reviewed
   lock, compares installed `vital_relay` members byte-for-byte to the source
   manifest, and runs negative module-spec checks for every trusted-host module
   class. Never open PyPI egress or copy packages from `/opt/venv`.
5. Confirm `/sandbox/vital-relay-runtime/bin/vital-relay-agent-worker`, the
   exact installed dependency inventory, and the absence of the ordinary
   `vital-relay` distribution, then capture their hashes with the live
   acceptance evidence.
6. Invoke `/sandbox/vital-relay-runtime/bin/python3.14` through the managed-exec
   launcher and record `/proc/self/exe`. OpenShell authorizes the kernel-trusted
   executable path rather than `argv[0]`. If the canonical path differs, use
   that exact value in both policy entries below. Never use a `python*` or
   recursive runtime wildcard.
7. Make `/sandbox/vital-relay-runtime` immutable to the sandbox process with a
   reviewed read-only image layer or mount, then retrieve and verify its full
   host-side manifest. Path-based egress authority must not survive writable
   runtime replacement across runs. If the selected NemoClaw deployment cannot
   make this path read-only, keep Agent A3 disabled; a self-check performed by
   writable worker code is not sufficient.
8. Do not use this general Deep Agents sandbox for interactive coding after it
   becomes an incident runtime. Rebuild/restage from the reviewed bundle when
   runtime bytes change.

The project environment contains no provider, persona, signing, or APNs
credential. NemoClaw continues to supply inference routing from its protected
managed configuration.

## Replace and attest the complete network policy

Deep Agents' agent-specific baseline includes GitHub and package endpoints even
when onboarding starts from Restricted. `policy-add` merges endpoints, so the
checked-in fragment cannot remove those routes. Previewing it is useful only to
review the route it would add:

```bash
nemo-deepagents vital-relay policy-add \
  --from-file infrastructure/nemoclaw/presets/vital-relay-tool-proxy.yaml \
  --dry-run
```

Before enabling A3, export the round-trippable base policy, edit that complete
document to remove every GitHub, PyPI, package, search, messaging, and other
network entry, and add only the exact Vital Relay tool route. Preserve the
reviewed filesystem/process controls and the existing managed-inference
endpoint/rules, but extend `network_policies.managed_inference.binaries` with
the exact canonical `/sandbox/vital-relay-runtime/bin/python3.14` executable
observed above. The stock Deep Agents entry authorizes only `dcode` and managed
Python 3.13 paths, so preserving it unchanged denies this worker. The
`vital_relay_tool_proxy` entry must authorize that same exact interpreter (and
the exact worker entry point if retained) and no `dcode`, `/opt/venv`, wildcard,
shell, curl, or package-manager binary.
Never round-trip `--raw` or `--full` output through `policy set` because it can
contain metadata or provider-composed entries.

```bash
nemo-deepagents vital-relay policy-get > vital-relay-base-policy.yaml
# Review and edit the complete base document, then replace it atomically.
openshell policy set vital-relay \
  --policy vital-relay-base-policy.yaml \
  --wait
openshell policy get vital-relay --base > applied-base-policy.yaml
openshell policy get vital-relay --full > applied-effective-policy.yaml
```

Canonicalize and hash both retrieved policies, record the exact NemoClaw
release/commit, OpenShell version, sandbox image digest, worker-runtime hash,
and policy revision, and mechanically reject every unapproved host/rule. A3
must remain disabled if the live effective policy differs from the reviewed
allowlist. Re-attest after every start, rebuild, inference/provider change, or
policy operation; NemoClaw can recompose provider entries and reapply registered
presets across lifecycle operations.

The effective-policy assertion must prove all of the following before the
backend is enabled:

- `managed_inference` retains only its reviewed OpenAI-compatible model,
  completion, and embedding paths, and includes the exact staged Python
  executable;
- `vital_relay_tool_proxy` permits only `POST` to the fixed internal path and
  only the exact staged executable/entry point;
- no direct upstream provider, GitHub, PyPI, package, search, messaging,
  observability, shell, curl, or wildcard network permission remains; and
- a real worker reaches `inference.local` and the internal tool route, while
  the same interpreter is denied for an unlisted host.

The backend invokes one worker per run with this fixed argv vector (no shell):

```text
/usr/local/bin/nemo-deepagents vital-relay exec --no-tty --timeout 90 --stdin -- /usr/local/lib/nemoclaw/dcode-managed-exec /sandbox/vital-relay-runtime/bin/vital-relay-agent-worker
```

Install or provision the reviewed NemoClaw CLI at that exact host path. The
backend launches it with a minimal environment containing only fixed locale and
PATH values plus bounded host/XDG directory identities; it does not pass any
`VITAL_RELAY_*` configuration, database URL, or provider credential to the CLI.

The image-owned managed-exec launcher reconstructs the root-pinned OpenShell
proxy, CA, and resource-limit environment for raw exec processes. The worker
independently verifies those proxy values against the root-owned image files
and passes an explicit CONNECT proxy and SSL context to HTTPX with ambient
proxy discovery disabled.

Do not manually construct the stdin envelope. It deliberately contains a
short-lived bearer capability and is safe only on the host-to-sandbox pipe.

## Required live evidence

Agent A3 is operationally accepted only after evidence shows:

1. NemoClaw/OpenShell report the pinned versions/image and the retrieved base
   and full effective policies exactly match the reviewed hashes and allowlist.
2. The staged worker and dependency hashes match the reviewed host inventory,
   and the exact runtime path is read-only to the sandbox process.
3. `/v1/models` exposes the exact configured tool-capable model through
   `inference.local` from the staged executable; an unlisted binary is denied.
4. An authenticated command run creates one durable record, makes only its
   state-allowed tool calls, and reaches a normalized terminal result.
5. Exact retry returns the stored result without a second model invocation.
6. Protected-file access and every unlisted network destination are denied.
7. Expired, cross-run, cross-scope, stale-state, revoked-policy, and unknown-tool
   capabilities are denied and append privacy-bounded audit evidence.
8. Killing a worker leaves a lease that can be reconciled only after its tool
   authority has expired; a late result is stored as `manual_required`.
9. Results, API responses, audits, and logs contain no credentials or hidden
   reasoning.

Until this evidence is captured, the code path is implemented and tested but
the external NemoClaw/vLLM deployment gate remains open.
