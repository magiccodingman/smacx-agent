# Efficient sovereign continuation acceptance

Checkpoint 1: settled successful actions return a versioned next decision using
ordinary decision enumeration. Pending actions, handoffs and uncertain execution
are excluded. Failure to collect the next decision preserves the original action
outcome. Native reads run outside the progress lock; subsequent selection remains
subject to ordinary revision, scope, consumption and failure-budget guards.

Validation: `post_action_decision_test.py` covers settlement, exclusions, scope
change and observation exceptions. `decision_recovery_test.py` exercises actual
frame/cache construction and guarded choice execution with a controlled bridge.
These establish adapter behavior, not live native mechanical accuracy or speedup.

Performance target: fewer explicit decision-only provider generations. No measured
percentage improvement is claimed. Disable independently with
`SMACX_POST_ACTION_DECISION=0` in the MCP process.

Checkpoint 2: pre-write stale observation errors include a fresh guarded decision
for reconsideration. No automatic retry/rebase occurs. Evidence-scope failures
explain optional summary event boundaries and prohibit unrelated replacements.
Writes that started never get this retry path. Existing lease-only attention
acknowledgement already avoids requiring a world/attention cursor selection.
`memory_repair_context_test.py` passes read-failure, pre-write-only and no-rebase
checks. Disable independently with `SMACX_MEMORY_REPAIR_CONTEXT=0`.

Checkpoint 3 source audit corrects the initial diagnosis: existing wire cleanup
already removed historical reasoning and prior-episode tool pairs. This change
preserves accompanying assistant prose, recent episode pairs, unresolved errors
and query evidence that is not explicitly superseded. Exact duplicate historical
prose is removed only above a size threshold. Consumed nested post-action frames
are replaced without losing execution receipts. Existing trusted runtime context
continues to provide scoped cognition, active intent, attention and world state;
no second model-written summary or new source of truth is introduced.

`continuation_retention_test.py` verifies verbatim rationale, conditional fallback,
stale sighting, diplomatic promise, unresolved error, recent episode retention,
protocol pairing and immutable input. Unknown strategic prose is retained. This
may retain more evidence than the old aggressive prior-episode cleanup; no
compaction latency gain is asserted. `SMACX_CONSERVATIVE_CONTINUATION=0` disables
this pass (retaining pairs rather than restoring unsafe blanket deletion).
