# Sovereign checkpoint 5: integrated acceptance

Status: **accepted within the explicit evidence limits below**. Parent checkpoint:
`8ff46e93cd8381086430785ba8856a701d761e7e`.

## Current findings

Concurrent single-player and two-seat LAN startup reproduced a readiness defect:
the single-player MCP port was healthy while its first 8,214-object observation
was still being reconciled. Collector telemetry recorded 47,851.667 ms; the
30-second test request timed out, and the server subsequently finished with a
broken pipe. The production provider request timeout is shorter still (10 s).
This was not a native UI request taking 47 seconds.

Managed startup now performs the initial active-world reconciliation before
opening its runtime and MCP listeners, then starts independent background
observation. Sidecar health startup grace is 60 s inside the existing 90 s
orchestration budget. Provider HTTP timeouts, native page bounds and the 500 ms
native/UI gates remain unchanged. A lobby may have no world yet: its observer
continues and later requests still require successful reconciliation. A blocking
startup contract proves listeners are not opened during the cold collection.
The corrected concurrent run passed startup and reached managed gameplay.
The initial reconciliations in that run measured approximately 4.5–6.0 s;
readiness waited for them. The final R12 single-player and two-seat LAN processes
both exited successfully after concurrent startup, complete managed actions and
checkpoint recovery on the latest image.

The first integrated two-seat LAN run passed native host/join, managed energy,
technology and Pact terms/acceptance/effects, checkpoint reload and preservation
of both seats' observed agreements. The final R12 run additionally passed a paid staged Social Engineering
choice, exactly-once charge and pending-model replication, and selected-policy
recovery on the latest batched-publication image. Its private native fixtures require both explicit test-mode and LAN
fixture flags; the production environment does not inherit the latter alone.

The R10 single-player run then exposed an invalid assertion in the movement
acceptance helper. The preserved target was current fungus/rolling terrain;
the cached public preview explicitly said `stochastic_earliest`, with no finite
guaranteed upper bound. Native movement rolled a failure and consumed movement
without changing location. The calculator's qualification was correct. The
exact-arrival comparison now requires `exact_known_state`, rather than treating
a conditional earliest arrival as a guarantee. No native movement rule or
probabilistic qualifier was removed.

## Measurements

| Gate | Current result |
| --- | --- |
| Quiet 256 → 25,600 tiles | 1,053 → 1,056 estimated anchor tokens; 0.285% growth |
| Fragmented Huge 64K | 5,449 tokens, below 6,000 |
| Chaotic Huge 64K / 256K | 5,669 / 15,809 tokens, below 6,000 / 16,000 |
| Large spatial scope | 65,536 tiles; 99-token public descriptor, 317-byte private definition; 2,118 ms create / 1,842 ms inspect |
| Semantic GC pressure | 45,697 → 2,436 estimated provider-wire tokens |
| Million-token cognition fixture | 1,074,269 → 13,185 estimated provider-wire tokens |
| Native 32-site / 512-base receipt | 231.477 ms wall / 222.187 ms maximum probe gap; 169,970 bytes |
| Native four-site / 511-base economy | 210.431 ms wall / 218.370 ms maximum probe gap; 52,338 bytes |
| Live Qwen prefix reuse | 21,424 of 23,843 prompt tokens; synthetic stable-prefix probe, no gameplay inference claim |

The collector benchmark exceeded its existing 30-second large-custom-map gate
both during concurrent native tests and in a dedicated run (38,123 ms; UI probe
331 ms). Stage timing isolated publication rather than projection: 25.8 s in
journal appends, 12.6 s in individual SQLite observation-cache writes, and 0.44 s
in the projector. Canonical journal writes remain unchanged. Rebuildable world
and semantic observation-cache rows are now grouped into one transaction per
publication group, after their canonical events have been appended and before
attention evaluation. No SQLite write lock is held while appending those events.

The targeted 25,600-square rerun measured **22,121 ms**, with a **272 ms** maximum
UI probe gap. The subsequent complete collector benchmark passed every unchanged
gate: the same quiet case measured **17,057.537 ms / 271.816 ms**; the dense
128-ready-Drop-unit case measured **20,503.319 ms / 286.841 ms**. Its routine
unit payload was 73,339 bytes. Overflow drained 768 events in three pages while
retaining incomplete-continuity evidence. Every unchanged case wrote zero
projected object rows, and canonical journal replay matched object counts. The frozen-publication regression passes injected failures before,
during and after the cache commit, preserving 279 journal/projected objects
without duplicated canonical events. Existing full observation crash/replay
contracts pass. The 4,096-square amphibious benchmark also passed at 2,431.661 ms
against its unchanged 5,000 ms gate. The uninterrupted R12 native sequence also passed.

## Geographic communication audit

The sovereign can express human-readable geographic descriptions with existing
queries: current known base/landmark names and `smac_world(mode="relation",
origin_ref=..., target_ref=...)` supply bearing and distance over its own known
map. A phrase such as “west of Gaia's Landing” can travel as ordinary chat text.
The recipient must establish what the name means from its own knowledge or
conversation; the sender cannot certify mutual knowledge. No extra provider
tool or cross-perspective reference resolver is necessary.

`smac_chat` passes text and native recipient/identity fields to
`semantic_chat`; `message_chat` sends the stock packet. The controller persists
speech with `untrusted_in_game_speech=true`. Its envelope contains participant
identity and message sequence metadata, not a map or a resolved location.
The controller regression passes a location-like string through received
speech and verifies text-only transport metadata. This establishes the managed
adapter boundary, not a native map-exchange outcome.

The native map-transfer branch in `tech_achieved` (`tech_id >= 9999`) explicitly
copies entitled map knowledge and remembered items, separate from chat. The
paired-human native clause record contains no map-transfer clause; AI map
negotiation has its own typed dialogs. No bridge chat wrapper adds a Pact map
synchronization call. Source review alone does **not** prove that every stock
Pact conversation automatically exchanges maps, that reopening it refreshes
newly learned geography, or that native packet processing never changes map
knowledge incidentally. Those specific contact/refresh outcomes lack a clean
controlled native comparison in this checkpoint and remain unclaimed. Pact
membership is never promoted into continuous allied live vision by the world
projector; existing entitlement and fair-play fixtures retain that boundary.

## Final acceptance

The complete R12 single-player sequence passed native entitlement/global-world
publication, guarded managed action/effect comparisons, six fixed-surplus native
production upkeeps and a deterministic one-phase move, and intent milestones.
Seven acknowledged runtime batches delivered milestone/production/interruption
attention. The trusted response hook was simulated; no provider gameplay call
was made.

The final checkpoint/park/recover sequence preserved native completed-unit
identities, the first-query journal plan/conflict and stationary assignment.
Old-timeline watches and old-session counterfactual choices were rejected.
The worker parked and its MCP sidecar was removed. The concurrent R12 LAN run
passed agreements, selected-policy recovery and native identity/seat restoration.
Both full processes exited zero; these are uninterrupted acceptance results.

All 30 definition-of-done rows have appropriate evidence and explicit limits.
The coverage matrix and [sanitized machine-readable evidence](2026-09-05-integrated-acceptance.json)
are updated before this checkpoint is committed. Existing checkpoint reports
retain historical failures and their original evidence boundaries. No native
UI budget, provider timeout, fair-play gate or journal authority was weakened.
Do not merge PR #48.

## Definition-of-done audit

These rows reference the accepted earlier checkpoints and final integrated
regressions. Qualifications limit claims even when the acceptance row passes.

| # | Requirement | Evidence / qualification |
| --- | --- | --- |
| 1 | Four geography blockers | Checkpoint 1; sovereign geography and hardening contracts |
| 2 | Fair and epistemic geography | Fair-play differential and geographic semantics contracts |
| 3 | Omitted geography queryable | World-geography pagination and containing-region adversaries |
| 4 | Physical geography independent | Terrain/mobility/ownership/diplomacy invariance fixtures |
| 5 | Separate distant theaters | Giant-continent multi-crisis and amphibious cross-region fixtures |
| 6 | Correct base/reinforcement dependencies | Relevant responder invalidation and irrelevant-change cache reuse |
| 7 | Cheap warm geography queries | Measured cold/warm queries; heavy reconstruction bypass contract |
| 8 | Global systems survive many bases | Mature 60-base strategic-anchor starvation fixture |
| 9 | Frontier promotion/freshness | Event/focus/plan/watch promotion and map-only freshness fixtures |
| 10 | Opaque semantic resolution | Equality mappings and outside-page native Drop execution; no reference decoding |
| 11 | Qualified repair/staging | Owned/stale Pact/no-riot/ferry/no-transport and rules fixtures; native healing totals not asserted |
| 12 | Legitimate Unity Survey | Compiled producer, actual managed native global pipeline, channel-entitlement adversaries |
| 13 | Zero-turn ETA ordering | Hardening contract |
| 14 | Field/type-correct watches | Selected production-field, mapping and null equality fixtures |
| 15 | Visible watch invalidation | Lifecycle notices, restart, expiry and publication retry contracts |
| 16 | Sovereign spatial scopes | Bounded private definitions, native-radius adapter and 65,536-tile scale fixture |
| 17 | Occurrence-correct production | Actual repeated native unit/facility/project production and interruption; semantic event and birth-identity contracts |
| 18 | Aggregate plan milestones | Journal-linked all/threshold/destroyed-requirement contracts and native-to-runtime milestone delivery |
| 19 | Explicit assignment awareness | Stationary assignment represented separately from native order and unassigned actionable units |
| 20 | Reservation/dependency conflicts | Journal-authoritative timed unit/base/credit conflict and stale dependency contracts |
| 21 | Meaningful bounded site economics | Feasible joint alternatives; actual founding/Recycling Tanks comparison; hidden-input differential; bounded four-site receipt |
| 22 | Explicit terraforming delta | Actual cooperative Farm completion versus standalone/owned-base yield predictions |
| 23 | Production plus deployment | Near/far build-and-travel, existing/transport/air/stale adapter cases; actual constant-surplus upkeep and movement comparison |
| 24 | Honest social preview | Native ratings/charge/support/stock comparisons; complete psych/commerce/research excluded |
| 25 | Managed parameter workflows | Checkpoint 2 actual 15-tool action/effect chains; R12 LAN staged social/charge/replication/recovery passes |
| 26 | Fifteen top-level tools | Current emitted-schema budget contract |
| 27 | Context/Huge scaling | Anchor/runtime/scope/GC and complete collector latency gates pass |
| 28 | Native responsiveness | R12 site/Drop receipt and independent probe assertions pass below unchanged 500 ms gates |
| 29 | Recovery and fairness | Final fair-play/rollback/cache-crash contracts and uninterrupted R12 single-player/LAN recovery pass |
| 30 | Accurate coverage/docs | Per-checkpoint evidence preserved; final matrix and 30-row audit accepted with explicit limitations |

## Reproduction and evidence interpretation

Run deterministic MCP-dependent scripts from the repository root:

```sh
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  smacx-agent-control:dev scripts/<test>.py
```

The integrated batch passed geography/hardening, scopes/milestones, runtime,
reference bounds, observation, attention/communication, operations, managed MCP,
briefing, strict prompt, specialist, memory/snapshot scale, strategic-world and
world-context tests. Its original collector failure stopped the batch before
amphibious routing. Both benchmarks subsequently passed separately. The final
publication regression group passed world-model, rollback, fair-play, schema and
readiness contracts. The batch-cache crash test and existing observation recovery
suite passed after the publication change. The JSON lists individual scripts.

```sh
PYTHONPATH=src python3 scripts/specialist_supervisor_contract_test.py
dotnet test Smacx.Agent.slnx --no-restore
docker run --rm --entrypoint /opt/hermes/.venv/bin/python \
  -e PYTHONPATH=/workspace/harness -v "$PWD:/workspace:ro" -w /workspace \
  smacx-agent-harness:dev scripts/harness_context_policy_test.py
python3 scripts/provider_prefix_cache_live_test.py --base-url <provider-v1-url> --model Qwen3.8-27B
```

The host specialist supervisor and 63 .NET tests passed at checkpoint 4; neither
implementation changed at checkpoint 5. The native worker's 38 cross-build steps
completed at checkpoint 4 and that exact image is reused. These are build steps,
not 38 gameplay tests. The control image was rebuilt after startup and cache
publication fixes. Native acceptance uses the local licensed game installation:

```sh
SMACX_TEST_GAME_SOURCE=<licensed-game-directory> \
SMACX_TEST_CONTROL_IMAGE=smacx-agent-control:pr48-integrated-r12 \
SMACX_TEST_WORKER_IMAGE=smacx-agent-worker:pr48-checkpoint4-r8 \
SMACX_TEST_MCP_IMAGE=smacx-agent-control:pr48-integrated-r12 \
PYTHONPATH=src <MCP-venv-python> -u scripts/control_worker_mcp_live_test.py
# Same environment, concurrent isolated installation:
# <MCP-venv-python> -u scripts/control_lan_live_test.py
```

The final report distinguishes native comparisons from native-shaped fixtures.
Provider prefix reuse is a synthetic two-request probe; no new autonomous
sovereign/specialist gameplay inference or strategy-quality result is claimed.
All token estimates are explicitly separated from the unchanged prompt's live
Qwen tokenizer measurement. Timings are individual runs, not latency percentiles.
