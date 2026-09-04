# Sovereign checkpoint 1: correctness and reliability

Status: checkpoint acceptance passed, including the complete isolated native suite. Baseline:
`b5c70fb93bc39f37d228316756d9b0d267aad9b5`, on PR #48's existing feature branch.
This checkpoint does not certify the remaining managed-action, intent,
counterfactual, or integrated acceptance checkpoints.

[Sanitized machine-readable evidence](results/2026-09-04-sovereign-correctness.json)
contains contract outcomes and measurements. Raw operator/container logs and game
assets are intentionally excluded.

## Corrected behavior and evidence

* Complete geographic identities are independent of anchor LOD. A paginated
  `smac_world` area query at `world-geography` discovers omitted masses and mobility
  regions; their area queries resolve the complete server-held membership.
* Fragmented-map fixtures retain a tiny owned colony, a current foreign base and
  six foreign land contacts without artificial promotion. A 60-base fixture
  retains economy, research, Social Engineering, Council, victory, project race
  and ecology. Unknown-gap relations differ from closed mapped separation.
* Local participant relationships and explicit route subjects connect theaters;
  shared continent membership and unrelated adjacent contacts do not. Existing
  amphibious, allied and quiet-plan fixtures remain enforced. Frontiers prioritize
  explicit concerns and nearby current contacts; freshness reads map fields only.
* Cache dependencies cover owned response/support units, infrastructure, map shape,
  diplomacy and movement rules. Moving a responder invalidates a base answer;
  changing a distant irrelevant noncombat unit does not. Warm unchanged queries
  avoid rich geography construction. The persisted-map selector regression uses
  an opaque alias and actual SQLite projection reconstruction.
* Repair and staging retain stale access, riot, facility, feature and ownership
  evidence. Current Pact, stale Pact, stale riot and unknown-access cases differ.
  Island repair and staging reuse the bounded transport scheduler, including a
  distinct complete no-known-transport result. Assisted staging reports reachable.
* Watch predicates dispatch only the requested typed comparison and compare the
  selected field. Frozen previous values are supplied at publication. Expiration
  and invalidation enqueue deduplicated lifecycle attention, including retry after
  status mutation. These changes do not execute gameplay or renew watches.
* Survey terrain/altitude are produced by the compiled native tile adapter using
  the native rule gate. Native-shaped enabled/disabled entitlement fixtures prove
  projection and filtering, with Survey provenance distinct from vision. Hidden
  ownership, resources and features remain absent.

## Verification

The 14-test container batch passed: world model, native observation, fair play,
strategic fixtures, geographic semantics, global pipeline, movement mechanics,
runtime context, attention/communication, rollback, provider schema budget,
managed tool surface, semantic choice binding, and sovereign hardening contracts.
`sovereign_geography_acceptance_test.py`, the collector and amphibious benchmarks
also passed. The specialist supervisor contract ran on the host and passed its
real process, cancellation, isolation and Docker-boundary tests. .NET passed
63 tests. The native cross-build linked successfully with 22 existing warnings;
a successful build is not counted as 38 native gameplay tests.

Reproduce container contracts from the repository root:

```sh
docker run --rm --entrypoint /opt/smacx/mcp-venv/bin/python \
  -v "$PWD:/workspace:ro" -w /workspace -e PYTHONPATH=/workspace/src \
  smacx-agent-control:dev scripts/sovereign_hardening_contract_test.py
PYTHONPATH=src python3 scripts/sovereign_geography_acceptance_test.py
PYTHONPATH=src python3 scripts/specialist_supervisor_contract_test.py
dotnet test Smacx.Agent.slnx --no-restore
```

The isolated live suite uses images rebuilt from this checkout, a separate test
stack, the host MCP venv matching the control image, and the configured game source.
It preserves checkpoint/crash recovery, managed opaque Drop execution and native
Pact-map regression gates. Its new dual-gated native fixture temporarily supplies
512 owned base input rows and 32 candidate squares, calls the production 21-square
receipt/overlap path, then restores the rows and visibility before returning.
The fixture also verifies exact restoration of base rows, visibility, current-base
pointer and native yield scratch state. The production read-only receipt isolates
its yield context and restores the native accumulator. This fixture measures
responsiveness; it does not claim successful legal founding.
Both operation wall time and concurrent UI probe gap must remain below 500 ms.
Final result: **229.131 ms** operation wall time, **214.944 ms** maximum concurrent
probe gap, **170,436 bytes**, 32 candidates and 512 base rows. Native restoration
checks passed. The complete suite also passed guarded outside-page Drop execution,
Pact-port movement/boarding, native checkpoint verification, crash recovery,
semantic identity survival and sidecar removal on park.

Diagnostic runs found and repaired the persisted-map selector bug. Two runs also
encountered an initial runtime-context timeout and a Pact-fixture timeout. No
timeout or UI budget was increased. The complete final rerun passed after the
native scratch-state guard and fixture isolation changes; this does not establish
the cause of those earlier timeouts. Retain them for the final integrated
reliability review. Optional provider/Hermes portions were not enabled.

## Size and performance

All token measurements below use the deterministic estimator, not the Qwen
endpoint. The unchanged 1,117 exact Qwen tokens and live prefix reuse from earlier
commits are historical evidence, not rerun results.

| Anchor | Baseline | Checkpoint |
| --- | ---: | ---: |
| Small quiet, 64K | 962 | 1,053 |
| Huge quiet, 64K | 965 | 1,056 |
| Huge fragmented, 64K | 4,751 | 5,449 |
| Huge chaotic, 64K | 5,895 | 5,668 |
| Huge chaotic, 256K | 15,902 | 15,809 |

Quiet growth for 100 times the squares is 0.2849% (baseline 0.312%). Hard anchor
ceilings remain 6,000/16,000. The managed surface remains 15 tools; the world schema
remains 1,475 bytes / 492 conservative schema tokens.

Cold/warm query timings are recorded in the JSON with the geography fixture; they
are single-run measurements, not latency percentiles. The warm-hit contract also
fails if rich geography construction is attempted, independently of timing noise.
The production collector with native-shaped 25,600-square quiet input took
19.18 seconds initially, with a 274.16 ms maximum independent UI probe gap.
The dense orbital case took 39.17 seconds, with a 328.53 ms probe gap. These are
collector-pipeline measurements; the native stress gate is recorded separately.

## Remaining proof boundaries

Read-only geography and repair evidence do not certify later action parameter
workflows or exact future healing predictions. Counterfactual mechanics require
controlled native comparison wherever exact game agreement is claimed. This
checkpoint makes no new completion claim for those subsequent layers.
