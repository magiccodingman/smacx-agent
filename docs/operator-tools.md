# Browser-free operator tools

Run `scripts/smacx-ops` from a checkout, or `smacx-ops` in the control image. The checkout client uses Python's standard library; it does not require MCP. Configure `SMACX_OPS_URL` to the portal's HTTP(S) address. The CLI uses the existing portal login, cookie and CSRF protections. Operator health, inspection, pause/resume and diagnostics require administrator access; ordinary lobby permissions still apply to lobby operations.

```sh
scripts/smacx-ops login --username admin
scripts/smacx-ops --json catalog
scripts/smacx-ops --json lobbies
```

Login prompts for the password without echoing it. Automation can use `--password-stdin`; do not put passwords in command arguments. Only the session cookie is persisted, in a mode-0600 file (default `~/.config/smacx-ops/session.cookies`, override with `--session-file` or `SMACX_OPS_SESSION`). Expired sessions require another login. No browser, database editing, service-token extraction, or disabled authorization is necessary.

## Create the acceptance roster

```sh
scripts/smacx-ops create --name 'AI acceptance' --request-id acceptance-001 \
  --source SOURCE_ID --runtime RUNTIME_ID --agent AGENT_ID
scripts/smacx-ops start MATCH_ID --wait 300
```

The preset assigns Peacekeepers to one sovereign AI with personality `none`, plus native Hive, University, Morgan and Spartan bots. The remaining two seats are open. Planet size is standard/normal, difficulty Librarian, spectators allowed, and other options follow current New Lobby defaults (including Graphiti enabled). Asset arguments may be omitted only when the catalog has exactly one candidate. The final roster is read back and checked before reporting creation success. Creation never starts the match.

`--request-id` deduplicates lobby creation per authenticated account while that lobby exists. Changing creation parameters under the same ID returns a conflict. Seat assignment uses the normal portal PUT operations. An interrupted creation can be retried with the same arguments; a room already starting/running is not reconfigured. `start` checks the current state before dispatch. Network errors never cause automatic mutation retries: inspect the match before deciding what to repeat.

## Observe without advancing gameplay

```sh
scripts/smacx-ops --json status MATCH_ID
scripts/smacx-ops --json health MATCH_ID
scripts/smacx-ops --json inspect MATCH_ID
scripts/smacx-ops --json inspect MATCH_ID --object-ref location-1214
scripts/smacx-ops --json events MATCH_ID --cursor-file ./match.cursor
scripts/smacx-ops --json watch MATCH_ID --cursor-file ./match.cursor --interval 10
```

`status` is the portal lobby view. `health` combines actual managed-container state, registered sovereign runs and process checks, active incidents, committed world heads, specialist mission state and fresh specialist child-process telemetry. It exposes sampling times and cached supervisor progress. `observed_active` means recent activity observations are available; it does not mean good strategy, verified native liveness, or victory. Stale/unavailable supervisor evidence is `unknown`. A long turn alone is not classified as a deadlock. Existing authoritative action/stall/continuation detectors retain their thresholds and incident handling.

`inspect` reads committed faction projections and preserves epistemic wrappers. It returns kind counts and up to 64 active units/bases/factions per perspective; the counts include the stored kind's full population, not just returned details. An exact object reference can retrieve a known map location or another represented object. These are faction observations, not an omniscient map. Private native identity metadata is removed. The operator interface is not exposed to the sovereign.

`events` reads canonical journal events, not terminal animation lines or a complete live provider reasoning stream. Stable event IDs distinguish repeated display from repeated execution. Each page returns up to 50 events per perspective; the first page starts near the current head. Cursors bind perspective and timeline. Timeline changes, missing events and a cursor falling more than 500 events behind are explicit gaps; use the full archive for older evidence. Large event payloads are replaced by byte count/hash notices. Output is written before the cursor advances, so a crash can replay an event; consumers deduplicate by event ID. `watch` continues across clean sovereign episode exits until interrupted or an API error; it does not start a model, pause automatically, or schedule agent wakeups.

## Pause and recover explicitly

```sh
scripts/smacx-ops pause MATCH_ID
scripts/smacx-ops resume MATCH_ID --incident-id INCIDENT_ID
```

Investigation pause first persists an operator incident and recovery-required lifecycle state. It freezes native workers, stops MCP collectors to release database locks, stops sovereign processes, and requests specialist cancellation. Read-back verifies worker/collector/process state. Fresh specialist child telemetry is required if child execution has existed; unavailable telemetry produces incomplete containment rather than a false success. The return includes `containment_verified`; exit code 2 means incomplete containment. Retrying pause is safe and can confirm asynchronous specialist shutdown.

**Investigation pause is not a save.** `checkpoint_created` is false. Frozen native RAM is preserved only as process memory; it does not survive container loss. Resume requires the exact operator-pause incident, refuses unrelated open incidents, and uses the existing verified native/AI checkpoint recovery path with runtime refresh. A replay of a recovered incident does not restore the game again. There is no arbitrary native unfreeze command or bypass for missing checkpoints.

For ordinary save-and-stop, use:

```sh
scripts/smacx-ops park MATCH_ID --wait 300
scripts/smacx-ops resume MATCH_ID
```

Parking reuses the portal's durable background maintenance operation. Completion requires parked status, a verified checkpoint, and stopped processes. Timeout or incomplete verification is not success. `resume` without an incident follows ordinary portal recovery and cannot bypass an active operator incident. Mutating requests are not automatically retried after transport errors.

## Evidence packets

```sh
scripts/smacx-ops diagnostics MATCH_ID --output ./campaign.zip
scripts/smacx-ops packet MATCH_ID --output ./incident-evidence
```

The archive uses the existing authorized diagnostic export. A packet adds health, faction inspection and recent journal events, with capture times and SHA-256 hashes. Its files have separate read watermarks: it is not a consistent recovery checkpoint. A partial capture retains a manifest with `complete: false`. Existing destinations are never overwritten. Full ZIP downloads are capped at 1 GiB by this client.

## Validation and boundaries

- Real portal HTTP/Identity/CSRF/SQLite smoke: `scripts/operator_portal_http_test.py` (build the Debug portal first).
- CLI HTTP roster, retry, cookie and packet tests: `scripts/operator_cli_test.py`.
- Authenticated control HTTP, projection/journal, pause/recovery-fence tests: `scripts/operator_contract_test.py` in the MCP Python environment.
- Real isolated Docker process containment: `scripts/operator_docker_containment_test.py` with the Docker socket and `smacx-agent-control:operator-tools` image. This tests process containment, not native game mechanics.
- Existing native checkpoint recovery remains the authority. Two fresh native startup checks have passed for the guided operator path, including packet export and verified parking. Full-game acceptance remains unproven; see [the evidence](benchmarks/operator-readiness.json).

GitHub issue automation, storage policy changes, recurring agent scheduling and the knowledge-transfer prompt are a subsequent phase. These tools do not create issues, launch monitoring agents, merge PRs or resume games on their own.

Accepted startup continues provisioning if the initiating HTTP client disconnects. The CLI may still report a timeout; inspect `status` before another mutation. For cold native preparation use `--timeout 900 start MATCH_ID --wait 300`. This shields caller cancellation, not server process loss; existing bounded control-service timeouts remain.

For two installations on the same hostname, assign different `SMACX_PORTAL_COOKIE_PREFIX` values as well as separate ports, Compose projects, networks, volumes, worker/MCP/harness image references, control-data volume references, and operator cookie files. Cookies are not port-scoped. The default prefix preserves existing login cookies; a custom prefix also separates auxiliary Identity cookies. Changing a prefix requires logging in again.

Graphiti runtime heartbeat reports event-loop liveness independently of batch completion. Read `metadata.phase`, `projected_events`, `failed_events`, and `last_projection_unix` to distinguish a pending projection from completed work. A healthy container alone does not establish provider progress.

## Guided startup and deployment read-back

Start with [the operator quickstart](operator-quickstart.md), including Hermes
execution and scheduling instructions. `deployment` checks an explicit local
Compose project and writes image IDs; `preflight` verifies installation identity,
image prerequisites and optionally the running operator module hashes and image
receipt. `bootstrap` creates the preset, saves its match ID before starting, locks
its state file and streams progress until verified native readiness or a bounded
unverified/failure result. It never silently resubmits an ambiguous start.

MCP startup failures now retain bounded inspection/log evidence in worker network
metadata before container cleanup and emit a campaign diagnostic event. Operator
health includes this receipt even after container removal. Historical receipts
are time-stamped; current `mcp_status` determines whether the failure is active.
Missing logs/inspection are explicit capture gaps. No recovery or fair-play
contract changes are involved.
