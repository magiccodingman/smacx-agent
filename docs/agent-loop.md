# Safe semantic play loop

SMACX Agent uses an explicit state machine and optimistic concurrency. The model does not send a stream of keys or clicks.

Every sovereign provider call also receives one request-only runtime envelope.
It supplies the current world anchor and net delta, immediate focus, leased
attention, active operation summary, watches, and projected durable cognition.
It is appended to the current request tail after semantic garbage collection;
it is not a transcript message and never accumulates as historical world
snapshots. The anchor is peripheral awareness. Use `smac_world` to zoom into a
decision-relevant area, route, relationship, base, force, logistical problem,
change, or global system, then reuse that evidence while its dependency and
world metadata remain valid.

For a specific mechanical question, `smac_world(mode="counterfactual")` can
preview nominated sites or current production, upgrade, Social Engineering and
Former choices, or compose preparation and travel for a requested capability.
These calculations stay outside routine context. They do not consume the
choice, create an operation, select strategy or revise a plan. The sovereign
reviews the assumptions and unknowns, then executes through the same guarded
opaque choice and verifies the effect. Stale or unavailable evidence requires
a fresh observation; it cannot be repaired by treating a projection as fact.

Before this loop is available in a new match, the player calls
`smac_match_briefing(action="read")`. The versioned configuration contract
combines the exact faction, difficulty, generated map, victory toggles,
advanced rules, multiplayer clock, scenario restrictions, match policy, and
game-artifact fingerprint. The player can investigate unfamiliar non-default
mechanics through `smac_investigate(faculty="reference")`, then acknowledges the
exact returned hash.
`smac_decision` returns no choices and `smac_execute_choice` rejects mutation until
that configuration is acknowledged.

The hash deliberately excludes resources, bases, units, ready-unit count,
turn/year, diplomacy, match lifecycle, reference-catalog prose, and other
runtime state. Those facts belong in fresh decision/event surfaces. A changed
rule, faction/seat, scenario, policy, ruleset, or executable fingerprint
produces a field-path delta and relocks mutation. Recovering the same
configuration into a new process keeps the durable acknowledgement and emits
one compact resume notice; the rotated `session_id` and `revision` still reject
every stale engine command. The opening read is complete, while acknowledgement
returns only its hash/status instead of echoing the contract.

The three guard values have different lifetimes:

| Value | Meaning | When it changes | What an old value prevents |
| --- | --- | --- | --- |
| `match_id` | Durable identity of one playthrough and its knowledge/save namespace | Only when starting a genuinely new match | Acting on or writing notes into the wrong game |
| `session_id` | Identity of one running game process | Every fresh launch or reload | Reusing engine objects or commands from a prior process; it does not by itself invalidate an unchanged configuration acknowledgement |
| `revision` | Fingerprint of the current fair-play decision state | Whenever action-relevant public/owned state changes | Replaying a choice after the board, modal, resources, or turn phase changed |

Native object IDs are private session-local implementation details, not provider knowledge keys. A snapshot's `ready_unit_refs` provides current actionable `own_unit_ref` values, names, semantic locations, and compact roles at that exact revision. Managed choice queries accept only semantic `own_unit_ref`, `base_ref`, `target_location_ref`, and `target_unit_ref` values from the active perspective. Never derive a native selector from a ref's spelling. Obtain fresh world refs and choices after every mutation that can create, consume, capture, reorder, or destroy an engine object.

The preferred loop is intentionally small. `smac_decision` assembles one
revision-stable focus and returns short semantic labels plus opaque
`choice_id` values. Native command names, confirmation flags, and raw revision
guards stay server-side. Execute at most one returned choice through
`smac_execute_choice(decision_id, choice_id)`, discard the frame, and call
`smac_decision` again. `smac_choices` remains available for bounded detailed
choice-family queries, but it also returns opaque executable choices.

The managed loop is:

1. Call `smac_decision`.
2. If it requires briefing, wait, or capability-gap handling, follow only that
   directive.
3. Otherwise inspect the single focus and its currently legal choices.
4. Execute exactly one opaque choice.
5. Discard both IDs and obtain a fresh decision.

Supplying an `own_unit_ref` is allowed only for a unit present in a fresh decision
frame; omission selects the first ready unit deterministically. After actually
considering the remaining units and deciding all are finished,
`finish_ready_units=true` switches that one frame to guarded game-management
choices. Combining it with `own_unit_ref` is rejected.

This is also the action-legality pipeline. The current interaction phase
selects the only usable choice family; the server binds each opaque ID to the
complete native payload and confirmation requirements; the identity/revision
guard makes the decision single-use; and the next observation verifies the
postcondition. Clients cannot invent native command strings or parameters.

The native bridge independently enforces that phase boundary. While any popup, research prompt, Council window, engine transition, or other non-turn state is active, every non-interaction mutation is rejected before its individual handler can run—even if a client fabricates a command with an otherwise fresh identity/revision guard.

An accepted native mutation is followed immediately by its canonical journal
append. If that durable write fails, the runtime reports that the native action
did occur, opens an operator-visible capability incident, and refuses every
later mutation. It never degrades an authoritative-history failure into a
warning and continues playing.

The game can advance between the snapshot and a later choice-family read, especially while the native AI turn is settling. If the family carries a different `revision` from the snapshot, discard the entire plan and restart at step 1. Likewise, `turn:end_blocked` is structured state—not a missing tool—and means that a fresh observation has ready units to resolve. Diagnose a capability gap only when the interaction is explicitly unsupported or a stable, same-revision state has no semantic choice for its required decision.

`stale_state` and revision churn are concurrency signals, never capability
gaps. The MCP may transparently re-enumerate and execute one harmless unit
state command when that exact command is still present in a fresh choice
family. Otherwise wait briefly and obtain a new decision frame. Capability-gap
reporting mechanically refuses revision-conflict reports so a settling native
session cannot permanently latch an otherwise playable campaign.

Every submitted popup action enters an exact native-window transition. During that handoff the snapshot reports `waiting_for_engine`, interaction enumeration rejects with `popup_transition_pending`, and replaying the old choice is rejected as `stale_state`, `popup_transition_pending`, or `popup_unavailable` after the exact object closes. Never acknowledge the same label twice. Some native sequences close a `BasePop` and immediately open a different modal class while leaving the old script label cached; the bridge tracks the exact window object and will expose the new semantic interaction when it is ready.

A capability-gap report is audited against the exact session and creates a
process-lifetime development latch with no agent-callable clear action.
Observation and memory remain available for diagnosis; opaque execution is
blocked. After implementing and deploying coverage, an owner or administrator
uses **Retry from verified checkpoint**. The control plane parks the preserved
process, refreshes every managed seat onto the current immutable
worker/bridge/MCP images, loads the verified save into a fresh `session_id`,
restores this match's Hermes conversation and journal timeline to that same
boundary, and rebuilds its Graphiti namespace before launching the agent. Only
then is the incident marked recovered. A failed retry remains latched.

Movement, diplomacy, Council, and probe choices may cross the native event
loop. The bound native operation receives an `action_id`; opaque execution
normally waits and reports `completed: true`. If the bounded wait expires, do
not overlap an ordinary turn action. Resolve each newly enumerated staged
interaction, then observe until the original deferred action completes.

End-turn uses the same deferred ledger even though the stock game implements
it as one long synchronous call. The bridge replies after queueing the native
Turn Complete command and starts that command from a Windows timer only after
the request frame has unwound. Any confirmation or technology presentation in
the transition therefore becomes the current required interaction rather than
starving the bridge. `smac_wait` propagates a lost bridge as an error; it never
wraps an unavailable observation in `ok: true`.

When any semantic result reports `turn_handoff_required`, native control has
passed. This includes a direct end-turn result and the next decision frame when
native automation moved from one turn into the next without exposing a stable
wait phase. No legal choices are returned across that boundary. Make no more
tool calls in that Hermes episode. The player emits one assistant message
headed `TURN HANDOFF`, under 120 words, with `Outcome`,
`Rationale`, `Changed conclusions`, `Next intent`, and `Uncertainty`. Hermes retains and
later compresses this durable decision summary. Raw interleaved reasoning is
kept throughout the active episode but stripped after the next user/episode
boundary. Completed tool protocol from earlier episodes is likewise omitted
from later provider requests while remaining in durable diagnostics. Current
episode tool calls/results stay coherent, and only superseded state payloads
inside that episode are compacted. The supervisor treats the handoff as a clean
yield and resumes the same campaign conversation; it is not match completion.

The handoff preserves why consequential choices mattered and what the sovereign
now believes or intends. Do not copy ordinary unit locations, raw map slices,
or event lists into it. The campaign journal and world projection preserve
mechanical observation history. Durable cognition is for beliefs, suspicions,
strategic conclusions, relationships, commitments, goals, plans, named
territories/concepts, and unresolved strategic questions.

Alien Artifact entry is another staged movement interaction. `ARTIFACT` exposes `no_action`, technology linking, and—only when natively present—acceleration of the exact current Secret Project or unprototyped unit. The two consuming choices require `confirm_consume_artifact=1`. A link can open a following technology notice; acknowledge it and keep waiting until the original move reports `native_artifact_consumed`.

The bridge rechecks ownership, turn phase, readiness, adjacency, buildability, research rules, and popup identity at execution time. It rejects:

- a command for another running process (`wrong_game_identity`);
- a command based on changed state (`stale_state`);
- an action outside the current phase (`not_actionable`);
- an object the player does not own or an option that is no longer legal.
- a second potentially blocking action while a deferred action is pending.

Native persistent orders are also state-machine states. A patrol, go-to, Road To, Mag Tube To, bombing run, terraform job, hold, or sentry order does not count as an unresolved ready unit merely because movement points remain internally. Its fresh `unit_actions` exposes `activate_unit` as the explicit cancellation path and suppresses unrelated actions until cancellation or native completion. Bombing-run targets are exact currently visible Vendetta bases returned by opaque tile ID and are revalidated against a conservative round-trip fuel bound.

The end-turn gate is enforced twice. `game_management` returns a non-executable `turn:end_blocked` status with `ready_unit_count` while any unit still requires a decision, and omits the `end_turn` command. When the model has deliberately decided that every listed unit is finished, that same fresh family offers `skip_all_ready_units` with the exact IDs/count and `confirm_skip_all_ready=1`. It is the only batch mutation: it can apply native skip only, prevalidates the complete set, and may immediately start the next turn under native auto-end preferences. Never use it merely to make a frame shorter or while any unit still needs tactical consideration. If a client fabricates `end_turn` anyway, execution rejects it with `units_still_ready` and does not advance the turn. Never synthesize either command from its name. Native automation can still make SMACX display `REALLYOVER`; resolve only its fresh `respond_to_end_turn_confirmation` choice. After proceeding, `turn:transition_pending` means wait—never submit another end-turn or unit command until a new turn or a fresh mandatory interaction arrives. A newly enumerated interaction supersedes that wait and must be resolved before the engine can finish the transition.

Base-status and production-completion popups are observations followed by one acknowledgement. Read their structured context first (event, owned base identity when available, completed item, and governor/queue metadata), acknowledge through the returned interaction choice, then re-list the affected base. Do not infer the post-notice state from the popup alone.

The choice families also encode local prerequisites. For example, `base_citizens` will not expose manual assignment commands while the governor manages citizens, held/sentry units expose `activate_unit` instead of movement, and boarded sea passengers expose only `remain_boarded` or legal disembarkation targets. Use `target_location_ref` for exact route, patrol, Road To, missile, artillery, terrain, and Drop targeting; `base_ref` for owned-base selection; and `target_unit_ref` for a current owned carrier or mechanically current foreign contact. The server resolves and revalidates private native selectors only after checking the active match, perspective, timeline, epoch, and revision. A fuel-limited aircraft route additionally reports `air_recovery` for a friendly base/standalone airbase or `air_round_trip` for a safe non-refueling sortie; an absent route is not permission to retry through generic `go_to`. Carrier recovery reserves capacity and holds the carrier until arrival/cancellation. Probe missions are bundled only for visible adjacent non-Pact bases and require the returned opaque incident-confirmed choice. Air Drop enumeration is bounded to 128 mapped legal destinations, while any different mapped semantic location—or a paginated opaque unmapped location obtained through `smac_world(mode="area", origin_ref="world-map")`—may be submitted for one exact guarded native receipt. A rejected exact target reveals no hidden reason. Artillery remains limited to visible non-Pact stacks and requires immediate re-observation because combat can compact native rows. Every missile launch consumes the unit, so obtain fresh refs. Unit-design component IDs remain catalog values rather than world-object selectors.

Non-executable `capability_status` records may appear beside valid choices. They document a native option that the bridge deliberately withholds because its continuation is not yet safe. Never submit their IDs or infer a command from their names. Do not invent a proposal-menu `offer_units` action: reverse engineering confirmed that its text is an unused `Script.txt` row which the executable never inserts into `PROPOSAL`; native ID 12 is used by the separate Council-vote request path. Direct unit transfer is available only as a confirmation-gated `give_unit` tuple on an eligible unit standing in visible Pact territory.

Loan offers are guarded interaction states, not free-form bargaining. Review their structured direction, principal, per-turn payment, term, scheduled total, and affordability; then use only a returned accept, reject, or half-principal counteroffer. Re-list factions afterward to observe the native loan ledger rather than carrying a guessed balance forward.

An AI response to a proposed joint attack can demand an exact energy price or a complete bundle of one to four technologies. The active `MAYBEWARPRICE`/`MAYBEWARTECH*` interaction exposes the counterpart, target, payment, ownership, and affordability together. Reject or accept only that fresh tuple; acceptance lets the native continuation transfer payment and declare the requested Vendetta, after which its outcome notice must be acknowledged before re-observation.

Council vote commerce has two directions. When asking another leader for a commitment, use only a returned `respond_to_council_vote_bargain` payment tuple. When an AI candidate offers to buy the player's Governor ballot, `VOTEFORME` names an energy payment and `VOTEFORMETECH` names exactly two technologies. Rejection is unconstrained; acceptance requires the fresh candidate plus `confirm_vote_commitment=1`. Re-observe the Council and owned energy/technologies after responding rather than assuming the outer Council window has completed its continuation.

Technology commerce is likewise exact. Review the named technology, category, price, counterpart, and affordability returned by the active interaction. For a sale, an alternate technology is legal only when it appears in that same fresh tuple. After acceptance, re-list technologies and snapshot energy; never infer that the transfer succeeded merely because a popup button was submitted.

Territorial and hostility interactions are guarded continuations of the action that opened them. `GETOUT1` through `GETOUT5` expose withdraw, mutual withdrawal, and explicit refusal; `THISLANDISMYLAND` exposes cancel or proceed; treaty/truce/Vendetta attack warnings expose cancel or declare Vendetta. Refusal, proceed, and declaration require the separately returned `confirm_hostility=1`. Never issue a second movement command while the original `action_id` is pending. Cancelling rejects that deferred action; confirmed combat may complete while the attacker remains on its origin tile, so trust `execution.status` and `resolution`, then re-list units and factions.

Combat-readiness/odds confirmations are the same kind of continuation. `HASTY` reports that the unit would attack now at a displayed fraction of full strength; `BADIDEA` means the native evaluator considers the odds strongly unfavorable; `GOODIDEA` variants carry the displayed odds when available. Choose cancel, or copy the returned proceed tuple including `confirm_attack=1`, then keep resolving any later hostility or odds stage while waiting on the original action ledger rather than issuing another move.

A Nerve Gas Pod attack can then open `USENERVE`. The safe `conventional` response continues the already-pending attack without chemical weapons. The `commit` response is exposed only with `confirm_atrocity=1`; omitting that independent confirmation leaves the native dialog open. Keep tracking the original movement `action_id`, resolve any resulting atrocity notices semantically, and then re-observe faction diplomacy and the attacker rather than assuming the effect.

Outside a base, a fresh ready-unit family can expose `self_destruct_unit`. Its context lists only owned or currently visible units in the native one-tile blast radius; fog remains fog, so unseen collateral is intentionally not disclosed. Review the blast damage and known lethal outcomes, then copy `confirm_self_destruct=1` only for a deliberate overload. The source always dies, non-base stacks receive native damage, and several IDs may shift; discard the entire frame and re-list units.

A Supreme Leader vote can later open `ACCEDE` or `ACCEDECOOP`; use only the returned accession or confirmation-gated defiance choice. The production victory stack exposes each passive `victory_interlude`, `credits`, `score_report`, `quayle_rating`, `hall_of_fame`, and `replay` window as `endgame_presentation`; copy its exact `phase` into one `advance_endgame_presentation`, then observe again. The bridge calls the native window's close callback and rejects every strategic/unit mutation during these phases. Final scoring then opens `GAMEOVERMAN`; explicitly choose finish or continue. Neither final choice is a generic acknowledgement, and continuing means the match remains completed even though the native engine permits additional turns. Autonomous launches default `narrative_ui=false`, which also disables optional external victory movies; enabling narrative UI may require visual/manual handling for an external movie player and is not claimed as semantic coverage.

Economic victory is initiated only from a fresh `game_management` family. Review the returned aggregate cost, available credits, Headquarters, and countdown; copy `confirm_corner_market=1` only after deliberately choosing to commit. A successful command opens the information-only `CORNERING` notice. Acknowledge it once, then use fresh `game_management` state to track the plan rather than carrying the old quote forward.

`match_id` is the durable namespace for one playthrough. `session_id` changes whenever the game process is relaunched. The managed control store owns match identity, lifecycle history, scoped knowledge, and save metadata. Standalone MCP launches may also maintain a compatibility `match.json` below their configured knowledge root. Named saves remain scoped to the match. Loading preserves `match_id`, creates a new `session_id`, and invalidates every old guard and engine object ID.

Managed checkpoint recovery also changes the private memory `timeline_id`.
Hermes history, journal-backed search/recall/chat/notebook state, and derived
Graphiti memory are restored together, so an action abandoned with the old
native process cannot remain as an AI memory in the recovered world.

Record durable conclusions through typed `smac_memory_update` records or named
`smac_notebook` entries, not free-form cross-game memory. Mechanical observed
history already belongs to the world projection and campaign journal; do not
duplicate raw map frames or ordinary unit positions merely to retain them.
Stable keys create canonical revisions with turn/year provenance. Managed reads
remain scoped to the active match, seat, perspective, and timeline so another
playthrough's intelligence cannot leak into the current one.

Save/load, process stop, checkpoint recovery, and worker replacement are
authenticated Control Center operations. They are deliberately absent from a
managed player's MCP surface.

The spectator window is output only. Screenshots, coordinates, mouse input, keyboard input, and raw UI text entry are not MCP capabilities.

Managed sidecar startup reconciles an already active native world before opening
its runtime/MCP listeners, so cold collection does not consume the first provider
request's HTTP timeout. Background observation starts afterward. A lobby can
remain available without an active world; every gameplay runtime request still
requires successful current reconciliation. Startup grace is separate from the
unchanged native/UI request responsiveness limits.
