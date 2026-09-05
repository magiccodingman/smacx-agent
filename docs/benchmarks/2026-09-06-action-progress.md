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
