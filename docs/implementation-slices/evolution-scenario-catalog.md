# Evolution scenario catalog — Wave 1

## Outcome

Wave 1 replaces the single-case laboratory fixture with a checked-in,
deterministic catalog that can compare the static baseline, ACE playbooks, and
typed policy candidates against frozen tool outcomes.

| Partition | Cases | Candidate visibility | Adaptation use |
|---|---:|---|---|
| Development | 12 | Full cases, inputs, expectations, and manifest | Bounded failure packets allowed |
| Protected validation | 6 | Initial conditions under opaque holdout aliases | Forbidden |
| Final test | 4 | Cadence only | Forbidden |

Every case carries virtual UTC time, a canonical case digest, typed tool
results, evaluator-only observable effects, and an oracle. Every candidate is
replayed by the host against the same frozen case. Withheld partition
construction metadata is not published. Three-repeat tests pin byte-identical
development and protected reports.

## Coverage

The development curriculum covers:

- escalating dispatch invitation;
- verification timeout with an explicitly labeled in-app notification fallback;
- first-responder decline followed by qualified acceptance;
- stale-responder exclusion;
- no exact skill match with labeled manual coordination;
- accepted-response AED routing with a labeled static route;
- model unavailability that correctly returns `manual_required`;
- immutable fixed-protocol selection;
- authoritative cancellation after acceptance;
- unsafe-recipient, duplicate irreversible action, protocol mutation/generated
  content, and state/tool authorization probes.

Protected and final coverage is deliberately withheld. Candidate-visible files
describe only partition boundaries, opaque inputs, and final cadence; they do
not enumerate hidden scenario families, outcome branches, or case mappings.

## Observable scoring and hard gates

The evaluator grades tool traces and world effects, not model prose. Wave 1 adds
observable requirements for:

- expected `completed` or safe `manual_required` run status and failure code;
- maximum virtual/run elapsed time;
- exact required fallback labels;
- immutable protocol content SHA-256 identities.

Denied calls caused by unknown/disallowed tools, expired or mismatched
capabilities, budget overruns, invalid arguments, or credential-shaped inputs
fail the audit/authorization and trace/world boundaries. Handler/provider
failures remain ordinary tool errors so a candidate can be scored on safe
fallback behavior.

The existing hard gates remain score-independent and reject:

- recipients outside the case allowlist;
- exact-location disclosure before acceptance;
- duplicate irreversible action IDs;
- invented tool results or incomplete audit traces;
- generated medical content or mutated fixed-protocol content;
- changes to the protected artifact bundle.

The protected digest binds the evaluator, scenario and evolution contracts,
policy source, conclusion allowlist, fixed protocol files, protected generator,
oracle index, every protected/final case, and both hidden manifest bindings.

## Partition boundary

`ScenarioCatalog.load(public_root, protected_root)` loads development and
protected validation only, verifies candidate-view hashes, and does not read or
retain the final manifest or cases. Protected views use independently assigned
opaque aliases and contain initial conditions only; hidden expected labels and
provider/branch choices are produced solely in host-owned oracles. Candidate
access remains narrow:

- development returns expectations;
- protected validation returns opaque, initial-condition-only views;
- final test is absent from the ordinary catalog;
- full protected/final manifests remain internal;
- `build_failure_packet` is the only adaptation-feedback export and accepts
  development reports only.

`FinalTestAuthority.load(...)` is a separate trusted-host path. It loads final
assets, asks the durable cadence ledger to atomically consume a run, and creates
a one-use session closure without placing raw authority or capability objects on
the session. The evaluator retains only a plain opaque issuance port, not bound
authority methods or an authority binding accessor. That port accepts exact
candidate, manifest, case, and execution inputs; it has no score, metric, gate,
eligibility, conclusion, evidence, or observation parameter. For final tests it
consumes the live capability before the exact host-bound scorer runs, then
recomputes all observations, derives integrity gates, constructs evidence, and
signs the report in one path. Shared scoring logic can still return unsigned
observations for diagnosis, but those objects cannot enter report issuance.
Signing material and raw signing primitives are not exposed on the evaluator or
authority objects. `ObservableEvaluator` rejects final data through its normal
method and exposes no boolean override.

Protected/final aggregate metrics and scenario scores may be used for candidate
selection and evidence, but they cannot be converted to Reflector/Curator
feedback by this layer.

### Accepted trusted-host boundary

For the hackathon deployment, `HostIntegrityAuthority`, `FinalTestAuthority`,
the evaluator, cadence ledger, protected assets, and signing material execute
only inside one trusted offline host process. Candidate policies, model output,
mutation generators, and any other untrusted code must execute in a separate
sandbox process and receive only the narrow serialized inputs/outputs described
above. They may never import evaluator modules, run Python inside the evaluator
process, or introspect host objects.

Python closure privacy is not a security boundary against arbitrary code that
already executes in this trusted process: such code could recursively inspect
closures and recover signing material. That condition is treated as trusted-host
compromise and is explicitly outside the accepted hackathon threat model. A
deployment that permits plugins or candidate code in the evaluator process must
move signing and final report issuance into a separate process or service before
making a stronger isolation claim.

## Asset layout and regeneration

```text
scenarios/
  development/{cases,inputs}/
  protected/inputs/
  final/manifest.json
  recorded/baseline-evaluation.json
protected/evolution/
  protected_validation/
  final_test/
  protected-manifest.json
  final-manifest.json
  oracle-index.json
  regenerate_catalog.py
```

Regenerate after an intentional evaluator, scenario schema, hidden case, fixed
protocol, or protected-generator change:

```bash
PYTHONPATH=backend/src .venv/bin/python protected/evolution/regenerate_catalog.py
```

Regeneration is host-only. Candidate execution must receive the protected tree
as read-only and must not receive the generator.

## Integration handoff

This slice does not edit `main.py` or add runtime endpoints. Integration should:

1. construct the protected `HostIntegrityAuthority` from production-managed
   signing material and the same complete protected artifact map;
2. load the development/protected catalog with `ScenarioCatalog.load(...)` in
   the offline evolution worker, never in the live incident runtime;
3. construct `FinalTestAuthority` separately with the protected evaluator and a
   durable cadence store; only this authority may access host final storage;
4. pass only development failure packets or separately consented/redacted
   rehearsal feedback into ACE;
5. store protected/final reports as selection evidence without forwarding their
   scores, failures, traces, or expected outcomes to the Reflector or Curator;
6. replace the in-memory final cadence ledger with durable compare-and-swap
   storage before multi-process use.

No production provider fallback, live notification path, runtime endpoint, or
shared application wiring is introduced by this slice.
