# Interaction overhead acceptance

## Checkpoint 1: continuation protocol (2026-09-10)

The AI - 8 Peacekeeper turn-9 sample required 23 provider responses and 25 calls,
with 535.64 seconds cumulatively inside provider requests. Five executions were
accepted, eight calls rejected, six were reads, five successful attention calls,
and one memory summary. This is a single faction-turn sample, not a throughput
claim for complete rounds or a controlled comparison with AI - 7.

Changes return a fresh decision after nonqueued accepted execution without
promoting accepted to completed. Queued work, handoff and sleep still stop
collection. Consumed recovery menus are retired from every earlier provider
result containing that menu; original errors and recovery provenance remain.
Durable history is unchanged. Empty attention batches omit the acknowledgement
invitation; real batches preserve evidence and their existing receipt.

Controlled acceptance: `decision_recovery_test.py` exercises real cache and
execution adapter, one-use and scope guards, long-generation expiry, accepted
observation and no collection during queued work/handoff/sleep.
`harness_context_policy_test.py` runs through the actual Hermes sanitizer and
checks duplicate recovery menus, preserved errors, uncertain effects, strategic
prose and immutable history. `empty_attention_context_test.py` checks empty and
nonempty request context. These tests pass; they prove adapter/provider behavior,
not native effect equivalence or strategic quality. Live validation remains pending.

Automatic information-notice handling, movement discoverability audit, turn
metrics and deployment are subsequent checkpoints and are not yet accepted.

## Checkpoint 2: reviewed notification continuation

The managed decision path drains at most four native-reviewed notifications.
Eligibility requires the exact reviewed-notification classification in the
cached native catalog and exactly one executable acknowledgement. The bridge's separately reviewed non-diplomatic faction-introduction classification
is also eligible. Generic engine-confirmed one-button popups and strategic
alternatives are not automated. Native whitelist/command guards
remain unchanged. Captured public information, state and identity are journaled
through durable attention before dispatch; capture failure prevents dismissal.
The ordinary one-use native execution path is used. Rejection, queued work,
unchanged observation or scope change stop continuation. Later observation does
not certify pending mechanics such as support-driven disbanding.

`automatic_notification_test.py` proves orchestration ordering, four-dismissal
bound, stop cases and preserved cognitive-acknowledgement obligation. Existing
`decision_recovery_test.py` still passes. Native effect verification remains a
live acceptance requirement. Movement audit found persistent destination queries
already implemented; the native tile-target query now receives an explicit
managed `own_unit_ref` / `target_location_ref` query hint. No automatic movement
or strategic choice was added. Empty attention invitations are also removed when
budgeting excludes every item; placement still requeues undelivered evidence.

## Checkpoint 3: repeatable latency reporting

`scripts/turn_latency_report.py` reads a single perspective's activity JSON array
or JSONL for explicit start/end timestamps. It deduplicates stable event IDs,
separates incomplete windows, counts errors/execution status, and reports
provider, first-content and per-tool durations plus available token usage.
It intentionally does not infer a full turn or verified effect from acceptance;
operators must establish boundaries from the native journal. Context assembly
is not in the activity stream and remains outside this report. Missing cache
usage is unavailable, not zero. No additional log storage or Graphiti work.

`turn_latency_report_test.py` validates duplicates, role-only chunks, partial
requests and unavailable usage. The retained sanitized
[baseline](interaction-overhead-baseline.json) reproduces the manually reviewed
AI - 8 turn-9 sample (23 complete responses, 535.639 cumulative provider seconds).
No improvement percentage or quality equivalence is claimed before live checks.

Installed-image checks additionally pass attention redelivery, restart, batch
acknowledgement, unseen-item requeue, and one-use opaque-choice execution.
The runtime-context fixture's former empty-batch invitation expectation is
updated to the new contract; its cognitive-placement checks remain intact.

Initial live acceptance: AI - 7 accepted an INTRO acknowledgement and returned
a fresh executable post-action frame in the same result (about one second for
the call). Recovery INTRO handling exposed the separately reviewed narrative
classification; it is now included without admitting generic one-button dialogs.
An unchanged post-dismissal observation explicitly offers a wait, with no
executable popup menu; it does not invite a second acknowledgement while native
presentation is settling. The unchanged-observation fixture verifies this.

## Deployed live acceptance

Final control/MCP image: `smacx-agent-control:interaction-overhead-intro`.
Final Hermes image: `smacx-agent-harness:interaction-overhead`.
All unrelated Compose settings compare equal, including provider, Graphiti,
ports and volumes. Both original campaigns resumed paired checkpoints (AI - 7
turn 17; AI - 8 turn 11). The [health sample](interaction-overhead-live.json)
shows active campaigns, two admitted sovereign runs each, and no incidents or
run-verification errors. A sleeping sovereign can legitimately have no live
process.

AI - 8's first recovery read automatically captured and dismissed INTRO and
SIMULYOU, then returned a turn-phase unit decision. Both receipts carry stable
attention IDs, report cognitive acknowledgement false, and have later changed
native observations. Those same IDs and notice payloads appear in three actual
subsequent provider requests. Thus evidence survived native dismissal and reached
the sovereign; the check does not certify unrelated pending mechanical effects.

The model did submit one old handle after restoration. It was rejected with a
fresh recovery frame and continued with world/reference queries; stale-handle
errors are not claimed eliminated. Strategic quality and complete-turn speedup
remain pending the three already-authorized scheduled checks. No new timer was
added and Graphiti was not changed. The early validation does not increment the
four-check ledger.
