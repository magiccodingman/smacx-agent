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
admission behind multiple provider timeouts.

The public projector image `6456944b0667c87f5f13d8ddfdac9a79c44b023b07570ccb2dfb757d4c207bf5`
reclaimed and completed AI10's originally interrupted
`rebuild-93ebe0b6665a40dba2cbd84dda5eb27c`. During the subsequent explicit
turn-81 recovery, perspective rebuild `rebuild-003f81e490024904be2c34fbd93ccac1`
completed and `rebuild-d30fa5d691f34a298fbfb5625df4eb31` remained queued
behind optional background work. Recreating the projector on the priority build
claimed the second request immediately and completed it without error. The
portal then admitted both sovereigns: operator health reported
`observed_active`, two active runs, two live sovereign processes, two healthy
native/MCP pairs, no verification error, and no incident. AI9 remained healthy
and advanced from turn 70 to turn 76 during this repair. Sustained autonomous
progress after turn 81 and a deliberately forced timeout remain open.
