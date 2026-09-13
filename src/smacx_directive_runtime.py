"""Synchronous directive execution inside the existing serialized gameplay lease.

No worker thread, second sovereign, generic command interface or blind replay.
The port deliberately separates policy/state from the native/MCP integration.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import time
from typing import Any, Mapping, Protocol
import uuid

from smacx_directives import (DirectiveError, DirectivePlanner, DirectiveStore, DirectiveWorld,
                             LIVE, RUNNABLE, Step, field_value, field_is_current, summary, transition)
from smacx_intent import may_close_turn
from smacx_world_types import content_hash


@dataclass
class NativeOptions:
    choices: list[dict]
    selectors: dict
    context: dict


class DirectivePort(Protocol):
    """Only this port may touch the game. Tests can replace it without an LLM."""
    def observe(self) -> DirectiveWorld: ...
    def gate(self, world: DirectiveWorld) -> dict | None: ...
    def options(self, world: DirectiveWorld, actor_ref: str = '') -> NativeOptions: ...
    def execute(self, world: DirectiveWorld, options: NativeOptions, choice: dict) -> dict: ...
    def action_status(self, action_id: int) -> dict: ...
    def interaction(self) -> dict: ...


def identity(world: DirectiveWorld) -> dict:
    return {key: world.snapshot.get(key, '') for key in ('match_id', 'session_id', 'revision')}


def _phase(world: DirectiveWorld) -> str:
    return str((world.snapshot.get('protocol') or {}).get('phase') or '')


def _native_pending(world: DirectiveWorld) -> bool:
    pending = world.snapshot.get('last_deferred_action') or {}
    return isinstance(pending, Mapping) and pending.get('status') == 'pending'


def match_step(world: DirectiveWorld, record: dict, step: Step, options: NativeOptions) -> dict:
    """Select exactly one freshly issued action, preserving all native guards."""
    target_tile = (step.position[0] + world.topology.shape.width * step.position[1]) // 2 if step.position else None
    candidates = []
    for choice in options.choices:
        if choice.get('command') != step.command:
            continue
        if step.command == 'move_unit':
            if target_tile is None or choice.get('target_tile_id') != target_tile:
                continue
            if choice.get('combat') or choice.get('may_initiate_combat_or_contact') or choice.get('boards_transport'):
                raise DirectiveError('next_move_requires_combat_contact_or_boarding_decision')
            if choice.get('consequential') or any(bool(v) for k, v in (choice.get('requires') or {}).items() if k.startswith('confirm_')):
                raise DirectiveError('next_move_requires_additional_confirmation')
            if set(choice.get('features') or ()) & {'supply_pod', 'monolith'}:
                raise DirectiveError('next_move_enters_consequential_site')
            if choice.get('known') is not True:
                if record['kind'] != 'explore' or list(step.position) not in record['scope_positions']:
                    raise DirectiveError('unknown_step_not_authorized')
            elif step.destination:
                actor = world.actor(step.actor_ref)
                square = world.topology.by_ref[step.destination]
                if not world.owner_allowed(square.owner_ref, field_value(actor, 'owner_ref'), record['policy']):
                    raise DirectiveError('next_step_access_not_authorized')
                threats = world.threats(step.destination, record['policy']['threat_radius']) if record['policy']['threat_radius'] else []
                if threats:
                    raise DirectiveError('next_step_enters_known_threat_radius')
            # `safe_local_move` belongs to the multiplayer catalog. An explicit
            # false value must never be promoted by the generic land planner.
            if 'safe_local_move' in choice and choice['safe_local_move'] is not True:
                raise DirectiveError('native_move_not_classified_safe')
        elif step.command == 'terraform':
            if choice.get('former_id') != step.former_id:
                continue
            if choice.get('energy_cost', 0) or choice.get('consequential') or choice.get('requires'):
                raise DirectiveError('work_requires_additional_consent')
        elif step.command != 'skip_unit':
            raise DirectiveError('unsupported_directive_step')
        candidates.append(choice)
    if len(candidates) != 1:
        raise DirectiveError('exact_directive_step_not_in_current_native_catalog')
    return candidates[0]


def select_step(world: DirectiveWorld, record: dict, step: Step, options: NativeOptions) -> tuple[Step, dict]:
    """A geometrically adjacent unknown square may have no legal native move.

    Try another scoped adjacent unknown only on that exact absence, never on a
    combat, contact, confirmation or risk rejection. No hidden reason is read
    or inferred from an omitted option.
    """
    try:
        return step, match_step(world, record, step, options)
    except DirectiveError as exc:
        if str(exc) != 'exact_directive_step_not_in_current_native_catalog' or record['kind'] != 'explore' or step.destination:
            raise
    origin = world.position(world.actor(step.actor_ref)['location_ref'])
    for position in world.topology.shape.neighbors(origin).values():
        if position == step.position or list(position) not in record['scope_positions']:
            continue
        known = world.topology.by_position.get(position)
        if known and known.terrain in {'land', 'ocean'}:
            continue
        alternative = Step(step.actor_ref, 'move_unit', position=position)
        try:
            return alternative, match_step(world, record, alternative, options)
        except DirectiveError as exc:
            if str(exc) != 'exact_directive_step_not_in_current_native_catalog':
                raise
    raise DirectiveError('no_legal_adjacent_exploration_step')


class DirectiveRuntime:
    def __init__(self, store: DirectiveStore, port: DirectivePort, attention=None):
        self.store, self.port, self.attention = store, port, attention

    def _save(self, records: list[dict], world: DirectiveWorld, reason: str) -> None:
        state = self.store.read()
        for r in records:
            current = state['records'].get(r['directive_id'], {})
            if current.get('notice') == r.get('notice') and current.get('delivered_notice'):
                r['delivered_notice'] = current['delivered_notice']
        changed = [r for r in records if r != state['records'].get(r['directive_id'])]
        if changed:
            self.store.commit(changed, expected_generation=state['generation'], world=world, reason=reason)

    def _notices(self) -> None:
        if self.attention is not None:
            self.store.flush_notices(self.attention)

    def _reconcile(self, world: DirectiveWorld) -> bool:
        """Resolve outstanding receipts before any next action, even if cancelled."""
        waiting = False
        updates = []
        for original in self.store.read()['records'].values():
            pending = original.get('pending')
            if not pending:
                continue
            r = deepcopy(original)
            # A restored epoch cannot lend native action IDs to a previous one.
            same_session = pending['session_id'] == world.session and r['epoch'] == world.epoch
            native = None
            if same_session and type(pending.get('action_id')) is int:
                status = self.port.action_status(pending['action_id'])
                native = status.get('action') if status.get('ok') else None
                if isinstance(native, Mapping) and native.get('status') == 'pending':
                    waiting = True
                    continue
                if not isinstance(native, Mapping):
                    native = None
                    if _native_pending(world):
                        waiting = True
                        continue
                    # Native ledgers are session-local and may retire old
                    # receipts. A stable observed postcondition can still
                    # resolve the durable attempt; absence is never failure.
            elif _native_pending(world):
                waiting = True
                continue
            if _phase(world) != 'turn':
                waiting = True
                continue
            actor = world.objects.get(pending['actor_ref'], {})
            command = pending['command']
            achieved = False
            if live_owned(actor):
                if command == 'move_unit' and actor.get('location_ref') in world.topology.by_ref:
                    achieved = list(world.position(actor['location_ref'])) == pending.get('target_position')
                elif command == 'skip_unit':
                    achieved = field_is_current(actor, 'ready') and field_value(actor, 'ready') is False or world.turn > pending['turn']
                elif command == 'terraform':
                    task = field_value(actor, 'terraform_task', {})
                    if field_is_current(actor, 'terraform_task') and isinstance(task, Mapping) \
                            and task.get('state') == 'active_terraform_order' and task.get('name') == pending.get('task_name'):
                        achieved = True
                        r.update(work_started=True, work_task_name=task['name'])
                    elif pending.get('target_ref') in world.objects:
                        from smacx_directives import WORK
                        tile = world.objects[pending['target_ref']]
                        features = field_value(tile, 'features', [])
                        feature = WORK[r['work']][1]
                        achieved = field_is_current(tile, 'features') and (
                            feature not in features if r['work'] == 'remove_fungus' else feature in features)
                        if achieved:
                            r['work_started'] = True
            definitive = bool(native and native.get('status') in {'completed', 'rejected'}) \
                or pending.get('definitely_not_dispatched', False)
            if achieved:
                r['pending'] = None
                if command != 'skip_unit':
                    r['last_progress_turn'] = world.turn
                r['steps'] += 1
                r['rejections'] = 0
                if actor.get('location_ref'):
                    r['last_positions'][pending['actor_ref']] = actor['location_ref']
                # Deliberately do not reset paused/cancelled directives or heal
                # a changed epoch merely because one endpoint was observed.
                if r['state'] in RUNNABLE:
                    r.update(state='active', reason='step_effect_verified')
            elif definitive:
                r['pending'] = None
                r['rejections'] += 1
                transition(r, 'paused', 'native_step_did_not_verify_objective_progress', world,
                           resolution=str((native or {}).get('resolution') or pending.get('error') or 'unknown'))
            else:
                # No fabricated exactly-once claim. An explicit cancellation
                # from a settled world may abandon this uncertainty; never retry.
                transition(r, 'paused', 'uncertain_action_outcome_review_required', world)
                waiting = True
            updates.append(r)
        self._save(updates, world, 'reconcile_native_directive_actions')
        return waiting

    def manual_override(self, actor_ref: str, world: DirectiveWorld) -> None:
        updates = []
        for original in self.store.read()['records'].values():
            if actor_ref not in original['actors'] or original['state'] not in LIVE:
                continue
            if original.get('pending'):
                raise DirectiveError('directive_action_pending_resolve_or_explicitly_cancel_first')
            r = deepcopy(original)
            transition(r, 'paused', 'manual_sovereign_override', world, actor_ref=actor_ref)
            updates.append(r)
        self._save(updates, world, 'manual_override')

    def run(self, prepared: Mapping, initial: DirectiveWorld, *, wall_seconds: float = 30.0) -> dict:
        """One explicitly yielded gameplay slice. Never cross its faction turn."""
        if prepared['turn'] != initial.turn or self.store.read()['generation'] != prepared['expected_generation']:
            raise DirectiveError('advance_authorization_changed_prepare_again')
        started = time.monotonic()
        executed = 0
        rejections = 0
        end_turn = bool(prepared['end_turn'])
        interrupted = ''
        world = initial
        passthrough = {}
        # Fair scheduling: each actor can receive one step before the next lap.
        # No policy or unit direction is chosen by a second model.
        last_scheduled = ''
        while executed < prepared['max_steps'] and time.monotonic() - started < wall_seconds:
            if executed or rejections:
                try:
                    world = self.port.observe()
                except DirectiveError as exc:
                    if str(exc) not in {'directive_observation_revision_changed', 'directive_world_revision_changed'}:
                        raise
                    interrupted = 'observation_revision_changed_reobserve_before_continuation'
                    break
            if world.timeline != initial.timeline or world.epoch != initial.epoch or world.session != initial.session:
                interrupted = 'world_identity_changed'
                break
            if world.turn != initial.turn:
                interrupted = 'turn_boundary'
                break
            gate = self.port.gate(world)
            if gate:
                passthrough = gate
                interrupted = 'gameplay_gate_requires_sovereign'
                break
            if _phase(world) != 'turn':
                # Existing whitelist may dismiss reviewed information, but an
                # interaction with choices is never selected here.
                frame = self.port.interaction()
                passthrough = {'post_action_decision': {'frame': frame}}
                if frame.get('turn_handoff_required'):
                    passthrough.update(turn_handoff_required=frame['turn_handoff_required'])
                    interrupted = 'turn_boundary'
                    break
                if frame.get('ok') and frame.get('focus', {}).get('kind') != 'interaction' \
                        and frame.get('automatic_notifications') and not frame.get('sleep'):
                    world = self.port.observe()
                    if _phase(world) == 'turn' and world.turn == initial.turn:
                        continue
                interrupted = 'native_interaction_or_engine_wait'
                break
            unresolved = self._reconcile(world)
            if unresolved:
                interrupted = 'outstanding_action_requires_resolution'
                break
            state = self.store.read()
            active = sorted((r for r in state['records'].values() if r['state'] in RUNNABLE), key=lambda r: r['directive_id'])
            # A linked plan is only a dependency, not implicit authorization.
            plans = self.store.journal.replay(self.store.scope, self.store.timeline_id, sections=('plans',)).get('plans', {})
            plan_states = {p.get('record', {}).get('plan_id'): p.get('record', {}).get('status', 'active') for p in plans.values()}
            if last_scheduled:
                active = [r for r in active if r['directive_id'] > last_scheduled] + [r for r in active if r['directive_id'] <= last_scheduled]
            candidate = None
            updates = []
            reservations = {r.get('frontier_target') for r in active if r.get('frontier_target')}
            for original in active:
                if original.get('linked_plan_id') and plan_states.get(original['linked_plan_id']) not in {'active', 'proposed'}:
                    r = transition(deepcopy(original), 'paused', 'linked_plan_no_longer_active', world)
                    step = None
                else:
                    r, step = DirectivePlanner(world).evaluate(original, reserved=reservations - {original.get('frontier_target')})
                updates.append(r)
                if r['state'] == 'paused':
                    # A strategic exception stops this execution slice, not
                    # every unrelated directive permanently. The model decides
                    # whether to resume the rest unchanged.
                    interrupted = 'directive_requires_decision'
                    break
                if step:
                    candidate = (r, step)
                    break
            self._save(updates, world, 'evaluate_directive_progress')
            self._notices()
            if interrupted:
                break
            if candidate is None:
                ready = world.snapshot.get('ready_unit_refs')
                if not isinstance(ready, list):
                    interrupted = 'ready_unit_evidence_unavailable'
                    break
                if ready:
                    interrupted = 'ready_units_need_new_or_manual_orders'
                    break
                if not end_turn:
                    interrupted = 'current_work_finished_turn_close_not_authorized'
                    break
                options = self.port.options(world)
                eligible = [c for c in options.choices if c.get('command') == 'end_turn']
                if len(eligible) != 1:
                    interrupted = 'native_end_turn_not_available'
                    break
                # end_turn always goes through the current intent/attention
                # checks and the existing deferred action ledger.
                passthrough = self.port.execute(world, options, eligible[0])
                interrupted = 'authorized_turn_close_submitted'
                break
            record, step = candidate
            last_scheduled = record['directive_id']
            try:
                options = self.port.options(world, step.actor_ref)
                step, choice = select_step(world, record, step, options)
            except (DirectiveError, ValueError) as exc:
                if 'revision' in str(exc) and rejections < 2:
                    rejections += 1
                    continue
                transition(record, 'paused', str(exc), world)
                self._save([record], world, 'directive_native_catalog_interruption')
                interrupted = 'directive_requires_decision'
                break
            if may_close_turn(choice['command'], world.snapshot, choice) and not end_turn:
                interrupted = 'last_ready_action_requires_turn_close_authorization'
                break
            pending = {'step_id': 'step-' + uuid.uuid4().hex, 'actor_ref': step.actor_ref,
                       'command': step.command, 'origin_ref': world.actor(step.actor_ref)['location_ref'],
                       'target_ref': step.destination, 'target_position': list(step.position) if step.position else None,
                       'turn': world.turn, 'session_id': world.session,
                       'task_name': choice.get('name') if step.command == 'terraform' else None}
            record['pending'] = pending
            # Write ahead BEFORE dispatch. A crash anywhere after this point
            # leaves a durable unresolved attempt instead of a replayable choice.
            self._save([record], world, 'directive_step_prepared')
            try:
                result = self.port.execute(world, options, choice)
            except Exception:
                # Keep the write-ahead record. The native call might have run.
                raise
            executed += 1
            action = result.get('execution') or {}
            action_id = result.get('action_id', action.get('action_id'))
            if type(action_id) is int:
                pending['action_id'] = action_id
            error = result.get('error') or {}
            code = error.get('code') if isinstance(error, Mapping) else str(error)
            pending['error'] = code
            if result.get('native_action_executed') is False or code in {'stale_state', 'unit_not_ready'}:
                pending['definitely_not_dispatched'] = True
            if code == 'stale_state' and pending.get('definitely_not_dispatched') and rejections < 2:
                # This is NOT opaque-choice rebasing. Retire the rejected
                # attempt, then re-observe and repeat ALL policy checks before
                # selecting another current action. A new threat stops here.
                record['pending'] = None
                record['rejections'] += 1
                rejections += 1
                self._save([record], world, 'directive_revision_retry_requires_full_revalidation')
                continue
            if result.get('native_action_executed') is False and code in {
                    'current_turn_intent_requires_review', 'critical_attention_requires_review',
                    'intent_guard_observation_unavailable'}:
                # An explicitly non-dispatched global review gate did not
                # fail this unit's objective. Keep its authorization intact.
                record['pending'] = None
                self._save([record], world, 'directive_yield_to_global_review')
                passthrough = result
                interrupted = 'gameplay_gate_requires_sovereign'
                break
            # Do not store raw payloads, native selectors or temporary choice IDs.
            pending['receipt_status'] = action.get('status', 'pending' if result.get('queued') else 'unverified')
            self._save([record], world, 'directive_step_receipt')
            if result.get('turn_handoff_required'):
                passthrough = result
                interrupted = 'turn_boundary'
                break  # no further native/tool calls across this episode fence
            if not result.get('ok'):
                passthrough = result
                interrupted = 'native_action_requires_review'
                break
            if result.get('queued'):
                passthrough = dict(result)
                # Safe to observe the required modal, but never overlap another
                # unit action. The outer model will receive its current choices.
                frame = self.port.interaction()
                passthrough['post_action_decision'] = {'frame': frame}
                passthrough['required_next'] = dict(frame.get('required_next') or {'tool': 'smac_decision'})
                if frame.get('choices'):
                    passthrough['required_next']['select_choice_from'] = 'post_action_decision.frame.choices'
                interrupted = 'native_action_pending'
                break
        self._notices()
        if not interrupted:
            interrupted = 'execution_slice_budget_reached'
        result = {'ok': True, 'completed': True, 'execution_status': 'completed',
                  'directive_execution': {'steps_dispatched': executed, 'stop_reason': interrupted,
                                          'elapsed_seconds': round(time.monotonic() - started, 4),
                                          'provider_calls_inside_executor': 0,
                                          'turn_close_authorization': 'this_turn_only' if end_turn else 'none'},
                  'unit_directives': self.store.dashboard(), **passthrough}
        # A passthrough native failure retains its uncertainty; no local success
        # can certify the native mutation or hide a runtime circuit.
        return result


def live_owned(actor: Mapping) -> bool:
    return actor.get('kind') == 'own_unit' and actor.get('status', 'active') == 'active'


class MCPDirectivePort:
    """Explicit adapter to the existing guarded MCP/native implementation."""
    def __init__(self, api: Any, attention: Any):
        self.api, self.attention = api, attention
        self.dispatch_count = 0

    def observe(self) -> DirectiveWorld:
        a = self.api
        gate = a._sovereign_gameplay_gate('Directive observation')
        if gate:
            raise DirectiveError('directive_gameplay_authority_unavailable')
        for _ in range(3):
            refreshed = a._refresh_managed_world()
            if not refreshed.get('ok'):
                raise DirectiveError('directive_world_refresh_failed')
            result = a._call('semantic_snapshot')
            snapshot = result.get('snapshot')
            projection = self.attention.world_store.load(self.attention.scope, self.attention.timeline_id)
            if result.get('ok') and isinstance(snapshot, dict) and projection \
                    and str(projection.get('action_revision')) == str(snapshot.get('revision')):
                return DirectiveWorld(projection, snapshot)
        raise DirectiveError('directive_observation_revision_changed')

    def gate(self, world: DirectiveWorld) -> dict | None:
        a = self.api
        for result in (a._sovereign_gameplay_gate('Directive execution'),
                       a._capability_gap_blocked('Directive execution')):
            if result:
                return result
        boundary = a._implicit_turn_handoff(world.snapshot, identity(world))
        if boundary:
            return boundary
        briefing = a._match_briefing_gate(str(world.snapshot.get('match_id') or ''), world.session)
        if briefing:
            return briefing
        critical = self.attention.unacknowledged_critical()
        if critical.get('items') or critical.get('more'):
            return {'ok': True, 'directive_blocked': 'critical_attention_requires_review',
                    'critical_attention': critical}
        return None

    def options(self, world: DirectiveWorld, actor_ref: str = '') -> NativeOptions:
        a = self.api
        selectors, context = a._resolve_managed_selectors(world.revision, own_unit_ref=actor_ref)
        family = 'unit_actions' if actor_ref else 'game_management'
        catalog = a._call('semantic_choices', kind=family, **selectors)
        if not catalog.get('ok'):
            raise DirectiveError('directive_native_catalog_unavailable')
        if {k: catalog.get(k, '') for k in identity(world)} != identity(world):
            raise DirectiveError('directive_catalog_revision_changed')
        return NativeOptions(catalog.get('choices', []), selectors, context)

    def execute(self, world: DirectiveWorld, options: NativeOptions, choice: dict) -> dict:
        a = self.api
        decision_id, public = a._cache_decision_choices(identity(world), [choice],
            choice_kind='unit_actions' if 'unit_id' in options.selectors else 'game_management',
            choice_arguments=options.selectors, semantic_context=options.context,
            turn=world.turn, year=world.snapshot.get('year'), phase=_phase(world), snapshot=world.snapshot)
        if len(public) != 1:
            return {'ok': False, 'native_action_executed': False,
                    'error': {'code': 'directive_choice_not_executable'}}
        # No post-action rich frame and no stale-state automatic rebase: a
        # changed state must first pass the directive policy again.
        self.dispatch_count += 1
        token = a.DIRECTIVE_NATIVE_DISPATCH.set(True)
        try:
            return a._execute_choice_once(decision_id, public[0]['choice_id'],
                                          directive_execution=True, allow_rebase=False)
        finally:
            a.DIRECTIVE_NATIVE_DISPATCH.reset(token)

    def action_status(self, action_id: int) -> dict:
        return self.api._call('action_status', action_id=action_id)

    def interaction(self) -> dict:
        return self.api.smac_decision()
