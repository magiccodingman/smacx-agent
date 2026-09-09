# Sovereign event waiting

2026-09-08. The two-AI turn-1 campaign reported a capability gap from the
Spartan perspective after 23 seconds of waiting, while the Peacekeeper replica
continued native progress. Incident `incident-6692fa23fe084ab9b91a407235e9a899`
preserved checkpoint `checkpoint-72e59cc68330493882b56d5b6e018301`.

## Checkpoint 1: deterministic waiting contracts

Native `waiting_for_turn` now yields a WAITING directive with optional eligible
chat. Request context prioritizes turn ownership over locally ready units.
Native engine processing remains distinct. Reports from native wait states do
not latch capability gaps; explicit native gaps and execution circuits retain
containment.

A cleanly exited waiting sovereign remains an active supervised run with durable
sleep metadata and no provider process. New published, scoped chat can wake a
communication episode. The same unread chat sequence does not repeatedly wake
it. Native phase and session changes wake gameplay; polling itself is not
progress. Incoming attention is not acknowledged by wakeup. Control restarts
preserve the sleep state. A model ignoring the yield instruction is suspended
at the existing bounded activity deadline rather than quarantining its peers.

The operator-only wait watchdog compares native markers from managed peers.
Progress renews its deadline; reads and messages do not. A managed turn owner
with all replicas waiting without progress for the configured deadline triggers
quarantine. An acting peer retains its provider-aware stall watchdog. Unmanaged
or human turn ownership is not subject to this autonomous waiting deadline.

Validation: supervisor sleeping/restart/chat deduplication/session/interaction
wake and real-stall boundary contracts pass. Existing continuation and runtime
ownership tests pass. Attention restart/redelivery tests pass. Wait receipt,
false-gap rejection and wait-before-ready-unit tests pass. Portal 87/87 and
activity reducer tests pass. Live communication delivery and recovered two-AI
turn progression remain pending; no full-game completion is claimed.


## Checkpoint 2: packaged deployment and native recovery

Shared `smacx-agent` stack redeployed with control/harness `event-wait` images
at repair commit `54b4c54` and portal `event-wait` (UI commit `e740690`).
Native worker remains PR74; doctrine fingerprint comparison passes. Installed
image wait receipts, supervisor race regression and continuation contracts pass.
Control and portal are healthy; specialist supervisor is running. Volumes and
existing provider/knowledge configuration were preserved.

Supported incident recovery completed at 2026-09-08 23:48:57 UTC from the paired
turn-1 checkpoint. Both workers and MCP sidecars became healthy; no active
incident remained. At 23:51 UTC the Spartan run
`run-48de08d041624a2e9970867c636c9623` had durable `waiting_for_turn` sleep,
while Peacekeeper run `run-920863985d8546bc88c84fd7c06fe6b3` was in native
`turn` phase. Live sovereign processes fell from two to one. Repeated supervisor
samples preserved the same sleep start timestamp. The authenticated spectator
activity endpoint for seat index 1 returned `status: sleeping`.

This proves native observation → managed waiting → process exit → durable sleep
and operator/spectator visibility in the recovered game. Native turn/session and
chat wake are covered by deterministic contracts; a complete live turn transfer
and live chat wake have not yet been observed in this acceptance window. The
campaign remains running for user testing. No full-game result is claimed.


## Foreign-owner engine wait correction

The recovered match reached turn 2 and the Spartan sovereign woke with
`native_phase_changed`, confirming live turn wake. Incident
`incident-80d19b27d670450f88ddd1279ad0a5e3` then exposed a missed classification:
Peacekeepers had `waiting_for_engine`, faction 1, current faction 2, while
Spartans were advancing. Three clean exits triggered the old no-progress guard.
This was supervisor containment, not evidence of a native process crash.

Native deferred work has precedence over turn ownership in `interaction_kind`.
A shared classifier now recognizes engine wait as foreign-turn sleep only with
explicit distinct valid faction IDs. Native interaction classification and
execution guards remain unchanged. Own/unknown-owner engine processing retains
its watchdog; foreign wait retains the managed-peer progress watchdog. Regression
contracts cover repeated clean exits, ownership returning, invalid/missing IDs,
and provider wait receipts. Deployment verification follows below.


Correction deployment: control and harness images rebuilt; installed control
imports and foreign-engine classifier pass. Shared control/portal health checks
pass after redeployment. Native binaries and database volumes were preserved.
The earlier live wake is confirmed by `wake_reason: native_phase_changed` and
Spartan native turn-2 progress. Recovery of the new clean-yield incident uses the
control API's verified-checkpoint recover operation with runtime refresh (the
portal retry-after-update route accepts capability-gap incidents only).
