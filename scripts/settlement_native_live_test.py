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
    with tempfile.TemporaryDirectory(prefix='smacx-settlement-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='Settlement native comparison')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-support-test', 'Support test')
            match = control.create_solo_match('Settlement native comparison', 'agent-support-test', faction_id=1)
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
                fixture = call('test_managed_action_fixture')
                assert fixture.get('ok') or fixture.get('error',{}).get('code') == 'fixture_actor_unavailable', fixture
                units = call('perspective_world_page', domain='units', cursor=0, limit=256)['items']
                colonies = [v for v in units if v.get('roles',{}).get('colony')]
                receipt = call('semantic_base_site_receipts',target_tile_ids=[v['tile_id'] for v in colonies])
                legal = {r['tile_id'] for r in receipt['items'] if r.get('legal_for_land_colony')}
                colony = next(v for v in colonies if v['tile_id'] in legal)
                tile = colony['tile_id']
                safe = call('test_counterfactual_read_safety', kind='site_economy', include_economy=True,
                            target_tile_ids=[tile], terrain_potential=True, check_hidden_independence=True)
                assert safe.get('ok') and safe['receipt'].get('ok'), safe
                preview = safe['receipt']['items'][0]
                assert preview['site_economy']['nutrients_per_citizen'] > 0
                assert preview['site_economy']['benchmark_colony_mineral_cost'] > 0
                center = preview['site_economy']['center']['yields']
                frame = call('semantic_choices', kind='unit_actions', unit_id=colony['id'])
                assert any(c.get('command') == 'found_base' for c in frame['choices']), frame
                action = call('semantic_command', command='found_base', unit_id=colony['id'],
                    match_id=frame['match_id'], session_id=frame['session_id'], expected_revision=frame['revision'])
                assert action.get('ok'), action
                for _ in range(80):
                    bases = call('perspective_world_page',domain='bases',cursor=0,limit=64)['items']
                    founded = next((b for b in bases if b.get('tile_id')==tile),None)
                    if founded:
                        actual = next(t['yields'] for t in founded['base_radius'] if t['location_ref']==f'location-{tile}')
                        assert actual == center, (actual,center)
                        print(json.dumps({'passed':True,'native_state_restored':safe['native_probe_state_unchanged'],
                            'hidden_input_independence':safe['hidden_input_independence'],
                            'founding_center_comparison':True,'center_yields':actual}),flush=True)
                        return
                    time.sleep(.1)
                raise AssertionError('founding effect unavailable')
            raise AssertionError('no actionable native state within bounded observation window')
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
