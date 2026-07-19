# WT-60: offline evolution laboratory foundation

## Outcome

This worktree now contains an offline-only AlphaEvolve/DGM-inspired laboratory.
It consumes normalized `AgentRunResult` records and calls an `AgentRunner` against
a frozen scripted tool world. The recorded runner replays choices previously made
by an agent; neither it nor the scenario harness chooses a production action or
acts as a deterministic incident coordinator.

The foundation includes:

- development, protected-validation, and limited-cadence final-test manifests;
- virtual time and frozen tool responses for the five Agent A2 production tools;
- hidden world effects used to measure notifications, acceptance, recipients,
  latency, duplicate irreversible actions, and safety violations without exposing
  those effects as model tool results;
- an observable evaluator that scores tool traces and world state, never hidden
  reasoning, and builds host-signed integrity evidence over the candidate,
  benchmark, cases, complete executions, failed and successful traces, protected
  artifact bundle, and conclusion-safety result;
- an exact allowlist validator for host-reviewed conclusion templates. Unknown
  free-form conclusion prose fails the medical-content gate closed;
- a metric vector, hard safety gates, and development-only bounded failure packets;
- canonical candidate/mutation/report hashes and explicit invalid-attempt records;
- host-signed evaluation reports and a content-addressed quality/diversity archive
  indexed by report identity rather than overwriting a candidate's prior evidence;
- cross-process archive locking for local workers, exact archived-report resolution,
  host-owned promotion thresholds, active-baseline checks, and operator-approved
  promotion/rollback through an atomic pointer abstraction;
- append-only lineage records proving which improver hash generated each child,
  plus an equal-seed/equal-budget comparison record for later N → N+1 → N+2
  evidence; and
- a checked-in baseline recording whose evaluation is reproducible byte for byte.

## Isolation boundary

Candidate-visible development/protected inputs live in `scenarios/`. The public
protected descriptor contains only visible scenario IDs; the public final
descriptor exposes only its cadence. Neither contains unsalted case, candidate
view, or oracle commitments. Full protected/final manifests, scripts, and oracles
belong under `protected/evolution/` and must be mounted read-only outside candidate
sandboxes. `ScenarioCatalog.candidate_view` exposes development expectations but
omits protected expectations, frozen tool responses, and world effects. It refuses
all final-test candidate views. Final evaluation runs inside a candidate-bound,
one-use host session and never returns reusable raw cases.

Every private benchmark manifest binds the expected protected-artifact bundle.
The host authority recomputes the observed bundle and signs derived evidence; a
candidate cannot supply integrity booleans or reuse evidence after changing a
conclusion, trace, world invocation, case, manifest, candidate, or policy. The
offline signing key in test support is a fixture only. A deployed laboratory must
load its key and pinned digest from host secret/release management.

The included final-test ledger, active pointer store, approval replay set, and
promotion event list are single-process laboratory adapters. The file archive now
serializes local processes, but it is not a database transaction. Production
integration must atomically persist the pointer, exact evidence, authenticated
approval, approval consumption, and event/outbox. The content-addressed report and
manifest objects are immutable and verified when loaded.

## Cross-lane contract with Agent A2

WT-60 deliberately does not duplicate `CoordinationPolicySnapshot` or its allowed
field/range rules. `PolicyArtifactAdapter` resolves a policy reference to:

1. canonical bytes for hash verification;
2. the opaque validated snapshot passed to the runner; and
3. an adapter-validated typed mutation/diff.

`A2PolicyArtifactAdapter` is the concrete implementation. It resolves exact A2
snapshots, permits only allowlisted strategy-code membership/order and numeric
budget paths, increments the patch version, revalidates the complete A2 schema,
and returns a bounded operator diff. Tool names/effects, objectives, policy IDs,
free-form strategy text, and protected paths are not evolvable.

The integration call is:

```python
runner.run(
    request,
    tools,
    policy_snapshot=policy_adapter.runner_snapshot(request.policy),
    invocation_context=preissued_context,
)
```

Agent A2 is rebased into this worktree. The runner receives an exact A2 pre-issued
`ToolInvocationContext`; it does not mint capabilities or own a capability-signing
key. Scenario inputs, outputs, effects, and names import the production contracts
from `vital_relay.agent.tool_contracts`:

- `get_incident`
- `get_incident_timeline`
- `get_dispatch_coordination`
- `coordinate_dispatch`
- `get_fixed_protocol`

`coordinate_dispatch` is registered as `MUTATE`; the other four tools are `READ`.
Recorded choices execute through `DeepAgentRunner` and its audited dispatcher, so
policy verification, state-specific tool filtering, budgets, the failure latch,
and stable mutation identity stay aligned with A2. Replay injects deterministic
opaque call IDs solely to make the checked baseline byte-repeatable; production
continues to use random call IDs. The scripted world independently caches a
mutation result by trusted operation identity and does not reapply effects on an
exact retry.

The baseline ends after creating an invitation while the incident remains
`escalating`. Responder acceptance is an asynchronous external transition. A
response-active continuation must therefore be a new `AgentRunRequest` with a new
state-version-bound capability before `get_fixed_protocol` becomes available.

Agent A2's production strategy fields are closed codes rendered by host-owned text.
WT-60 may mutate allowlisted principle membership/order and numeric tool budgets;
it must not introduce free-form coordination prose. The current implementation is
therefore a bounded policy-mutation and evaluation foundation with an
AlphaEvolve-inspired archive/selection loop. It is not yet an autonomous optimizer
because the vLLM mutation proposer and candidate-bundle store are deliberately pending.
Quarantined strategy-fragment evolution remains deferred until adversarial output
gates exist.

Improver mutations now pass through a schema-owning adapter that applies the typed
mutation, resolves canonical bytes, and verifies the child reference hash. The
checked-in baseline improver is real content rather than a placeholder digest.
The archive does not yet retain policy bytes, improver bytes, and mutation manifests
as one verified candidate bundle, so this slice makes no recursive self-improvement
claim.

## What remains

- Connect NemoClaw-isolated Deep Agent and mutation-proposer processes to vLLM.
  The current lane uses recorded agent choices and no live model endpoint.
- Expand the initial fixtures to the proposed 10-12 development, six protected,
  and four-to-six final cases.
- Add a local-model mutation proposer. It must emit `MutationManifest`; the
  protected evaluator remains the judge.
- Persist a verified candidate bundle containing exact policy/improver bytes and
  the mutation manifest before running inherited-improver experiments.
- Replace single-process archive/pointer/cadence adapters with transactional
  persistence and authenticated approval verification before exposing an admin
  API.
- Add a live activation bridge that atomically binds the WT-60 candidate hash to
  the A2 policy hash. Selection remains development/protected-only; a separate
  cadence-limited final attestation is required before any live promotion.
- Compute inherited-improver comparisons from archived reports rather than trusting
  the current comparison record's caller-supplied outcome. Do not claim recursive
  improvement unless equal-seed/equal-budget I1 descendants reproducibly beat I0.

## Verification

Run the offline lane with:

```text
python -m pytest -q -p no:cacheprovider backend/tests/unit/evolution
```

The full fast backend suite is the A2/WT-60 integration gate.
