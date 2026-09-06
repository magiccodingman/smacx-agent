# Gameplay diagnostics acceptance mission

Controlling specification: the user's 19-section gameplay observability review,
accepted proposal, and autonomous execution instruction. This mission does not
equate native turn progression with competent strategic play. No automatic merge.

Baseline: AI - 3, match-2a0fe070c51c48089642bfeb556d8b51, is parked at turn 26.
Preserve its saves, Hermes history, journal, attention and checkpoint evidence.
Previous action-progress PR #54 is merged. New work starts from updated main.

## Reviewable checkpoints

1. Causal diagnostics: Hermes pre-dispatch failures, managed tool calls/results,
   deferred native effects, actor/call/request/revision correlation, and explicit
   capture failures. Human summaries must never invent a verified effect.
2. Provider request audit: final wire content, exposed tools, runtime envelope,
   cognition revisions, omissions and history compaction. Distinguish assembled,
   submitted and responded; SQLite existence is not proof of provider receipt.
3. Cognition and specialist lifecycle: synchronous visibility, journal failure
   windows, handoff/resume/GC/recovery, reference tools and child traces, attention
   placement and acknowledgement. Do not equate acknowledgement with action.
4. Semantic/action corrections: named research preferences, qualified discovery
   changes, production consequences, management availability and nonblocking
   focus. Replay historical production/citizen scenarios against native state.
5. Bounded typed-intent reconciliation, including automatic native turn changes.
   Reuse plans/goals; explicit defer/cancel/block choices; no prose-to-obligation
   inference or mandatory completion of long-horizon strategy.
6. Authorized live/archive lobby diagnostic download, consistent event watermark,
   completeness manifest, performance/retention checks and integrated acceptance.
7. Deploy and resume or replace acceptance campaign. Monitor every ten minutes,
   inspect traces and strategic behavior, pause/fix/test/update PR/deploy as needed.

Update coverage and evidence at every checkpoint before moving on. Individual
classes or synthetic tests do not complete a checkpoint. Diagnostics stay outside
journal authority and outside sovereign input. Preserve fair-play and native safety.

## Initial storage substrate evidence

`smacx_diagnostics.py` introduces versioned per-actor streams with unique event and
stream IDs, sequence and timestamps, correlation fields, credential-field redaction,
bounded record payloads and a terminal capacity-gap record. Contract tests cover
concurrent records, actor isolation, redaction and explicit size-limit gaps.

This is only the storage substrate. No live interception, provider-wire audit,
export authorization, aggregate retention or gameplay correctness is claimed yet.
Generic field redaction is not sufficient for arbitrary raw provider/network data;
capture adapters must supply allowlisted metadata and separately sanitized content.

## Hermes dispatch interception (partial checkpoint 1)

An opt-in hook now captures emitted calls and returned tool rows outside Hermes's
execution dispatcher, including errors rejected before MCP invocation. It retains
original arguments/results and return behavior, correlates IDs, and records missing
results and batch exceptions. Parallel-batch timing is explicitly batch timing,
not falsely attributed to each tool. The hook is not enabled in deployment yet.
A contained regression verifies an unavailable-tool failure is captured exactly
once without changing its result. Live upstream integration, per-tool timing,
MCP/native correlation and capture retention across restarts remain open.

## Serialized provider request adapter (partial checkpoint 2)

The opt-in Hermes diagnostics hook now intercepts synchronous HTTPX chat-completion
POSTs after JSON serialization. It records the actual body, byte count/hash, and
request ID; response headers and transport failures retain the same ID. Headers,
URL credentials, queries and exception messages are excluded. HTTP response headers
are explicitly not a completed model response. An HTTPX MockTransport test in the
actual Hermes image verifies body equality and unchanged success/failure behavior.

This covers the synchronous chat-completions transport used by the current local
provider. Async transports, other provider protocols, response-stream completion,
per-item omission manifests and cross-stream correlation remain open. The general
control image lacks HTTPX; this adapter's test runs in the Hermes image where the
integration actually lives. No live provider request was made for this contract.

## Integrated adapters and export acceptance (still partial)

Managed tool registration now preserves signatures while recording invocation,
arguments, return and exception events. Native calls inside those invocations share
an invocation identifier. The request-only context assembly records its result and
cognition selection inventory; this inventory explicitly starts after journal
working-set selection and does not claim to reconstruct earlier omissions.
Hermes records its emitted assistant message before handoff truncation and records
history compaction metrics. Deployment flags enable these adapters for new workers.
They have not yet been verified against the live parked campaign.

Campaign exports include match-scoped relational state, specialist child tables
joined through their mission, compressed specialist traces, retained Hermes history,
and new diagnostic streams. The manifest records file hashes, byte watermarks,
partial tails, missing sources and export limits. Retained history is bounded and
is labeled retained history, never retroactive provider-wire capture. Helper failure
fails the download explicitly. The administrator-only lobby route is implemented.
The completeness flag remains false pending integrated acceptance and retention work.

Validation: campaign export contract passes scope isolation, partial-tail reporting,
manifest honesty and readable summaries; diagnostic writer contract passes; strict
prompt contract passes in the control image. Portal suite: 72/72. Browser validation,
live helper execution, aggregate retention and end-to-end capture remain required.

## Semantic and current-turn review checkpoint (implementation; native acceptance pending)

The runtime now presents bounded owned capability-role counts, observed orders and
production counts. Roles explicitly overlap and are not inferred assignments; absent
or noncurrent fields are counted separately. Ready-unit focus explicitly preserves
management access. Blind research exposes category names and hidden-target semantics;
hurry receipts identify the current item and distinguish added minerals from verified
completion. Native cross-build passed after these additive receipt changes.

Existing goal `trigger` / plan `timing` supports six `intent_horizon` values. Only
explicit `this_turn_required` / `this_turn_preferred` work participates in the bounded
review. Unbudgeted journal replay supplies it, so cognition trimming cannot hide a
blocking item. A same-record reconciliation records current-turn deferral/blocking
and a reason; it does not complete the goal. Cancellation uses existing abandoned
status. Next turn reconsiders deferred current-turn work; longer horizons never gate.
Native execution is guarded before End Turn, skip-all and final-unit actions, including
stale retries. Production remains reachable. Critical committed attention must be
considered/acknowledged separately. Actual native auto-transition acceptance is still
required; adapter tests alone do not certify the running game's behavior.

Discovery batches now report newly known extent and qualified feature counts, rather
than implying physical creation. Terrain differences distinguish current/current
observations from knowledge refresh, with cause and occurrence time not invented.
Native cache-change events are explicitly labeled as cache evidence. Runtime delta
semantics explain that anchor representation changes do not establish physical growth.

Validation: runtime context/huge-map contract passes (64K and 256K); guarded controller
memory survives journal reinitialization; internal journal crash cases pass. Intent
journal/policy and production managed-command adapter tests pass, including last-unit
closure, explicit closure, management reachability and critical-attention gating.
Discovery aggregation/epistemic contract, native observation contract, concurrent
publication visibility/crash cases, opaque choice execution and doctrine content
contracts pass. Full provider delivery/recovery chain, native management replay,
performance/retention, deployment and full-game acceptance remain open.

## Delivery-chain and export evidence update

`cognition_delivery_contract_test.py` exercises the production typed writer with a
controlled fair-play observation, real SQLite and canonical journal, real runtime
assembler, actual Hermes request sanitizer/semantic GC and HTTPX MockTransport.
The same goal survives the immediate next request, handoff/resume, a 385,083-token
history pressure case (11,090 tokens after GC), and journal reinitialization. Request
assembly leaves durable message history untouched. Injected post-journal projection
failure reports `journal_committed`; subsequent canonical reads retain the record.
This is not a bridge/native checkpoint recovery claim.

The provider adapter now captures emitted response-stream JSON, including reasoning
fields legitimately returned by the provider. It records stream exhaustion, truncation
and the completion marker separately from header receipt. Request/context hashes,
episode and attention-lease IDs correlate the assembled envelope with serialized
wire requests and subsequent dispatch. Controlled transport tests pass.

Production diagnostic streams use gzip members and persistent match-wide byte limits
(512 MiB per stream, 2 GiB per diagnostic root/match). Restart cannot bypass the limit;
a durable exhaustion marker and capture-gap receipt remain visible. Export accepts
compressed streams, preserves journal committed-prefix watermarks, produces bounded
human summaries and layer-labeled failure/latency metrics. Failure observations from
multiple layers are not falsely counted as distinct incidents. The three latest
regenerable ZIP exports per match are retained. Administrator authorization is tested
at the HTTP endpoint, including denial to an authenticated lobby participant.

The parked AI - 3 Hermes snapshot helper was exercised read-only: 405 retained messages
(26 user, 196 assistant, 183 tool), 806,041 bytes. A storage-only benchmark reconstructed
615,919-byte payloads from retained messages: 20 compressed records occupied 1,860,960
bytes; median emit 9.50 ms, maximum 10.12 ms. This is not retroactive provider-wire
capture. Full original requests remain unavailable for that historical run.

Specialist supervisor host contract passes including real child processes, isolation,
cancellation, timeout, bounded retries, trace hashes/redaction and retention. Specialist
trace rows now carry faculty actor, mission, attempt, parent episode and timeline IDs.
The existing specialist proxy already captures offered tools/provider exchanges and
MCP queries; campaign exports now retain those compressed traces and related tables.

Reconciliation follow-up: review frames now return before consuming an opaque choice
or incrementing unchanged-action attempts. Eight consecutive reviews in the controlled
managed boundary test leave the decision available and do not open an execution
failure circuit. Genuine failed submissions keep the existing circuit contract.
Historical case classifications and verified message IDs are consolidated in
`gameplay-diagnostics-findings.md`; the sanitized parked-session audit is committed.

Operational adapter follow-up: the helper's exact production restrictions (no network,
read-only root, dropped capabilities with DAC_OVERRIDE/CHOWN/FOWNER for the read-only
Hermes volume and output ownership handoff) passed against the parked volume. These
ownership capabilities are required to publish files readable by the control/portal
UID. Compact `SMACX_TRACE` request/result lines now reach normal process stderr; giant
records stay in the compressed streams. Terminal control characters are removed and
human lines are capped at 1,400 characters with an explicit details marker.

Native acceptance extension prepared: the existing double-gated managed-action fixture
can leave one owned ready unit and disable native pause-at-end-turn in an isolated
single-player test. The acceptance driver will prove that a current-turn goal blocks
that unit's Skip without changing native readiness, then explicitly defer and verify
an actual automatic native turn transition. It also checks named hurry receipts and
blind research labels against native state. This extension is not yet a passing
native acceptance result; it must run before deployment acceptance is claimed.

## Live prompt cost and damaged-stream checkpoint

The current operational prompt measures 1,324 exact Qwen3.8-27B tokens. Compiled
match doctrine cases range from 8,407 to 8,714 exact tokens. A controlled pair of
real provider requests each used 8,548 prompt tokens; the second reused 6,592
tokens according to an isolated cache-counter delta. This validates prompt cost
and prefix reuse, not gameplay cognition receipt. Sanitized evidence is retained
in `benchmarks/gameplay-diagnostics-prompt-cost.json`.

Campaign export now contains malformed compressed members without losing the
whole download: a regression injects an invalid deflate block, verifies the raw
file remains in the ZIP and checks the explicit compressed-stream gap. Scope and
readability contracts still pass. Native integration is running separately; its
result and deployed browser acceptance remain pending.

## Packaged-service acceptance correction

The first isolated native run failed before gameplay: the MCP sidecar could not
import `smacx_diagnostics`. The control Dockerfile used an explicit copy list
that omitted the four new diagnostics/intent modules. Source-mounted contracts
did not cover this packaging boundary. The list now includes those modules and
the build imports the actual packaged MCP, campaign-export and intent modules.
The repaired image builds successfully; the export contract also passes against
installed image modules with only the test scripts mounted. Full native rerun
remains required. No game deployment or full-game acceptance is claimed.

Live MCP inspection after packaging repair confirms 11 managed calls and 11
results, 21 correlated native receipts, two selected choices and runtime/journal
selection records. It also exposed a production entry-point wiring gap: `record`
used the writer's uncompressed/nonhuman defaults. The entry point now explicitly
enables compression and compact process summaries, with a regression that invokes
the actual environment-driven entry point and checks both outputs. The running
isolated native test predates this logging-only correction; deployment must use
the corrected image. Full native integration is still in progress.

## Real Hermes wire and native action checkpoint (partial native run)

The actual derived Hermes process, across low/medium/high gameplay and communication
profiles, produces diagnostic request bodies equal to the controlled HTTP provider's
received JSON. Captured responses share request/context correlation. This verifies
the installed hook, rather than calling its HTTPX adapter directly.

Native integration verifies production loss/deployment previews, queue edits, citizen
and unit effects, founding, diplomatic energy and technology terms before stopping
at council settling. Sanitized individual verified capabilities are preserved in
`benchmarks/gameplay-diagnostics-native-partial.json`; it is explicitly incomplete.
The last snapshot reports native `wait` while the ballot is pending. The fixture
now waits instead of enumerating an interaction during that phase. Separately,
read-only choice enumeration retries one coherent refresh on revision races; staged
preparations never replay. Persistent races retain their error and explicit fresh-frame
guidance. Adapter regression proves bounded retry, no preparation replay and unchanged
invalid-reference rejection. Opaque execution and 15-tool schema contracts pass.
Automatic-turn reconciliation and post-intent native recovery remain pending.

Corrected production MCP capture is now observed live: six compressed actor streams
contain 16 managed calls/results, 45 native receipts and three runtime-context
publications; named summaries reach container stderr. Human receipts retain explicit
execution status and decision consumption, so queued execution remains distinguishable
from verified completion. The summary regression passes. A separate isolated cold
start failed before bridge readiness; an identical fresh launch succeeded. Its root
cause remains unresolved and must not be presented as fixed by the enumeration repair.

Provider audit completeness follow-up: metrics now identify requests without any
captured response or transport-failure terminal event. Header receipt alone does
not close the request. The bounded ID list explicitly permits in-flight, interrupted
or missing capture interpretations; it does not infer provider failure. Summary
and campaign-export regressions pass. This does not certify full stream capture.

## Native managed action and automatic-turn acceptance

The corrected native run passes council result verification, citizen/governor
management, production queue/retool/hurry effects, transport/founding and the
remaining managed action fixtures. Named Explore flags match native blind research.
Production timing matches six controlled native upkeeps; this is not a full campaign
simulation. Milestone/production/interruption attention reaches runtime across seven
acknowledged batches (trusted response hook simulated, no provider inference claim).

The new intent boundary passes end-to-end: typed goal write → next runtime → final
ready-unit Skip rejected without consumption or native state change → same-record
explicit deferral → accepted Skip → actual automatic native turn 1→2. This proves
the automatic transition guard against the running game. The bounded 512-base site
fixtures complete in 233/250 ms, with maximum observed probe gaps about 227 ms.
Sanitized evidence is in `benchmarks/gameplay-diagnostics-native-checkpoints.json`.
Post-intent native recovery and the full test's terminal acceptance remain pending,
as do deployment, browser export and autonomous full-game acceptance.

Native driver follow-up: automatic turn advancement succeeded, but its next fixture
ran before the next turn's passive project popup settled. The boundary driver now
requires the controlled first-turn state, observes the required handoff, ends that
episode, acquires the next episode, then finishes passive presentation before
returning an actionable turn. A repeat on already-advanced arbitrary console state
did not auto-end and is not acceptance evidence. The complete fresh native driver
must pass; its prior partial result is retained rather than relabeled successful.

## Integrated native driver passed

The fresh complete driver passes, including actual automatic turn advancement,
explicit episode handoff before next-turn presentation, actionable next turn,
post-intent checkpoint/park/recovery, native unit identity continuity, journaled
plans/conflicts/assignments, and rejection of old-timeline watches and old-session
choices. Sanitized checkpoint evidence now records the full terminal pass. Optional
autonomous Hermes/specialist stages were not enabled; separate actual-Hermes wire
capture is proved against a controlled provider, not autonomous gameplay.

Before deployment, a fresh read-only reference audit found that content-enabled
search bypasses the hybrid/title ranking used by metadata search. The same Alien
Artifacts query returns relevant articles in metadata mode and unrelated results
in content mode, despite the installed help containing the mechanic. This confirmed
retrieval-path gap is being repaired; it is not retroactive proof that the old
sovereign attempted research (the retained run contains no such invocation).

## Reference retrieval correctness checkpoint

The installed corpus contains the Artifact mechanic. Live metadata search returns
Alien Artifact/Artifacts/Network Node, whereas content-enabled search for the same
short query returns unrelated research/configuration entries and no artifact
mechanic evidence. Code confirms that IncludeContent returned early through a
different search path before hybrid/title ranking. The repair uses one ranked
document list for both modes, preserves matched body chunks with document fallback,
reserves bounded evidence shares across results and applies requested topic scope
to title fallback as well. Knowledge service tests pass 8/8, including ranking
parity, scope isolation and token-budget/truncation regression. Sanitized before
evidence is retained; installed-image/live retrieval verification remains pending.
This does not prove the old sovereign tried a lookup: its retained calls show none.

### Deployment and reference acceptance (1416e24)

All five current images were built and tagged; control, portal, knowledge and specialist services were recreated and healthy. Existing databases and parked turn-26 checkpoint were preserved. The live content lookup now ranks Alien Artifact first and includes the Network Node/technology mechanic within 447 approximate tokens. See the updated reference benchmark.

The deployed admin lobby route generated a 1,133,538-byte archive; ZIP integrity passes and it contains committed journal, retained Hermes history, state, human transcript, metrics and manifest. Historical missing streams and the telemetry row limit are explicitly declared; completeness remains false. Desktop and 390px phone layout were visually inspected without horizontal overflow. Browser automation reported ERR_BLOCKED_BY_CLIENT on delivery (and its downloads settings page is unavailable); actual browser file delivery remains an open acceptance item, not a passed test.

### Real retained-history recovery permission repair

The first deployed turn-26 recovery failed closed before starting a native worker: the Hermes helper (uid 10000, shared gid 10001) could not read the sealed 0600 checkpoint owned by control uid 10001. The earlier native driver had no retained Hermes history at this stage, so it did not prove this permission boundary. The restore path now grants only group read during the helper lifetime and reseals to 0600 in `finally`; its control mount remains read-only, network disabled, capabilities dropped. No checkpoint bytes or authority are changed.

The actual checkpoint/restore scripts now run under the production distinct UID/GID and umask in `checkpoint_helper_permissions_live_test.py`: SQLite memory restores, the archive is resealed after success, and an injected helper failure also reseals it. `ai_memory_checkpoint_test.py` passes target-only history rollback, unrelated-campaign preservation, journal timeline/Graphiti rotation and native save binding. Deployed retry remains the next gate; the campaign is still parked.

### Resumed-provider system-prompt repair

The restricted-archive repair allowed the actual turn-26 native/Hermes checkpoint to restore, including 54 native identity slots and retained conversation. Explicit doctrine recompilation changed the stored profile hash, but the newly captured provider request still contained the old system hash: Hermes resumed its persisted system row without invoking the overridden builder. The sovereign was stopped and the campaign parked again. This was a confirmed delivery defect, not evidence of model disregard for the new instructions.

The managed sanitizer now constructs one canonical hash-validated system row on every wire copy, for sovereign and specialist modes. It does not mutate historical messages. A real derived-Hermes two-invocation test retains its SQLite session, changes SYSTEM.md and the approved hash, resumes with --continue, and verifies the receiving provider gets exactly the new prompt while prior conversation remains. All four provider profiles pass their original request/tool/generation/runtime/capture checks; the strict prompt regression rejects stale duplicate system rows and preserves history. The cognition delivery/GC chain also passes (385,100 to 11,107 fixture tokens). Deployed corrected-wire acceptance remains next.

### Repeated recovery snapshot retention repair

A second recovery of the same deployed checkpoint failed with `world_snapshot_missing`: checkpoint creation had not assigned a snapshot pin, so `discard_future` collected its derived accelerator after the first restore. The native save and journal remained, but the advertised snapshot file and row were gone. Integrity checks were retained; no missing content was fabricated. AI - 3 remains parked for evidence preservation.

Checkpoint publication now atomically registers checkpoint ownership with snapshot creation. Restore also adopts still-present legacy unpinned snapshots after integrity validation and before timeline GC. Only after a replacement checkpoint is published does checkpoint retention release older checkpoint pins; any other owner still prevents collection. The new retention contract passes two timeline restorations/GC sweeps and ownership release behavior; the Huge specialist snapshot fixture still peaks at one 5,615,765-byte snapshot and ends at zero. The controlled native driver now repeats recovery from the same checkpoint before checking identities, plans, conflicts and invalidated handles. That native rerun is the next gate.

The complete extended native driver passed at a847852: the same checkpoint recovered twice after timeline GC, native unit identities and journaled plans/conflicts/assignments survived, and old handles remained invalid. Native automatic turn 1→2 reconciliation/handoff and all earlier managed mechanics also passed. Stress samples were 273.714ms/300.05ms maximum probe gap (512-base site collection) and 267.653ms/265.575ms (counterfactual sites). Updated sanitized native checkpoint evidence records the exact driver and image versions. Optional autonomous provider/specialist stages were not enabled in that driver.

### Earlier Hermes validation and control lifecycle error capture

The short deployed resume emitted an invalid direct tool name before Hermes entered `_execute_tool_calls`. Its response/history was captured, but the named tool-failure stream and metrics missed that earlier validation. The pinned Hermes validation branch now records rejected call IDs, parsed arguments, available names, and explicit no-execution status before any retry/partial-return branch. Normal valid calls retain the existing executor trace; invalid calls are not double counted as executed requests.

A real receiving-provider regression emits an unknown tool through SSE, verifies the pre-executor rejection and exact request/call correlation, then verifies normal continuation and prompt replacement on actual session resume. This also exposed that HTTP transport and conversation validation can use different threads. Correlation now joins the emitted provider call ID through a bounded 512-entry process-local registry; response events retain their own immutable request context rather than reading whichever thread-local context happens to be current. The four-profile provider test passes.

Authenticated match lifecycle failures now enter the campaign stream even when no native/MCP actor can start. The HTTP regression proves anonymous calls create no stream, the original 409 remains unchanged, and raw request/exception details are omitted while the bounded failure category reaches metrics. No credentials, request body or query strings are recorded.

### AI - 4 opening and worker-loss evidence checkpoint

AI - 4 started with the requested Peacekeeper/Librarian/Standard roster and spectators. The first captured provider wire contained exactly the approved system hash `14991938f0e39728b81697c762b0bb499fbd0319d5073689cb9f6193766e7bdc`. This proves fresh deployed delivery, not the separate retained-history resume gate. A live archive completed in 2.242 seconds (855,386 bytes), passed ZIP integrity, and explicitly reported one in-flight provider request among 14 requests, with 13 finished streams. Browser file delivery remains unverified.

After planetfall the sole ready Scout received native Auto Explore, with a verified persistent-order receipt and a wait-phase snapshot. Approximately 66 seconds later supervision replaced the unavailable worker and successfully restored the bridge-verified turn-1 checkpoint with real Hermes history. The campaign was then parked for diagnosis. The original process logs and Docker health probes were not retained before replacement, so the cause is undetermined; a crash or a native UI stall cannot yet be distinguished. No full-game acceptance is claimed.

Supervision now emits bounded, sanitized worker state, the last six health probes and a managed-container log tail before recovery/quarantine. This operational evidence stays in administrator diagnostics rather than cognition or public incident details. The incident regression verifies evidence exists before the recovery callback and remains available when recovery fails, including redaction. Normal authenticated `checkpoint_waiting_for_quiescence` 409 responses are now classified as deferred operations, not failure observations; the HTTP regression verifies both categories without changing their status codes. The native last-unit Auto Explore continuation remains under investigation.

The controlled AI - 4 checkpoint replay did not reproduce worker loss: assigning Auto Explore to the sole Scout advanced turn 1→2 into GOODYCOMM, with all 15 semantic probes responding in at most 3ms and Docker healthy/no OOM. The supervisor/model were paused during this diagnostic window, then the verified checkpoint was restored again before model continuation. `gameplay-auto-explore-replay.json` records this limited negative reproduction; it does not resolve the original failure.

### Hermes MCP watcher lifetime repair

The live warning `MCPServerTask._watch_stdio_children was never awaited` came from the pinned Hermes MCP handler creating a coroutine to test awaitability, then creating another for the RPC race. It did not duplicate the RPC. The derived-image patch now creates at most one watcher, only for an asynchronous RPC, and schedules that same object. Existing RPC cancellation/reconnect behavior remains intact. A regression extracts the actual installed race block: the old image fails with two watcher creations; the repaired image passes RPC success, child exit/reconnect/cancellation and synchronous stubs without orphan tasks or unawaited warnings. The full real receiving-provider/Hermes regression also passes its four profiles, request/response correlation, unknown-name rejection and changed-system resume.

AI - 4 has autonomously advanced to turn 3 after checkpoint restoration with no new worker loss observed. Resumed provider requests contain the approved system hash and preserved turn-handoff conversation. This is ongoing acceptance, not a completed campaign.

The b0826b1 harness is now deployed (container/image digest verified), after a fresh native+AI turn-3 checkpoint. Autonomous play reached turn 4. The actual Hermes specialist regression also passes 13 captured provider calls across iterative world/reference queries, citation derivation, fresh-process isolation and compressed traces. Critical-attention reconciliation rejected a closing action without consuming it, then accepted the sovereign's acknowledgement. There is still no spontaneous specialist-use or full-game acceptance claim. Sanitized opening/deployment evidence is in `gameplay-diagnostics-live-opening.json`.

### Reject silently discarded tool arguments

The turn-4 trace showed `smac_decision(focus_id=...)` succeeding even though its schema accepts `own_unit_ref`, not `focus_id`. The pinned MCP SDK's argument model silently discarded unknown fields before the managed trace/function saw them. Although this observation selected the default unit and did not prove an incorrect mutation, silently substituting defaults makes intent and execution ambiguous.

The shared managed/specialist MCP boundary now advertises `additionalProperties:false` and rejects undeclared top-level parameters before dispatch, returning `unknown_tool_arguments`, allowed names and explicit no-execution status. Declared dictionaries retain their nested data; valid parameters/defaults are unchanged. Rejections enter managed diagnostics or the specialist attempt trace without becoming evidence/citations. The packaged main server and actual SDK contract pass, including raw rejected argument capture. Managed staged-action and schema-budget contracts pass (15 managed tools; smac_world conservative proxy 628, below target 700). The real Hermes specialist regression now emits one invalid call per mission, verifies its trace, then completes valid world/reference work: 16 provider calls, unchanged valid evidence query counts 3/3/4.

Autonomous play was stopped, a verified turn-5 native+AI checkpoint was published, and the campaign parked for deployment. This is another reviewable repair checkpoint, not full-game acceptance.

The a629b22 control/MCP and harness image digests were verified in their running containers; the turn-5 checkpoint restored and autonomous play advanced to turn 6. Across resumed episodes the sovereign still guessed missing parameter names before retrieving schemas. The operating contract now explicitly directs parameterized SMACX calls to retrieve a missing schema with `tool_describe`. This adds 25 exact Qwen tokens (1,324→1,349 for the same fixture operating contract, excluding dynamic doctrine), without changing gameplay authority or policy. Doctrine content and strict prompt/hash/history-preservation contracts pass. Recompilation and deployed wire verification are the next gate.

### Suppress inactive/countdown sunspot alarm churn

Live turn-6 attention marked otherwise stable ecology critical (priority 95) solely because `sunspot_duration` changed to -6. Native `gameturn.cpp` decrements this counter below zero while inactive; the collector treated every ecology delta as urgent. This is confirmed harness attention churn, not model inattention.

The classifier now suppresses an ecology interrupt only when both counters are known integers, their active/inactive status is unchanged, and every other ecology field is equal. Raw counters and all state still enter journal/projection. Starts, ends, sea level, council pressure, perihelion, volcano and unknown future-field changes retain critical treatment; missing prior/counter data remains conservative. An 11-case native-shaped collector→journal→projection→attention regression verifies these boundaries and unchanged provider-visible state. Batched observation crash/recovery tests pass. Autonomous play reached turn 7, then stopped at a fresh verified native+AI checkpoint for deployment. Live post-repair attention behavior is the next gate.

Deployed verification at turn 8: native and current projected ecology both retain `sunspot_duration=-8`; six new attention rows since the resumed provider request contain zero ecology rows and two other critical rows. Thus the live inactive-counter transition is retained without an alarm. Control and MCP container digests match the repaired image. The recompiled system hash `0f22b958ddba7b127443fd55b82e49b148ffefe68a7d37cbbf901d870355b6c3` matches the actual resumed provider request, and the sovereign subsequently retrieved missing schemas before constructing acknowledgement/execution calls. `gameplay-ecology-attention.json` preserves the bounded evidence. Autonomous full-game monitoring continues.

### Publication-cut diagnostic checkpoint

AI - 4 contact attention sometimes changed references around adjacent observations. The existing temporal contracts require conservative identity at an unproven snapshot/feed cut; no identity stitching or attention suppression was introduced. Completed publication receipts now record stable-cut status, identity reset, cursor/revision, counts and publication hash after the commit barrier. Failed/uncommitted publications produce no such receipt. Private native handles are excluded. Batched crash/recovery, cross-publication episode, publication visibility and transaction crash-matrix regressions pass. Deployed cut attribution remains pending.

### Truncated-response lease and cognition guard checkpoint

At turn 12 provider request `758b832939f347b18be2c3fc59ccbfcb` ended with `length`. Hermes appended a continuation, but the managed hook released leases only for `stop`/`end_turn`; the next context request correctly failed `sovereign_invocation_already_active`. No native failure was observed. The hook now cancels no-tool length/incomplete/filtered response leases, preserving completed versus cancelled semantics. Episode identity uses user-boundary ordinal rather than tool-row position, so wire-only GC cannot create a new identity. Real installed Hermes passes a receiving-provider truncation/continuation test against an exclusive-lease context endpoint, alongside its existing prompt/resume/tool-boundary profiles. Strict prompt regression covers each disposition and GC identity stability.

The same trace showed the sovereign explicitly declining a typed plan write because required `session_id` was absent. Runtime identity now includes the observed native session ID alongside its action revision. Runtime 64K/256K/Huge contracts pass with identical truth and bounded envelopes. No scope or revision guard was weakened. Failure diagnostics add episode correlation, exception type and bounded code locations (no stack locals), so earlier `invalid_tile_id` failures can be attributed on recurrence. `gameplay-episode-lifecycle.json` records the evidence and remaining live gates.

### Nested MCP failure summary checkpoint

Live Hermes wraps MCP failures as an outer `error` containing serialized negative result JSON. Human summaries previously printed escaped JSON and metrics grouped by the whole message. The summary decoder now unwraps only an explicitly negative nested result, retaining its code and no-execution receipt. Plain error text and nested successes cannot erase an outer failure. Summary and scoped campaign-export contracts pass. This changes diagnostics only; the raw capture and provider result are untouched. Deployment is deferred to the next runtime maintenance checkpoint.

Deployed lifecycle checkpoint: a verified native+AI turn-12 checkpoint restored successfully; control/MCP and Hermes digests match the repaired images. Actual provider request `952839d5bb014de4a058c97fd7356af0` contains the current native session and revision and the unchanged approved system hash. The committed publication receipt is present. Native play advanced 12→13 through End turn and 13→14 through automatic movement. Typed cognition remains empty so far; delivery of a successful autonomous write is not yet claimed. Deployment evidence is appended to `gameplay-episode-lifecycle.json`.

### Governor permission semantics checkpoint

Turn-12 wire delivered `governor.active=false` and `governor.permissions.hurry_production=false`; the sovereign inferred it could not hurry. At turn 14, a read-only native production catalog with the same false governor permission and population 1 offered Colony Pod, Formers and an affordable hurry (8 minerals, 19 credits, 63 available). This proves the flag does not disable direct player hurry; it does not reconstruct turn-12 costs. Native base and management governor records now explicitly label permission scope as governor automation and direct player legality as obtained from the relevant choice family. Existing booleans and action guards are unchanged. Native cross-build passes; deployed native/projection/query delivery is the next gate. See `gameplay-governor-permission-scope.json`. A fresh verified turn-14 native+AI checkpoint was captured before parking for deployment.

### Explicit recovery deployment option checkpoint

The first governor deployment verification failed: ordinary checkpoint recovery intentionally retained the seat's pinned prepared worker image. The manager already supported `refresh_runtime`, but the authenticated recover endpoint did not forward it. The endpoint now accepts literal JSON `refresh_runtime: true` for an operator-requested upgrade through that existing verified recovery path. Default/false/string values preserve ordinary pinned-image recovery. Authenticated HTTP tests cover these cases and unauthorized access; incident recovery tests still pass failure latching, checkpoint-before-acknowledgement and image selection. No checkpoint or native identity verification is bypassed. Live upgraded-DLL verification remains pending.

Explicit image-refresh acceptance now passes: the endpoint returned `changed=true` from the old pinned prepared image to the newly built worker. Running DLL SHA matches the new image; native list_bases and base_management both return the scope annotation. The restored current world projection contains it as owned-state evidence at turn 14 / observation 60. Native production still offers an affordable 19-credit hurry and Colony Pod despite the false governor hurry permission. Sovereign restarted and portal is unpaused. This establishes native→projection delivery; autonomous query/interpretation remains open.

### Pre-action automatic turn-boundary affordance checkpoint

At turns 13 and 14 the sovereign delegated its final ready scout to Auto Explore intending to free attention for base development; native processing advanced the turn before that management. The existing reconciliation predicate already guards these possible closures, but the offered choices did not expose the risk. Opaque choices now carry `may_close_turn` from that same predicate, and affected frames provide one bounded sequencing notice. This is a conservative possibility, not a prediction or new blocker. Management remains available; no intention is inferred from prose. Opaque choice and managed intent guard contracts pass, including one/two ready units, untouched private command binding, and continued management access. Human diagnostic summaries retain the flag/notice. Live provider receipt remains pending; evidence is in `gameplay-turn-boundary-affordance.json`.

### Direct bounded schema delivery checkpoint

Repeated resumed-episode parameter guessing persisted after schema-retrieval education. Hermes deferred the MCP schemas behind tool_search/tool_describe/tool_call; completed-episode protocol compaction removed earlier retrieved schemas. The sovereign profile now uses the same direct schema mode as specialists, with an explicit 15-tool gameplay allowlist and existing 9-tool communication allowlist. Default resource/prompt helpers discovered in the first wire test are explicitly excluded. The operating prompt directs use of declared schemas and no longer names an absent discovery tool. No gameplay tool or authority was added.

Real Hermes/provider tests pass initial and resumed schema equality, exactly15 gameplay/9 communication tools, a direct MCP invocation returning its negative guard receipt, unknown-name rejection, truncated-response lease continuation and existing generation/prompt boundaries. Semantic GC handles both direct namespaces and retained historical wrappers without mutating durable messages; all three500-call cognition/context cases pass, including million-token fixtures. Profile/manager, strict prompt and doctrine contracts pass. Serialized MCP schemas measure4479 exactQwen tokens (7012 conservativeproxy within8192reserve); the same operating-contract budget fixture decreases1351→1338 exacttokens. These are serialized-schema measures, not a claim about provider chat-template overhead. Live deployment/use remains the next gate; see `gameplay-direct-schema-delivery.json`.
