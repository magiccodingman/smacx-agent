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

## Instrumented AI - 4 findings through turn 12

- **Confirmed harness/runtime bug:** an output-limit response retained the sovereign lease while Hermes appended a continuation. The next context acquisition failed `sovereign_invocation_already_active`. Repaired cancellation is verified in a real Hermes/provider/lease-endpoint continuation; normal ownership checks remain enforced.
- **Confirmed tool-affordance gap:** the sovereign explicitly wanted to persist its strategic read but lacked the required native session ID in runtime context. The current observed session now accompanies action revision; runtime assembly passes. Autonomous typed write/use remains to be observed.
- **Confirmed SDK boundary bug:** undeclared top-level parameters were silently discarded by MCP before execution. Managed and specialist servers now reject them before dispatch; actual SDK and iterative specialist tests pass. Subsequent live rejections are visible, so they are not silently substituted actions.
- **Confirmed semantic attention weakness:** inactive sunspot counter decrements generated critical alerts. The corrected collector retains state without alarming on counter-only unchanged activity; live 7→8 verification passes.
- **Confirmed upstream coroutine allocation bug, not duplicate RPC:** pinned Hermes created a discarded watcher to inspect awaitability. Installed-code tests verify one watcher and preserved cancellation.
- **Insufficient evidence:** the early worker outage recovered but its original logs were replaced. Controlled Auto Explore replay did not reproduce it. Pre-replacement liveness capture now retains future evidence.
- **Insufficient evidence:** earlier `invalid_tile_id` context failures occurred near native transitions. No causal attribution is made from the exception name; new failures retain bounded stack locations.
- **Temporal identity contract preserved:** apparent contact-reference churn can occur around an unproven feed/snapshot cut. Added committed publication receipts permit causal comparison; private-handle identity stitching and unsupported attention suppression were not introduced.

These findings are harness and delivery defects or explicit evidence gaps, not a diagnosis of poor model intelligence. The acceptance game has resumed from a verified turn-12 native+AI checkpoint on the repaired runtime.

The production audit subsequently verified a narrower semantic problem: AI - 4 read a false governor hurry flag as a direct player restriction. The governor object was delivered, so this was not omitted data; its scope was inferred incorrectly. The native turn-14 catalog offered hurry and Colony Pod production despite that flag and population 1. Explicit governor-automation scope metadata addresses the ambiguity without changing legality or dictating production strategy. Turn-12 costs and universal legality are not inferred from the later catalog.

### Turn 16→17 handoff contradiction

Classification: **confirmed harness response bug**, behavioral contribution not isolated. Actual provider request `fd51506abb8249fe81d28f1dec40a541` contained both a required no-more-tools handoff and `required_next.tool=smac_decision`; sovereign followed the latter. The immediate receipt was present, so later state-result compaction does not prove omission at the decision point. `_attach_turn_handoff` now makes the stop/message next step authoritative for explicit and automatic transitions. Regression and live acceptance evidence: `benchmarks/gameplay-handoff-next-step.json`.

### Named technology lookup at turn 19

Classification: **confirmed retrieval ranking weakness**. The sovereign requested both Centauri Ecology and Planetary Networks; only the former reached its evidence and it explicitly cited the missing latter when rejecting a trade. Exact-name control lookup finds the missing document. Query/title stemming differed, and full names inside the question lacked precedence. The scoped rank repair has 9/9 knowledge-service regression coverage. This does not establish that Datalinks prose exhaustively describes all unlocks; nor does it validate the sovereign's separate assumption that sharing a technology means losing it. See `benchmarks/gameplay-named-reference-search.json`.

### Working-set private action selectors at turn 20

Classification: **confirmed provider projection bug**. The live managed memory response included raw selectors in historical own-action choice_parameters because working_set returned before the shared provider filter. Other read paths also did not strip all native entity selector names. The repair filters every managed memory view and performs managed search matching after filtering; it leaves the authoritative journal untouched and does not infer historical public identity from current native slots. This observation proves internal selector exposure, not a hidden enemy-state disclosure. Evidence and recovery tests are in `benchmarks/gameplay-memory-provider-filter.json`.

### Semantic preflight and resume checkpoint

The stopped turn-21 resume reported raw ~198,817-token preflight compression despite send-time semantic GC. Pinned Hermes preflight reads durable history before the sanitizer; its small-context floor also raised the configured 50% ratio to 75%. The managed adapter now estimates its copy-only semantic projection plus tool schemas and bounded runtime reserve without fetching runtime state or leasing attention. Profiles supply the supported absolute threshold cap. Irreducible durable history still fails closed.

The real receiving-provider Hermes test resumes SQLite containing a deliberately oversized old tool result: exactly one resumed provider request, no summarization, original durable result preserved, disposable marker absent from wire. Existing direct-schema, runtime lease, generation and prompt-integrity cases pass. Installed-Hermes context tests cover no runtime fetch, input immutability, irreducible failure and effective 64K/256K caps. Live restore/deployment remains pending; the verified turn-21 checkpoint predates the unwanted compaction. See `docs/benchmarks/gameplay-semantic-preflight.json`.

Deployed preflight acceptance: restored verified turn 21 and matching AI history into `timeline-restore-07b030faf15046d19cbd2ec3`; new run `run-548df7b0ff4248d09b0d0ebebca27001` has effective profile cap 131,072. Actual semantic preflight projected 412 durable rows to 62, estimated 55,150 including 32,768 runtime reserve, and did not fetch runtime context. Provider request `4d21ba0908a84f2885a0968ff95f21db` followed without the previous generic summarization. Portal unpaused; sustained gameplay acceptance continues.

### Native collection transition race checkpoint

Live turn-21 Auto Explore executed successfully, then request-time collection rejected mixed native revisions (`world_changed_during_collection`, event `ac98b9f1bf804b8499884edea446edca`). The snapshot guard is correct; treating this first transient rejection as a fatal Hermes context failure is brittle. The private handler now retries that exact condition at most three times, before runtime services or attention acquisition. No mixed snapshot is returned; other failures and exhausted retries still return 409. Deferred attempts remain visible in diagnostics and human traces.

Actual HTTP handler tests verify two races followed by one context/lease, exhausted races with none, and immediate unrelated-error rejection. Collector crash-stage recovery and cold-readiness contracts pass. Native+AI checkpoint `checkpoint-4108d3755f6744e892a792fbdcb4c672` verifies turn 22 after the successful order. Live repaired retry remains pending, separately tracked in `benchmarks/gameplay-runtime-collection-retry.json`.

### Recent diagnostics database window checkpoint

The live unordered telemetry limit followed the category index: its 10,000 rows contained attention/collector data and none of the 2,730 runtime-context records. Capped state exports now explicitly select most-recent timestamps with deterministic ties and advertise retained counts/order/timestamp bounds. Older omitted records still produce a row-limit gap; raw stream capture policy is unchanged. Regression covers adversarial category/insertion ordering and a newer foreign-match row. A read-only export against the live database produces a valid ZIP with 10,000 recent records, including 975 runtime-context entries; this proves backend selection, not browser download. Evidence: `benchmarks/gameplay-diagnostics-recent-window.json`.

Collection-race repair deployment: restored turn 22 into `timeline-restore-21dddfdc676747d789a229c9`; sovereign resumed and queried both production and citizens through managed choices. No repeated live revision-race has yet exercised the new retry, so that proof remains controlled-handler coverage.

### Production quote delivery checkpoint

The sovereign switched to Colony Pod at turn 22, then repeated production queries because hurry disappeared. Native already supplied current production and hurry metadata at catalog top level, but the managed frame omitted it. An operator native read verifies Colony Pod 30 minerals, 2 accumulated, +2 surplus; hurry legal but unaffordable at 95 credits versus 58 available. Managed production frames now retain an allowlisted bounded `production_context` with current/queue/hurry facts; missing values remain absent, and no action or legality changes. Native entity slot IDs in execution receipts are removed through the existing semantic translator after execution/journaling, preserving effect fields and opaque choice linkage.

Managed entry-point adapter, opaque execution, staged paths, failure circuit and semantic binding tests pass. Native quote verification is separate from the adapter fixture; actual provider delivery remains pending. Verified native+AI turn-22 checkpoint `checkpoint-0d35c5d505a34342b5a31dd1fede56ba` retains the production switch. Evidence: `benchmarks/gameplay-production-context-delivery.json`.

Live acceptance through turn 23: corrected end-turn receipt and sovereign handoff observed. Actual request `dc431aed58ee4f00a7e938c921d27ffa` contains three Scouts, two Auto Explore/one none, Colony Pod production, zero missing/noncurrent force fields, and explicit overlapping-capability/home-support qualifiers. Deployed authenticated backend archive `...8b8a64553b74474c8a79f42f77ae81cc.zip` is 15,225,068 bytes, ZIP-valid, with 897 recent runtime-context telemetry records in the bounded window. The first startup export returned HTTP409 without retained response body; cause is unknown, and the next request succeeded. Browser delivery is still unverified. See recent-window and force-summary live evidence files.

### Diagnostic export error capture checkpoint

The first live export attempt returned409, but authenticated GET artifact creation was outside the existing lifecycle error-capture scope. The endpoint now enables the same safe operation/code capture after authentication; no raw exception text, credentials or anonymous probes enter the archive. Actual HTTP regression verifies anonymous401/no new record and authenticated409/one scoped record. The original startup409 cause remains unknown; subsequent export succeeded. See `benchmarks/gameplay-export-error-capture.json`. This control-API-only change does not require native/MCP replacement.

Live research acceptance: delivered owned technology count is 2 at turn24 (request `63dd9ddad64949198117772ac67f0634`) and 3 at25 (`abf6f0a23a414d13b72264d299724745`), with Social Psych present. The sovereign’s acquired-Build-tech statement is supported; it is not diagnosed from counters alone. Request `f46e2491892f4d9289336d8a1f5b4d0a` explicitly contains selected Build preference, category/target distinction, and hidden next target with null name/ID. Authenticated export-error capture is deployed on healthy control API; native/MCP were not restarted for that endpoint-only patch. Evidence files retain controlled versus live distinctions.


### Turn-25 incident and collector fault origin checkpoint

AI - 4 acquired Social Psych at turn 25, then experienced `invalid_tile_id` during collection and a subsequent bridge timeout after technology-presentation acknowledgement. Incident `incident-0def74e153274f11a66ea9b7884fe006` quarantined worker and collector; the native process and incident bundle remain preserved. The last verified native+AI recovery point is the turn-22 Colony Pod checkpoint. These observations establish failure order, not that the two errors share a cause. Read-only inspection finds the modal state released; it does not identify the blocked native continuation.

Collector telemetry now retains up to eight original source frames (basename, line, function) and a bounded error code while preserving the original exception and stage restoration. The regression verifies exclusion of raw exception detail and locals; all three journal/cache crash-recovery windows pass. Deployment, original invalid-tile diagnosis, native hang repair and full-game acceptance remain open. Evidence: `benchmarks/gameplay-collector-failure-origin.json`.


### Native request-stall diagnostic checkpoint

Read-only frozen-process inspection identifies request_in_progress=true, pending=true, modal service depth1 and no active modal. The recovered Windows stack runs ModPeekMessage → native wait_task/Console_human_turn → control_turn/control_game, rather than the technology window. The native acknowledgement returned successfully; a later snapshot failed. A rebuild of the original native source exactly matches the preserved DLL text SHA-256, validating symbol locations. This proves a stuck dispatch guard, not why cleanup was skipped. No guard reset was attempted.

A diagnostic-only native exception observer and atomic request-stage markers now preserve active-stage/first-chance-exception codes in timeout errors. The observer continues normal Windows exception search and never releases the request guard. Real Windows dispatch under worker Wine verifies active UI-thread/request scoping, pass-through to the next exception handler and unchanged guard state. The native cross-build passes. These diagnostics do not constitute the hang repair; controlled replay and root-cause repair remain pending. Source compatibility was reviewed and registered for this observation-only addition. See `benchmarks/gameplay-native-request-stall.json` and the frozen evidence in `gameplay-collector-failure-origin.json`.
