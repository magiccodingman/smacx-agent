#!/usr/bin/env python3
"""Isolated native turn transition; no existing campaign is resumed or mutated."""
import json
import os
from pathlib import Path
import tempfile
import time
import semantic_playthrough as play
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore, MemoryScope
from smacx_worker_manager import WorkerManager


def main():
    assert os.environ.get('SMACX_AGENT_TEST_MODE') == '1'
    assert os.environ.get('SMACX_ACCEPTANCE_MANAGED_ACTIONS') == '1'
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix='smacx-end-turn-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='End turn native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-end-turn-test', 'End turn test')
            match = control.create_solo_match('End turn native test', 'agent-end-turn-test', faction_id=1)
            scope = MemoryScope(match['match']['match_id'], 'agent-end-turn-test', match['perspective']['perspective_id'])
            worker = manager.provision_worker(scope, source['game_source_id'], runtime['runtime_id'],
                autostart={'enabled': True, 'difficulty': 0, 'world_size': 0, 'faction_id': 1}, view_enabled=False)
            manager.start_worker(worker['instance_id'], timeout=300)
            def call(op, **args):
                timeout = args.pop('timeout', 20)
                return manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
            play.bridge_request = call
            start = None
            action_id = None
            refusal_verified = False
            gate_set = False
            last_diagnostic = None
            for _ in range(180):
                snap = call('semantic_snapshot').get('snapshot', {})
                if not snap:
                    time.sleep(.25); continue
                engine = snap['interaction']['engine_state']
                diagnostic = {'turn': snap['turn'], 'kind': snap['interaction']['kind'], 'engine': engine, 'action': snap.get('last_deferred_action')}
                if diagnostic != last_diagnostic:
                    print(json.dumps({'event': 'native_state', 'payload': diagnostic}), flush=True)
                    last_diagnostic = diagnostic
                assert all(isinstance(engine.get(k), bool) for k in (
                    'base_window_visible', 'native_turn_complete_flag', 'native_human_turn_input_active', 'end_turn_timer_queued',
                    'end_turn_native_returned', 'end_turn_receipt_pending')), engine
                if start is not None and snap['turn'] > start:
                    assert action_id is not None and refusal_verified, 'explicit refusal/retry not exercised'
                    receipt = call('action_status', action_id=action_id)['action']
                    assert receipt['status'] == 'completed' and receipt['native_call_attempted'] is True, receipt
                    assert not engine['end_turn_receipt_pending'], engine
                    print(json.dumps({'passed': True, 'classification': 'isolated guarded native end-turn acceptance',
                        'source_turn': start, 'observed_turn': snap['turn'], 'receipt': receipt,
                        'engine_state': {k: engine[k] for k in engine if k.startswith('end_turn_')},
                        'controlled_native_refusal_verified': refusal_verified, 'original_turn69_refusal_reproduced': False}), flush=True)
                    return
                if snap['interaction']['kind'] != 'turn':
                    play.handle_interaction(snap)
                    time.sleep(.25); continue
                if start is None:
                    setup = call('test_managed_action_fixture', phase='diagnostics_explicit_turn_boundary')
                    assert setup.get('ok'), setup
                    start = snap['turn']
                if action_id is not None and not refusal_verified:
                    refused = call('action_status', action_id=action_id)['action']
                    assert refused['status'] == 'rejected' and refused['resolution'] == 'native_turn_transition_not_accepted', refused
                    assert engine['end_turn_native_returned'] and not engine['end_turn_receipt_pending'], engine
                    assert call('test_managed_action_fixture', phase='diagnostics_end_turn_release').get('ok')
                    refusal_verified = True
                    action_id = None
                units = call('list_units', scope='own', limit=256).get('items', [])
                ready = next((u for u in units if u.get('ready')), None)
                if ready:
                    choices = call('semantic_choices', kind='unit_actions', unit_id=ready['id'])
                    result = play.command(choices, 'skip_unit', unit_id=ready['id'])
                else:
                    if not gate_set:
                        assert call('test_managed_action_fixture', phase='diagnostics_end_turn_refusal').get('ok')
                        gate_set = True
                    choices = call('semantic_choices', kind='game_management')
                    result = play.command(choices, 'end_turn')
                    if result.get('ok'): action_id = result.get('action_id')
                print(json.dumps({'event': 'native_command', 'result': result}), flush=True)
                assert result.get('ok') or result.get('error', {}).get('code') == 'stale_state', result
                time.sleep(.25)
            raise AssertionError('native turn failed to advance within bounded observation window')
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
