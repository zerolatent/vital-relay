# Slice 10A — Agent Runtime and Sandbox Foundation

**Branch:** `codex/wt50-agent-sandbox`
**Base:** `c8a6322` (committed Slice 08)
**Status:** Runtime contracts, Deep Agents adapter, synthetic tool path, and
containment profiles implemented; live vLLM/NemoClaw execution remains an
environment gate.

## Purpose

Establish one model-owned coordination runtime without adding a deterministic
planner. The backend remains authoritative over state, authorization,
idempotency, recipients, and protected content. If the model or a bounded tool
cannot complete safely, the run returns `manual_required` and performs no
substitute strategy.

This worktree deliberately does not wire the agent to live incident services.
Slice 09 is changing incident resolution and assignment revocation in the main
checkout; live tool-proxy composition belongs after that lifecycle contract is
merged.

## Implemented boundary

### Normalized contracts

- Immutable `AgentRunRequest` and `AgentRunResult` envelopes.
- Small approved incident summary rather than an unbounded health stream.
- Pinned coordination-policy ID, semantic version, and SHA-256 identity.
- Observable tool traces containing arguments, bounded result/error codes, and
  timestamps; hidden reasoning is never copied into the result.
- Only `completed` and `manual_required` outcomes. Model timeout,
  unavailability, invalid output, denied/failed tools, and an agent-requested
  handoff are explicit closed failure categories.

### Typed tool gateway

- Explicit registered-tool allowlist with Pydantic input schemas.
- Unknown tools and invalid arguments are denied.
- Credential-shaped schema fields or arguments are denied before a handler is
  called and are redacted from the audit trace.
- Handler exceptions and non-JSON results are normalized without exposing raw
  provider errors.
- The synthetic smoke tool proves incident/run binding without using a device,
  responder, notification, or inference credential.

### Deep Agents and vLLM

- `LangChainDeepAgentFactory` lazily loads the optional `agent` dependency set.
- `ChatOpenAI` targets the configured vLLM `/v1` endpoint using Chat
  Completions, not a remote default provider.
- A model-specific Deep Agents harness profile removes todo, filesystem, shell,
  and subagent tools. Only explicitly registered Vital Relay tools remain.
- The model returns a bounded structured conclusion; missing or malformed
  structured output fails closed.
- The application remains Python 3.14. Apple-Silicon vLLM-Metal stays in a
  separate native arm64 Python 3.12 environment and communicates only through
  its OpenAI-compatible HTTP service.

### Containment paths

NemoClaw is primary. The documented profile uses its Restricted tier and
managed `inference.local` route. A custom preset contains exact future
tool-proxy paths and must not be applied until that internal proxy exists and
is reviewed. It adds no package, GitHub, web-search, messaging, or arbitrary
network access.

The Docker fallback runs the same `vital-relay-agent-smoke` entry point. The
agent container is read-only, non-root, capability-free, PID/memory bounded,
has no host mounts, and joins only an internal network. A separate dual-homed
gateway forwards only `/v1/models` and `/v1/chat/completions` to the configured
host vLLM service.

Docker is an execution-substrate fallback, not a deterministic decision
fallback.

## Current machine readiness

The read-only readiness command currently reports:

- Node `20.2.0`; NemoClaw requires Node `22.16.0` or newer.
- Docker/Colima daemon unavailable from the current shell. Homebrew Docker and
  Colima binaries exist, but Colima reports its `lima` dependency missing.
- `nemo-deepagents` is not installed.
- `~/.venv-vllm-metal/bin/python` does not exist; Homebrew Python 3.12 is
  available as the bootstrap interpreter.
- no vLLM model is serving at `http://127.0.0.1:8001/v1`.

No installer, runtime daemon, model download, credential, or sandbox was
created automatically. Those actions require an operator-selected vLLM model,
review of the NemoClaw third-party terms/policy, and approval for the external
machine changes.

## Files

- `backend/src/vital_relay/agent/contracts.py`
- `backend/src/vital_relay/agent/tools.py`
- `backend/src/vital_relay/agent/runner.py`
- `backend/src/vital_relay/agent/deep_agent.py`
- `backend/src/vital_relay/agent/readiness.py`
- `backend/src/vital_relay/agent/smoke.py`
- `infrastructure/nemoclaw/**`
- `infrastructure/docker-agent/**`
- focused tests prefixed `test_agent_` plus `test_deep_agent_runner.py`

## Verification and evidence boundary

The repository gates cover contract invariants, credential denial/redaction,
unknown-tool denial, observable success traces, model-timeout/manual behavior,
invalid structured output, agent-requested human control, current Deep Agents
factory construction, readiness diagnostics, and static containment policy.

Verification on Python 3.14 with the pinned optional agent ranges:

- `24` focused agent/runtime/policy tests passed;
- `165` fast backend tests passed, with `38` PostgreSQL-marked tests deselected;
- the real `deepagents 0.6.12` and `langchain-openai 1.3.5` factory constructed;
- both YAML profiles parsed and `git diff --check` passed;
- all Python sources compiled with bytecode redirected outside the worktree.

The following evidence is intentionally still required before claiming a live
sandboxed local agent:

1. install/start the selected local toolchain;
2. select and serve an exact vLLM-Metal model with tool calling enabled;
3. observe the model complete exactly one synthetic typed tool call;
4. run the same probe inside NemoClaw and Docker;
5. capture protected-file and unlisted-egress denials;
6. compare normalized result envelopes across both containment paths.

## Agent A2 follow-on

The typed coordination policy, host-issued run capability, initial
privacy-bounded service ports, deny-by-default internal proxy, append-only audit
contracts, and bounded local idempotency adapter are now implemented in
`agent-a2-policy-tool-proxy-foundation.md`. They remain deliberately unwired
from a live HTTP route or persistence adapter until Slice 10's final
persona/session scope is available. Close and handoff remain unregistered.

The AlphaEvolve-style lane may start after `AgentRunResult`, tool traces, and
the first versioned `coordination_policy.yaml` are frozen. Its reproducible
scenario runner is an offline evaluator, not a production coordination
fallback.

## Upstream references

- [LangChain Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [LangChain Deep Agents customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)
- [vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/)
- [vLLM-Metal](https://github.com/vllm-project/vllm-metal)
- [NemoClaw prerequisites](https://docs.nvidia.com/nemoclaw/user-guide/deepagents/get-started/prerequisites)
- [NemoClaw network policies](https://docs.nvidia.com/nemoclaw/user-guide/deepagents/reference/network-policies)
