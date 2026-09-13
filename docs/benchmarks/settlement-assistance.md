# Settlement assistance acceptance

Scope: intent/area-led discovery using existing smac_world; bounded economic alternatives;
settlement-only terrain/resource entitlement through fog; persistent owned-base access changes.
Movement redesign, hidden enemy state, campaign playthroughs and automatic site selection are excluded.

## Checkpoint 1 — mechanics and provider path

- `settlement_assistance_test.py`: feasible joint allocations; own worker reservations;
  shared capacity; modified consumption/cost inputs; epistemic and unchanged-event suppression.
- `settlement_facade_test.py`: actual MCP function with controlled native receipts: four
  candidates, 1,384 conservative tokens at standard detail, two bounded native calls;
  changed action revision rejected. This proves adapter/provider shape, not live yield accuracy.
- `counterfactual_contract_test.py`: 45 exhaustive allocation comparisons pass; standard
  dense preview remains available (2,021 tokens); deep 2,371 tokens.
- `geographic_semantics_contract_test.py`: passes existing geographic contracts.
- Native cross-compilation succeeded. Compilation is not a running-game comparison.

Economic descriptions use a documented heuristic: positive growth allocation plus colony
mineral accumulation in <=6/<=12/>12 turns is strong/moderate/weak under zero support.
These are not promised completion times, general site rankings or empirical player skill labels.
Existing native_helper_hypothesis status remains conditional. Border recomputation is NOT
simulated; installed Thinker territory_border_fix changes tie-breaking. Exact territorial
predictions are deliberately not claimed. Fog grants physical terrain/resource descriptors,
not hidden improvements, workers, ownership, units or guaranteed workability.

## Checkpoint 2 — persistent attention and recovery

Implementation stores the latest observed access change on the base and journals an attention
item through frozen observation publication. Acknowledgement does not erase the base record.
Recovery/publication tests and installed-image verification pending.

## Operational state

AI - 7 and AI - 8 - Instruct are completed, not resumable. Normal parking failed at its
safe boundary; operator pause verified containment before the supported end lifecycle.
Historical logs and incident records retained. No timers or new campaigns started.

Checkpoint 2 results:
- `settlement_attention_test.py` passes real collector -> frozen publication -> semantic
  journal -> attention capture -> provider-queryable base -> collector restart without duplicate.
  Latest access event remains on the base after subsequent observation. Cause is unestablished.
- `settlement_native_access_test.py` compiles and executes the actual C++ public-access helper
  with controlled state: Treaty/Pact occupation distinction, convoy restriction, hidden-unit
  exclusion, foreign territory, own-base worker reservation and fog exclusion. This is adapter
  evidence, not independent validation of the running game's territorial algorithm.
- Access changes do not infer construction from newly discovering a foreign base. Ordinary
  worked/unworked reassignment alone is not an access-loss alert.

Build compatibility review: native changes add read-only receipt fields and observed access
facts, plus align preview occupation with the existing Treaty/Pact rule. No gameplay rule,
fixed doctrine text, or runtime auto-approval was changed. Reviewed engine fingerprint was
explicitly registered using `scripts/doctrine_engine_contract.py --register-reviewed`.

## Checkpoint 3 — native and installed-image acceptance

`settlement_native_live_test.py` passed in an isolated single-player native process,
without provider calls: full native preview state restored, hidden-input independence
passed, and an actual guarded founding produced the predicted center N2/M1/E1.
This validates that center case, not every site/population or territorial outcome.
The first fixture attempt lacked an unrelated coastal port; the narrowed test uses
its legal colony site without requiring a transport actor.

Final provider fixture: four candidates in 1,481 conservative tokens, two bounded
native calls. Existing opaque execution, recovery, global world pipeline, runtime
context, managed action paths, geographic and attention communication suites pass.
The built control image imports the new module without a workspace mount and its
module SHA-256 matches the checkout. Full native source in the bridge build image
also matches the checkout. No automatic action was added to settlement search.

[Sanitized acceptance summary](settlement-assistance.json).

Deployment verified: public-stack control API healthy on
`smacx-agent-control:settlement-assistance`; worker/MCP/harness settings point to
settlement-assistance images. Control and harness installed settlement, MCP and
engine-compatibility file hashes match source. AI - 7 and AI - 8 - Instruct both
remain `completed`. No campaign or timer restarted. Isolated native test workers,
volumes and their two prepared images were removed; other installation resources
were not changed.
