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
