#!/usr/bin/env python3
"""Deterministic directive contracts with a real journal and a native-shaped port.

This suite verifies execution/authorization behavior, not live SMACX effects or
multiplayer synchronization. Run with PYTHONPATH=src python scripts/unit_directive_test.py.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from smacx_directives import (DirectiveError, DirectivePlanner, DirectiveStore, DirectiveWorld,
                             Step, field_value, validate_spec, summary, WORK)
from smacx_directive_runtime import DirectiveRuntime, NativeOptions, match_step
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope


def fields(**values):
    return {key: {'value': value, 'epistemic_status': 'current', 'source': 'owned_state'}
            for key, value in values.items()}


def tile(x, y, terrain='land', features=(), owner='faction-1'):
    return {'object_ref': f'cell-{x}-{y}', 'kind': 'location', 'status': 'active',
            'metadata': {'native_x': x, 'native_y': y},
            'fields': fields(terrain=terrain, features=list(features), owner_ref=owner,
                             foreign_movement_zoc=False, blocking_contact_occupied=False)}


def unit(name='scout-alpha', location='cell-0-2', former=False):
    return {'object_ref': name, 'kind': 'own_unit', 'status': 'active', 'location_ref': location,
            'metadata': {'native_id': 0},
            'fields': fields(name=name, triad='land', roles={'combat': not former, 'former': former, 'boarded': False},
                             hp=10, max_hp=10, owner_ref='faction-1', ready=True, order_name='none',
                             movement_points=9, movement_scale=3, moves_remaining=9,
                             terraform_task={'state': 'no_active_terraform_order'})}


class NativeFixture:
    def __init__(self):
        self.projection = {'identity': {'match_id': 'match-directive', 'perspective_id': 'perspective-directive',
                                       'timeline_id': 'timeline-main', 'world_epoch': 'epoch-directive'},
                           'map_shape': {'width': 16, 'height': 5, 'horizontal_wrap': False},
                           'action_revision': 'r1', 'observation_cursor': 1, 'continuity': 'complete',
                           'objects': [tile(x, y) for y in range(5) for x in range(16) if (x+y) % 2 == 0] + [unit()]}
        self.snapshot = {'match_id': 'match-directive', 'session_id': 'session-directive', 'revision': 'r1',
                         'turn': 1, 'year': 2101, 'protocol': {'phase': 'turn'}, 'last_deferred_action': None}
        self.actions = []
        self.receipts = {}
        self.callback = None
        self.global_gate = None
        self.pending = False
        self.observe_count = 0
        self.update_ready()

    def obj(self, ref):
        return next(o for o in self.projection['objects'] if o['object_ref'] == ref)

    def bump(self):
        cursor = self.projection['observation_cursor'] + 1
        self.projection.update(observation_cursor=cursor, action_revision=f'r{cursor}')
        self.snapshot['revision'] = f'r{cursor}'
        self.update_ready()

    def update_ready(self):
        self.snapshot['ready_unit_refs'] = [{'own_unit_ref': o['object_ref']}
            for o in self.projection['objects'] if o['kind'] == 'own_unit' and o['status'] == 'active'
            and field_value(o, 'ready') is True]

    def observe(self):
        self.observe_count += 1
        self.update_ready()
        return DirectiveWorld(deepcopy(self.projection), deepcopy(self.snapshot))

    def gate(self, world):
        return self.global_gate

    def options(self, world, actor_ref=''):
        if not actor_ref:
            return NativeOptions([{'command': 'end_turn'}] if not world.snapshot['ready_unit_refs'] else [], {}, {})
        actor = world.actor(actor_ref)
        origin = world.position(actor['location_ref'])
        choices = []
        if field_value(actor, 'ready') is True and field_value(actor, 'order_name') == 'none':
            for direction, p in world.topology.shape.neighbors(origin).items():
                square = world.topology.by_position.get(p)
                choices.append({'command': 'move_unit', 'unit_id': actor['metadata']['native_id'],
                                'target_tile_id': (p[0] + world.topology.shape.width * p[1]) // 2,
                                'known': square is not None, 'visible_now': square is not None,
                                'features': list(square.features) if square else [],
                                'safe_local_move': True, 'combat': False, 'boards_transport': False})
            choices.append({'command': 'skip_unit', 'unit_id': actor['metadata']['native_id']})
            if field_value(actor, 'roles')['former']:
                for name, (former_id, _) in WORK.items():
                    choices.append({'command': 'terraform', 'unit_id': actor['metadata']['native_id'],
                                    'former_id': former_id, 'name': name})
        return NativeOptions(choices, {'unit_id': actor['metadata']['native_id'], 'actor_ref': actor_ref}, {})

    def execute(self, world, options, choice):
        command = choice['command']
        if command == 'end_turn':
            self.actions.append(deepcopy(choice))
            self.snapshot['turn'] += 1
            for o in self.projection['objects']:
                if o['kind'] == 'own_unit':
                    o['fields'].update(fields(ready=True, moves_remaining=9))
            self.bump()
            return {'ok': True, 'completed': True, 'turn_handoff_required': {'required': True}}
        actor = self.obj(options.selectors['actor_ref'])
        if command == 'move_unit':
            tid = choice['target_tile_id']
            y, n = divmod(tid, self.projection['map_shape']['width'] // 2)
            x = 2*n + y % 2
            location = f'cell-{x}-{y}'
            if not any(o['object_ref'] == location for o in self.projection['objects']):
                self.projection['objects'].append(tile(x, y, owner=None))
            actor['location_ref'] = location
            remaining = max(0, field_value(actor, 'moves_remaining') - 3)
            actor['fields'].update(fields(moves_remaining=remaining, ready=remaining > 0))
        elif command == 'skip_unit':
            actor['fields'].update(fields(ready=False, moves_remaining=0))
        elif command == 'terraform':
            actor['fields'].update(fields(ready=False, order_name='terraform',
                terraform_task={'state': 'active_terraform_order', 'name': choice['name'], 'accumulated_work_points': 1}))
        else:
            raise AssertionError('unexpected command')
        self.actions.append(deepcopy(choice))
        aid = len(self.actions)
        receipt = {'action_id': aid, 'status': 'pending' if self.pending else 'completed', 'resolution': 'native_move_resolved'}
        self.receipts[aid] = receipt
        self.snapshot['last_deferred_action'] = receipt
        self.bump()
        if self.callback:
            self.callback(self, choice)
        return {'ok': True, 'completed': not self.pending, 'queued': self.pending,
                'action_id': aid, 'execution': deepcopy(receipt)}

    def action_status(self, action_id):
        receipt = self.receipts.get(action_id)
        return {'ok': receipt is not None, 'action': deepcopy(receipt)}

    def interaction(self):
        return {'ok': True, 'focus': {'kind': 'interaction'}, 'choices': [{'choice_id': 'current-popup'}],
                'required_next': {'tool': 'smac_execute_choice'}}

    def renew_turn(self):
        self.snapshot['turn'] += 1
        for obj in self.projection['objects']:
            if obj['kind'] == 'own_unit' and field_value(obj, 'order_name') == 'none':
                obj['fields'].update(fields(ready=True, moves_remaining=9))
        self.bump()


class DirectiveContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scope = MemoryScope('match-directive', 'agent-directive', 'perspective-directive')
        self.journal = CampaignJournal(self.root / 'journal')
        self.store = DirectiveStore(self.journal, self.scope, 'timeline-main')
        self.port = NativeFixture()
        self.runtime = DirectiveRuntime(self.store, self.port)

    def tearDown(self):
        self.temp.cleanup()

    def assign(self, **overrides):
        spec = {'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-6-2', **overrides}
        world = self.port.observe()
        prepared = self.store.prepare({'action': 'assign', 'directives': [spec]}, world)
        self.store.apply(prepared, world, 'assignment-' + str(self.store.read()['generation']))
        return prepared['updates'][-1]['directive_id']

    def run_slice(self, **overrides):
        world = self.port.observe()
        prepared = self.store.prepare({'action': 'advance', 'end_turn': True, **overrides}, world)
        return self.runtime.run(prepared, world)

    def record(self, did):
        return self.store.read()['records'][did]

    def test_travel_executes_several_steps_without_provider(self):
        did = self.assign(target_ref='cell-4-2')
        result = self.run_slice()
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], 'cell-4-2')
        self.assertEqual(self.record(did)['state'], 'completed')
        self.assertEqual(result['directive_execution']['provider_calls_inside_executor'], 0)
        self.assertEqual(len(self.port.actions), 2)

    def test_travel_persists_across_turns(self):
        did = self.assign(target_ref='cell-12-2')
        first = self.run_slice()
        self.assertTrue(first.get('turn_handoff_required'))
        self.assertEqual(self.record(did)['state'], 'waiting')
        result = self.run_slice()
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], 'cell-12-2')
        self.assertEqual(self.record(did)['state'], 'completed')

    def test_unassigned_units_are_never_skipped(self):
        other = unit('scout-beta', 'cell-0-0')
        other['metadata']['native_id'] = 1
        self.port.projection['objects'].append(other)
        self.assign(target_ref='cell-2-2')
        result = self.run_slice()
        self.assertEqual(result['directive_execution']['stop_reason'], 'ready_units_need_new_or_manual_orders')
        self.assertTrue(field_value(self.port.obj('scout-beta'), 'ready'))
        self.assertFalse(any(c['command'] in {'skip_unit', 'end_turn'} for c in self.port.actions))

    def test_native_former_enums_match_authorized_work(self):
        import re
        source = (Path(__file__).resolve().parents[1] / 'bridge/src/engine_veh.h').read_text()
        symbols = {'farm': 'FARM', 'mine': 'MINE', 'solar_collector': 'SOLAR',
                   'forest': 'FOREST', 'road': 'ROAD', 'sensor': 'SENSOR', 'remove_fungus': 'REMOVE_FUNGUS'}
        for name, symbol in symbols.items():
            value = int(re.search(r'FORMER_' + symbol + r'\s*=\s*(\d+)', source)[1])
            self.assertEqual(WORK[name][0], value)

    def test_last_ready_action_requires_explicit_turn_authority(self):
        self.assign()
        result = self.run_slice(end_turn=False)
        self.assertEqual(len(self.port.actions), 0)
        self.assertEqual(result['directive_execution']['stop_reason'], 'last_ready_action_requires_turn_close_authorization')

    def test_two_directives_share_one_slice_without_double_assignment(self):
        other = unit('scout-beta', 'cell-0-0'); other['metadata']['native_id'] = 1
        self.port.projection['objects'].append(other)
        w = self.port.observe()
        prepared = self.store.prepare({'directives': [
            {'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-4-2'},
            {'kind': 'travel', 'actor_ref': 'scout-beta', 'target_ref': 'cell-4-0'}]}, w)
        self.store.apply(prepared, w, 'batch-assign')
        result = self.run_slice()
        self.assertEqual(len(self.port.actions), 4)
        self.assertEqual(len({a['unit_id'] for a in self.port.actions[:2]}), 2)
        self.assertTrue(all(r['state'] == 'completed' for r in self.store.read()['records'].values()))
        self.assertEqual(result['directive_execution']['provider_calls_inside_executor'], 0)

    def test_batch_assignment_is_atomic_and_exclusive(self):
        world = self.port.observe()
        with self.assertRaises(DirectiveError):
            self.store.prepare({'directives': [
                {'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'},
                {'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-4-2'}]}, world)
        self.assertEqual(self.store.read()['records'], {})

    def test_generation_conflict_does_not_reassign(self):
        w = self.port.observe()
        p = self.store.prepare({'directives': [{'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'}]}, w)
        self.store.apply(p, w, 'first-transaction')
        with self.assertRaises(DirectiveError):
            self.store.apply(p, w, 'second-transaction')
        self.assertEqual(len(self.store.read()['records']), 1)

    def test_explicit_replace_cancels_old_directive(self):
        did = self.assign()
        w = self.port.observe()
        p = self.store.prepare({'action': 'assign', 'replace_existing': True,
            'directives': [{'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'}]}, w)
        self.store.apply(p, w, 'replacement')
        self.assertEqual(self.record(did)['state'], 'cancelled')

    def test_manual_override_pauses_without_returning_to_old_route(self):
        did = self.assign()
        self.runtime.manual_override('scout-alpha', self.port.observe())
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'paused')
        self.assertEqual(len(self.port.actions), 0)

    def test_resume_requires_fresh_explicit_authorization(self):
        did = self.assign(target_ref='cell-2-2')
        self.runtime.manual_override('scout-alpha', self.port.observe())
        w = self.port.observe()
        prepared = self.store.prepare({'action': 'resume', 'directive_ids': [did]}, w)
        self.assertEqual(self.record(did)['state'], 'paused')
        self.store.apply(prepared, w, 'resume-transaction')
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')

    def test_new_enemy_stops_before_second_step(self):
        did = self.assign(target_ref='cell-6-2')
        def reveal(port, choice):
            port.callback = None
            enemy = {'object_ref': 'contact-new', 'kind': 'foreign_contact', 'status': 'active',
                     'location_ref': 'cell-4-2', 'fields': fields(last_seen_turn=1, owner_ref='faction-2')}
            faction = {'object_ref': 'faction-2', 'kind': 'faction', 'status': 'active', 'fields': fields(relationship='hostile')}
            port.projection['objects'].extend([enemy, faction])
            port.bump()
        self.port.callback = reveal
        self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.assertEqual(self.record(did)['state'], 'paused')
        self.assertEqual(self.record(did)['reason'], 'relevant_hostile_contact')

    def test_new_local_base_evidence_interrupts_without_claiming_construction(self):
        did = self.assign()
        def reveal(port, choice):
            port.callback = None
            port.projection['objects'].append({'object_ref': 'base-newly-observed', 'kind': 'base',
                'status': 'active', 'location_ref': 'cell-4-2', 'fields': fields(owner_ref='faction-2')})
            port.bump()
        self.port.callback = reveal
        self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.assertEqual(self.record(did)['reason'], 'new_local_base_evidence')
        self.assertIn('not proof of construction', self.record(did)['detail']['meaning'])

    def test_damage_interrupts_even_when_route_still_legal(self):
        did = self.assign()
        def damage(port, choice):
            port.obj('scout-alpha')['fields'].update(fields(hp=8))
        self.port.callback = damage
        self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.assertEqual(self.record(did)['reason'], 'unit_damaged')

    def test_epoch_change_requires_review_not_rebinding(self):
        did = self.assign()
        self.port.projection['identity']['world_epoch'] = 'epoch-restored'
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'paused')
        self.assertEqual(len(self.port.actions), 0)

    def test_stale_actor_is_not_inferred_dead(self):
        did = self.assign()
        self.port.obj('scout-alpha')['status'] = 'stale'
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'paused')

    def test_confirmed_destruction_fails_without_replacement(self):
        did = self.assign()
        self.port.obj('scout-alpha')['status'] = 'destroyed'
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'failed')
        self.assertEqual(len(self.store.read()['records']), 1)

    def test_pending_modal_does_not_dispatch_second_action(self):
        did = self.assign()
        self.port.pending = True
        def popup(port, choice):
            port.snapshot['protocol'] = {'phase': 'interaction'}
        self.port.callback = popup
        result = self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.assertTrue(self.record(did)['pending'])
        self.assertEqual(result['post_action_decision']['frame']['focus']['kind'], 'interaction')

    def test_resolved_deferred_action_is_not_replayed(self):
        did = self.assign(target_ref='cell-2-2')
        self.port.pending = True
        self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.port.pending = False
        self.port.receipts[1]['status'] = 'completed'
        self.port.snapshot['last_deferred_action']['status'] = 'completed'
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')
        self.assertEqual(len(self.port.actions), 1)

    def test_retired_native_receipt_reconciles_actual_endpoint(self):
        did = self.assign(target_ref='cell-2-2')
        self.port.pending = True
        self.run_slice()
        self.port.pending = False
        self.port.receipts.clear()
        self.port.snapshot['last_deferred_action'] = None
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')
        self.assertEqual(len(self.port.actions), 1)

    def test_retired_receipt_without_postcondition_requires_review(self):
        did = self.assign(target_ref='cell-2-2')
        self.port.pending = True
        self.run_slice()
        self.port.receipts.clear()
        self.port.snapshot['last_deferred_action'] = None
        self.port.obj('scout-alpha')['location_ref'] = 'cell-0-2'
        self.port.bump()
        self.run_slice()
        self.assertEqual(self.record(did)['reason'], 'uncertain_action_outcome_review_required')
        self.assertEqual(len(self.port.actions), 1)

    def test_crash_after_movement_reconciles_observed_endpoint(self):
        did = self.assign(target_ref='cell-2-2')
        def crash(port, choice):
            raise ConnectionError('response lost after native movement')
        self.port.callback = crash
        with self.assertRaises(ConnectionError):
            self.run_slice()
        self.assertTrue(self.record(did)['pending'])
        self.port.callback = None
        self.store = DirectiveStore(CampaignJournal(self.root / 'journal'), self.scope, 'timeline-main')
        self.runtime = DirectiveRuntime(self.store, self.port)
        self.run_slice()
        self.assertEqual(len(self.port.actions), 1)
        self.assertEqual(self.record(did)['state'], 'completed')

    def test_crash_before_dispatch_does_not_blindly_retry(self):
        did = self.assign()
        self.port.execute = lambda *args: (_ for _ in ()).throw(ConnectionError('unknown dispatch'))
        with self.assertRaises(ConnectionError):
            self.run_slice()
        self.run_slice()
        self.assertEqual(self.record(did)['reason'], 'uncertain_action_outcome_review_required')
        self.assertEqual(len(self.port.actions), 0)

    def test_journal_failure_before_dispatch_prevents_movement(self):
        self.assign()
        original = self.store.commit
        def commit(*args, **kwargs):
            if kwargs['reason'] == 'directive_step_prepared':
                raise OSError('disk full')
            return original(*args, **kwargs)
        self.store.commit = commit
        with self.assertRaises(OSError):
            self.run_slice()
        self.assertEqual(len(self.port.actions), 0)

    def test_checkpoint_fork_restores_authorization_not_future_progress(self):
        did = self.assign(target_ref='cell-2-2')
        head = self.journal.replay(self.scope)['manifest']['head_hash']
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')
        self.journal.fork_timeline(self.scope, 'timeline-rewind', native_save_sha256='a'*64,
                                  from_event_hash=head, parent_timeline_id='timeline-main')
        rewound = DirectiveStore(self.journal, self.scope, 'timeline-rewind').read()['records'][did]
        self.assertEqual(rewound['state'], 'active')
        self.assertEqual(rewound['steps'], 0)
        self.assertIsNone(rewound['pending'])

    def test_exploration_scope_is_frozen_and_unknown_steps_are_adjacent(self):
        self.port.projection['objects'] = [tile(0, 2), unit()]
        w = self.port.observe()
        p = self.store.prepare({'directives': [{'kind': 'explore', 'actor_ref': 'scout-alpha',
            'origin_ref': 'cell-0-2', 'radius': 2, 'direction': 'E'}]}, w)
        record = p['updates'][0]
        frozen = deepcopy(record['scope_positions'])
        evaluated, step = DirectivePlanner(w).evaluate(record)
        self.assertIn(step.position, w.topology.shape.neighbors((0, 2)).values())
        self.assertEqual(step.position, (2, 2))
        self.assertEqual(record['scope_positions'], frozen)
        match_step(w, evaluated, step, self.port.options(w, 'scout-alpha'))

    def test_unavailable_unknown_direction_uses_another_legal_scoped_neighbor(self):
        from smacx_directive_runtime import select_step
        self.port.projection['objects'] = [tile(0, 2), unit()]
        w = self.port.observe()
        record = validate_spec({'kind': 'explore', 'actor_ref': 'scout-alpha',
                                'origin_ref': 'cell-0-2', 'radius': 2, 'direction': 'E'}, w)
        _, step = DirectivePlanner(w).evaluate(record)
        options = self.port.options(w, 'scout-alpha')
        blocked = (step.position[0] + 16 * step.position[1]) // 2
        options.choices = [c for c in options.choices if c.get('target_tile_id') != blocked]
        alternative, choice = select_step(w, record, step, options)
        self.assertNotEqual(alternative.position, step.position)
        self.assertIn(list(alternative.position), record['scope_positions'])
        self.assertEqual(choice['command'], 'move_unit')

    def test_travel_cannot_use_unknown_step(self):
        did = self.assign()
        w = self.port.observe()
        step = Step('scout-alpha', 'move_unit', position=(2, 2))
        options = self.port.options(w, 'scout-alpha')
        for choice in options.choices:
            choice['known'] = False
        with self.assertRaises(DirectiveError):
            match_step(w, self.record(did), step, options)

    def test_known_pods_monoliths_combat_and_boarding_require_decision(self):
        did = self.assign()
        w = self.port.observe()
        step = Step('scout-alpha', 'move_unit', 'cell-2-2', (2, 2))
        for change in ({'features': ['supply_pod']}, {'features': ['monolith']}, {'combat': True},
                       {'may_initiate_combat_or_contact': True}, {'boards_transport': True},
                       {'safe_local_move': False}):
            with self.subTest(change=change):
                options = self.port.options(w, 'scout-alpha')
                for choice in options.choices:
                    if choice['command'] == 'move_unit':
                        choice.update(change)
                with self.assertRaises(DirectiveError):
                    match_step(w, self.record(did), step, options)

    def test_air_and_boarded_units_fail_closed(self):
        for change in ({'triad': 'air'}, {'roles': {'boarded': True, 'combat': True}}):
            with self.subTest(change=change):
                actor = self.port.obj('scout-alpha')
                before = deepcopy(actor['fields'])
                actor['fields'].update(fields(**change))
                with self.assertRaises(DirectiveError):
                    self.assign()
                actor['fields'] = before

    def test_work_starts_exact_job_and_waits_for_observed_effect(self):
        self.port.obj('scout-alpha')['fields'].update(fields(roles={'former': True, 'combat': False, 'boarded': False}))
        did = self.assign(kind='work', work='road', target_ref='cell-0-2')
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'working')
        self.assertEqual(self.record(did)['work_task_name'], 'road')
        self.port.obj('cell-0-2')['fields'].update(fields(features=['road']))
        self.port.obj('scout-alpha')['fields'].update(fields(order_name='none', ready=True))
        self.port.bump()
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')
        self.assertEqual(sum(a['command'] == 'terraform' for a in self.port.actions), 1)

    def test_work_order_cleared_without_effect_is_not_success(self):
        self.port.obj('scout-alpha')['fields'].update(fields(roles={'former': True, 'combat': False, 'boarded': False}))
        did = self.assign(kind='work', work='road', target_ref='cell-0-2')
        self.run_slice()
        self.port.obj('scout-alpha')['fields'].update(fields(order_name='none', ready=True))
        self.port.bump()
        self.run_slice()
        self.assertEqual(self.record(did)['reason'], 'work_stopped_before_verified_effect')

    def test_foreign_follow_stops_when_contact_identity_is_lost(self):
        self.port.projection['objects'].append({'object_ref': 'contact-ally', 'kind': 'foreign_contact', 'status': 'active',
            'location_ref': 'cell-4-2', 'fields': fields(last_seen_turn=1, owner_ref='faction-2')})
        did = self.assign(kind='follow', target_ref='contact-ally')
        self.port.obj('contact-ally')['status'] = 'retired'
        self.run_slice()
        self.assertEqual(self.record(did)['reason'], 'target_lost_or_not_current')
        self.assertEqual(len(self.port.actions), 0)

    def test_escort_moves_guard_first_and_preserves_cohesion(self):
        escort = unit('guard-alpha')
        escort['metadata']['native_id'] = 1
        self.port.projection['objects'].append(escort)
        did = self.assign(kind='escort', escort_ref='guard-alpha', target_ref='cell-4-2')
        self.run_slice()
        self.assertEqual(self.port.actions[0]['unit_id'], 1)
        self.assertEqual(self.port.actions[1]['unit_id'], 0)
        self.assertEqual(self.record(did)['state'], 'completed')
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], self.port.obj('guard-alpha')['location_ref'])

    def test_unknown_fields_and_strategy_text_never_authorize_actions(self):
        for additional in ({'attack': True}, {'policy': {'allow_war': True}}, {'work': 'road'}):
            with self.subTest(additional=additional):
                with self.assertRaises(DirectiveError):
                    self.assign(**additional)
        self.assertEqual(self.store.read()['records'], {})

    def test_linked_abandoned_plan_pauses_execution(self):
        did = self.assign(linked_plan_id='plan-abandoned')
        self.run_slice()
        self.assertEqual(self.record(did)['reason'], 'linked_plan_no_longer_active')
        self.assertEqual(len(self.port.actions), 0)

    def test_public_summary_excludes_paths_native_ids_and_geometry(self):
        did = self.assign()
        record = self.record(did)
        record.update(native_id=11, choice_id='old-choice', scope_positions=[[0, 2]], pending={'action_id': 5})
        shown = summary(record)
        self.assertNotIn('native_id', shown)
        self.assertNotIn('choice_id', shown)
        self.assertNotIn('scope_positions', shown)
        self.assertNotIn('pending', shown)
        self.assertTrue(shown['pending_action'])

    def test_execution_budget_returns_control_without_reissuing_assignment(self):
        did = self.assign(target_ref='cell-6-2')
        result = self.run_slice(max_steps=1)
        self.assertEqual(result['directive_execution']['stop_reason'], 'execution_slice_budget_reached')
        self.assertEqual(len(self.port.actions), 1)
        self.assertEqual(len(self.store.read()['records']), 1)
        self.run_slice()
        self.assertEqual(self.record(did)['state'], 'completed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
