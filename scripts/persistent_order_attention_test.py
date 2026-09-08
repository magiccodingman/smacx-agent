#!/usr/bin/env python3
"""Committed order baseline to current observation to leased provider attention."""
import json
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from observation_collector_benchmark import (NativeFixture, SmacxStore, MemoryScope,
    CampaignJournal, WorldStore, ObservationCollector, AttentionService)
from smacx_order_attention import unit_state
from smacx_runtime_context import _attention_payload
import smacx_mcp as mcp


def run(mode):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        store = SmacxStore(root / 'state.sqlite3')
        store.ensure_agent('agent-order', 'Order')
        store.create_match(match_id='match-order', display_name='Order', mode='solo')
        store.create_perspective('match-order', 'agent-order', perspective_id='perspective-order')
        scope = MemoryScope('match-order', 'agent-order', 'perspective-order')
        journal = CampaignJournal(root / 'campaigns', timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / 'snapshots')
        attention = AttentionService(store, journal, scope)
        fixture = NativeFixture(32, 16, contacts=0, ready_drop_units=1)
        unit = fixture.units[0]
        unit.update(order_name='none', ready=True, moves_spent=0)
        def collect():
            ObservationCollector(scope=scope, session_id='session-order', bridge_call=fixture,
                journal=journal, world_store=worlds, attention=attention).collect_once()
        collect()
        projection = worlds.load(scope, attention.timeline_id)
        choice = {'command': 'return_to_base', 'unit_id': unit['id']}
        with patch.object(mcp, 'MANAGED_ATTACHED', True), patch.object(mcp, '_runtime_services', return_value=(None, attention)):
            baseline = mcp._order_attempt_baseline(choice, {'revision': projection['action_revision']}, {'turn': 50})
            assert baseline, projection['objects'][-1]
            assert mcp._order_attempt_baseline(choice, {'revision': 'wrong'}, {}) is None
        if mode == 'epoch': baseline['world_epoch'] = 'other'
        if mode == 'epoch':
            journal.append(scope, 'game.action', {'persistent_order_attempt': baseline, 'choice_parameters': choice})
        else:
            identity = {'match_id': scope.match_id, 'session_id': 'session-order',
                        'revision': projection['action_revision']}
            mcp.DECISION_CACHE['decision-order'] = {'created_monotonic': time.monotonic(),
                'identity': identity, 'choices': {'choice-order': choice}, 'turn': 50}
            mcp.ACTION_PROGRESS.clear()
            def record(match, session, payload, **kwargs):
                assert payload['persistent_order_attempt'] == baseline
                event = journal.append(scope, 'game.action', payload)
                return {'ok': True, 'journal_event_id': event['event_id']}
            with patch.object(mcp, 'MANAGED_ATTACHED', True), \
                 patch.object(mcp, '_runtime_services', return_value=(None, attention)), \
                 patch.object(mcp, '_sovereign_gameplay_gate', return_value=None), \
                 patch.object(mcp, '_turn_reconciliation_gate', return_value=None), \
                 patch.object(mcp, '_attach_turn_handoff'), \
                 patch.object(mcp, '_call', return_value={'ok': True, 'snapshot': {'turn': 50}}), \
                 patch.object(mcp, 'smac_command', return_value={'ok': True, 'order': 'go_to'}), \
                 patch.object(mcp, 'controller_record_campaign_action', side_effect=record):
                assert mcp._execute_choice_once('decision-order', 'choice-order')['ok']
        attention.capture_persistent_orders()
        if mode == 'pending': unit.update(order_name='go_to', ready=False)
        if mode == 'spent': unit.update(ready=False, moves_spent=3)
        if mode == 'movement': unit['tile_id'] = fixture.tiles[8]['tile_id']
        if mode == 'superseded': journal.append(scope, 'game.action', {'choice_parameters': choice})
        fixture.revision += 1
        collect()
        if mode == 'crash':
            append = journal.append
            def fail(scope, kind, *args, **kwargs):
                if kind == 'attention.persistent_order_checked': raise RuntimeError('injected')
                return append(scope, kind, *args, **kwargs)
            with patch.object(journal, 'append', side_effect=fail):
                try: attention.capture_persistent_orders()
                except RuntimeError: pass
                else: raise AssertionError('expected crash')
        lease = attention.lease('episode-order')
        notices = [i for i in lease['items'] if i['attention_kind'] == 'persistent_order']
        expected = mode in {'cleared', 'movement', 'crash'}
        assert len(notices) == int(expected), (mode, notices)
        if notices:
            payload = _attention_payload(notices[0])
            assert payload['cause'] == 'unknown' and payload['arrival_verified'] is False
            assert payload['guidance']
        attention.placed(lease['attention_lease_id'])
        attention.responded(lease['attention_lease_id'])
        attention.acknowledge(lease['attention_lease_id'], through_cursor=lease['through_cursor'])
        AttentionService(store, journal, scope).capture_persistent_orders()
        with store._connect() as c:
            assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='persistent_order'").fetchone()[0] == int(expected)
        projection = worlds.load(scope, attention.timeline_id)
        target = next(o for o in projection['objects'] if o['object_ref'] == baseline['before']['unit_ref'])
        target['fields']['order_name']['epistemic_status'] = 'stale'
        assert unit_state(projection, target['object_ref']) is None
        assert journal.verify(scope)['ok']
        return mode

if __name__ == '__main__':
    print(json.dumps({'passed': [run(m) for m in ('cleared', 'pending', 'spent', 'movement', 'superseded', 'epoch', 'crash')]}))
