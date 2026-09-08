# Active roster and foreign-force evidence

Date: 2026-09-08

This checkpoint corrects two independent evaluation errors: open portal seats
were silently materialized as native bots, and non-Pact movement ZOC used a
name that encouraged a hostile-intent inference. It also carries exact
technology-demand transfer consequences and automatically surfaces bounded
base-defense mechanics for nearby visible foreign combat units.

## Acceptance chain

| Layer | Evidence |
| --- | --- |
| Observed and represented | Current contact roles, location, owner and faction relation flags retain their evidence envelopes. Location ZOC is represented as `foreign_movement_zoc`; Treaty, Truce, Pact and Vendetta remain distinct booleans. Lobby seat assignments produce mask bits by stable roster slot. |
| Calculated | Base mechanics include current owned garrison, friendly response ETA, visible foreign contact lower-bound ETA, geometric range, formal flags, movement constraint and unknown inferred intent. Shared turn/year is labeled era context without resource-parity inference. |
| Provider-queryable | World results annotate movement-ZOC limits. A request-only `nearby_base_defense` block appears only when a current visible combat contact is within range 3 of a current owned base. Technology-demand choices state exact conditional transfers. |
| Sovereign-expressible and executable | Existing guarded diplomacy choices remain unchanged; accept/reject/counter consequences are now explicit. Existing native technology-demand acceptance verifies transfer effect and retention. Lobby start sends an exact active mask through the existing managed startup surface. |
| Effect verified | Isolated native launch with mask `62` reports configured mask `62`, living mask `62`, living count 5, and inactive slots 6–7. Portal capture verifies a one-seat launch sends mask `2` and preserves six open rows. |
| Recovery-safe | The seven-entry roster mapping remains stable for native selectors and saves. Inactive slots skip initial setup and delayed Progenitor/Cult spawning. Existing campaigns and saves retain their recorded native living state; no migration rewrites them. A deployed fresh-match checkpoint/restore remains pending. |

## Reproduction

```bash
PYTHONPATH=src python3 scripts/strategic_world_fixtures_test.py
PYTHONPATH=src python3 scripts/runtime_spatial_context_test.py
PYTHONPATH=src python3 scripts/control_plane_test.py
PYTHONPATH=src python3 scripts/active_faction_mask_contract_test.py
dotnet test portal/Smacx.Portal.Tests/Smacx.Portal.Tests.csproj
docker build -f worker/Dockerfile -t smacx-agent-worker:active-roster-review .
SMACX_AGENT_TEST_MODE=1 \
SMACX_ACCEPTANCE_ACTIVE_ROSTER=1 \
SMACX_TEST_WORKER_IMAGE=smacx-agent-worker:active-roster-review \
SMACX_TEST_GAME_SOURCE="$LICENSED_GAME_SOURCE" \
PYTHONPATH=src python3 scripts/native_active_roster_live_test.py
```

Results: strategic-world fixture passed 20 groups; runtime spatial/defense
fixture passed; control-plane and mask validation passed; portal passed 87/87;
native worker completed 39/39 build steps; isolated native five-faction launch
passed with no UI input.

## Limits

The defense panel is mechanical assistance, not a recommendation or an estimate
of unseen forces. Visible contacts are a lower bound. Foreign movement ZOC does
not establish war or intent. Shared era does not establish economic or military
parity. The active-roster proof covers a fresh single-player native launch;
deployed five-faction recovery and full-game behavior remain for the next run.
