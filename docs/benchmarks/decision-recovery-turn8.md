# Turn-8 invalid decision recovery

The Peacekeeper sovereign successfully skipped a Former, then submitted four
invalid choice requests without a new decision read. Three distinct decision
IDs were absent from captured decision responses. The exact provider request
contained the rejection and `required_next: smac_decision`; the failure was not
caused by dropping that tool response. The fourth invalid submission latched
the existing failure circuit and froze both native clients. No invalid request
was dispatched to the game. This incident is a protocol safety stop, not evidence
of a native crash.

Private packet SHA-256:
`0e94e3f02af6180db72c361fbbb2c3dc7394b53488469ca0e137ac1da28936e5`.
The packet is preserved outside Git. No credentials or full game transcript are
published here.

## Repair and acceptance

- Invalid handles retain their failed/non-dispatched receipt and failure count.
  Before the circuit limit, one normal decision enumeration supplies fresh
  opaque choices inside `recovery.frame`. No action selection, replay, gameplay
  strategy or native argument is synthesized by recovery.
- Enumeration retains authority, fair-play, briefing, turn and revision checks.
  Wait/handoff and unavailable observations remain explicit. Cross-session
  frames are discarded. Refreshing does not reset the four-failure budget.
- Controlled bridge test covers actual enumeration → represented fresh frame →
  JSON receipt → sovereign-selectable opaque action → guarded execution. It
  proves one dispatch only after a separate selection, not a running-game effect.
- Actual Hermes sanitizer and controlled HTTP transport preserve the rejection
  and fresh frame in provider messages without changing durable history.
- Existing decision, opaque-choice, failed-submission, movement-receipt,
  fair-play and managed-schema contracts pass. The managed surface remains
  15 tools; the system prompt is unchanged.

## Deployment and campaign recovery

Deployed `smacx-agent-control:decision-recovery` to the main control service and
both MCP sidecars. Running MCP source hashes match the repaired checkout.
Native worker and Hermes images remain the previously validated versions.

The existing turn-8 checkpoint
`checkpoint-d9f7891e19ce43bda95cb1a5c6289155` successfully restored both managed
seats and paired AI state, and is now `restore_tested` (1788964747.888539).
The durable portal recovery operation completed at 2026-09-09T14:39:31Z.
Both sovereign runs/processes are active, both native clients and MCPs are
healthy, and the health report has no active incident or health reason.
The Peacekeeper sovereign subsequently selected and acknowledged the restored
SIMULYOU notification through a fresh guarded decision; the receipt was accepted.

This proves deployment and continuation of the original campaign. The automatic
invalid-handle recovery path is covered by controlled adapter/provider-wire tests;
it has not yet been exercised by a new autonomous invalid submission in this
restored campaign. No intentionally invalid action was injected into production.
