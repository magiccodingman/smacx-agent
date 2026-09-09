# Multiplayer supply-pod and settlement-choice checkpoint

This checkpoint closes three gaps observed in the parked autonomous campaign:
ordinary multiplayer movement could not intentionally enter a visible supply
pod, opaque numeric references invited invented spatial reasoning, and a legal
`found_base` choice did not repeat enough local evidence to distinguish native
legality from strategic quality.

## Implemented chain

- **Observed:** the bridge recognizes an actual, currently visible adjacent
  native pod through `goody_at`; remembered or inferred pods are ineligible.
- **Represented:** `unit_actions` emits a distinct consequential
  `collect_supply_pod` choice. It says the benefit, danger, and possible
  follow-up are unknown. Normal safe movement continues to exclude pod tiles.
- **Calculated/queryable:** `smac_world(mode="compare")` remains the bounded
  site-comparison surface. Each legal founding choice now repeats current
  native legality, minimum spacing, nearest known base range, known-radius
  coverage, and overlaps with known bases. These fields are semanticized to
  opaque public references by equality mapping.
- **Sovereign-expressible/executable:** the existing managed command surface
  accepts only a fresh `collect_supply_pod` choice. The bridge revalidates
  ownership, readiness, adjacency, visibility, pod presence, occupancy,
  territory, and terrain immediately before dispatch through the stock LAN
  movement order.
- **Effect verified:** the managed receipt reports only observed pod removal.
  It does not predict or generalize the random result and directs the sovereign
  to inspect fresh unit, world, event, and interaction state.
- **Recovery-safe:** deferred status remains pending while the native movement
  stack unwinds. No map/unit reads occur in that transient window. Pod orders
  use the stock network packet exactly once; ordinary movement retains its
  separately validated survivor sync, resolved through stable unit identity.

## Acceptance evidence

`scripts/multiplayer_supply_pod_contract_test.py` compiled the actual bridge
eligibility helper and passed its positive case plus stale visibility, non-pod,
occupied, foreign-territory, ocean, non-adjacent, non-ready, and wrong-owner
rejections.

The isolated native command below used the turn-9 Thinker multiplayer save
whose SHA-256 is
`368c740ab61b7333af854c906b8685a7018714d4f504b46a115bdca7e29c99f9`:

```sh
SMACX_DEVELOPMENT_POD_ONLY=1 \
SMACX_DEVELOPMENT_TEST_SAVE="$PWD/runtime/astra/native-hang-turn9.sav" \
SMACX_TEST_GAME_SOURCE="/home/dmint/.local/share/Steam/steamapps/common/Sid Meier's Alpha Centauri" \
SMACX_TEST_WORKER_IMAGE="smacx-agent-worker:pr78-gameplay" \
SMACX_TEST_CONTROL_IMAGE="smacx-agent-control:development" \
PYTHONPATH=src python3 scripts/native_multiplayer_development_test.py
```

It returned `passed:true`, `two_peer_effect_verified:true`,
`pod_removed:true`, and `native_outcome_not_predicted:true`. The test creates
two isolated native clients, applies the fixture identically, executes the
choice from the active host, and compares complete vehicle/base/faction state
on both peers. Earlier iterations exposed and then prevented an empty second
DirectPlay wait and a status-read race during native movement unwind.

Managed receipt, journal, opaque-choice, command-schema, doctrine content, and
provider integration contracts cover the remainder of the chain. The broader
development fixture subsequently reaches its unrelated synthetic end-turn
handoff slowly and is not counted as evidence for this checkpoint. No claim is
made that the sovereign will value every random pod result optimally or that a
legal settlement is a recommended settlement; live strategic uptake remains a
later gameplay observation.
