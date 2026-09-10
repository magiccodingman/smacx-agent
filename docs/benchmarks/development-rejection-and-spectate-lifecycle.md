# Development rejection and spectator lifecycle acceptance

Date: 2026-09-10

## Observed failures

AI - 9 stopped safely at turn 6 after the Peacekeeper sovereign selected the
same `Farm` order for the same Former at the same represented location three
times. The first two guarded native actions were rejected before development
dispatch with the aggregate `state_changed_before_execution` receipt. A fresh
managed decision offered the identical semantic choice again; the third
selection opened the existing no-progress circuit before native dispatch.
The complete incident packet is retained outside the repository and a verified
paired turn-5 checkpoint remains available. This evidence proves the failure
and recovery boundary, not the exact native predicate that rejected the lock.

The live portal browser separately reproduced an unhandled
`ObjectDisposedException` from `Spectate.DisposeAsync`. Parameter changes and
component teardown could both cancel a retained, already-disposed
`CancellationTokenSource`. Portal server health remained available.

## Repair and controlled evidence

Deferred multiplayer development now reports the precise pre-dispatch stage:
native context/turn, unit existence/ownership/location/identity, renewed
legality, network lock refusal/remapping, or post-lock identity/legality. A
failed network lock is no longer described as a native call, and no unlock is
issued for a lock that was not acquired. The queued receipt retains its command
so managed errors name terraforming or founding rather than movement.

The managed choice cache binds a pre-dispatch rejection to its complete semantic
choice key and meaningful state fingerprint. The exact choice is withheld from
the next unchanged frame while other guarded choices remain. A changed focus
or native state permits fresh enumeration. The three-attempt circuit and
ordinary native-rejection budgets remain unchanged.

`development_receipt_test.py` proves rejection classification, non-dispatch,
command-specific guidance, same-state withholding, alternate-choice retention,
and reappearance after meaningful state change. Existing opaque-execution and
failed-choice suites pass. `multiplayer_development_contract_test.py` checks the
compiled source boundary contains every distinct receipt and does not mark a
terraform call attempted before the network lock succeeds. The worker-image
cross-build and restored two-client effect remain deployment gates.

Spectate, lobby-directory, and player pages now give their polling loops local
timer ownership, atomically clear lifecycle fields before cancellation, await
the stopped loop, and dispose JavaScript resources once. Expected cancellation,
disposed-object, and disconnected-JS teardown paths are contained. The portal
build and 88-test suite pass without warnings. Repeated navigation in the
deployed browser remains the UI acceptance gate.

## Acceptance chain

| Capability | Current evidence | Remaining limit |
| --- | --- | --- |
| Development rejection | observed incident → exact choice/state represented → stage-specific receipt calculated → provider-queryable fresh frame → sovereign can select remaining choice → rejected command not dispatched → repetition circuit retained | Restored native campaign must identify the exact failing stage and demonstrate subsequent progress; a successful native terrain effect is not claimed by the rejection fixture. |
| Spectator lifecycle | browser exception observed → owned refresh resources represented → idempotent teardown compiled → authenticated route remains provider/query accessible | Deployed desktop/mobile route transitions must show no Blazor error or console exception. |
| Popup presentation handoff | Live Spartan spectate retained an `INTRO` image after the semantic snapshot had entered foreign-turn wait → guarded acknowledgement now distinguishes command acceptance from observed dismissal → the exact popup object/generation remains authoritative during transition → a fresh native map frame is presented only after that object leaves the modal stack | Bridge cross-build and source contract pass. Per owner direction, the campaigns are parked and live visual verification is delegated to the next run; this does not claim the deployed stream has yet been observed clean. |

## Popup presentation follow-up

The instruct and low-thinking campaigns were parked at verified checkpoints
before this repair. The observed defect was a stale rendered dialog on an
inactive multiplayer perspective, while the authoritative semantic state had
already advanced to `waiting_for_turn`. `acknowledge_popup` now returns
`dismissal_verified` and `transition`; acceptance alone no longer states that
the native modal has disappeared. When the tracked popup object or generation
actually leaves the modal stack, the UI-thread bridge redraws `WorldWin` before
the perspective can remain idle. `popup_transition_redraw_contract_test.py`
guards the postcondition ordering and redraw boundary, and the full bridge
cross-build passes. Live verification is intentionally pending.

## Parked runtime rebase follow-up

The first owner resume after deployment failed closed because both parked seat
specifications still referenced the prepared image based on worker
`0f36a318cf6a`, while the reviewed deployed worker was `287e04469e47`.
Both verified checkpoints remain intact. Every verified restore now reconciles
managed seat image references against the configured immutable worker before
native startup. An unchanged worker uses the existing content-addressed image;
a changed worker prepares and binds the new one. The controlled recovery-order
suite exercises ordinary `refresh_runtime=false` recovery and proves this
reconciliation precedes worker start and collector publication. Owner resume
remains the live effect-verification gate.
