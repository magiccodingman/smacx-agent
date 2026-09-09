#!/usr/bin/env python3
"""Isolated native support comparison; no existing campaign is resumed or mutated."""
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
    with tempfile.TemporaryDirectory(prefix='smacx-switch-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='Production switch native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-support-test', 'Support test')
            match = control.create_solo_match('Production switch native test', 'agent-support-test', faction_id=1)
            scope = MemoryScope(match['match']['match_id'], 'agent-support-test', match['perspective']['perspective_id'])
            worker = manager.provision_worker(scope, source['game_source_id'], runtime['runtime_id'],
                autostart={'enabled': True, 'difficulty': 0, 'world_size': 0, 'faction_id': 1}, view_enabled=False)
            manager.start_worker(worker['instance_id'], timeout=300)
            def call(op, **args):
                timeout = args.pop('timeout', 20)
                return manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
            play.bridge_request = call
            for _ in range(180):
                snap = call('semantic_snapshot').get('snapshot', {})
                if not snap:
                    time.sleep(.25); continue
                if snap['interaction']['kind'] != 'turn':
                    play.handle_interaction(snap)
                    time.sleep(.25); continue
                setup = call('test_managed_action_fixture', phase='production_switch_prepare')
                assert setup.get('ok'), setup
                base_id = setup['base_id']
                before = call('semantic_choices', kind='production', base_id=base_id)
                hurry = play.command(before, 'hurry_production', base_id=base_id)
                assert hurry.get('ok') and hurry['minerals_accumulated'] > 0, hurry
                catalog = call('semantic_choices', kind='production', base_id=base_id)
                assert type(catalog['population']) is int and catalog['population'] > 0
                colonies = [c for c in catalog['choices'] if c.get('name') == 'Colony Pod']
                assert colonies and type(colonies[0]['population_effect']['population_at_query']) is int
                current_name = catalog['current']['name']
                same = next(c for c in catalog['choices'] if c.get('name') == current_name)
                target = next(c for c in catalog['choices'] if c.get('name') == 'Colony Pod')
                assert same['switch_effect']['mineral_change'] == 0, same
                forecast = target['switch_effect']
                assert forecast['epistemic_status'] == 'conditional'
                assert forecast['minerals_after_switch'] == 0 and forecast['retool_penalty'] == 0, forecast
                again = call('semantic_choices', kind='production', base_id=base_id)
                assert again['current'] == catalog['current'], 'read-only forecast changed progress'
                # Execute through the existing guarded native semantic command helper.
                result = play.command(again, 'set_production', base_id=base_id, item_id=target['item_id'])
                assert result.get('ok'), result
                assert result['minerals_before'] == forecast['minerals_before']
                assert result['minerals_accumulated'] == forecast['minerals_after_switch']
                assert result['mineral_change'] == forecast['mineral_change']
                after = call('semantic_choices', kind='production', base_id=base_id)
                assert after['current']['name'] == 'Colony Pod'
                assert after['hurry']['available_energy'] == catalog['hurry']['available_energy']
                print(json.dumps({'passed':True,'classification':'controlled running native hurry then switch',
                    'forecast':forecast,'receipt':result,'hurry':hurry,'query_preserved_state':True}),flush=True)
                return
            raise AssertionError('no actionable native state within bounded observation window')
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
