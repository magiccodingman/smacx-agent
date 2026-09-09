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
