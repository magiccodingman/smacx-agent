# Multiplayer land development — turn-9 capability incident

The Spartan sovereign stopped on a reported Colony Pod capability gap. The
multiplayer unit catalog omitted founding and terraforming even though the
single-player paths existed. Its current pod had spent its movement and was
adjacent to an existing base: that particular observation did not prove a legal
immediate founding opportunity. The catalog also called supply-pod collection
“pods”, inviting confusion with Colony Pods.

The repair exposes guarded land founding, seven basic land terrain operations,
and activation of held land units/Former orders through the existing opaque
choice surface. Movement, current role/order/task and unavailable-action reasons
are returned with the choices. Sea development, foreign-territory work, advanced
terrain operations and custom multiplayer base names remain unvalidated and closed.

Native actions dispatch once on the deferred UI thread with identity, position,
readiness and legality rechecked. Native network return is not effect proof:
receipts remain pending until pod consumption/base creation or actual work/terrain
changes are observed. Work starting is a persistent order, not a completed terrain
improvement. Activation follows native Thinker semantics: a wholly unused skip
may be refunded; actual movement is not refunded.

## Acceptance

- Compiled production eligibility gates pass ownership, movement, terrain,
  technology, native founding legality, advanced-action and activation checks.
- Actual managed catalog → opaque choice → execution → journal adapter tests pass.
  These use native-shaped evidence and establish interface behavior only.
- Fair-play, opaque execution, stale rebasing, decision recovery, failed-choice
  bounds, managed action paths and movement receipt regressions pass.
- Final-build two-client native replay passes all 24 cases: host founding and
  seven work/completion pairs, working-Former cancellation and both held-unit
  activation movement cases; remote founding, road work/completion, cancellation
  and both activation cases. Every action checks invalid/duplicate rejection
  and exact agreement of vehicle, base and faction arrays. Native save/reload
  preserves exact vehicle/base state and save digest. [Results](multiplayer-development.json).
- Native cross-build completes 39/39 targets; packaged MCP receipt tests and
  reviewed engine compatibility checks pass.
- Deployed `smacx-agent-control:development` and
  `smacx-agent-worker:development` to the main stack. Both MCP source hashes
  match the checkout. Both prepared workers derive from the tested worker image.
- Original turn-9 paired checkpoint `checkpoint-8e0a5fe8706449178a0088e605ba3a9d`
  restored both managed seats and is `restore_tested` at 1788969801.839774.
  Portal recovery completed at 2026-09-09T16:03:29Z. Both sovereign processes
  are live, all four worker/MCP services are healthy, and no incident/health
  reason remains. Both sovereigns have acknowledged their restored introduction
  through actual fresh guarded choices.
- Autonomous selection of the new development actions is not yet observed;
  native mechanical effect proof is the isolated replay above.

Private incident evidence is retained under `runtime/astra/evidence-colony-turn9`.
The scoped diagnostic archive SHA-256 is
`42247a80779893fee9eec789a9dc46003a374d90590cd9854c8249673d7e4190`.
The isolated replay starts from private save SHA-256
`2a9b12d39a13e8a51698254d2ad9743f962b43cc5b7bc04f2e6205e8bbc6371f`;
test-only setup applies matching visible development fixtures to both replicas.
No native exactness claim comes from the adapter fixture alone.

Validation caught and rejected candidate implementations before deployment:
assuming a network return meant immediate completion, using the road-to packet's
continuation argument for basic work, and using the full native console path
(which changed an unrelated Former flag on only one peer). The final path uses
the native lock → order → vehicle synchronization → native action sequence.
A replay-driver handoff check also required correction: foreign ownership can
appear as waiting-for-engine while the remote client acknowledges its introduction.
These failures remain in private test logs; they are not reported as production
incidents or silently counted as passing runs.

The final replay first retires pre-existing ready units through guarded skip
actions to establish a matching baseline after native turn-entry selection. No
state bits are masked and no production state is changed by this test setup.
