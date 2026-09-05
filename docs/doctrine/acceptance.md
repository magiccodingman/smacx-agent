# Dynamic Sovereign Gameplay Doctrine — acceptance ledger

## Checkpoint 1: content/source contract

Source design: supplied Sovereign Gameplay Doctrine and Dynamic Gameplay Context Generator. Operational law stays first. Principle-based strategy remains authored guidance; no harness action ranking. The 16-block manifest is unchanged. See `claim-inventory.json`, `input-manifest.json`, `evidence/sources.json`, and the representative full prompts and inputs in `fixtures/`.

Compiler context has strict required/optional fields; optional world/roster absence is not a stock default. Faction mechanics use controlled typed fact renderers. Unknown mechanics fail explicitly. No stock faction summary is automatically selected by name. A compatibility identity and loaded rules fingerprint are mandatory; production must additionally establish that identity from authenticated loaded evidence rather than accept a caller's label. That adapter is checkpoint 2, not proven by these fixtures.

Important source findings and changes:

- Owned contentment: `base.cpp:4232`/`mod_psych_check` uses configurable human-seat contentment from the global difficulty; the renderer consumes the resolved native value, not a difficulty-name table.
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

Checkpoint 3 source recheck: the naturally content population uses the native global difficulty, while Social Engineering and ecology use the seat difficulty. The receipt now calls `mod_psych_check` directly so imported/mixed values cannot silently substitute the wrong difficulty index. The final native receipt is rerun against this correction.

## Checkpoint 3: behavior, cost and final acceptance

Version: `smacx.sovereign-doctrine.v1`; final static SHA256 `790ba661c4b6a45a77065fbcc112187db5e75654bb8cecb12ea46b5ea0e10c26`. The static source retains 4,812 words and 143 inventoried paragraphs/bullet groups. Compiler: `smacx.doctrine-compiler.v1`. The final reviewed native source digest is in `src/doctrine/engine-compatibility.json`; profile metadata carries that compatibility evidence indirectly through the loaded fingerprint and the exact confirmed configuration.

The live Qwen3.8-27B ablation used 23 frozen situations, three arms, identical evidence, no personality override, temperature zero, seed 1729 and thinking disabled. Initial and final raw responses are preserved, with a per-case author review in `evidence/behavior-review.json`. Three source-checked clarity edits followed the first run: Specialists can relieve unrest without being Talents; lower SUPPORT can increase upkeep; fixed faction modifiers are not proposed action deltas. The compiled block now explicitly states Progenitor Council ineligibility. These changes preserve the principle-based strategic doctrine. The Progenitor fixture was also aligned with the actual adapter's conditional economic/transcendence eligibility and loaded research flag; its final C response was rerun.

All 69 final responses used an offered candidate or requested investigation, with no truncation. **This is format validity, not native legality certification.** The final compiled arm understands broad Blind Research selection better than A/B, but still guesses the Discover/resource-lifting association. Both doctrine arms now recognize Specialist riot relief, but overstate the exact resulting Drone balance. The static-only arm recommends an invalid Progenitor Governor route; C chooses the habitation bottleneck. C preserves unknown geography on the Huge map and handles the accelerated start without a fixed opening. Different limited-war/peace recommendations remain legitimate alternatives.

Material residual errors are retained: a five-turn reserve is described as closing a three-turn attack window; a farm is assumed to improve output without checking the cap; opening native diplomacy is called read-only in one response; some investigation repeats facts already given. Doctrine improves vocabulary and some rule comprehension, but **does not establish overall strategic superiority or reliable autonomous gameplay competence**. Native legal receipts and effect verification remain authoritative. Requested investigation counts are A=66, B=60, C=52; they are textual requests, not executed tool calls. Lower counts sometimes reflect failure to investigate, so they are not automatically better. Median response latency was approximately 6.56/7.58/7.35 seconds, with shared-provider/concurrency/order effects; no causal speed comparison is claimed.

The original Social Engineering case's phrase “reduces support” was ambiguous. Its evidence remains frozen. A separately reported explicit SUPPORT-rating case makes the penalty direction unambiguous; all three arms request the missing upkeep delta before choosing. This is a methodological correction, not a relabeled successful original result.

Exact final full-system counts across the 18 rendered inputs: **8,034–8,380 Qwen tokens**, 41,600–43,373 UTF-8 bytes, roughly 12.3–12.8% of 65,536 tokens. The 15 serialized tool schemas measure 4,323 exact tokens (provider chat/tool framing excluded); `smac_world` is 407 exact tokens / 619 conservative proxy. The original operational budget fixture remains 1,117 exact tokens; the evaluation identity is 1,115. Doctrine plus serialized tools occupies about 12,357–12,703 tokens before framing, history, request-local runtime, reasoning and output. Conservative system/tool reserves are 22,059–22,650; the normal 26,214 history ceiling plus 8,192 output and 8,192 reasoning reserves fits all measured cases at 64K.

The isolated live prefix-cache probe produced 0 hits for the first 8,217-token request and **4,944 hits for the second 8,217-token request** (about 60.2%). Counter query deltas matched each request exactly; caching is confirmed but partial. Provider usage omitted its cached-token field, so the report uses separately recorded vLLM counters rather than guessing from latency. The exact system text stayed unchanged while only the user tail changed.

Final contract/native/provider checks are recorded in `evidence/final-contracts.json`. The strict hook validates large-prompt headroom at request assembly after installing its override: Python's startup exception handling cannot cause a budget failure to leave the upstream prompt builder active. The final native source recheck and disposable native receipt pass. Coverage and this ledger are updated before the checkpoint commit. Full commands and evidence classifications are in `validation.md`.

Remaining boundaries: one live native Tiny/Librarian seat, with other scenarios/factions covered by source review and fixtures; arbitrary modified rules and unsupported material scenarios fail closed; incomplete initial public rosters omit opponent/density blocks; no live Pact map-refresh equivalence claim; no full-game A/B win-rate, native action-loop or tool-latency measurement; no universal tokenizer-independent guarantee from the capacity-planning proxy. Existing profiles require explicit recompilation on transition. The active deployment was not replaced by this follow-up.
