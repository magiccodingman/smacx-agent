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
