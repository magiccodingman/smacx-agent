# Unit directive acceptance

## Scope

Built on merged main `2a261641d16013412135d92170625b0aa9fd96bd`, including settlement
assistance and the multiplayer production-upgrade notice fix. This is a managed
harness feature, not a change to the native game's rules or multiplayer allowlist.

Implemented: opaque batch authorization; durable travel, scoped exploration,
specified Former work, follow and two-actor escort; supervised native steps;
manual override; bounded execution; one-turn closure consent; interruption,
recovery and timeline rollback; attention outbox; bounded operational context;
provider-menu retirement; packaged modules; repeatable validation.

Aircraft/automatic transport/combat/founding directives and unattended
multi-turn play are outside this implementation. Existing manual controls stay
available. Single-player and multiplayer share the supervised execution backend;
no native persistent-route fast-path or new network command is assumed valid.

## Automated evidence

`python scripts/unit_directive_validation.py` runs 24 suites:

- `unit_directive_test.py`: 38 deterministic cases covering assignment atomicity,
  actor exclusivity, guarded multi-step/multi-turn travel, fair scheduling,
  explicit closure, manual override, contact/base/damage interruption, unknown
  exploration geometry and native-option fallback, work enum/effect contracts,
  follow/escort, journal failures, pending receipts, restart and rollback.
- `unit_directive_mcp_test.py`: 16 cases through actual registered MCP tools,
  opaque cache and command dispatch, with real journal/world/attention stores.
  Includes stale approval rejection, policy revalidation after native revision
  rejection, preserved intent/critical-attention/briefing/communication gates,
  native row reordering, no-selector-leak previews, and idempotent outbox recovery.
- `unit_directive_context_test.py`: 3 cases exercising the production wire
  compactor and actual runtime assembler. Consumed approvals retire, unreviewed
  approvals remain, history is unchanged, and a 64-directive dashboard retains
  counts within its 1,400-token component budget.
- 21 existing suites cover journal/crash/idempotency, rollback, movement,
  attention, opaque execution, decision/recovery/notifications, managed action
  paths and tool surface, strict prompt, runtime context, post-action frames,
  intent gates, persistent native order attention, plan health, and memory.

All 24 suites passed in the implementation workspace with the repository's
actual pinned MCP 2.0.0 dependency. The PR validation workflow reruns this same
entry point and retains per-suite logs and the JSON result. Python compilation
is a separate workflow check. Fixture tests use no game or provider calls.

The MCP fixture demonstrates that one approved advance executes two current
native moves, journals their effects, verifies arrival, and produces one
completion notification. This demonstrates removal of intermediate model calls
for that fixture; it is not an empirical campaign speedup claim.

## Manual gameplay acceptance

The owner will perform live testing. Suggested saved-state checks: an ordinary
multi-turn route; a scout revealing a hostile; a known pod or monolith; a
movement-triggered diplomacy/combat/Artifact continuation; a Former completing
and losing a job; a slowed escort; a lost allied contact; manual retreat and
resume; bridge loss while an action is unresolved; checkpoint restoration; and
adjacent movement across two multiplayer clients.

Compare provider responses per useful objective, complete faction-turn elapsed
time, unnecessary interruptions, and campaign decisions—not just dispatched
steps. The executor reports slice steps, stop reason, elapsed time and zero
internal provider calls through `directive_execution` diagnostics. Native
receipts and canonical directive transactions retain mechanical evidence.

No live game, campaign quality evaluation, two-client network test, Docker image
build/deployment, or production recovery was performed for this change. No
active campaign was touched and no auto-merge or timer was enabled.
