# Worker incident reliability checkpoint — 2026-09-05

## Observed failure and scope

A live single-player campaign with a managed AI stopped after its native Explore
order. The journal last confirmed turn 1 / year 2101; the preserved game screen
showed year 2102. The AI process stopped, but the native worker and MCP collector
remained running. Docker reported the worker unhealthy, without an exit or OOM.
A direct authenticated ping returned `game_timeout`: requests reached the UI
handler, while the native request-in-progress guard remained set.

Sixteen native checkpoint attempts had journal/save receipts but no published
complete checkpoint generation. Their Hermes archives were empty and mode 0640.
The control API creates files as uid/gid 10001 under umask 0027; the helper writes
as uid 10000, gid 10001. `touch(mode=0660)` was masked to 0640. Consequently the
helper could not write the archive. The recovery refusal was correct; the
message that the native process had stopped was inaccurate.

## Repairs and acceptance

| Capability | Appropriate observed-to-recovery chain | Evidence / limit |
| --- | --- | --- |
| Checkpoint archive permissions | Production failure → explicit temporary group-write permission → real SQLite backup/archive helper → private 0600 archive → existing memory restore contracts | `checkpoint_helper_permissions_live_test.py` runs the actual manager method and helper under distinct production uids, common gid and umask 0027, in an isolated Docker container. `ai_memory_checkpoint_test.py` checks target-campaign restore, unrelated-memory preservation, journal timeline isolation and save digest binding. This does not claim a live end-to-end campaign recovery. |
| Stop after worker/bridge loss without a complete checkpoint | Unhealthy native worker → lifecycle error → pause all managed game workers and collectors → stop harnesses → cancel sovereign leases → durable quarantine latch | `incident_recovery_test.py` exercises owned-resource pause ordering/idempotence and two supervision passes. The reported live worker and collector were separately frozen and Docker-confirmed paused; original RAM, saves and journal were retained. |
| Unavailable bridge after exit code zero | Unavailable authoritative progress → retain outage start/count → withhold another provider invocation → bounded operator incident; or continue after fresh valid progress | `harness_continuation_contract_test.py` covers unavailable clean exits, no restart/token-budget consumption, deadline preservation and successful fresh-observation recovery. Existing live-process outage behavior is shared. |
| Durable incident deduplication | Repeated same incident state → stable journal idempotency key → one durable event, retaining status/detail changes | `incident_recovery_test.py`: repeated recording and repeated supervision leave the journal head unchanged. Database last-seen time can still advance. |
| Operator explanation | Bridge unavailable distinct from process exit → freeze receipt overrides incident text → incomplete native/AI checkpoint cannot be resumed | Portal tests: 56 passed. Native stop from a Docker-paused disposable process was also verified, so ending/parking does not require an autonomous resume. |

Additional passing checks: `operations_contract_test.py`,
`harness_manager_contract_test.py`, and `git diff --check`.

## Native cause remains open

The Explore order is the last observed action, not a proven cause. A fresh
isolated campaign passed through turn 5. Two isolated reproductions from a copy
of the reported native save also passed through turn 5 with the full observation
collector; one included twenty repeated native save attempts before play.
Collector consistency retries occurred during transitions, without reproducing
the persistent bridge stall. No provider or original campaign state was resumed
for these probes. No native guard was cleared or weakened.

The initiating native fault/re-entrancy path is **not yet identified or claimed
fixed**. This checkpoint fixes independently proven recovery and containment
bugs. It does not certify a complete autonomous game or turn the incomplete
original checkpoint into a safe recovery point. Native binaries, saves, provider
history, credentials and unsanitized debugger traces are excluded from this
committed evidence.

## Deployment verification

The repaired control API, portal and harness images were built and the affected
services recreated successfully. Control API and portal health checks pass;
the original worker and MCP container remain Docker-paused. The existing control
database now records the durable quarantine receipt with two paused containers
and no complete recovery checkpoint. No database reset or unverified game
restore occurred. Image-packaged Python modules also passed the memory,
incident/quarantine and continuation regressions (tests invoked by absolute
mounted paths to avoid selecting an older bundled test script).
