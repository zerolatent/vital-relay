# ACE-00 — Immutable Operational-Playbook Contracts

**Branch:** `codex/ace-00-contracts-local`

**Status:** Complete

**Scope:** Contract and reviewed-baseline foundation only; no model invocation,
runtime prompt injection, merge engine, persistence, or production fallback.

## Outcome

ACE-00 establishes a closed, content-addressed boundary for later Agentic
Context Engineering work. A future Reflector/Curator can propose only a bounded
typed delta; it cannot use live incidents, protected validation, or final-test
evidence, and it cannot place protected content into operational context.

All contracts use Pydantic with `extra="forbid"`, `frozen=True`, and
`revalidate_instances="always"`. Every content-bearing artifact has a factory
that computes its canonical SHA-256 and revalidates the completed object.
Deserialization and nesting both rerun validators, so an invalid
`model_copy(update=...)` instance cannot cross a parent boundary even if its
caller recomputes a plausible hash.

Each hash factory first validates a separate, non-artifact draft model that has
no top-level hash field, hashes that normalized material, and then constructs
the final artifact through ordinary `model_validate`. No artifact validator
reads validation context and no sentinel, closure, private object, or other
hash-suppression capability exists. Both the final artifact and every nested
artifact therefore verify their exact hashes on every validation path,
including caller-supplied `model_validate(..., context=...)`.

## Contracts

| Contract | Boundary |
|---|---|
| `PlaybookProvenance` | Admits only a human-reviewed baseline, redacted synthetic development evidence, or an explicitly consented and redacted rehearsal. Live, protected-validation, and final-test origins always fail. Adaptation must bind a host-produced failure-packet hash. |
| `OperationalTacticSpec` | Selects one reviewed host template and, only for sequencing tactics, effect-typed observation/action tools. Canonical title and instruction text are derived, serialized, and required to match the template exactly. There is no model-authored instruction field. |
| `PlaybookApplicability` | Uses typed incident kind, active coordination state, and reviewed tool enums. Values are unique and canonically sorted. This metadata is never rendered as free-form prose. |
| `PlaybookItem` | Stable ID, integer revision, structured tactic, applicability, allowlisted tags and deprecation reason, provenance, status, exact reviewable-content digest, and canonical item hash. |
| `BaselineReviewManifest` | Detached, self-hashed decision/scope manifest binding playbook ID/version, every approved item ID/content digest, and the aggregate reviewable-content digest without a provenance recursion. |
| `Playbook` | Stable ID, semantic version, provenance, optional exact parent hash, at most 64 sorted unique items, and canonical playbook hash. A reviewed baseline must carry and cryptographically match its detached review manifest; adapted playbooks cannot claim that baseline review. |
| `PlaybookDeltaOperation` | Closed operation shape for `ADD`, `REFINE`, `TAG`, or `DEPRECATE`. Each kind accepts only its own fields and uses optimistic item-hash binding for every existing target. |
| `PlaybookDelta` | Exact parent, provenance, Curator role identity, model identity, at most 16 operations, per-kind limits, unique targets, and canonical delta hash. Added items must be active and share the delta provenance. |
| `RoleIdentity` / `ModelIdentity` | Self-hashed identities binding exact reviewed role configuration and exact model artifact/inference configuration. |
| `ContextSelection` / `SelectedContext` | Host-owned selection inputs, exact Generator input hash, exact role/model identities, a complete validated playbook proof, selected item bodies/hashes, exact member equality, applicability checks, and hard item/character budgets. An arbitrary item plus an unrelated syntactically valid playbook hash cannot validate. |

The current hard bounds are 64 playbook items, 8 tags per item, 16 operations
per delta, at most 4 `ADD`, 4 `REFINE`, 8 `TAG`, and 4 `DEPRECATE`
operations, and at most 12 selected items / 6,000 rendered characters.

## Fail-closed injectable-content boundary

ACE-00 does not rely on a keyword denylist. Playbook instructions are selected
from the closed `OperationalTactic` enum and rendered by immutable host
templates. Parameters are limited to reviewed observation/action tool enums;
tags and deprecation reasons are also enums. Supplied `title` or
`instruction_text` must exactly equal the derived host rendering, and arbitrary
fields are forbidden. Consequently paraphrased medical advice, PHI, identity,
coordinates, secrets, authority changes, arbitrary tool/state/protocol edits,
and evaluator knowledge have no representable injectable field.

The allowlist includes safe read-before-action and reread-after-action tactics,
so reviewed tool sequencing and incident-state rereads remain expressible
without opening a prose channel. All ACE inputs recursively reject every C0
and C1 control character plus DEL before normal field validation.

Later ACE host merge code must retain deterministic validation, partition
isolation, redaction, audit, and operator-review gates. A future context
injector must render only the canonical tactic title/instruction fields—not
artifact IDs or provenance metadata.

## Reviewed baseline

`agents/playbooks/baseline/playbook.yaml` contains five generic operational
coordination habits. They apply no medical judgment and change no authority.
The checked-in canonical playbook identity is:

```text
24b194959d60e8f3f7afcb69351700f86740df9da4d4ff13536d1a2064cc5326
```

`playbook.sha256` pins that canonical model identity. `review.yaml` is the
detached nonrecursive review manifest. It records the decision and scope plus
the exact approved item digest list. Its canonical manifest identity is:

```text
359588f22a49d5e38d181cd587f55d27dd1b433e39b056fdcadf1b62063f3779
```

The aggregate exact reviewable-content digest is
`051aee47d0d1f0a2520c7033ec2ab6a2fd63fdb0db523cfbcb1134324cb5cd1a`.
The playbook provenance and every baseline item bind the manifest identity;
the `Playbook` validator recomputes every approved item digest and rejects reuse
of the review for different content.

## Verification

Focused command:

```bash
PYTHONPATH=backend/src /Users/sidreddy/dev/hackathon/vital-relay/.venv/bin/python \
  -m pytest -q -o addopts='' \
  backend/tests/unit/evolution/ace/test_contracts.py
```

Result: `91 passed`.

The suite covers baseline pins and exact review binding, nested in-memory model
tampering with a recomputed hash, hash tampering, every forbidden source
partition, missing redaction/consent, paraphrased protected prose, every C0/C1
control plus DEL, safe sequencing/state reread rendering, all four operation
shapes, per-kind delta limits, role/provenance mismatches, exact selected-item
membership, direct forged context deserialization, applicability, and budgets.
It also attempts the former `build_canonical_hash` context forgery directly and
through nested playbook, item, review-manifest, role, model, delta, and selected
context artifacts; every zero-hash forgery is rejected. A module-introspection
regression also proves there is no validation-context hook, sentinel object, or
module-level closure from which a reusable hash-suppression capability could be
recovered.

## Integration handoff

The Wave-2 ACE host-merge lane can import these contracts without changing the
existing protected `vital_relay.evolution.contracts` module. It should resolve
the exact parent playbook, apply operations deterministically, increment item
and playbook versions, detect duplicates/contradictions, prune within these
bounds, and derive helpful/harmful evidence only from host-observed evaluation.

The Reflector/Curator lane should construct `PlaybookProvenance` before any
model invocation and constrain Curator output to the tactic/tag/reason enums.
That makes live/protected/final input a pre-inference rejection and makes
arbitrary generated prose structurally inadmissible. Production Generator
injection remains a separate integration lane and must fall back to the
reviewed static baseline on any selection, membership, or verification failure.
