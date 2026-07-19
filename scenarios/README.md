# Offline benchmark fixtures

This tree contains the candidate-visible side of the deterministic Wave 1
catalog. Development publishes 12 full cases under `development/cases/`, their
candidate views under `development/inputs/`, and the complete manifest.
Protected validation publishes six input-only views under `protected/inputs/`
with opaque `holdout_01`–`holdout_06` aliases and a descriptor containing only
those aliases. The views publish incident initial conditions, not semantic
families, seeds, provider/fallback choices, expected branches, or case/oracle
hashes. Final metadata publishes only cadence.

Full protected/final manifests, scripts, expected outcomes, scores, and hidden
oracles live under `protected/evolution/`, which must be mounted read-only and
outside a candidate sandbox. `ScenarioCatalog.load(...)` cross-checks every
development/protected public view against its host-owned case and never loads
or retains final cases.

The final-test manifest carries a cadence limit. A separately constructed
`FinalTestAuthority` is the only loader for final assets. After its durable
ledger opens a run, it issues an identity-bound, one-use capability to a
candidate-bound host session. There is no caller-provided authorization flag.
The bundled in-memory ledger is for a single-process lab only; integration must
supply a durable compare-and-swap implementation.

The hackathon accepts this evaluator as a trusted offline host boundary.
Candidate/model/mutation code must run in a separate sandbox process and cannot
execute or introspect Python inside the evaluator process. Closure privacy does
not resist arbitrary code already running in that trusted process; permitting
such code requires a separate signer process/service before claiming stronger
isolation.

Protected and final reports are selection/evaluation evidence only. The sole
bounded adaptation export, `build_failure_packet`, rejects every non-development
report. Final reports additionally require a candidate-bound, one-use host
session.

`recorded/baseline-evaluation.json` is a pinned, host-signed evaluation of recorded
agent choices. Replaying it exercises the exact Agent A2 runner, capability shape,
policy snapshot, dispatcher, and five production tool contracts. It is experimental
instrumentation, not a production coordinator or fallback planner.
