# Graphiti rebuild interruption recovery

During AI10's supported turn-81 restore, one perspective completed its required
checkpoint-memory rebuild. The second request,
`rebuild-93ebe0b6665a40dba2cbd84dda5eb27c`, remained queued while the projector was
unhealthy. Restarting the rebuildable projector claimed it, but the backing
model server then closed the provider connection and the request remained
`running` without a completion bound. Native workers stayed healthy and the
sovereign admission gate correctly refused partial memory.

The projector now gives each Graphiti provider request a bounded timeout and
places an independent bound around the complete timeline replacement. A
projector restart atomically returns crash-interrupted `running` rebuilds to the
durable queue, clears their stale start timestamp, and records the interruption
reason. It does not mark projection complete, admit the sovereign, or alter the
campaign journal. A completed rebuild remains required before gameplay resumes;
a bounded terminal rebuild failure remains visible to the operator.

The focused worker contract proves exact-scope scheduling, crash-interrupted
requeue, bounded timeout configuration, same-loop liveness, failure visibility,
and journal authority. Required perspective rebuilds also drain before optional
background projection; otherwise a slow unrelated scope can hold checkpoint
admission behind multiple provider timeouts. Public projector deployment and
successful completion of AI10's interrupted rebuild remain open gates.
