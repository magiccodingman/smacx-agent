#!/usr/bin/env python3
"""Accepted assignment → committed native-shaped observation → provider attention."""
import json
import tempfile
import time
from unittest.mock import patch
from pathlib import Path
from observation_collector_benchmark import (
    NativeFixture, SmacxStore, MemoryScope, CampaignJournal, WorldStore,
    ObservationCollector, AttentionService,
)
from smacx_former_attention import former_state
from smacx_runtime_context import _attention_payload
import smacx_mcp as mcp


def run(mode):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = SmacxStore(root / 'state.sqlite3')
        store.ensure_agent('agent-former', 'Former')
        store.create_match(match_id='match-former', display_name='Former', mode='solo')
        store.create_perspective('match-former', 'agent-former', perspective_id='perspective-former')
        scope = MemoryScope('match-former', 'agent-former', 'perspective-former')
        journal = CampaignJournal(root / 'campaigns', timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / 'snapshots')
        attention = AttentionService(store, journal, scope)
        fixture = NativeFixture(32, 16, contacts=0, ready_drop_units=1)
        unit = fixture.units[0]
        unit.update(roles={'former': True}, order_name='none', moves_spent=0,
                    terraform_task={'state': 'no_active_terraform_order', 'automation_active': False})
        def collect():
            return ObservationCollector(scope=scope, session_id='session-former', bridge_call=fixture,
                journal=journal, world_store=worlds, attention=attention).collect_once()
        collect()
        original = mcp._runtime_services
        attached = mcp.MANAGED_ATTACHED
        mcp.MANAGED_ATTACHED = True
        mcp._runtime_services = lambda: (None, attention)
        try:
            def assign():
                projection = worlds.load(scope, attention.timeline_id)
                choice = {'command': 'automate_former', 'unit_id': unit['id'], 'automation_mode': 'improve_home_base'}
                baseline = mcp._former_attempt_baseline(choice, {'revision': projection['action_revision']}, {'turn': 50})
                assert baseline and baseline['before']['unit_ref'] == 'own-unit-2000'
                assert mcp._former_attempt_baseline(choice, {'revision': 'wrong'}, {}) is None
                assert mcp._former_attempt_baseline({'command': 'skip_unit'}, {}, {}) is None
                if mode == 'epoch': baseline['world_epoch'] = 'world-other'
                if mode == 'epoch':
                    return journal.append(scope, 'game.action', {'former_automation_attempt': baseline,
                        'choice_parameters': choice})
                identity = {'match_id': scope.match_id, 'session_id': 'session-former',
                            'revision': projection['action_revision']}
                mcp.DECISION_CACHE['decision-test'] = {'created_monotonic': time.monotonic(),
                    'identity': identity, 'choices': {'choice-test': choice}, 'turn': 50}
                mcp.ACTION_PROGRESS.clear()
                def record(match, session, payload, **kwargs):
                    event = journal.append(scope, 'game.action', payload)
                    if mode == 'rejected': assert 'former_automation_attempt' not in payload
                    else: assert payload['former_automation_attempt'] == baseline
                    return {'ok': True, 'journal_event_id': event['event_id']}
                with patch.object(mcp, '_sovereign_gameplay_gate', return_value=None), \
                     patch.object(mcp, '_turn_reconciliation_gate', return_value=None), \
                     patch.object(mcp, '_attach_turn_handoff'), \
                     patch.object(mcp, '_call', return_value={'ok': True, 'snapshot': {'turn': 50}}), \
                     patch.object(mcp, 'smac_command', return_value=({'ok': False, 'error': 'native_action_rejected'}
                         if mode == 'rejected' else {'ok': True})), \
                     patch.object(mcp, 'controller_record_campaign_action', side_effect=record):
                    result = mcp._execute_choice_once('decision-test', 'choice-test')
                assert result['ok'] == (mode != 'rejected')
            assign()
            attention.capture_former_automation()  # Same observation cannot resolve assignment.
            with store._connect() as c:
                assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='former_automation'").fetchone()[0] == 0
            if mode == 'active': unit['terraform_task']['state'] = 'active_terraform_order'
            if mode == 'pending': unit['terraform_task']['automation_active'] = True
            if mode == 'movement': unit['tile_id'] = fixture.tiles[8]['tile_id']
            if mode == 'tile_change':
                next(t for t in fixture.tiles if t['tile_id'] == unit['tile_id'])['features'] = ['farm']
            if mode == 'unknown': unit['terraform_task'] = {}
            if mode == 'replacement': unit['order_name'] = 'sentry'
            if mode == 'superseded':
                journal.append(scope, 'game.action', {'choice_parameters': {'unit_id': unit['id'], 'command': 'skip_unit'}})
            if mode == 'overlap': assign()
            fixture.revision += 1
            collect()
            if mode == 'crash':
                original_append = journal.append
                def fail_checked(scope, kind, *args, **kwargs):
                    if kind == 'attention.former_automation_checked': raise RuntimeError('injected')
                    return original_append(scope, kind, *args, **kwargs)
                journal.append = fail_checked
                try: attention.capture_former_automation()
                except RuntimeError as error: assert str(error) == 'injected'
                else: raise AssertionError('crash not reached')
                journal.append = original_append
            lease = attention.lease('episode-former')
            if mode == 'repeated':
                assign()
                fixture.revision += 1
                collect()
                attention.capture_former_automation()
            with store._connect() as c:
                rows = c.execute("SELECT * FROM attention_items WHERE attention_kind='former_automation' ORDER BY attention_sequence").fetchall()
            expected = 2 if mode == 'repeated' else 1 if mode in {'stopped', 'crash', 'overlap'} else 0
            assert len(rows) == expected, (mode, len(rows))
            if rows:
                payload = json.loads(rows[-1]['payload_json'])
                assert payload['cause'] == 'unknown'
                assert payload['repeated_observations'] == (2 if mode == 'repeated' else 1), payload
                assert rows[-1]['priority'] == (80 if mode == 'repeated' else 55)
                assert not rows[-1]['critical']
                compact = _attention_payload({'attention_kind': 'former_automation', 'payload': payload})
                assert compact['guidance'] and compact['progress_evidence']
                assert any(i['attention_kind'] == 'former_automation' for i in lease['items'])
            attention.placed(lease['attention_lease_id'])
            attention.responded(lease['attention_lease_id'])
            attention.acknowledge(lease['attention_lease_id'], through_cursor=lease['through_cursor'])
            AttentionService(store, journal, scope).capture_former_automation()
            with store._connect() as c:
                assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='former_automation'").fetchone()[0] == expected
            # Stale fields cannot qualify cancellation.
            projection = worlds.load(scope, attention.timeline_id)
            for obj in projection['objects']:
                if obj['object_ref'] == 'own-unit-2000': obj['fields']['terraform_task']['epistemic_status'] = 'stale'
            assert former_state(projection, 'own-unit-2000') is None
            assert journal.verify(scope)['ok']
            return {'mode': mode, 'notices': expected}
        finally:
            mcp._runtime_services = original
            mcp.MANAGED_ATTACHED = attached


if __name__ == '__main__':
    print(json.dumps({'passed': True, 'cases': [run(mode) for mode in
        ['stopped', 'repeated', 'active', 'pending', 'movement', 'replacement', 'superseded', 'overlap', 'epoch', 'crash', 'rejected', 'tile_change', 'unknown']]}))
