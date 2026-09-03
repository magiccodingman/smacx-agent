# Strategic world and provider-context acceptance

These content-free measurements were produced by the deterministic fixtures in
this repository on 2026-09-03. They contain no prompt bodies, game assets,
conversation text, saves, credentials, or private endpoints.

| Gate | Result |
| --- | ---: |
| Small quiet 64K anchor | 615 estimated tokens |
| Huge quiet 64K anchor | 617 estimated tokens |
| Huge quiet growth | 0.325% for 100× as many tiles |
| Huge quiet 256K anchor | 617 estimated tokens |
| Huge fragmented 64K anchor | 3,307 estimated tokens; 3,176 regions explicitly omitted |
| Huge chaotic 64K anchor | 5,976 estimated tokens |
| Huge chaotic 256K anchor | 15,980 estimated tokens |
| `smac_world` serialized schema | 1,475 bytes; 492 conservative tokens |
| v6 system prompt | 5,020 bytes; 1,067 exact Qwen3.8 tokens |
| Managed provider tools | 17 |
| Live stable-prefix second request | 23,072 locally cached tokens of 25,243 prompt tokens |
| Live read-only specialist | 449 prompt, 299 completion, 0 reasoning tokens; strict schema accepted |
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
`scripts/specialist_provider_live_test.py` independently proves that a direct
child call receives no tools or sovereign history and returns the strict cited
result envelope without model reasoning.

The end-to-end managed integration fixture also passed against the real game
installation and provider. It created an isolated control plane, prepared a
fresh Tiny/Citizen game with multiplayer timer **None**, started the native
worker and exact-seat MCP sidecar, acknowledged the match briefing, made and
verified a checkpoint, killed the native process, recovered it without UI
input, and confirmed the restored turn. A managed low-reasoning Hermes/Qwen
seat then executed an opaque legal choice that advanced the native semantic
revision. Its session and live worker volume survived a verified backup, and
parking removed the sidecar. This bounded gate made six provider calls and used
55,719 input, 936 output, and 427 reasoning tokens. The fixture is self-cleaning
and never used the developer's normal portal stack or desktop.

An earlier exploratory run of the same no-timer path continued from turn 1 to
turn 3 before the integration fixture was tightened to stop on an observed
semantic revision rather than waiting for an autonomous campaign to terminate.
That longer sample made 34 provider calls and used 323,801 input, 7,498 output,
and 3,976 reasoning tokens. These are cumulative provider totals, not active
context occupancy, and are recorded only as directional integration evidence.

During live acceptance, the fixture exposed and the rebuild fixed four real
integration defects: the MCP sidecar lacked its exact process-session binding,
the collector used obsolete bridge operation names, the MCP runtime helper was
missing two local utilities, and Docker's non-TTY multiplexed log framing was
being mistaken for invalid checkpoint JSON. Each now has a deterministic
regression contract in addition to the live test.

The historical no-timer gameplay report remains separately documented in
[Bounded agent runtime](2026-09-02-bounded-runtime.md). Every future gameplay
comparison must continue to use multiplayer timer **None** so the native clock
cannot impersonate model success.
