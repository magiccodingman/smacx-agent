# Scheduled check 1 — September 10, 2026

AI - 7 remained at turn 12 and stopped after a 316.99-second deliberation expired
a decision's independent 300-second handle. It had successfully activated a Former
before this sequence. The refreshed decision query took 0.35 seconds; the later
execution was rejected before native dispatch. A subsequent request arrived past
the no-progress admission deadline and was stopped. The new streaming signal was
working, but the handle lifetime had not been aligned with the permitted generation.

Decision and staged-choice retention now share the generation budget (1,200 seconds)
plus 60 seconds for dispatch. Original revision/session/legality and single-use
checks remain unchanged. This is cache retention, not permission to execute against
changed native state. The existing cache-size bound remains. Controlled selection
after a 1,201-second age succeeds once; expired, consumed and invented handles are
still rejected. No automatic action replay or ID remapping was added.

AI - 8 progressed from turn 4 to turn 8 and stopped on four invented decision IDs.
The exact current recovery IDs were present in the actual provider request bodies;
one earlier recovery was used correctly. This is not evidence that compaction
removed the handles. The failure circuit worked. The error headline still said to
fetch a new decision while required_next supplied one; the headline now points to
the supplied frame consistently. This clarification is not a demonstrated cure for
model-generated IDs. Checkpoint retry retains the guard and failure budget.

Validation: real managed decision enumeration/cache/selection and post-action-chain
fixtures pass, including long-generation retention and one-use rejection. Opaque
choice execution, stale/cross-scope semantic binding and four-failure containment
checks pass. The live phase-mutation test was not run: its standalone invocation
requires a dedicated native worker/token. No live mutation-contract claim is made.
Installed-image and paired recovery acceptance follow below. Both incident states
and diagnostic bundles were preserved before deployment. Disk has about 14 GiB free;
no other agent's services or data were modified.

## Completed recovery

Installed-image decision tests pass. Rebuilt `generation-handles` control/MCP and
Hermes images were deployed with unchanged networking, providers and Graphiti.
Both paired checkpoints and memory projections restored; AI - 7 at turn 12 and
AI - 8 at turn 8 are observed active, two active sovereign runs each, no incidents
or run verification errors. No specialist missions are active. One sovereign can
legitimately sleep during the other faction's turn. Live proof that the model
uses a handle after five minutes or stops inventing IDs remains for later checks.
[Health receipt](stream-check-1-health.json). Check 1 of 4 completed; schedule unchanged.
