#!/usr/bin/env python3
"""Real MCP/cache/guard/journal integration over a controlled native bridge.

No live-game, strategic-quality or network-synchronization claim. Each mutation
travels through the real smac_execute_choice -> smac_command implementation.
"""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import smacx_mcp as m
from smacx_attention import AttentionService
from smacx_directives import DirectiveStore
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope, SmacxStore
from smacx_world import WorldService
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity, WorldObject
from unit_directive_test import NativeFixture, fields


class MCPDirectiveContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scope = MemoryScope('match-directive', 'agent-directive', 'perspective-directive')
        self.state = SmacxStore(self.root / 'state.sqlite3')
        self.state.ensure_agent(self.scope.agent_id, 'Directive test')
        self.state.create_match(match_id=self.scope.match_id, display_name='Directive test', mode='lan')
        self.state.create_perspective(self.scope.match_id, self.scope.agent_id, perspective_id=self.scope.perspective_id)
        self.journal = CampaignJournal(self.root / 'campaigns', timeline_resolver=self.state.active_timeline_id)
        self.worlds = WorldStore(self.state, self.root / 'world-snapshots')
        self.world = WorldService(self.worlds, self.scope)
        self.attention = AttentionService(self.state, self.journal, self.scope)
        self.port = NativeFixture()
        self.port.projection['objects'].append({'object_ref': 'world-map', 'kind': 'map_state',
            'status': 'active', 'fields': fields(width=16, height=5, horizontal_wrap=False)})
        self.lease = self.attention.acquire_sovereign('episode-directive', 'gameplay')
        self.native_calls = []
        self.briefed = True
        self.pending_intents = 0
        self.reject_once = False
        self.on_rejection = None
        self.patchers = []
        def replace(name, value):
            p = patch.object(m, name, value); p.start(); self.patchers.append(p)
        replace('MANAGED_ATTACHED', True)
        replace('_managed_scope_identity', lambda: (self.scope.match_id, 'session-directive', self.scope.agent_id, self.scope.perspective_id))
        replace('_runtime_services', lambda: (None, self.attention))
        replace('controller_world_service', lambda *args, **kwargs: (self.scope, self.world, self.attention))
        replace('_refresh_managed_world', self.refresh)
        replace('_call', self.native)
        replace('_compose_match_briefing', lambda *args, **kwargs: {'ok': True, 'acknowledged': self.briefed, 'configuration_hash': 'fixture-rules'})
        replace('controller_match_briefing_is_acknowledged', lambda *args: self.briefed)
        replace('current_turn_intents', lambda *args, **kwargs: {'total_pending': self.pending_intents, 'items': []})
        replace('controller_record_campaign_action', self.record_action)
        replace('_attach_working_state', lambda frame, *args, **kwargs: frame)
        replace('_attach_chat_attention', lambda frame, *args, **kwargs: frame)
        for cache in (m.DECISION_CACHE, m.ACTION_PROGRESS, m.RUNTIME_CIRCUITS, m.FAILED_CHOICE_ATTEMPTS,
                      m.TURN_HANDOFF_STATE, m.RUNTIME_EPISODE_TURNS, m.CAPABILITY_GAPS, m.MATCH_BRIEFING_CACHE):
            cache.clear()
        m.MATCH_BRIEFING_CACHE[(self.scope.match_id, 'session-directive')] = 'fixture-rules'
        self.refresh()
        self.directives = DirectiveStore(self.journal, self.scope, 'timeline-main')

    def tearDown(self):
        for p in reversed(self.patchers): p.stop()
        self.tmp.cleanup()

    def refresh(self, **kwargs):
        self.port.update_ready()
        projection = self.port.projection
        self.worlds.replace_projection(self.scope, WorldIdentity(**projection['identity']),
            [WorldObject.from_dict(o) for o in projection['objects']],
            observation_cursor=projection['observation_cursor'], action_revision=projection['action_revision'],
            continuity=projection['continuity'], journal_head_hash=self.journal.replay(self.scope)['manifest']['head_hash'])
        return {'ok': True}

    def record_action(self, match, session, payload, **kwargs):
        event = self.journal.append(self.scope, 'game.action', payload,
            session_id=session, turn=kwargs.get('turn'), year=kwargs.get('year'))
        return {'ok': True, 'journal_event_id': event['event_id']}

    def native(self, op, **args):
        if op == 'semantic_snapshot':
            self.port.update_ready()
            return {'ok': True, 'snapshot': deepcopy(self.port.snapshot)}
        if op == 'perspective_world_page':
            items = []
            if args['domain'] == 'units':
                for o in self.port.projection['objects']:
                    if o['kind'] == 'own_unit' and o['status'] == 'active':
                        items.append({'id': o['metadata']['native_id'], 'own_unit_ref': o['object_ref'], 'owned': True})
            return {'ok': True, 'action_revision': self.port.snapshot['revision'], 'items': items, 'next_cursor': None}
        if op == 'semantic_choices':
            ident = {k: self.port.snapshot[k] for k in ('match_id', 'session_id', 'revision')}
            if args['kind'] == 'interaction':
                return {'ok': True, **ident, 'choices': [
                    {'command': 'respond_to_combat_confirmation', 'response': 'cancel', 'label': 'Cancel attack'}]}
            actor_ref = ''
            if args['kind'] == 'unit_actions':
                actor_ref = next(o['object_ref'] for o in self.port.projection['objects']
                                 if o['kind'] == 'own_unit' and o['metadata']['native_id'] == args['unit_id'])
            return {'ok': True, **ident, 'choices': self.port.options(self.port.observe(), actor_ref).choices}
        if op == 'semantic_command':
            self.native_calls.append(deepcopy(args))
            if self.reject_once:
                self.reject_once = False
                if self.on_rejection:
                    self.on_rejection()
                self.port.bump()
                return {'ok': False, 'error': {'code': 'stale_state'}}
            if args['expected_revision'] != self.port.snapshot['revision']:
                return {'ok': False, 'error': {'code': 'stale_state'}}
            if args['command'] == 'respond_to_combat_confirmation':
                self.port.snapshot['protocol'] = {'phase': 'turn'}
                self.port.pending = False
                for receipt in self.port.receipts.values(): receipt['status'] = 'completed'
                self.port.bump()
                return {'ok': True, 'completed': True}
            actor_ref = ''
            if args['unit_id'] >= 0:
                actor_ref = next(o['object_ref'] for o in self.port.projection['objects']
                                 if o['kind'] == 'own_unit' and o['metadata']['native_id'] == args['unit_id'])
            options = self.port.options(self.port.observe(), actor_ref)
            choice = next(c for c in options.choices if c['command'] == args['command']
                          and (c['command'] != 'move_unit' or c['target_tile_id'] == args['target_tile_id']))
            return self.port.execute(self.port.observe(), options, choice)
        if op == 'action_status':
            return self.port.action_status(args['action_id'])
        raise AssertionError(f'unexpected native operation {op}')

    def prepare(self, request):
        result = m.smac_directives(action='prepare', request_json=json.dumps(request))
        self.assertTrue(result.get('ok'), result)
        self.assertTrue(result.get('choices'), result)
        return result

    def approve(self, frame):
        return m.smac_execute_choice(frame['decision_id'], frame['choices'][0]['choice_id'])

    def assign(self, target='cell-4-2'):
        f = self.prepare({'action': 'assign', 'directives': [
            {'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': target}]})
        self.assertEqual(self.native_calls, [])
        self.assertEqual(self.directives.read()['records'], {})
        result = self.approve(f)
        self.assertTrue(result.get('ok'), result)
        self.assertEqual(self.native_calls, [])
        return result

    def test_registered_tool_and_gameplay_profile(self):
        from smacx_hermes import GAMEPLAY_MCP_TOOLS, COMMUNICATION_MCP_TOOLS
        self.assertIn('smac_directives', GAMEPLAY_MCP_TOOLS)
        self.assertNotIn('smac_directives', COMMUNICATION_MCP_TOOLS)
        tool = m.mcp._tool_manager.get_tool('smac_directives')
        self.assertIsNotNone(tool)
        self.assertFalse(tool.parameters['additionalProperties'])

    def test_prepare_approve_advance_reaches_target_through_real_executor(self):
        assigned = self.assign()
        frame = assigned['post_action_decision']['frame']
        self.assertIn('unit_directives', frame)
        self.assertIn('Advance approved', frame['choices'][0]['label'])
        result = self.approve(frame)
        self.assertTrue(result.get('ok'), result)
        self.assertEqual(len(self.native_calls), 2)
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], 'cell-4-2')
        self.assertEqual(next(iter(self.directives.read()['records'].values()))['state'], 'completed')
        actions = [e for e in self.journal.latest_events(self.scope, limit=100) if e['event_type'] == 'game.action']
        self.assertEqual(sum(e['payload'].get('selected_action') == 'move_unit' for e in actions), 2)
        notices = self.attention.lease('episode-outcomes')
        self.assertEqual(sum(i['attention_kind'] == 'unit_directive' for i in notices['items']), 1)

    def test_opaque_assignment_choice_is_single_use(self):
        frame = self.prepare({'directives': [{'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'}]})
        first = self.approve(frame)
        self.assertTrue(first.get('ok'), first)
        again = self.approve(frame)
        self.assertEqual(again['error']['code'], 'consumed_decision')
        self.assertEqual(len(self.directives.read()['records']), 1)

    def test_revision_changed_between_prepare_and_approval_commits_nothing(self):
        frame = self.prepare({'directives': [{'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'}]})
        self.port.bump()
        result = self.approve(frame)
        self.assertFalse(result['ok'])
        self.assertEqual(self.directives.read()['records'], {})
        self.assertEqual(self.native_calls, [])

    def test_changed_generation_rejects_old_advance_authority(self):
        self.assign()
        f = self.prepare({'action': 'advance', 'end_turn': True})
        did = next(iter(self.directives.read()['records']))
        pause = self.prepare({'action': 'pause', 'directive_ids': [did]})
        self.assertTrue(self.approve(pause)['ok'])
        result = self.approve(f)
        self.assertFalse(result['ok'])
        self.assertEqual(self.native_calls, [])

    def test_native_stale_state_is_not_rebased_behind_directive_policy(self):
        self.assign()
        f = self.prepare({'action': 'advance', 'end_turn': True})
        self.reject_once = True
        def new_threat():
            self.port.projection['objects'].append({'object_ref': 'foreign-rover', 'kind': 'foreign_contact',
                'status': 'active', 'location_ref': 'cell-2-2',
                'fields': fields(owner_ref='faction-2', last_seen_turn=1)})
            self.port.projection['objects'].append({'object_ref': 'faction-2', 'kind': 'faction',
                'status': 'active', 'fields': fields(relationship='hostile')})
        self.on_rejection = new_threat
        result = self.approve(f)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(self.native_calls), 1)
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], 'cell-0-2')
        self.assertEqual(next(iter(self.directives.read()['records'].values()))['state'], 'paused')

    def test_harmless_revision_retry_reobserves_and_revalidates_policy(self):
        self.assign()
        f = self.prepare({'action': 'advance', 'end_turn': True})
        self.reject_once = True
        result = self.approve(f)
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(self.native_calls), 3)
        self.assertEqual(self.port.obj('scout-alpha')['location_ref'], 'cell-4-2')
        self.assertEqual(next(iter(self.directives.read()['records'].values()))['state'], 'completed')

    def test_direct_manual_choice_pauses_directive(self):
        self.assign()
        frame = m.smac_choices(kind='unit_actions', own_unit_ref='scout-alpha')
        choice = next(c for c in frame['choices'] if c['label'] == 'Skip unit')
        result = m.smac_execute_choice(frame['decision_id'], choice['choice_id'])
        self.assertTrue(result.get('ok'), result)
        self.assertEqual(next(iter(self.directives.read()['records'].values()))['reason'], 'manual_sovereign_override')

    def test_briefing_gate_still_blocks_directive_approval(self):
        frame = self.prepare({'directives': [{'kind': 'travel', 'actor_ref': 'scout-alpha', 'target_ref': 'cell-2-2'}]})
        self.briefed = False
        result = self.approve(frame)
        self.assertFalse(result['ok'])
        self.assertEqual(self.directives.read()['records'], {})
        self.assertEqual(self.native_calls, [])

    def test_current_turn_intent_gate_prevents_last_unit_step(self):
        self.assign()
        frame = self.prepare({'action': 'advance', 'end_turn': True})
        self.pending_intents = 1
        result = self.approve(frame)
        self.assertEqual(result['error']['code'], 'current_turn_intent_requires_review')
        self.assertEqual(self.native_calls, [])
        row = next(iter(self.directives.read()['records'].values()))
        self.assertFalse(row.get('pending'))
        self.assertNotEqual(row['state'], 'paused')

    def test_critical_attention_stops_execution_even_before_last_unit(self):
        self.assign()
        frame = self.prepare({'action': 'advance', 'end_turn': True})
        self.attention.enqueue('chat', {'message': 'stop'}, observation_cursor=0, critical=True)
        result = self.approve(frame)
        self.assertEqual(result.get('directive_blocked'), 'critical_attention_requires_review')
        self.assertEqual(self.native_calls, [])

    def test_communication_episode_cannot_prepare_gameplay_authority(self):
        self.attention.release_sovereign(self.lease, committed=False)
        self.attention.acquire_sovereign('episode-communication', 'communication')
        result = m.smac_directives(action='prepare', request_json=json.dumps({'action': 'advance'}))
        self.assertFalse(result['ok'])
        self.assertEqual(self.native_calls, [])

    def test_native_row_reordering_does_not_change_semantic_actor(self):
        self.assign()
        self.port.obj('scout-alpha')['metadata']['native_id'] = 7
        self.port.bump()
        frame = self.prepare({'action': 'advance', 'end_turn': True})
        result = self.approve(frame)
        self.assertTrue(result.get('ok'), result)
        self.assertTrue(all(c['unit_id'] == 7 for c in self.native_calls))

    def test_actor_specific_decision_does_not_offer_broad_advance(self):
        self.assign()
        frame = m.smac_decision(own_unit_ref='scout-alpha')
        self.assertFalse(any('Advance approved' in c['label'] for c in frame['choices']))

    def test_outbox_recovery_reuses_attention_identity(self):
        self.assign(target='cell-2-2')
        self.approve(self.prepare({'action': 'advance', 'end_turn': True}))
        row = next(iter(self.directives.read()['records'].values()))
        notice = row['notice']
        row.pop('delivered_notice', None)
        self.directives.commit([row], expected_generation=self.directives.read()['generation'],
            world=self.port.observe(), reason='fixture_restore_before_delivery_marker')
        self.directives.flush_notices(self.attention)
        lease = self.attention.lease('episode-outbox')
        items = [i for i in lease['items'] if i['attention_kind'] == 'unit_directive']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['payload'], notice['payload'])

    def test_no_native_selector_or_scope_geometry_in_authorization_preview(self):
        frame = self.prepare({'directives': [{'kind': 'explore', 'actor_ref': 'scout-alpha', 'origin_ref': 'cell-0-2', 'radius': 2}]})
        serialized = json.dumps(frame)
        for private in ('scope_positions', 'native_id', 'target_tile_id', 'former_id', 'expected_generation'):
            self.assertNotIn('"' + private + '"', serialized)


if __name__ == '__main__':
    unittest.main(verbosity=2)
