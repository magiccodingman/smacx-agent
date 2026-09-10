# Sovereign lease lifecycle repair

AI - 8 - Instruct stopped at turn 1 because its Spartan gameplay episode exceeded
the 900-second sovereign lease. The durable lease was acquired at
1789007892.3275077 and expired at 1789008792.3275077. Subsequent gameplay calls
returned `sovereign_episode_not_active`, but reads and some memory writes continued.
The independent watchdog finally quarantined the campaign after 397.7 seconds
without native progress (16 provider calls, 2,869 generated tokens in that window).
All 76 completed response usage records reported zero reasoning tokens; this was
not a failure to disable thinking.

## Implementation checkpoint

- The trusted Hermes process sends an authenticated private heartbeat every 30
  seconds, including during provider inference. The existing unexpired ownership
  token, episode, run, native session, perspective and active timeline must match.
  Run/session validation also precedes initial managed lease acquisition.
- Renewal changes only lease expiry. It preserves acquisition time and the original
  turn fence; it does not count as semantic progress or reset watchdog counters.
- Runtime reads renew/check ownership before issuing provider context. Expired,
  cancelled, replaced, stopped-run, stale-session and lost-cache ownership cannot
  be silently adopted. A private transport receipt carries the heartbeat token;
  it is excluded from provider context and runtime diagnostic payloads.
- Lost authority blocks gameplay and managed memory mutations. Hermes exits cleanly
  at the next safe request/response boundary, before another provider request or
  returned tool batch can proceed. The supervisor's existing confirmed-process-stop
  cleanup and fresh-state admission handle continuation. No failed action is replayed.
- A permanent heartbeat rejection marks authority lost immediately. Three consecutive
  transient transport failures also fail closed. Episode completion stops heartbeats.
  The independent native-progress watchdog and its bounded clean-yield policy remain.

## Deterministic acceptance

`sovereign_lease_lifecycle_test.py` advances a controlled clock across more than
the original 15-minute lease, with real SQLite ownership and actual HTTP handling.
It verifies rejection after expiry/replacement or wrong run/session, stop policy,
private receipt isolation, unchanged turn fences and blocked dead-episode writes.
Heartbeats extend the lease without another context/world build.

`sovereign_heartbeat_client_test.py` verifies the private transport, heartbeat loop,
loss-to-clean-exit path before another request, and stop-on-episode-end behavior.
Existing runtime collection/restart-fence, memory, attention/communication,
supervisor isolation, foreign-turn wait and schema-budget checks pass. The managed
surface remains 15 tools / 8,180 conservative schema tokens, with no prompt changes.

Installed control-image lifecycle, runtime collection, memory and attention checks
pass. Installed Hermes heartbeat and context-policy checks pass. A real Hermes CLI
against a controlled slow provider confirms private heartbeats during inference,
receipt exclusion from every captured provider request, and unchanged generation,
resume, tool-boundary and semantic-GC contracts. A forced authority rejection
exits the actual CLI with status 0 before any further provider request.
Live paired recovery and actual lease-extension observations remain pending.
AI - 7 is parked; AI - 8 retains its verified turn-1 checkpoint. No timer is scheduled.
