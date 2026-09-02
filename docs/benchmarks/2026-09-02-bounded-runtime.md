# Bounded agent runtime: no-timer smoke test

Date: 2026-09-02
Tested code: `89081385ac34` (`Make parking durable and surface native rule constraints`)

This report evaluates runtime correctness, not strategy strength. The model ran
one isolated Alien Crossfire game on a Tiny map at Citizen difficulty against
one native bot. The native multiplayer turn clock was **None**, so elapsed time
could not advance a turn on the agent's behalf. The managed seat used the local
Qwen3.8-27B low-reasoning profile, Graphiti was enabled, and all game actions
used the semantic MCP surface without screenshots, pointer input, or keyboard
input.

## Pass criteria

- Advance multiple native turns through journaled agent actions rather than a
  turn timer.
- Execute only opaque choices enumerated for the current native revision.
- Preserve reasoning inside the active `think -> tool -> result` episode, then
  retain a bounded ordinary `TURN HANDOFF` instead of old raw reasoning.
- Produce no unresolved capability incident, stale-revision failure,
  repetition circuit, malformed tool call, or tool-result error.
- Write valid hash-linked campaign-journal chains and verified checkpoints.
- Park the match durably after the test.

## Result

The smoke test passed. The run reached turn 8 / Mission Year 2108 and parked
cleanly.

| Measure | Clean run |
|---|---:|
| Causally advanced action-turns | 4 |
| Journaled native actions | 9 |
| Native accepted outcomes | 9 |
| Journal events / valid hash chains | 23 / yes |
| Verified checkpoint events | 3 |
| Malformed tool-call records | 0 |
| Exact repeated tool-call pairs | 0 |
| Longest exact tool-call run | 1 |
| Automatic stale rebases | 0 |
| Repetition circuits | 0 |
| Supervision incidents | 0 |
| Tool-result errors | 0 |
| `TURN HANDOFF` messages within 120 words | 4 of 4 |
| Median observed turn advance | 39.655 s |
| Median successful portal turn duration | 36.027 s |

The model made 30 provider calls and used 533,129 input, 8,220 output, and
4,844 reasoning tokens over the audited Hermes session. These are cumulative
provider token totals, not the model's instantaneous context-window occupancy.
Graphiti remained healthy; scoped recall completed in approximately 195 ms and
returned no facts, which is expected because this short opening contained no
projectable political or relationship events.

## Directional comparison

The earlier no-timer baseline reached turn 12 through 12 causally advanced
action-turns. It traversed different native events and ran longer, so this is a
directional regression comparison rather than a controlled performance trial.
Normalizing by causally advanced action-turn gives a useful signal:

| Measure per action-turn | Baseline | Bounded runtime | Change |
|---|---:|---:|---:|
| Hermes input tokens | 470,146 | 133,282 | 71.6% lower |
| Portal input tokens | 397,141 | 111,178 | 72.0% lower |
| Hermes API calls | 8.33 | 7.50 | 10.0% lower |
| Handoff contract compliance | 0 of 3 | 4 of 4 | passed |

The baseline itself had no malformed calls or repetition circuit. The measured
gain therefore comes primarily from bounded state presentation, opaque-choice
execution, episode compaction, and concise durable handoffs—not from merely
terminating an obvious error loop.

## Defects exposed and fixed during the run

The no-timer simulations also exercised failure paths that timed games had
previously obscured:

- Direct parking now records a durable maintenance operation before returning
  to the caller. Portal restart or a slow checkpoint cannot strand a match in
  an ambiguous half-parked state.
- Native rule advisories are visible separately from executable choices. A
  Colony Pod blocked by minimum base spacing now relocates instead of reporting
  a false capability gap.
- The Alien Crossfire `FIRSTBASE` modal is recognized before research selection
  has initialized, allowing a fresh alien-faction game to name its first base
  and continue.

## Reproduction

Run a test-owned managed match with the native time control set to `0` / None,
then park it before collecting the reports. Use paths from that test instance;
do not copy provider secrets, raw conversations, or game files into the
repository.

```bash
python3 scripts/agent_simulation_report.py \
  --campaign-root /path/to/control/campaigns \
  --portal-db /path/to/portal.sqlite3 \
  --match-id match-... \
  --output docs/benchmarks/results/<date>-<label>.json

python3 scripts/hermes_session_audit.py \
  --database /path/to/hermes/profile/state.db \
  --output docs/benchmarks/results/<date>-<label>-hermes.json
```

The committed evidence is deliberately content-free:

- [clean causal report](results/2026-09-02-no-timer-clean-final.json)
- [clean Hermes audit](results/2026-09-02-no-timer-clean-final-hermes.json)
- [baseline causal report](results/2026-09-02-no-timer-baseline.json)
- [baseline Hermes audit](results/2026-09-02-no-timer-baseline-hermes.json)

The reports contain aggregates only. They contain no prompts, responses,
reasoning text, tool arguments, provider endpoints, credentials, chat, saves,
screenshots, or game assets.
