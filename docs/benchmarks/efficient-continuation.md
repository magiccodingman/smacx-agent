# Efficient sovereign continuation acceptance

**Performance acceptance remains open.** The [matched baseline audit](continuation-baseline-audit.md)
measures the retention cost versus pre-PR policy and supersedes informal net
token-saving claims. Guarded next-decision use is observed; faster turns are not established.

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

Resume fixture clarification: an old unparseable tool error is no longer treated
as disposable merely because an episode ended. The oversized disposable fixture
now explicitly identifies superseded state; separate retention tests pin errors.
Emergency protocol eviction also preserves accompanying strategic prose and
non-success results. This is stricter retention, not an increased context budget.

Integrated acceptance (controlled, no paid provider/game campaign):
- Real Hermes HTTP capture passes initial/resumed gameplay and communication,
  exact diagnostic/request correspondence, low/medium/high settings, runtime-only
  context, lease failure recovery and oversized SQLite-history preflight.
- Actual Hermes sanitizer passes complementary choices, bundled frame delivery,
  consumed nested frame removal with execution outcome retained, immutable audit,
  latest-batch protection and oversized unseen-batch fail-closed behavior.
- 500 settled-action fixture: 170,972 → 8,235 conservative history tokens.
  500 cognition/notebook fixture: 1,074,282 → 15,247 conservative history tokens.
  Irreducible history still fails closed. These are synthetic budget tests.
- Opaque execution, failed choice budget, transport errors, journal evidence
  scope/timeline, MCP schema, fairness, attention and runtime budget tests pass.
- Controlled bridge chain executes exactly two sovereign-selected mutations using
  the bundled second decision; no intervening explicit decision tool call needed.
- Diagnostic `post_action_decision_built` records availability, collection duration
  and decision identity for later attribution. Existing provider/tool traces and
  history compaction counters support generation-count and latency comparisons.

No live strategic equivalence, checkpoint restore, or percentage speedup is
claimed. The parked campaign remains the user's next behavioral validation run.

Deployment acceptance: shared stack control API is healthy on the new image;
MCP/harness image selection and four installed source hashes match this branch.
Provider/Graphiti/portal/network/volume configuration was compared and preserved.
Campaign remains parked at turn 6/year 2106, zero active sovereign runs/incidents.
No database rebuild, native image change, or automatic campaign resume occurred.
[Sanitized deployment receipt](efficient-continuation-deployment.json).
