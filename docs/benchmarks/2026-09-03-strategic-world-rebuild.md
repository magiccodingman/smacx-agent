# Strategic world and provider-context acceptance

These content-free measurements were produced by the deterministic fixtures in
this repository on 2026-09-03. They contain no prompt bodies, game assets,
conversation text, saves, credentials, or private endpoints.

| Gate | Result |
| --- | ---: |
| Small quiet 64K anchor | 622 estimated tokens |
| Huge quiet 64K anchor | 624 estimated tokens |
| Huge quiet growth | 0.322% for 100× as many tiles |
| Huge quiet 256K anchor | 624 estimated tokens |
| Huge fragmented 64K anchor | 3,482 estimated tokens; 3,176 regions explicitly omitted |
| Huge chaotic 64K anchor | 5,885 estimated tokens |
| Huge chaotic 256K anchor | 15,989 estimated tokens |
| Large-cognition 64K complete runtime context | 3,725 estimated tokens |
| Same strategic truth at 256K | 7,095 estimated tokens |
| Huge chaotic 256K complete runtime context | 24,811 estimated tokens: 15,265 anchor, 5,766 cognition, 3,501 attention |
| Mature notebook scale | 240 notes of approximately 24KB each; metadata-only list/search and targeted full get remained bounded |
| Semantic GC 50%-crossing gate | 45,697 → 2,436 estimated provider-wire tokens before Hermes generic compression |
| Note/memory-heavy 500-action gate | 1,074,269 → 13,185 estimated provider-wire tokens with durable write receipts |
| `smac_world` serialized schema | 1,475 bytes; 492 conservative tokens |
| v6 system prompt | 5,280 bytes; 1,760 conservative tokens; 1,117 exact Qwen tokens |
| Managed provider tools | 15 |
| Live stable-prefix second request | 21,424 locally cached tokens of 23,143 prompt tokens |
| Live Hermes world specialist | 5 provider calls, 9 world queries, 326,786 cumulative replay tokens, 89,492 peak prompt tokens, 4,343-byte strict result, 58.35 seconds |
| Real-game no-timer specialist | accepted; 5 provider calls, 10 world queries, 375,582 cumulative replay tokens, 97,541 peak prompt tokens, 51.714 seconds; native bridge probe 188 ms; native turn unchanged |
| Real-game no-timer sovereign action | 12 calls; 135,383 input, 4,959 output, 3,541 reasoning tokens; guarded semantic revision advanced |
| Native bridge build | current worker image built successfully, including the 32-bit bridge stage |
| .NET tests | 63 passed |

The Huge fixtures use equivalent strategic activity while changing raw map size,
then add controlled active complexity. `scripts/world_context_benchmark.py`
enforces the 15-percent quiet-growth rule and the 6K/16K tier caps.
`scripts/provider_schema_budget_test.py` measures the actual MCP schemas and can
use the provider tokenizer. `scripts/provider_prefix_cache_live_test.py` creates
a unique stable prefix, changes only its final volatile tail, and reads vLLM's
content-free local-cache counters. The live result proves the request layout is
not merely structurally cache-friendly; the configured provider reused it.
`scripts/specialist_provider_capture_test.py` starts real disposable Hermes
children against a recording provider and proves iterative world/reference
retrieval, exactly one faculty tool, stable system/tool prefixes, trace-derived
citations, and no cross-mission or sovereign state. The independent live test
uses the configured Qwen provider and immutable world snapshot. It accepted a
strict result with zero sovereign-history rows. The 326,786-token figure sums
replayed prompt plus completion usage across five requests; the largest single
prompt was 89,492 tokens. This is aggregate inference cost, not simultaneous
sovereign context occupancy.

`scripts/runtime_context_contract_test.py` measures the complete request-only
runtime envelope rather than the anchor alone. Its Huge-chaotic 256K fixture
proves the coherent allocator can retain mandatory identity, focus, live
cognition, critical attention and deltas alongside a high-detail anchor while
remaining below the 32,768-token rich-tier ceiling. The 64K large-cognition
fixture separately proves a high-priority active goal and an older accepted,
still-binding commitment survive while newer dead history stays out of routine
provider context.

`scripts/harness_context_policy_test.py` runs in the real managed Hermes image.
The first pressure fixture deliberately crosses Hermes's actual 50-percent
threshold and proves the shared semantic policy reduces the wire request before
generic compression. The second covers 500 cognition, notebook and recall
actions: committed historical writes become typed receipts and old reads are
evicted as query-scoped evidence without damaging the newest complete tool
pair. `scripts/notebook_scale_test.py` proves list/search never returns full
note bodies and that targeted `get` is independently token-capped.

The end-to-end managed native integration fixture passed against the real game
installation. It created an isolated control plane, prepared a
fresh Tiny/Citizen game with multiplayer timer **None**, started the native
worker and exact-seat MCP sidecar, acknowledged the match briefing, made and
verified a checkpoint, killed the native process, recovered it without UI
input, and confirmed the restored turn. Before capture it created multiple
owned units, deleted an earlier compacting native VEH row, and verified after
recovery that all surviving semantic refs and the private handle capsule were
identical. Its live worker volume survived a verified backup and parking
removed the sidecar. The run also queried the real native Unity Survey and
current Governor fields and the native owned-orbital and completed-project
global adapters before recovery. The provider and specialist measurements in
the table are separately rerunnable live gates; the earlier real-game provider
sample remains recorded as causal no-timer evidence.
The fixture is self-cleaning and never used the developer's normal portal stack
or desktop.

The completed global pipeline carries owned systems, exact Pact/infiltration/
Governor/Empath Guild report entitlements, observed public Secret Project race
events, orbital state, ecology, victory posture, native-life ontology, base
radius/yields/facilities, support, convoy, and transport state from native-shaped
input through projection, `smac_world`, anchor, runtime context, and frozen
specialist snapshot. Unknown rival state remains unknown.

Production-shaped collector measurements were:

| Collector case | Initial / unchanged wall time | Bridge calls | Maximum UI-probe gap |
| --- | ---: | ---: | ---: |
| Small quiet | 399 / 137 ms | 11 | 18 ms |
| Stock Huge quiet | 4,142 / 2,423 ms | 35 | 102 ms |
| Stock Huge active | 5,032 / 2,637 ms | 36 | 99 ms |
| 25,600-square custom quiet | 24,854 / 10,218 ms | 110 | 446 ms |
| Dense overflow/reconciliation | 1,151 / 444 ms | 16 / 14 | 24 ms |

The overflow case drains multi-page native events, reports deliberate
continuity gaps, leaves no native backlog, and retains zero unchanged
projection-object writes. A separate durability fixture stages 1,800 events,
well beyond the native ring capacity, and replays both injected publication
crash windows exactly once. After each partial publish, the fixture changes the
native feed and action revision: immutable N completes with its original
projection/deltas/events, then N+1 consumes the new activity once. The
25,600-square custom case is a
stress fixture beyond the stock Huge map, remains below the 30-second hard gate,
and does not block native/UI responsiveness.

The custom 4,096-square amphibious stress fixture completed in 1.76 seconds.
Candidate pruning retained four embark and eight landing frontier
states while enforcing transport ownership/access, phase-refreshed movement,
capacity, current boarding state, and opposed-disembarkation legality. Search
results expose coverage and optimality; an adversarial feasible landing beyond
the first eight is reported as an incomplete bounded miss, never unreachable.

An earlier exploratory run of the same no-timer path continued from turn 1 to
turn 3 before the integration fixture was tightened to stop on an observed
semantic revision rather than waiting for an autonomous campaign to terminate.
That longer sample made 34 provider calls and used 323,801 input, 7,498 output,
and 3,976 reasoning tokens. These are cumulative provider totals, not active
context occupancy, and are recorded only as directional integration evidence.

During live acceptance, the fixture exposed and the rebuild fixed seven real
integration defects: the MCP sidecar lacked its exact process-session binding,
the collector used obsolete bridge operation names, the MCP runtime helper was
missing two local utilities, and Docker's non-TTY multiplexed log framing was
being mistaken for invalid checkpoint JSON. It also caught an incomplete
authoritative base page and shell-launcher handling for the pinned Hermes entry
point. The final pass additionally caught duplicate `/v1/v1` routing when both
an operator-configured provider base and Hermes supplied the OpenAI API prefix.
Each now has a deterministic
regression contract in addition to the live test.

The historical no-timer gameplay report remains separately documented in
[Bounded agent runtime](2026-09-02-bounded-runtime.md). Every future gameplay
comparison must continue to use multiplayer timer **None** so the native clock
cannot impersonate model success.
