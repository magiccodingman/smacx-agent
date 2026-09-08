# Operator quickstart (Hermes or another shell-capable agent)

Use this recipe before reading historical investigations. The operator manages the
campaign through the portal APIs. It is a different process from the sovereign
Hermes inside the game container. Do not attach an extra sovereign to its session.

## Required handoff values

Obtain an explicit checkout path, portal URL, Compose file and project, private
login/session file, and installation ID. Keep one private mission directory and
one operator per campaign. Never guess an installation, provider or AI profile.
This repository's standard-library CLI is `scripts/smacx-ops`; no host MCP package
or browser is required. Python contract tests that import MCP use the control
image's `/opt/smacx/mcp-venv/bin/python`.

The CLI prints JSON. Lifecycle/read reports use different schemas: do not guess
field names. `--help` is available on every command. Nonzero exit means inspect
what failed, not repeat mutations until one succeeds.

## Deployment and authentication

Use a clean, dedicated Git worktree when another agent is editing the repository.
Fetch main before repair work. Do not switch the other agent's checkout underneath
it. Use installation-specific image tags and Compose projects, volumes, networks,
cookie prefixes and cookie files. A tag update does not replace a container.

Deployment is an explicit maintenance step. Park active campaigns through the
operator API before replacing their services. An investigation-pause is frozen
RAM, not a save: retain it until the incident's supported recovery is decided.
Do not rebuild databases, unfreeze native containers manually or use Docker
cleanup on another installation. For a known, already configured deployment,
build the intended images with its explicit source context and image references,
then use `docker compose -f COMPOSE -p PROJECT up -d --no-build`. Do not deploy
while those builds are still running. This guide does not authorize repairs or
restarts beyond the owner's current instructions.

After deployment, use read-back rather than searching DLL strings:

```sh
scripts/smacx-ops deployment --compose COMPOSE --project PROJECT --output /private/mission/images.json
scripts/smacx-ops --url PORTAL --session-file /private/mission/session.cookies login --username admin
scripts/smacx-ops --url PORTAL --session-file /private/mission/session.cookies --json preflight --verify-checkout --images-file /private/mission/images.json
```

`deployment` is host-only and requires Docker CLI access. It verifies core running
service image IDs and the control service's worker/MCP/harness image, network and
volume settings against the selected Compose configuration. It does not build or
restart anything. Use a new receipt filename for every deployment.

Login prompts privately; automation uses `--password-stdin` with a private input
source. Never put passwords in command arguments or logs. An expired cookie needs
another login. Use separate cookie files for separate portal installations.

Read `installation_id` from the preflight once and record the intended identity.
Subsequent checks/startups must pass `--expect-installation`. Preflight exposes
configured image IDs, five running control/operator module hashes, Docker network
and volume availability, storage free bytes and the lobby catalog. Its cached
catalog is not a live provider generation test. `--verify-checkout` compares those
five module hashes, not the whole application. Image receipts compare actual
configured worker/MCP/harness image IDs, not guessed version strings.

## Start the approved fresh campaign

Select IDs from `catalog.gameSources`, `catalog.runtimes`, `catalog.agents` in the
preflight result. The agent list contains the portal's available AI profiles.
Do not silently substitute a different provider or model when the intended one is
unavailable.

```sh
scripts/smacx-ops --url PORTAL --session-file /private/mission/session.cookies bootstrap \
  --expect-installation INSTALLATION_ID --verify-checkout \
  --images-file /private/mission/images.json \
  --state-file /private/mission/startup.json \
  --name 'AI acceptance — clean run' --request-id UNIQUE_REQUEST_ID \
  --source SOURCE_ID --runtime RUNTIME_ID --agent AGENT_ID --wait 900
```

This creates the Peacekeepers AI (no personality), four native bots (Hive,
University, Morgan, Spartans), standard/normal world, Librarian, spectators enabled
and other current preset defaults. Two unused seats remain open; seven seat rows
do not mean seven players. It verifies the roster including the assigned AI ID.

It saves `match_id` before starting, locks the mission file, and streams JSON Lines
while startup proceeds. Run it through the shell tool's supported background
mechanism if the foreground tool has a short deadline. Preserve and poll the
returned process/session ID; do not launch another copy. Rerunning the same
command reads the saved identity, but an unresolved previously submitted start is
never automatically resubmitted. Inspect status and health first.

`native_ready` means the lobby is running, native worker and MCP are healthy,
one sovereign process exists, committed world observations exist and no incident
is active. It does not mean a tool action was successful, strategy is good, a
checkpoint exists or the game is won. `needs_attention`/`startup_unverified` exits
2. The state file retains the health report; collect a packet before investigating.
The CLI does not wait for a background POST when its observation deadline expires. A timed-out POST may still complete server-side. Never create a replacement lobby
merely because the CLI timed out.

## Observe and investigate

```sh
scripts/smacx-ops --url PORTAL --session-file SESSION --json health MATCH_ID
scripts/smacx-ops --url PORTAL --session-file SESSION --json inspect MATCH_ID
scripts/smacx-ops --url PORTAL --session-file SESSION --json events MATCH_ID --cursor-file /private/mission/events.cursor
scripts/smacx-ops --url PORTAL --session-file SESSION packet MATCH_ID --output /private/mission/incident-UNIQUE
```

| Command | Fields to read |
| --- | --- |
| `create` | `match_id`, `roster_verified`, `lobby.seats[].controllerKind`, `requestedFactionId`, `agentId` |
| `status` | `matchId`, `status`, `needsAttention`, `lastError` (portal camelCase) |
| `health` | `schema`, `installation_id`, `state`, `reasons`, `turn`, `workers`, `incidents`, `runs`, `world_heads` (operator snake_case) |
| worker health entry | `health` is a string or null; `mcp` is nested; `startup_failure` is retained evidence, not necessarily a current failure |
| `bootstrap` stream | `phase`, `match_id`, `state_file`; detailed health is in the state file |
| `events` | `events`, `next_cursor`; use event IDs to distinguish replay from duplicate execution |

MCP startup evidence includes exit/OOM state, image ID, last five health-check
results and bounded sanitized logs, retained before cleanup. Missing evidence
has `capture_complete: false` and `capture_errors`. An unhealthy probe is not
necessarily a crash; an isolated test passing does not prove an environmental
cause. This evidence is diagnostic and cannot authorize recovery.

On an actionable failure, use `pause MATCH_ID`, verify `containment_verified`,
collect the packet and open/update a GitHub issue. Remain paused pending authorized
repair. A healthy slow turn alone is not a deadlock. Review sovereign and specialist
activity, rejected actions, semantic progress, attention delivery and actual
mechanical effects. See [the full operator reference](operator-tools.md) for
recovery and evidence semantics. Only use `resume` through its supported verified
checkpoint/incident path. Use `park` for ordinary save-and-stop.

## Hermes-specific execution

Hermes's tool protocol is not Codex's protocol. Describe the available tool before
calling it. Call directly exposed tools directly. If a tool is deferred, Hermes's
`tool_call` wrapper requires `{"name":"EXACT_DISCOVERED_NAME","arguments":{...}}`.
A bare arguments object produces `tool_call requires a 'name' argument` and does
not execute the requested command. Do not keep retrying that malformed shape.

Inspect the actual terminal result before indexing it. The observed terminal
results contain `output` and `exit_code`; a long command can return a process ID
for polling. Use the exact fields declared by your installed tool. A missing
`content` field is an adapter assumption error, not a harness failure.

Hermes on this host supports:

```sh
hermes cron status
hermes cron list
hermes cron create 'every 10m' 'Read /ABSOLUTE/private/mission/startup.json and its operator instructions. Check the bound campaign through smacx-ops. Preserve evidence and pause/file an issue on an actionable failure. Do not start another campaign or sovereign.' --name 'SMACX campaign operator'
hermes cron doctor
hermes cron runs JOB_ID
```

Create only one job, after startup, if the owner has authorized ongoing monitoring.
Verify it exists, the scheduler is running and an actual attempt completes; a job
entry alone proves no wakeup. Check the resolved provider/model follows the owner's
selection. Do not silently change model configuration. Scheduled executions must
reload the durable mission; do not assume they inherit the interactive thread.
Use five minutes during uncertain startup, ten while healthy. Pause the job when
holding an unresolved incident or when the owner stops the mission. `watch` only
streams journal events; it is neither model reasoning nor a wakeup scheduler.

Before declaring readiness, prove one native startup using this exact operator
path. Before declaring a repaired gameplay capability complete, prove its
appropriate chain: observed → represented → calculated → provider-queryable →
sovereign-expressible → executable → effect verified → recovery-safe.
