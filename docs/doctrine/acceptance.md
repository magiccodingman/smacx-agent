# Dynamic Sovereign Gameplay Doctrine — acceptance ledger

## Checkpoint 1: content/source contract

Source design: supplied Sovereign Gameplay Doctrine and Dynamic Gameplay Context Generator. Operational law stays first. Principle-based strategy remains authored guidance; no harness action ranking. The 16-block manifest is unchanged. See `claim-inventory.json`, `input-manifest.json`, `evidence/sources.json`, and the representative full prompts and inputs in `fixtures/`.

Compiler context has strict required/optional fields; optional world/roster absence is not a stock default. Faction mechanics use controlled typed fact renderers. Unknown mechanics fail explicitly. No stock faction summary is automatically selected by name. A compatibility identity and loaded rules fingerprint are mandatory; production must additionally establish that identity from authenticated loaded evidence rather than accept a caller's label. That adapter is checkpoint 2, not proven by these fixtures.

Important source findings and changes:

- Owned contentment: `base.cpp:4232`/`mod_psych_check` uses configurable per-seat contentment; the renderer consumes the resolved value, not a difficulty-name table.
- Ecology: `base.cpp:2127` resolves different difficulty factors; the reviewed loaded adapter supplies the classification.
- Random event eligibility: `gameturn.cpp:561` rejects turns strictly below `75 - difficulty*10`; prose says “from turn”, not “after turn”, and does not equate eligibility with occurrence.
- Social Engineering: `faction.cpp:social_upheaval` uses seat difficulty, changed categories, and an alien surcharge. Exact current cost remains native-choice evidence; the compiler only teaches free/paid.
- Research cost: modified `tech.cpp` cannot justify a universal human difficulty burden table; omit inherited difficulty escalation and inspect loaded current costs.
- Research mode: `game.cpp:1384` forces alien directed research only in its single-player setup branch; current managed research choices inspect the loaded rule bit. Effective research mode is required separately, never guessed from alien identity.
- Progenitor victory: `game.cpp:end_of_game` implements a cooperative result although shipped `conceptsx.txt` says none. The loaded implementation controls this block; no universal stock assertion survives.
- Progenitor generator count/population come from loaded `Rules`, not hardcoded six/ten.
- Council 75% threshold is shipped documentation evidence (`conceptsx.txt`, ADVCONCEPT5), with candidate/defiance conditions kept bounded. Unconquered Progenitors prevent the ordinary diplomatic route.
- Do or Die: `faction.cpp:2043` gates noninitial respawn; prose retains native eligibility conditions.
- Pact refresh: remove the unsupported session-trigger precision, automatic intelligence-refresh promise and implication of allied live vision. Inspect the actual exchange/evidence. No new live Pact comparison is claimed.
- Add the native time-limit ending to the existing victory block; omit a false “only these standard victories can end the match” assertion.
- Worker/Specialist assignment and Talent/Drone happiness are distinguished. Operational epistemics are referenced rather than redefined; derived evidence need not be fresh.
- Iron Man never delegates platform recovery to the sovereign. Opening text is campaign-start history and is omitted for imported positions.

The inventory identifies every authored paragraph/bullet by hash. General conceptual material is supported at player-documentation level, not certified as exact predictive native mechanics. Detailed/high-consequence claims above have stronger source review or deliberately restrained wording. No claim is promoted to live comparison evidence.

Validation: `PYTHONPATH=src python3 scripts/doctrine_content_contract_test.py` passes 21 representative cases (18 rendered, 3 deliberate failures), plus four prohibited top-level input cases. They cover all requested match categories; random-resolution and custom-faction examples are native-shaped compiler fixtures, not yet a proven actual launch path. Golden output is reviewed for selected/omitted branches, no unresolved placeholders, deterministic bytes and no controller/volatile metadata.

Area convention: raw native width × height compared with the 80×80 native-coordinate seven-faction reference (40×80 playable stock dimensions; native X is doubled by `game.cpp`). The playable parity factor cancels. Unknown/incomplete public roster must omit the density estimate. Density is only a generation prior.

No persistent schema change is needed for compiler receipts; existing profile metadata and exact system-prompt storage will be used. Future schema changes still require forward migrations.

## Checkpoint 2: native assembly and persistence

The implementation and composition order are described in `implementation.md`. Coverage now runs through authenticated native public observation → allowlisted confirmed context → deterministic compilation → persisted profile → provider-visible exact system bytes. Doctrine supplies literacy, so execution/effect verification apply to the existing legal action path rather than to a new doctrine action. No gameplay mutation was added.

Evidence in `evidence/checkpoint2.json` distinguishes the actual isolated running-game receipt from synthetic adapter adversaries, actual SQLite profile re-open/re-assembly, packaged-image tests and real Hermes provider request capture. The isolated game resolves Cybernetic Consciousness mechanics from loaded state rather than guessing the requested faction index. The rule-loading attestation and native UI-thread receipt were exercised in that game. No complete-game win-rate or live Pact mechanics claim follows from this test.

The adapter excludes controller/AI-agenda fields and runtime turns, technologies, units and diplomacy. Missing research/victory/compatibility facts fail, as do scope mismatch, unknown rules/bonuses, unsupported scenario/configuration and persisted hash corruption. Required nested fields are validated. Recompilation of a changed fixed contract requires an explicit authenticated request. Existing profile/seat metadata suffices, so no migration or database reset is necessary. API diagnostics expose a clear 409 doctrine error rather than an internal server error.

Native/source corrections found during integration: the Social Engineering rating array has a TALENT slot; DRONE/TALENT faction values are population denominators; the population modifier lowers the habitation limit when positive; TERRAFORM means cheaper elevation Energy cost, not doubled Former work; FREEABIL needs a typed public mapping; ordinary scenario objective thresholds initialize to 9999; native X dimensions are doubled relative to the stock size table. Updated golden prose reflects these findings. The doctrine's strategic principles were preserved.

The 15-tool schema boundary is unchanged. A conservative prompt-size-dependent system/tool reserve and minimum history headroom protect the longer prefix; exact provider token/cost and behavioral measurements are checkpoint 3. The packaged Hermes context tests retain semantic GC before half-window compression, current tool-chain reasoning, durable cognition and a single request-only runtime tail.
