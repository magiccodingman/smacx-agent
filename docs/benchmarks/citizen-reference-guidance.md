# Citizen and focused-reference guidance (2026-09-08)

The observed sovereign confused a Talent's happiness category with its worker
assignment and searched unrelated management families for an unfamiliar artifact
action before consulting the rules. This checkpoint addresses only those two
clarifications; it adds no elapsed-time UI or reconsideration policy.

Citizen-choice context now explicitly distinguishes happiness from assignment,
explains that Talents/Drones can work tiles, and states that converting a worker
to a specialist removes its tile yields. Existing observed population, worked
tiles, yields and specialist lists remain unchanged. No per-citizen happiness
mapping or additional happiness counts are inferred. The diagnostic summary
retains the explanation. The guarded convert → fresh query → assign workflow
is unchanged.

The operational reference guidance adds a focused lookup when an unfamiliar
mechanic or missing prerequisite blocks the next decision, before searching
unrelated action families. It explicitly requires checking current actions:
reference knowledge does not establish present legality. Existing doctrine and
strategic ownership are unchanged; the operational text grows by 258 bytes.

## Acceptance

- Citizen delivery test passed in the MCP container environment: actual managed
  `smac_choices` output contains the clarification and existing tile evidence;
  diagnostic summary retains it; guarded two-step reassignment and single-use
  choices still pass. This is native-shaped adapter evidence, not new gameplay.
- Strict prompt test passes: guidance is present, provider system message uses
  the exact composed bytes, stale duplicate system rows are replaced without
  altering history, and integrity failures remain closed.
- Doctrine integration passes: native-shaped context, exact persisted reuse,
  explicit recompilation guard and context headroom remain intact. Fixture
  composed prompt is 46,131 bytes.

Deployment checks are recorded after rollout. New profiles receive the new
operational guidance; saved profiles retain their frozen prompt and require
explicit recompilation to change it. No campaign is resumed or started here.
