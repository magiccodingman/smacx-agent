# AI-2 turn-transition regression

This focused regression reproduces the native boundary that stopped the AI-2
campaign at the end of 2129. It validates commit `eda56e1` without a native
multiplayer turn timer and without mouse, keyboard, screenshots, or a model
request.

## Conditions

- Preserved managed save immediately before the failed 2129 end-turn boundary.
- Peacekeeping Forces perspective, no ready units.
- Native time control: **None**.
- Final worker image built from the branch source.
- Direct authenticated semantic bridge calls using only enumerated choices.
- Save, credentials, host paths, and game assets are not included here.

## Observations

1. `game_management` enumerated the legal `end_turn` choice.
2. `semantic_command(end_turn)` returned a queued action in **3.0 ms**.
3. The bridge remained responsive and exposed the real `REALLYOVER`
   confirmation as a structured interaction.
4. Executing its fresh proceed choice advanced turn 29/year 2129 to turn
   30/year 2130.
5. The bridge then exposed the real Social Psych technology presentation as a
   structured interaction.
6. Acknowledging that presentation returned the game to an actionable `turn`
   phase.
7. `action_status(1)` reported `completed` with resolution
   `native_turn_advanced`.

The original build hung inside the synchronous native end-turn call and stopped
answering semantic requests. The corrected bridge returns first, schedules the
stock Turn Complete command from the native Windows timer, and keeps modal
interactions observable throughout the transition.

## Supporting contracts

- `scripts/decision_frame_test.py`: bridge failure propagates through
  `smac_wait` instead of becoming a successful unchanged observation.
- `scripts/harness_continuation_contract_test.py`: a persistent bridge outage
  stops Hermes and creates an operator-required incident.
- `scripts/capability_incident_contract_test.py`: an immediately visible
  supervisor incident is asynchronously enriched with the redacted diagnostic
  ZIP.
- Portal test suite: 54 tests passed.

