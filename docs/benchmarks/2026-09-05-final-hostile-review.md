# Final hostile-review repairs

Reviewed baseline: `234bcc2f277e48288e16c0b97a3d1458087d53b2`. PR remains unmerged.

## Gate A — F1/F3 publication transaction

Accepted targeted native-shaped evidence: `publication_transaction_test.py`,
`plan_dependency_publication_test.py`, `native_observation_contract_test.py`,
`observation_batch_recovery_test.py` passed in the supplied MCP container.
[Crash matrix](results/2026-09-05-publication-transaction.json): nine boundaries
× unchanged/reversed native state, all 18 passed. Birth completion, garrison
arrival/departure, current-field changes and scope invalidation use candidate N
on its first evaluation, without pre-saving the fixture's candidate.

Invariant: canonical N evidence and idempotent watch/milestone effects computed
from immutable candidate N precede head N. Head installation certifies completion
of those pre-head effects, but does not certify dependency attention. A remaining
frozen package plus exact installed cursor/identity/action revision requires
finishing dependency attention and acknowledgement before draining N+1. A failed
pre-head publication replays its immutable input; a failed post-head publication
finishes from the installed N head. No native mutations are replayed. Existing
attention dedupe and enqueue-before-state ordering provide one logical transition
with at-least-once delivery. No extra phase table or duplicate map snapshot was
introduced. Full performance distribution remains pending until final acceptance.

## Gate B — F2 visible episodes

Pending.

## Gate C — F4/F5/F6 reference lifecycle

Pending.
