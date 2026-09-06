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

Real Hermes/provider tests pass initial and resumed schema equality, exactly 15 gameplay/9 communication tools, a direct MCP invocation returning its negative guard receipt, unknown-name rejection, truncated-response lease continuation and existing generation/prompt boundaries. Semantic GC handles both direct namespaces and retained historical wrappers without mutating durable messages; all three 500-call cognition/context cases pass, including million-token fixtures. Profile/manager, strict prompt and doctrine contracts pass. Serialized MCP schemas measure 4,479 exact Qwen tokens (7,012 conservative proxy within8,192 reserve); the same operating-contract budget fixture decreases 1,351→1,338 exact tokens. These are serialized-schema measures, not a claim about provider chat-template overhead. Live deployment/use remains the next gate; see `gameplay-direct-schema-delivery.json`.

Native compatibility registration checkpoint: recompilation correctly rejected the newly built governor annotation DLL until its source digest was reviewed. The complete native diff from the prior registered build is four additive output lines in base/governor records; action legality, mechanics, native UI-thread dispatch and rules are unchanged. Cross-build and running-DLL response verification precede explicit registration of source digest `9ce50239765aeba286c672ecfe6d44858a5bb922eb17b3dd3c05ae942dfbbd04`. Engine-manifest and doctrine integration contracts pass, including rejection of unknown builds and unsupported overrides. The fail-closed build check remains intact.

Live direct-schema deployment checkpoint: AI - 4 restored verified native+AI turn 16, started new run `run-b7129c212ee3449587e045edc99a7eff`, and the portal is unpaused. Actual provider requests contain exactly 15 direct gameplay schemas, the approved recompiled system hash `78456966b3c07d5aec25e18f30f8d15f2d7afa6dabe74f8ad452a3f79310d4f4`, and the current native session. The End turn closure notice reaches the next actual provider request. The sovereign then acknowledges attention and requests the production catalog before closure. This proves delivered interface and observed query use, not a completed strategy or autonomous cognition persistence. Coverage and both sanitized delivery artifacts are updated; continuous live acceptance remains active.

### Consistent handoff next-step checkpoint

Live turn 16→17 execution delivered `turn_handoff_required` but generic consumed-decision handling added a contradictory `required_next.tool=smac_decision`. The actual immediate provider request retained both; the sovereign continued querying turn 17 instead of yielding. This is a confirmed response contradiction, not proof that the model never received the handoff. Native handoff attachment now sets the same stop/message next step as implicit boundaries, before generic lifecycle guidance. Public opaque-execution and automatic-boundary regressions pass, as does current-turn intent reconciliation. A fresh verified turn-17 native+AI checkpoint precedes deployment. Live corrected-response/compliance verification remains open.

### Stable failure taxonomy checkpoint

The live turn-17 archive passes integrity at 10,779,333 bytes in 1.974 seconds, with 160 provider requests, 159 terminal stream records and one explicitly unmatched request. It reports telemetry row limits, absent specialist traces and ongoing acceptance. Its metrics exposed known runtime error strings classified as unspecified and pinned Hermes schema rejections grouped by whole message. Diagnostic normalization now retains known codes, groups the exact pre-invocation schema/bridge-dispatch forms, and leaves other free text explicitly unclassified. Raw records are unchanged. Summary and scoped export regressions pass. Updated deployed aggregate verification waits for the next maintenance checkpoint.

Live handoff acceptance: the deployed explicit 18→19 receipt contains only the stop/message next step, reaches actual provider request `75bd4fedda37403c9e661157483f799b`, and sovereign emits a TURN HANDOFF before supervised episode sequence 4. The implicit 17→18 path also yielded a handoff. This establishes observed compliance for those two boundaries, not all future turns.

### Nonexhaustive decision-family scope checkpoint

At turn 18 the sovereign interpreted an End-turn-only frame as no meaningful management and claimed Formers/Colony Pods unavailable at population 1, although the turn-16 production catalog offered both. Nonblocking decision frames now explicitly identify the selected family, say they do not enumerate all management actions, and point to existing production/citizen/research queries with separately checked native legality. Blocking interactions get no such hint. Receipt precedence also replaces unconditional fresh-frame wording in the offered frame. Decision-frame regression covers ready/no-ready cases, unchanged native call count and opaque action count, and blocking interaction exclusion. No automatic strategy or prose-derived intent was added. Live scope delivery/interpretation remains pending.

### Diplomatic human-trace terms checkpoint

Live first contact at turn 19 offered a 15-credit Spartan commlink through the Data Angels. The provider received the native terms and the sovereign accepted; its following context reported the commlink and 65→50 credits. The human trace displayed two identical “Respond to diplomatic offer” labels, losing the accept/reject distinction. Summaries now retain response, meaning, price/affordability and target location, plus bounded scalar information terms. Structured source records and provider content are unchanged. Summary regression distinguishes both responses, preserves price/counterpart terms, bounds a long field, and scoped archive tests pass. Deployment is queued with the pending frame-scope package.

### Named reference retrieval checkpoint

The sovereign spontaneously called direct_reference during turn-19 diplomacy to compare Centauri Ecology and Planetary Networks. The returned evidence omitted Planetary Networks, and the sovereign rejected the trade citing that uncertainty. An exact-name control lookup finds its corpus document immediately. Search ranking stemmed query tokens but compared unstemmed title words, and did not prioritize complete names embedded in a natural-language question. Titles now use the same lexical normalization, and complete normalized title phrases rank ahead of partial-term/hybrid matches within the requested collection scope. Knowledge-service tests pass 9/9, including both named technologies in longer questions despite poor semantic rank, scope isolation and 256-token evidence bounds. No corpus content or game mechanics changed. Deployed original-query comparison remains pending.

Deployed reference replay: the unchanged original two-technology question now returns Centauri Ecology and Planetary Networks first, both with body evidence, at 623 approximate content tokens. Running knowledge image digest is recorded in its benchmark. AI - 4 restored verified native+AI turn 20 and resumed with the updated control/MCP and harness images. The actual turn frame displays nonexhaustive management scope. Human trace follow-up now also preserves bounded nested player-gives/player-receives technology names; the regression excludes unrelated large fields. That nested-only diagnostic refinement is committed for the next maintenance image.

### Managed memory provider-filter checkpoint

The sovereign's turn-20 working_set read exposed internal choice selectors from its own historical action records. That early-return path bypassed the provider filter used by other memory views; the older filter also omitted only native-prefixed fields rather than all private entity selectors. Working-set responses now apply the same recursive read filter, preserving saved public refs while omitting raw choice identifiers and native selector fields. Managed search filters records before scoring, so a private-only term cannot affect result membership. Canonical journal content remains unchanged and internal search retains diagnostic detail. Mature-campaign memory regression covers 120 records per typed projection, all four historical read routes, private-only search/recall exclusion and raw-record preservation. Journal, fair-play and AI-memory checkpoint regressions pass. A fresh verified turn-21 native+AI checkpoint precedes deployment.

The memory filter also preserves known public historical outcome fields as `execution_receipt` (command, completion/queue disposition, action ID, turn/year and status/resolution), while omitting unrecognized internal result fields. The mature memory test verifies this receipt alongside private-marker exclusion and unchanged raw journal data.

Deployed memory read acceptance: the actual MCP tool, called by the operator before sovereign restart, returns the restored active timeline with 12 recent actions, 12 public execution receipts and zero private selector paths. Control and MCP digests match the repaired image. This is a deployed interface check, not an autonomous memory write or read claim. The game resumes at turn 21 and portal is unpaused.

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


### Recovery observation-order repair checkpoint

The controlled turn-22 restore published temporary native handles before identity import: attention cursors110/111 reported the three existing Scouts removed under the original refs, then removed under temporary refs. Native still had three owned units; no sovereign was running. `start_worker` started MCP immediately, while `recover_match` imported the identity capsule only afterward. Recovery now defers collector startup for both solo and LAN, restores all native identity capsules, starts collectors, and only then marks the match running. Identity failure leaves collectors absent and readiness withheld.

Actual recovery-orchestration tests with controlled adapters pass solo/LAN success and identity-failure cases. Existing incident recovery and complete AI-memory checkpoint regressions pass. Deployment must additionally prove that the first observations retain checkpoint identities without false removal/reintroduction events. The native diagnostic image is deployed and healthy at22; its turn25 replay remains pending while this newly found recovery defect is repaired. See `benchmarks/gameplay-recovery-observation-order.json`.

Deployed solo recovery acceptance: session `session-57943c3baafd4fc39e8d8d0138372a3b` restores turn22 into `timeline-restore-e6a48dffed4d4a919f5bbf4c`. First publication cursor110 has complete native-feed continuity, zero material deltas, zero semantic events and zero critical groups; pending attention is empty and native owned-unit count remains3. This verifies removal of the transient identity publications for the real solo checkpoint. Live LAN acceptance remains separate.


### Turn-25 native popup lifetime repair checkpoint

Guarded operator replay reached the exact original Social Psych revision `6843548069520344689`. Acknowledgement returned, then the following snapshot reproduced access violation0xC0000005 at stage13. Attached native debugging identifies recursive `Win_is_visible` during `semantic_revision`/snapshot serialization. No modal or BasePop exec remains active, but historical/default popup pointers were still inspected for visibility. Those pointers can reference reused stack storage after the notice closes. This explains the escaped callback and latched request guard; no serialization reset is used as a workaround.

Popup object access now requires membership in the native current modal or active BasePop execution slot before dereferencing saved objects. Copied historical labels/substitutions remain available for passive technology evidence. Pending-transition/default-popup probes use the same lifetime check. Timeout diagnostics also retain the exception instruction. Windows protected-memory tests verify expired/inaccessible history is untouched, unrelated modals cannot reactivate it, and live modal/exec membership remains usable. Real exception dispatch, cross-build and doctrine adapter checks pass. Matched post-fix replay and active-popup regression remain pending; the separate transient invalid_tile_id error is still unresolved. See `benchmarks/gameplay-popup-lifetime-repair.json`.

Post-fix native replay acceptance: the same verified turn22 save and actions reach Social Psych25, acknowledge it, and return to normal turn control. Five following snapshots succeed (24.73ms first; 2.01–2.29ms thereafter). Owned technologies still include Social Psych, and the next blind research target remains hidden with native progress reset0/cost50. The semantic revision changes under the corrected popup-lifetime qualification; comparison is the same save/actions/award, not an unchanged hash claim. No request-guard reset or exception consumption was used. Active-popup/managed-action live regression is still running; separate invalid_tile_id origin and autonomous full-game acceptance remain open.

The isolated full control/worker/MCP live regression passed on the popup-lifetime images: 15 managed tools, active gift/purchase/loan/Council dialogs, native production and counterfactual effects, typed intent reconciliation, repeated checkpoint recovery after timeline GC, native identity preservation and verified backups. Runtime attention delivery in this suite uses a simulated trusted response hook; it does not prove provider inference or autonomous completion. The acceptance match has been cleanly restored to22 for autonomous continuation.

### World-changes budget recovery checkpoint

The resumed sovereign requested changes since108 at standard detail; the first full historical base snapshot exceeded the 2,048-token ceiling. Structured call IDs show one failed query, then attention acknowledgement and Auto Explore to24, not a repeated-call loop. Operator replay at deep detail returns the first row in7,867 estimated tokens with cursor-1. The error now supplies an exact smac_world deep retry preserving since_cursor and continuation; it does not skip same-observation siblings, silently omit evidence or perform an automatic query. Regression verifies the supplied retry, stale epistemics, unmodified source records and console visibility; the full world-model contract passes. Deployment/provider response acceptance remains pending. See `benchmarks/gameplay-world-changes-retry.json`.

Autonomous post-fix acceptance reached26: the sovereign recovered from guarded unknown_decision/invalid_choice rejections, acknowledged Social Psych with a fresh frame, and returned to normal turn control. Two native probes succeed in2.93/2.54ms. Portal lifecycle checkpoint reconciliation also corrected the operator-resume mismatch (portal remained parked while control was running); the browser now renders Lal’s watchable seat and Selkies frame. The portal started one new run, `run-33f771fe6a5c49fdbba2fb71d28c80d6`. Full-game and remaining behavioral gates remain open. The world-query retry passes in the packaged image; current restored changes fit598 tokens, so autonomous use of the new retry remains unobserved.

### Contact feed/snapshot alignment checkpoint

Actual publications129/130 at27 remove/reintroduce the same Fungal Tower at location-1576 under a temporary contact ref and the canonical episode ref, generating repeated critical attention. A native-shaped collector regression reproduces this with an unrelated turn event arriving during collection; no visibility loss occurs. The collector now durably catches up late feed events within its existing three-attempt window. It reuses a coherent snapshot only after an empty complete probe matches both revision and exact sequence, refreshing private contact evidence from the durable stage; otherwise it recollects. Exhausted unaligned cuts retain the conservative fallback. The regression preserves a stationary contact, separates genuine loss/reappearance and remains bounded under continuous activity. Existing cross-publication,22 publication crash cases, three batch crash windows and fair-play differential pass. Verified checkpoint28 is preserved; live repaired recurrence/performance acceptance remains pending. See `benchmarks/gameplay-contact-feed-alignment.json`.

Feed alignment is deployed: autonomous28→29 retains the canonical Tower with zero contact changes or collector failures. No actual alignment retry occurred in that quiet window, so recurrence acceptance is still pending. Isolated LAN startup fails at TOPMENU stage5; source inspection identifies the SetupWin source-popup ownership relationship omitted by the prior lifetime guard. The game is paused again for that native repair and an observed memory-guard discoverability gap.

### Native SetupWin ownership correction

The isolated LAN recovery test failed before gameplay, at native TOPMENU stage5. The lifetime predicate omitted an existing native relationship: active modal SetupWin owns its source BasePop at+0x1014, while that BasePop is neither the modal object nor the exec slot. The predicate now recognizes that source only under the reviewed SetupWin vtable and positive modal state. Protected-memory tests preserve rejection of expired/unrelated pointers and add valid-source, wrong-vtable and closed-modal cases. Native cross-build and pass-through exception tests pass; the live LAN comparison is running. The turn25 expired-popup protection remains in place.

### Native memory-guard discoverability checkpoint

At29 the sovereign explicitly wanted to persist strategy but claimed observed_revision needed an unavailable database revision. Actual provider request `033c93ab425346ae9b810c9aac3d8f05` contains the current decision identity with native revision14083237302485968098. The old tool text said only fresh snapshot guard. Read/write tool descriptions now map observed_revision to smac_decision.identity.revision and match/session to the same identity, and distinguish this from a journal hash or memory revision. No guard or persistence behavior was weakened. Production decision→guarded writer→journal regression passes and a subsequent native revision change rejects the old guard without writing. Actual MCP schema totals4,550 exact Qwen tokens /7,131 conservative, with15 tools; operating prompt remains1,338 exact tokens. Autonomous use remains pending. See `benchmarks/gameplay-memory-guard-discovery.json`.

Live LAN acceptance now passes after the SetupWin correction: two isolated native workers host/join/start, execute managed energy/technology/Pact/social changes, save and recover the native campaign with journal and identity capsules, and preserve agreement effects. The strengthened test audits every new-timeline own-unit publication for both seats: no transient appeared/removed identities, with owned refs unchanged. This closes the separate live LAN recovery-order gate; it does not claim sovereign inference.

### Complementary tool-result delivery repair checkpoint

Actual turn29 production call `chatcmpl-tool-b2488bb8e382e2f2` returned an affordable37-credit hurry choice for the Colony Pod (16/30 minerals,79 energy). Seven subsequent captured provider requests lack that answer; the sovereign then said hurry was unavailable. Semantic GC had treated production, citizen, research and decision results as interchangeable latest-state frames. This is a confirmed harness delivery defect, not evidence of poor strategy.

Choice queries now retain distinct signatures; execution/briefing receipts remain subject to ordinary episode retention. Every result in the latest assistant tool batch is protected from supersession and pressure eviction before the next provider response. An irreducibly oversized unseen batch fails explicitly. Actual installed Hermes sanitizer plus controlled HTTP transport verifies complementary answers, action/handoff receipts, identical-query supersession, copy-only history and pressure behavior (73,101→5,699 tokens;61,124-token unseen batch rejected). Existing context policy and writer→journal→runtime→Hermes transport contracts pass. The previous test that demanded erasing execution outcomes was corrected; all budget, request-only and history bounds remain enforced. Packaged integration and deployed autonomous acceptance remain pending. The game is paused at bridge-verified checkpoint30. See `benchmarks/gameplay-complementary-result-delivery.json`.

Packaged Hermes integration also passes: receiving-provider/capture equality, exact15/9 tool surfaces, canonical prompt replacement on real resume, preserved conversation, oversized-history preflight, unknown-tool rejection, direct MCP guard response and truncation lease release. Live autonomous complementary-answer delivery remains the next gate.

Deployment acceptance: verified checkpoint30 restores into session `session-2ed3c287614e4a2dba6ea85932aa2495`; exactly one run `run-f7de387e8f0a4d22b02287a2d50309a0` advances native gameplay to31. Actual request `ff5b23a6dbdf4b2ab0cbf05ebfea4d57` retains both citizen/research answers and the end-turn execution receipt. The new memory guard mapping is present in actual tool schemas. Fresh production query/hurry use and autonomous typed writing remain unobserved; historical unavailable-hurry prose is not evidence of current legality.

### Final-attempt feed alignment repair

Autonomous turn32 reproduced contact churn at cursors145/146: the first publication explicitly records an unmatched snapshot cut and registry reset, followed by a matched cut and restored canonical Tower ref. Collector telemetry shows24 bridge calls but zero alignment retries. A regression reproduces the omitted catch-up when two earlier bundle failures consume attempts0/1. Every coherent bundle now receives catch-up even on the final attempt; the bound is three bundles, three catch-ups and six probes. Actual live retry error codes were not retained, so that specific precursor remains inferred; bounded bundle-failure telemetry now captures them. Cross-publication identity/gap cases pass, with genuine loss and conservative unmatched-cut behavior unchanged. Verified checkpoint32 is retained. Live recurrence acceptance remains pending.

### Complete bounded world-history pagination

The history adapter previously capped expanded deltas at512 and temporal events at256, while the query sliced only the already-capped world list and repeated temporal events on later pages. A real committed700-delta/400-event batch fixture failed to terminate within100 pages. Storage now expands batch siblings before bounded SQL pagination; the opaque continuation carries independent world/temporal positions and still accepts old item-only cursors. New cache fingerprints exclude old truncated responses. The fixture returns every record exactly once in seven8,192-token-bounded pages, preserving epistemic status and excluding an uncommitted future row. Oversized temporal-only rows return a position-preserving deep retry instead of a successful empty/stalled page. Full world-model, existing budget-retry, four concurrent publication-visibility cases and three batch recovery crash windows pass. This is actual storage/query acceptance, not provider inference; packaged deployment and autonomous traversal remain separate. See `benchmarks/gameplay-history-pagination.json`.

### Live production delivery and effect acceptance

The turn32 resumption included a one-time operational notice: prior no-hurry prose was based on discarded answers, so requery current production without prescribing strategy. The sovereign queried production and citizens together. Actual request `6d7459bf0dbd4d5bbd74c4625e8ecc1f` retains the19-credit quote; it chose hurry, and request `8bb146bec6e04ed78246fddd5d1a5bb4` retains the execution receipt. An independent native read confirms22→30 minerals and88→69 energy. This closes delivery→sovereign expression→guarded execution→observed effect for the repaired quote after assisted re-anchoring. Pod completion, spontaneous future use, typed writing and recovery of the new effect remain separate. Current run `run-3b1fcc21b52146eeb4cfe5d5f4477048` resumes checkpoint32 on the history/final-cut image.

Turn33 completion and checkpoint acceptance: native unit listing and sovereign decision frame both show the completed Colony Pod `own-unit-11`. Verified checkpoint `checkpoint-ed196a377617411f857396090e4bb86d` safely freezes/resumes one active AI controller and contains the new native/AI state; it has not yet been restored. The repaired collector also performs an actual native catch-up (10 events,14 bridge calls,388.946ms) with zero contact changes on the new timeline. The specific final-attempt/two-error precursor remains regression-only evidence. The coverage matrix is consolidated to current established evidence and remaining gates; chronological evidence stays in this log and findings.

### Citizen reassignment affordance checkpoint

The sovereign repeatedly concluded that Doctor conversion was the only citizen action and that allocation could not improve growth. Actual request `2756d2be1a45472bbdb40f47603dba69` contains one conversion choice but no citizen context. Native catalog inspection shows a0-nutrient/3-mineral worked tile and19 unworked assignable tiles, including1-nutrient alternatives. The existing native workflow frees a worker as a temporary specialist, then exposes specialist-to-tile choices. The managed frame omitted native tile/yield/specialist context and did not explain that sequence.

Frames now deliver bounded native allocation evidence and conditional two-step instructions; public choices identify their semantic location/yields while keeping allocation indices private. No native command or arbitrary argument path is added. Both guarded steps and journaling pass through the actual managed adapter with controlled native state; single-use and no-fabricated-default cases pass, as do existing opaque/staged action and summary contracts. Human traces summarize allocation/workflow instead of dumping21 tile rows. Verified checkpoint34 is retained. Live provider delivery and resulting native allocation remain pending. See `benchmarks/gameplay-citizen-context-delivery.json`.

Citizen-context deployment resumes verified34 with the completed Pod intact, closing recovery of the earlier production effect. The sovereign advances to36, rechecks production and selects Formers before its last-unit move. Citizen requery/use remains unobserved. The isolated native managed-action regression is expanded to execute reassignment to a different tile and compare its worked state/yields with a native read.

The expanded isolated native acceptance passes: the actual15-tool endpoint converts a worker, obtains a fresh frame, assigns it to a different tile and restores specialist count. Independent native worked flags and yields match the managed context. The full native control/MCP suite also passes active dialogs, counterfactual comparisons, repeated checkpoint/identity recovery and backup. Optional provider inference remains disabled; this proves the managed/native chain, not autonomous allocation choice.

### Movement outcome delivery checkpoint

Turn35 native action3 resolved but reported origin1254, target1295 and observed1254. The managed response omitted these addresses, and the sovereign described arrival at1295; the next decision still showed1254. Invocation `6b5b9a95ac604152a6c7d75ad753fe21` retains the native evidence. A later action4 genuinely reported1254→1334 (`4ee9d73404ce4e068d7d2b6d74a252e9`). The cause of the first nondisplacement is unknown; native_result1 alone cannot prove arrival or blockage.

The managed receipt now translates those guarded action observations into public location references while excluding native entity slots. Terminal movement receipts distinguish reported displacement and reported target arrival, qualify their historical scope and require a fresh decision for current state. Pending, combat and consumption receipts acquire no arrival/survival inference. Adapter regression, opaque execution, production context and fair-play honeytoken differential checks pass. Verified checkpoint38 (`checkpoint-2b6518c31ebc4906978fe21cce498159`) is retained. Deployment and actual provider delivery remain the next gates; no native mechanics change is claimed. See `benchmarks/gameplay-movement-receipt-delivery.json`.

Movement-receipt image deployment restores verified38 and advances autonomously to39 with exactly one run. Native reads confirm two bases, Headquarters building Formers and Commerce Committee initially building a Scout. Actual resumed provider requests exactly match the current approved system prompt SHA25678456966b3c07d5aec25e18f30f8d15f2d7afa6dabe74f8ad452a3f79310d4f4, closing the old stale resume-prompt deployment gate. A direct movement receipt has not yet been emitted after this deployment; its live delivery gate remains open.

### Compact human choice previews

Live CLI output cut production frames at2,000 characters, sometimes inside the Hurry row, because repeated choice IDs and long instructional paragraphs consumed the preview. Structured records and provider delivery were complete. Human rendering now groups repeated labels with counts and bounded semantic samples, retains the decision ID and management access, and points to the full structured choices. It does not change tool responses or choose/rank actions. A43-choice regression retains late Hurry/Skip/End families in887 characters; diplomacy terms, queued/rejected semantics, citizen context and movement evidence regressions pass. Deployment of this rendering-only change remains pending.

Live citizen acceptance at40: actual request f4090b380fe94223ac8ae0ccd71b2ae1 contains worked tile1053 and the repaired workflow. The sovereign selects conversion, requeries, and assigns to1253; requests53c7db66f6c443479a5d8b5fd159132c/39505a6c75f54bbb84840b7d9f1afc4b/815bf8aac8c940179c9421b9efac26f5 retain the two receipts and intermediate catalog. Independent native semantic_choices confirms1253 worked with1N/1M/2E and zero specialists; list_bases confirms3 nutrient intake and+1 surplus. This closes actual delivery→sovereign expression→both executions→native effect. Earlier operational repair notices remain part of the conversation; recovery of this allocation is next. The sovereign also requeried and hurried both Formers at40 for2 and37 credits.

### Read-only Hermes WAL checkpoint repair

At41, two official checkpoint attempts fail in sqlite3 backup (`unable to open database file`). The stopped Hermes profile has a persistent WAL-mode database and no WAL/SHM sidecars;21GiB remains free. The source volume is intentionally read-only. A real helper under uid10000 reproduces the old failure with a cleanly closed WAL database and unwritable source directory. SQLite documents this constraint at https://www.sqlite.org/wal.html#read_only_databases.

The already-frozen match-bound database, retained WAL and rollback journal are copied together to private temporary storage; SQLite reconstructs transient metadata there before normal backup and match filtering. Source mounts remain read-only; unexpected source metadata changes fail closed. Session-filter SQLite errors now reject the checkpoint instead of silently archiving an unfiltered database. Tests verify closed WAL, committed retained WAL, excluded uncommitted rows, other-match filtering, unchanged source files, integrity and malformed-schema rejection. Existing full AI-memory restore and real uid/gid/umask archive/restore contracts pass. Native41 and allocation remain resident with the sovereign stopped; deployed checkpoint/recovery acceptance is next.
