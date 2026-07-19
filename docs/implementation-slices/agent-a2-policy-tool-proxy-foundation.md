# Agent A2 — Coordination Policy and Tool-Proxy Foundation

**Branch:** `codex/wt50-agent-sandbox`
**Status:** Typed policy, capability, proxy, audit, and local idempotency
foundations implemented. No HTTP route, production persistence adapter, or live
sandbox-to-backend connection is claimed.

## Decision boundary

The agent owns coordination strategy. This foundation does not add an ordered
action plan or a deterministic production coordinator. The backend remains
authoritative for identity scope, incident state/version, recipients,
idempotency, dispatch atomicity, and immutable protocol content. A rejected or
failed tool call makes the run return `manual_required`; no substitute planner
runs.

## Versioned policy snapshot

`agents/policies/baseline/coordination_policy.yaml` is parsed into a frozen
`CoordinationPolicySnapshot`. Its sole identity representation is sorted,
minified UTF-8 JSON. The recorded SHA-256 is computed over those canonical
bytes, not the YAML formatting.

The schema contains policy identity/objective, an allowlisted mission code,
allowlisted strategy/review codes, and total/mutating/per-tool attempted-call
ceilings. Only host-owned prose for those codes enters the system prompt, so an
evolved policy cannot inject arbitrary or medical instructions.
It intentionally has no `action_sequence`, state-machine transition, recipient,
medical threshold, or protocol-text field. Duplicate tools, unknown runtime
tools, effect mismatches, invalid budgets, and request/reference hash mismatches
fail closed.

The exact verified snapshot materially enters the Deep Agent system prompt.
`AgentRunResult` retains only `AgentPolicyReference`, so experiment artifacts
address policy without copying strategy content into operational results.

The runner contract shared with WT-60 is:

```python
run(
    request,
    tools,
    *,
    policy_snapshot=verified_snapshot,
    invocation_context=host_issued_context,
) -> AgentRunResult
```

## Capability boundary

The host-side `ToolCapabilityAuthority` issues a short-lived HMAC-SHA256 token
with explicit token version and audience. Signed claims bind `run_id`,
opaque persona/tenant `scope_id`, `incident_id`, authoritative `state_version`,
policy SHA-256, issue/expiration times, and a state-specific allowed-tool
subset. The proxy verifies scope against its own composition, not just the
caller envelope.

The signed payload is authenticated, not encrypted, and contains no wearer data
or credentials. Verification uses constant-time comparison. Wrong
version/audience/signature, naive or future timestamps, and expired lifetimes
are rejected.

The signing key stays in host-side orchestration/tool-proxy composition. The
runner receives only `ToolInvocationContext`, containing a masked and
serialization-excluded short-lived capability. Neither capability nor trusted
idempotency UUID appears in model tool schemas, model arguments,
`AgentToolTrace`, or proxy audit records.

## Initial tool surface

| Tool | Effect | Model-visible result |
|---|---|---|
| `get_incident` | read | State/kind/version/times; no wearer, location, or health IDs |
| `get_incident_timeline` | read | Bounded observable entries; no internal correlation IDs |
| `get_dispatch_coordination` | read | Counts and coarse responder/AED facts; no exact locations |
| `coordinate_dispatch` | mutate | The same coarse projection after the application-owned atomic operation |
| `get_fixed_protocol` | read | Immutable protocol identity/hash only; no medical steps or generated text |

Close, handoff, cancellation, responder decisions, arbitrary notification
recipients, exact dispatch routes, and protocol selection are not registered.

Every input repeats incident ID and expected state version for explicit model
feedback, but those values grant no authority. The proxy checks them against
the capability and a fresh scope-bound incident read. Application ports—not
repositories or unfinished Slice 10 types—are the only backend dependency.
Live wiring must use Slice 10's final persona/tenant scope; the ports are
documented as scope-bound until then.

## Proxy, activation, and idempotency

The proxy denies unknown/unlisted tools and rejects wrong run, incident,
policy, state version, inactive state, invalid input, and expired capability
before calling an application service. It consults `PolicyAuthorizationPort`
on every invocation, so promotion immediately revokes the previous hash and
rollback can restore it without reconstructing the proxy.

Mutating calls require a trusted runtime-generated idempotency UUID, never a
model field. It is stable UUIDv5 identity derived from immutable run ID, tool
name, and canonical validated arguments, so the same intent deduplicates across
graph/run retries. Dedupe retention is independently configurable (24 hours by
default), not coupled to the short capability lifetime. The bounded in-memory
executor is only a local/test adapter: identical repeats replay before stale
state checks, changed arguments conflict, capacity is bounded, virtual time
drives expiry, and an ambiguous application failure remains
`idempotency_in_doubt` instead of being blindly retried. Live wiring requires a
durable adapter aligned with the application transaction/outbox boundary.

Dispatch coordination re-reads the incident after mutation and reports the
resulting authoritative state version. It never stamps a pre-call version onto
a post-call result.

## Observable audit boundary

The append-only audit port records both requested and authenticated-grant
scope/run/incident/policy identities, tool/effect, state version, status,
idempotency UUID, bounded error code, and canonical request/result hashes. It
records no raw capability, HMAC, argument, result body, credential, or hidden
reasoning. Denials are audited even when no handler runs, and a `started` record
must append before a fresh mutation begins.

The dispatcher latches the first denied/failed call. Every later call in that
run is denied as `run_failed_closed`, so a failed observation cannot be followed
by a mutation while the model graph is winding down.

This foundation does not claim atomicity between a terminal proxy-audit record
and today's application-owned dispatch transaction. A live durable adapter
must use an outbox/shared unit of work or reconcile `idempotency_in_doubt`
before operational use.

## WT-60 handoff

WT-60 may create typed candidate snapshots and pass them through the same
runner. It should derive identity from `canonical_bytes`, keep scenario
conditions/evaluator logic protected, and authorize a candidate hash only
inside its isolated laboratory. Candidate policy fields guide strategy and
resource budgets; they never replace the model with a scripted production
workflow. Live promotion remains an explicit operator action.

## Remaining integration gates

1. Rebase after Slice 10 freezes persona/session scoping.
2. Implement durable authenticated transport and scope-bound service wiring.
3. Keep signing material only in host/proxy secret management.
4. Prove revocation, stale-state denial, idempotent replay, audit durability,
   and exact tool egress with PostgreSQL and sandbox integration tests.
5. Keep resolution and handoff unavailable until a separate product/safety
   decision grants them.
