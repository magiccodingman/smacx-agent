# Persistent unit directives

## What changes

The sovereign chooses a goal once; deterministic execution performs routine
steps until the goal is complete or a decision is needed. This does not give a
second model control of the faction. Plans remain cognition, directives are
explicit authorizations, and each native action is still fresh and guarded.

A normal turn is now: review strategic changes and operational status, assign
or revise several objectives, then select **Advance approved unit directives**.
The executor takes multiple mechanical steps without a provider call between
them. The model may instead select any ordinary tactical choice.

Directives survive faction turns and process restarts. Execution does **not**
run in the background: each slice is explicitly approved in a serialized
sovereign gameplay episode. One turn-level review remains. An approval to close
the current turn never authorizes another turn, skips unrelated ready units, or
overrides current-turn intent, briefing, critical-attention, or handoff gates.

## Available objectives

| Kind | Required fields beyond `kind` and `actor_ref` | Behavior |
| --- | --- | --- |
| `travel` | `target_ref` | Reach an exact known location or current base. Verify arrival. |
| `explore` | Exactly one `origin_ref` or issued `scope_ref` | Explore a frozen geometric area around the origin/frontier/region/scope. Optional `radius` and compass `direction`. |
| `work` | `target_ref`, `work` | Travel, start the specified Former job, and verify its observed effect. Never substitute another improvement. |
| `follow` | `target_ref` | Follow a current owned unit or visible foreign contact. Optional `follow_distance` (0–4). Loss of foreign contact pauses; no hidden tracking. |
| `escort` | `target_ref`, `escort_ref` | Rendezvous a protected actor and an owned combat escort, then alternate movement so the protected actor follows the guard's square. Wait for the slower participant. |

Actors must be unboarded land or sea units. Escort participants must have the
same triad. Aircraft, automatic boarding/transports, combat missions, founding,
and arbitrary task scripts are not accepted as directives in this version.
Their existing sovereign-controlled tools remain unchanged. Following is not
an authorization to board, attack, or enter an occupied foreign square.

Supported Former jobs are `farm`, `mine`, `solar_collector`, `forest`, `road`,
`sensor`, and `remove_fungus`. Actual native legality is checked at the work
site; an unavailable job returns control rather than choosing a substitute.

## Model-facing workflow

`smac_directives(action="prepare", request_json="…")` validates a bounded typed
request and returns a preview plus a single opaque approval choice. Preparation
may reconcile previously observed execution evidence, but never assigns a new
directive or dispatches its actions. Approve with the ordinary
`smac_execute_choice(decision_id, choice_id)`.

A batch assignment can contain up to 16 objectives:

```json
{
  "action": "assign",
  "directives": [
    {"kind": "travel", "actor_ref": "<current owned unit>",
     "target_ref": "<selected base>", "purpose": "Reinforce the northern corridor"},
    {"kind": "explore", "actor_ref": "<current scout>",
     "scope_ref": "<issued frontier>", "radius": 3, "direction": "NW",
     "purpose": "Find northern expansion access"},
    {"kind": "work", "actor_ref": "<current Former>",
     "target_ref": "<selected location>", "work": "road"}
  ]
}
```

Use real perspective-local references, not the illustrative placeholders.
Optional `linked_plan_id` must name an active canonical journal plan. Completing
or abandoning that plan invalidates continued directive execution; prose in a
plan never creates a directive by itself.

Each actor has one live assignment, including escort reservations. Invalid or
conflicting batches assign nothing. `replace_existing: true` explicitly
supersedes previous assignments, but cannot replace an unresolved native action.

The default decision frame includes operational counts, bounded directive
status, unassigned ready actors, and an **Advance** choice. Selecting that choice
explicitly permits finishing **this turn** when ordinary gates permit it.
For a different slice budget, prepare:

```json
{"action": "advance", "max_steps": 64, "end_turn": true}
```

`max_steps` is 1–128; execution also yields after a 30-second mechanical slice.
A budget yield does not cancel the objectives. `end_turn: false` additionally
prevents the last ready-unit action because native preferences can themselves
advance the turn. There is no automatic multi-turn quiet-play authority.

Controls use `{"action":"pause|cancel|resume","directive_ids":["<id>"]}`.
Resume can include `review_after_turns`. Retarget or change policy with an
explicit replacement assignment. An ordinary manual unit choice first pauses
that unit's directive, so automation cannot pull a retreating unit back onto
its old route. A pending native continuation must be resolved first; explicit
cancellation can abandon an uncertain attempt only from a settled world,
without claiming the action failed or authorizing a replay.

Read progress using `smac_directives(action="status")`, optionally with one
`directive_id`, or paginated `offset` / `limit`. Mechanical progress lives in
the journal, not repetitive sovereign memory summaries.

## Policy and boundaries

Defaults are `threat_radius=2`, `foreign_territory="pact"`,
`detour_budget_turns=3`, `max_idle_turns=3`, and
`interrupt_on_new_contact=true`. Overrides are strictly validated. Every
directive has a review horizon, default 10 turns, adjustable from 1 to 100.

The threat radius is a geometric interruption rule, not a tactical guarantee
against every enemy's movement range. Known occupation, zones of control,
unauthorized ownership, pods, monoliths, and consequential native choices are
excluded independently. New local contact or base evidence, damage, changed
target ownership, missing participants, or an exceeded policy/review budget
pause execution. New base evidence is not reported as proof of construction.

Exploration geometry is explicitly authorized and frozen at assignment. A
radius expands an issued scope's known anchors by geometric distance; it does
not import secret terrain. The preview reports its authorized square count.
Only a freshly enumerated adjacent native step can enter unknown territory.
Known-route transit may approach another frontier, but newly explored squares
must stay inside the approved geometry. An unavailable unknown step can be
replaced by another currently legal scoped adjacent step, never by guessing a
hidden reason. Bounded frontier-search failure is not called full exploration.
The settlement-only through-fog entitlement is not used here.

Routine movement, exhausted movement points, and an active specified Former
job do not require new strategic decisions. Completion and interruption notices
use a journal-backed outbox and existing at-least-once attention delivery.
The operational dashboard remains bounded; counts and a detail-query handle
survive item omission. Consumed directive approval menus leave provider replay
just like consumed native choices, without changing durable conversation history.

## Native execution and failure handling

This version uses supervised adjacent movement for both single-player and
multiplayer. It does not broaden native multiplayer allowlists or rely on an
unverified persistent-order interruption guarantee. Every step follows:

**observe → validate objective/policy → enumerate native choices → journal
attempt → execute one opaque choice → reconcile its observed outcome.**

The executor uses the existing topology, movement mechanics, identity resolver,
native action ledger, mutation gate, and reviewed notification continuation.
No stale opaque choice or native unit-array index is persisted as future work.
A bounded stale-state retry retires the old attempt, re-observes, and repeats
policy checks; it never lets ordinary opaque-choice rebasing bypass a new threat.

| Event | Outcome |
| --- | --- |
| Previously unseen danger is revealed by a step | Observe the revealing step's outcome, then stop before another step. No retroactive safety promise. |
| Combat, diplomacy, Artifact or other choice-bearing modal | Return the current interaction to the sovereign; keep the original pending action linked. Never issue a second unit action. |
| Reviewed informational notice | Existing bounded whitelist may capture and dismiss it; revalidate before continuing. One-button shape alone is insufficient. |
| Rejected move, failed work, invalid access, or no progress | Bounded revision recovery where definitely undispatched; otherwise pause with observed evidence. No endless retry or strategic retargeting. |
| Manual order | Pause the affected directive before dispatch. |
| Actor destroyed or contact lost | Fail on confirmed owned loss, pause on missing/stale evidence or lost foreign contact. Never silently recruit a replacement. |
| Response lost after dispatch | Recover the journaled attempt, query its native receipt when available, and verify the actual postcondition. Never blindly replay. |
| Old native receipt retired | Use a current verified postcondition if available; otherwise retain uncertainty and request review. |
| Journal write fails before dispatch | Do not dispatch. Failure after an attempted mutation stops further execution without inventing its outcome. |
| Checkpoint rollback | Replay directive state from the same canonical timeline prefix. Future progress does not survive the rollback. |
| Process restart / epoch change | Rebuild transient routes and reconcile pending evidence. A changed epoch requires explicit review, not silent rebinding. |
| Current-turn intent or critical attention requires review | Yield without treating a definitely undispatched global gate as failure of the unit's goal. |

## Implementation and validation

`smacx_directives.py` owns typed contracts, canonical state, and deterministic
planning. `smacx_directive_runtime.py` owns the synchronous executor and guarded
MCP adapter. `smacx_directive_api.py` owns preparation, opaque approval, manual
override, and operational presentation. The journal, MCP, runtime context,
prompt/continuation policy, gameplay tool list, and packaged control image wire
these into normal play. Native bridge source is unchanged.

After installing the same `mcp==2.0.0` dependency used by the control image:

```sh
python scripts/unit_directive_validation.py
```

This runs three new suites plus affected regressions and writes individual logs
and `summary.json` under `runtime/directive-validation`. `--quick` runs only new
suites; `--output PATH` chooses a different results directory. Tests use real
journal, world, attention, MCP cache and command adapters against controlled
native fixtures, plus production context functions. They do not start a game
or call a model. See [acceptance evidence](benchmarks/unit-directives.md).

Live campaign performance, strategic quality, interrupted-dialog effects in the
actual game, and two-client synchronization remain gameplay acceptance work.
No campaign, installed stack, timer, or engine policy is changed by this feature PR.
