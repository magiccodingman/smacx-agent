# Active provider generation and gameplay stalls

AI - 7 turn 12 acknowledged a popup, generated a 301-second response ending in a
read-only decision query (0.37 seconds), then streamed another 267 seconds before
the old 360+180-second gameplay watchdog stopped it. The lease remained valid.
This was active deliberation, not a proved native deadlock or failed tool.

The trusted SSE adapter now records a bounded last-content watermark for emitted
text, reasoning or tool arguments. Role-only, usage and keepalive events do not
count. Writes are throttled to once per second and scoped to the current run and
request; terminal requests cannot be revived by late chunks.

The existing no-gameplay-progress clock and repeated clean-yield checks remain.
One request admitted before the gameplay deadline may finish while emitting
content, bounded by 20 minutes from submission and a 120-second content-silence
limit. The old 180-second transport grace remains for requests without content.
A subsequent request cannot replace the latched request to gain more time.
Completion has at most 30 seconds to dispatch an action, within the hard bound.
None of these observations are counted as semantic gameplay progress. Stream
silence, absolute generation-budget exhaustion and ordinary no-progress activity
are distinguished in incident details. No prompt, game mechanic or DB change.

Controlled tests cover the old transport boundary, continuous content beyond it,
silence, absolute bound, replacement rejection, terminal state and unchanged
native progress in actual supervisor reconciliation. Installed-image and live
acceptance are recorded below after validation. This does not establish strategic
quality or prove a long deliberation would eventually choose a useful action.

Installed control-image tests pass. The real Hermes provider-capture test passes
with rebuilt images, including actual streaming, terminal metadata, tool deltas,
authority heartbeat and clean authority-loss exit. The direct schema/prompt,
resume and semantic-GC contracts remain intact. Live deployment follows this gate.
