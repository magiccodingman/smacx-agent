# AI - 2 action-progress repair evidence

## Supervisor fingerprint regression

The preserved AI - 2 transcript contains alternating rejected moves, three Skip
submissions rejected at the decision protocol boundary, and an eventual valid
Skip followed by a turn-5 frame. The cause of the native move rejections remains
unproven; the model's ZOC explanation is not accepted as evidence.

The supervisor hashed `last_deferred_action`, including changing attempt IDs
and targets. Rejected attempts could therefore reset its no-progress window.
The fingerprint now excludes the entire receipt, including queued/completed
claims: progress must be represented in observed gameplay state.

Validation on the host:

```sh
PYTHONPATH=src python3 scripts/semantic_progress_contract_test.py
PYTHONPATH=src python3 scripts/harness_continuation_contract_test.py
```

Both pass. The fingerprint regression covers four alternating targets across
queued/running/rejected/completed receipts and clearing the receipt. It also
checks that changes to supplied unit location, movement and readiness remain
distinguishable, alongside existing turn/resource/interaction checks.

Evidence level: deterministic production fingerprint and supervisor contracts.
The unit rows in this regression are synthetic inputs, not proof that the live
snapshot exposes every unit effect. No live native movement equivalence,
full managed failure-loop containment, deployment, or resumed-game acceptance
is claimed. Those remain required before starting the replacement campaign.

## Managed submission budget and lifecycle receipts

The production managed choice wrapper now counts four consecutive rejected
submissions across targets and decision IDs. Protocol errors count even before
native dispatch; fabricated IDs are scoped using the immutable managed seat.
An accepted submission resets this submission budget. This is distinct from
proof of native progress: the unchanged-state circuits remain necessary for
accepted orders that do not produce effects. The fourth failure latches the
runtime circuit and invokes journal/diagnostic reporting; further submissions
are blocked before native dispatch. No action or ID is substituted.

Responses explicitly expose decision consumption (unknown when absent from the
cache), execution status, and the requirement to obtain a fresh consumed frame.
Acceptance and order assignment are not labeled effect verification.

Passing container contracts: `failed_choice_budget_test.py`,
`opaque_choice_execution_test.py`, `managed_action_path_contract_test.py`,
`capability_gap_latch_test.py`, and `capability_incident_contract_test.py`.
The new test exercises actual choice validation/dispatch against native-shaped
rejections, mixing two destinations with consumed and fabricated decisions,
then proves subsequent dispatch is blocked. It also checks session isolation
and successful-submission budget reset. Reporting is mocked in this test;
existing incident/latch tests independently cover reporting and durable
ingestion. This is not yet an integrated running-container quarantine proof.

`decision_frame_test.py` fails because its world-page fixture omits current
revision identity (`semantic_reference_native_revision_changed`). The same
failure was reproduced using the committed pre-wrapper `smacx_mcp.py` in the
same container. The fixture/guard contract needs correction and revalidation
before final acceptance; no weakening of reference guards is intended.

## Incident containment and fixture correction

Capability report ingestion now quarantines the whole managed match using the
existing worker-manager operation: native workers and MCP collectors are paused,
harnesses stopped, and active sovereign authority cancelled. A durable match
quarantine prevents supervision from restarting a paused sidecar or treating it
as an ordinary lost worker. Partial containment remains eligible for ingestion
retry, while automatic checkpoint restoration stays blocked. Explicit verified
recovery retains its existing authority.

The supervisor's sustained-active-stall path now queues a diagnostic report and
quarantines immediately instead of spending an automatic restart budget on the
same state. The old `semantic_stall_recovery_limit` cannot enable retries here.

Passing: `capability_incident_contract_test.py` now runs production quarantine
through a simulated Docker boundary and verifies both worker/collector pauses,
durable lifecycle/incident state and duplicate ingestion. Existing redaction and
bundle bounds still pass. `incident_recovery_test.py` and
`harness_continuation_contract_test.py` pass, including verified-recovery ordering
and sustained-stall quarantine despite a configured recovery limit of two.

`decision_frame_test.py` now supplies the required summary action revision and
separate unit/base pages, and checks semantic command arguments independently
of observation-call ordering. It passes, including a new explicit stale-summary
rejection assertion. No production revision guard was changed.

Limits: these are contained lifecycle/adapter tests, not a deployed Docker or
portal acceptance run. Native move rejection causes, interrupted persistent
orders, focused movement information and provider playthrough remain open.
