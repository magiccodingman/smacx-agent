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
