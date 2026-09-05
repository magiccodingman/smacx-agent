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

## Saved native replay and movement feedback

An isolated worker loaded a read-only copy of the retained AI - 2 turn-4 autosave
using the rebuilt native bridge. It reproduced all four rejected moves (2835,
2756, 2715, 2794) from tile 2755. Each receipt now proves the native movement
function was actually attempted, with native result zero and unchanged position.
No claim is made that ZOC, a particular enemy, or a bridge precondition caused
these rejections. The exposed cause remains explicitly unknown.

The Scout's observed budget was 3 native movement units with scale 3, capacity 3,
HP 8/10. A return-to-base order was assigned successfully, but subsequent native
processing cleared the order and again presented the unit as ready. A valid
Skip immediately removed it from the turn-4 ready list. This is live native
evidence of the reported state, not a synthetic substitute and not a resumed
sovereign campaign. The original retained save volume was mounted read-only;
the disposable worker and its private data/secret volumes were removed.

The first probe failed before gameplay because the test's copy helper created
root-owned state. The helper now assigns the disposable files to the worker's
actual account. This was a probe setup defect, not a reproduced campaign crash.

Ready-unit frames now include current HP, native movement budget/scale, and order.
Return-to-base reports order assignment and explicitly unverified arrival, with
readiness read from current native state. Deferred moves distinguish dispatch
precondition failure from an attempted native rejection. Managed receipts preserve
that distinction without inventing causes for older receipts lacking the field.

Operational prompting explicitly requires a new frame after rejection. Doctrine
explains scaled movement, repair-phase refresh, Skip and interruptible persistent
orders. Its paragraph inventory and 21 deterministic golden cases were updated;
all content cases pass. Strict prompt, doctrine integration, opaque choice,
failed-choice budget and decision-frame contracts pass. The native cross-build
and separately tagged worker/control Docker builds succeed. The final tool
description edit still requires the deployment image rebuild.

Reproduction: `action_progress_saved_native_test.py` requires the legal source,
retained save volume and optional archived save path via `SMACX_TEST_GAME_SOURCE`,
`SMACX_TEST_SAVE_VOLUME`, and `SMACX_TEST_SAVE_PATH`. Its output is an investigative
trace; it does not certify every action family or provider behavior. Native saves,
binaries and private runtime logs remain excluded from Git. Full managed live
acceptance, final image deployment, portal validation and the sovereign game
remain pending.

## Integrated repair acceptance

The final rebuilt control image and worker passed the isolated
`control_worker_mcp_live_test.py` with `SMACX_TEST_ACTION_CONTAINMENT=1`.
Four invalid submissions crossed the real MCP endpoint, returned no native
dispatch, opened the failure circuit, and caused both the native worker and MCP
collector to become Docker-paused. This verifies deployed containment in an
isolated installation, not merely the simulated Docker boundary.

The same run passed the existing 15-tool managed action families, controlled
native counterfactual comparisons, crash/checkpoint restoration, semantic identity
preservation, journaled plan/reservation recovery, and rejection of old-timeline
watches and choices. A preceding run also passed the full baseline. Neither run
used a sovereign provider; provider behavior and the full game remain unproven.

The reviewed engine registry now includes the native receipt/movement-field
source revision. Final images: control
`sha256:f70a2c32ec7f15933ffa06eb3ec94022e0dccb449e796417b783d078d8973703`,
worker `sha256:c1f5297a28a13f4378bd2c7d1fdadd4f96c77c64c29de56b330c7f881944d57c`.
The local Qwen3.8-27B tokenizer measured the operational prompt at 1,175 tokens
and the representative compiled stock fixture at 8,340. These are tokenizer
measurements, not cache reuse or campaign performance claims. Sanitized results
are in `2026-09-06-action-progress-live.json`.

Production deployment, browser incident presentation, and sustained sovereign
playthrough remain the next checkpoint. The actual cause of the four native
move rejections remains unknown; this repair does not assert a movement-engine fix.
