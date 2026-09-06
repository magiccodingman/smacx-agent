# Diagnostics-first review: evidence and attribution

Source campaign: AI - 3, parked at turn 26; match
`match-2a0fe070c51c48089642bfeb556d8b51`. The read-only retained-history snapshot
contains 405 messages. Message IDs below refer to its Hermes SQLite `messages.id`.
The privacy-safe aggregate is in `benchmarks/gameplay-diagnostics-parked-baseline.json`.
A closed Hermes session is not a completed game. Reported cache counters are not
proof of provider cache behavior. Original provider wire requests were not captured.

| Concern | Evidence and classification | Repair / remaining proof |
| --- | --- | --- |
| Forgotten typed intentions | No `smac_memory_update`, notebook or specialist calls in retained history. Message 115 rejects a malformed read-only `smac_memory` call before invocation. No successful typed write is shown to have disappeared. **Prompt/tool-affordance weakness plus observability gap.** | Explicit read/write education; typed horizon/reconciliation; real delivery-chain fixture. Native recovery still required. |
| Artifact uncertainty | No investigation or direct-reference invocation in retained calls; no failed specialist publication demonstrated. **Prompt/tool-affordance weakness; inadequate evidence to blame specialist execution.** | Fresh live audit confirms content-enabled search bypassed hybrid ranking and missed Artifact articles that metadata search found. Unified ranking and bounded body evidence now pass service regression; deployed verification and actual commissioning remain required. |
| Colony Pod availability | Message 139 explicitly offers Colony Pod (30 minerals), Formers (20), Scout Patrol (10) and hurry (11 energy, 5 minerals). Message 147 accepts Colony Pod selection. **Claim that Colony Pod was universally unavailable is contradicted.** | Controlled native replay required for earlier states, switching penalties and observed completion. |
| Hurry rationale | Message 141 accepts 11-credit/5-mineral hurry, leaving 15 energy; old receipt lacks item name. Message 152 says it hurried Scout to free production. **Semantic receipt weakness; strategy quality not established.** | Name current item in catalog/receipt, expose minerals, distinguish completion from hurry. |
| Citizen reassignment | Message 152 carries reassignment intent only in handoff prose; no citizen-management invocation in retained calls. **Intent/focus affordance weakness; no failed native reassignment demonstrated.** | Management remains reachable; explicit current-turn review. Replay legality/yields against native state. |
| Focus tunnel | Ready units are not inherently mandatory, but prior prompt directed clearing focus before unrelated play. **Confirmed prompt/representation mismatch.** | Prompt and ready-focus metadata preserve management access; bounded force summary informs production. |
| Research zero | Message 161 maps 0 to Explore, 1 Discover, 2 Build, 3 Conquer. Later interpretations of zero as unset contradict that catalog; persistent snapshot used an integer. **Semantic representation weakness.** | Named selected preferences and explicit hidden blind-research target semantics; verify native flags. |
| Fungus/landmark expansion | Aggregate counts represent known extent. Generic changed deltas did not establish physical growth; stale refresh could produce a terrain-change label. **Confirmed qualification gap; physical growth not proven.** | Discovery aggregation and change-basis qualifiers; never report zero physical events as proof of no change. |
| Spore distance estimates | Retained calls show no route/reachability query supporting the reported distances. **Unsupported estimates / insufficient provenance, not demonstrated calculator inconsistency.** | Spatial-query education and full tool argument/result capture. |
| Tool failures | Retained results include invalid bases, an unknown decision, an unready unit and wrapper-level failures. Wrapper validation failures never reached MCP. **Confirmed observability gap above MCP.** | Outer Hermes dispatch capture, decoded envelopes and layer-labeled failure metrics. |
| Handoff truncation | Message 152 ends a changed-conclusion sentence mid-phrase. 17 retained handoffs meet the 120-word ceiling, which proves boundedness, not semantic adequacy. **Prose compaction limitation.** | Typed intent is independent of prose; capture emitted response before handoff truncation. |

Current implementation and acceptance gates are tracked in
`gameplay-diagnostics-mission.md` and `game-semantics-coverage.md`. These findings do
not establish poor sovereign strategy after adequate context delivery. The complete
rerun must establish observations, delivered context, chosen actions, effects and
recovery continuity before making that attribution.

## Deployment acceptance findings after the retained run

The first instrumented turn-26 resume proved three harness defects before meaningful new strategy could be assessed: a sealed Hermes archive was unreadable to its restore uid; resumed Hermes reused the saved old system prompt despite explicit profile recompilation; and repeated recovery lost an unpinned derived checkpoint snapshot during timeline GC. Each is now covered by a targeted or controlled native regression, including two actual recoveries from one checkpoint. The old campaign remains parked because its missing accelerator cannot be claimed intact.

The captured short resume also exposed an unknown-name rejection before the executor and a cross-thread correlation gap. These are repaired and verified with an actual provider-driven Hermes session. A subsequent final-unit attempt was rejected for unacknowledged critical attention without consuming the decision or executing native action; this is guard behavior, not proof that the new prompt confused the model, because the old prompt was still on that wire. A fresh AI - 4 lobby is staged for autonomous acceptance with the corrected package.
