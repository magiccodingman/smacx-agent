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

Area convention: raw native width × height compared with the 80×40 seven-faction reference. The playable parity factor cancels. Unknown/incomplete public roster must omit the density estimate. Density is only a generation prior.

No persistent schema change is needed for compiler receipts; existing profile metadata and exact system-prompt storage will be used. Future schema changes still require forward migrations.
