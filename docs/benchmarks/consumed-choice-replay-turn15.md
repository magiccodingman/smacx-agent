# Consumed-choice replay containment and repair

Date: 2026-09-09

The parked multiplayer campaign reached turn 15/year 2115 before incident
`incident-77dee9db365041c3b8bb9e64e4e25a19` stopped both managed seats. Native and
AI checkpoint state remained bridge verified. The incident was caused by a
managed protocol feedback defect rather than a native crash.

The Peacekeeper sovereign successfully skipped its last ready Former. The
execution receipt reported `decision_consumed: true`, the native action revision
advanced, and a fresh native snapshot reported zero ready units. The following
provider request nevertheless retained the consumed decision frame and a slower
world-projection summary whose three `none` orders did not establish current
readiness. The sovereign replayed the same opaque IDs four times. Every replay
was rejected without native dispatch; the unchanged four-failure circuit then
quarantined the campaign as designed.

The repair makes three authority boundaries explicit:

- Provider-wire compaction recognizes direct and Hermes-wrapped successful
  execution receipts and replaces their matching consumed decision frame with
  an explicit supersession marker. The receipt itself remains intact and the
  durable transcript is unchanged.
- Request-only context now carries a `native_protocol` block sourced from the
  current snapshot, including phase, required action, ready-unit count,
  end-turn block and action revision. Its meaning states that projected order
  counts do not prove native readiness.
- Invalid-handle recovery identifies `recovery.frame.choices` as the replacement
  source and names the submitted decision and choice IDs that must not be reused.
  The failure budget, no-replay rule and cross-session checks remain unchanged.

Acceptance evidence:

- `hermes_complementary_results_test.py`: direct and real wrapped result shapes
  supersede the consumed frame while preserving the execution receipt and
  provider-valid tool pairs.
- `decision_recovery_test.py`: all four invalid-handle classes return fresh
  guarded frames with explicit replacement and prohibited-reuse metadata; the
  four-failure circuit remains active.
- `runtime_context_contract_test.py`: 64K and 256K contexts carry identical
  current-native protocol truth and remain within their existing bounds (5,719
  and 9,089 conservative tokens in this run).

This evidence proves observation, representation and provider delivery at the
managed boundaries. The parked campaign has not yet been resumed on the repaired
image, so sustained autonomous correction remains a live acceptance item.
