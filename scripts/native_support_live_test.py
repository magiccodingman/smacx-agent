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
    with tempfile.TemporaryDirectory(prefix='smacx-support-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='Support native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-support-test', 'Support test')
            match = control.create_solo_match('Support native test', 'agent-support-test', faction_id=1)
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
                comparison = call('test_managed_action_fixture', phase='diagnostics_support_comparison')
                assert comparison.get('ok'), comparison
                rows = comparison['comparisons']
                assert len(rows) == 16, rows
                for row in rows:
                    predicted = row['projection']
                    assert predicted['epistemic_status'] == 'conditional', row
                    assert predicted['current_support_minerals'] == row['native_before'], row
                    assert predicted['support_after_one_completion'] == row['native_after'], row
                    assert predicted['additional_support_minerals'] == row['native_after'] - row['native_before'], row
                    assert predicted['exceeds_current_gross_output'] == (row['native_after'] > predicted['gross_mineral_output']), row
                    assert 'no casualty is predicted' in predicted['condition'], row
                assert any(row['native_after'] > row['native_before'] for row in rows)
                setup = call('test_managed_action_fixture', phase='diagnostics_support_shortage')
                assert setup.get('ok'), setup
                for _ in range(100):
                    popup = call('semantic_snapshot')['snapshot']
                    if popup.get('interaction', {}).get('popup_label') == 'NOSUPPORT': break
                    time.sleep(.1)
                else: raise AssertionError('native support notice not observed')
                catalog = call('semantic_choices', kind='interaction')
                information = next(row for row in catalog.get('choices', [])
                    if row.get('event') == 'unit_support_shortage')
                assert information['base_name'] == setup['base_name'], information
                assert information['effect_status'] == 'forced_disband_pending_native_processing', information
                assert 'not a combat loss' in information['meaning'], information
                assert 'base_id' in information, information
                acknowledgement = play.command(catalog, 'acknowledge_popup')
                assert acknowledgement.get('ok'), acknowledgement
                for _ in range(100):
                    observed = call('semantic_snapshot')['snapshot']
                    if observed.get('interaction', {}).get('kind') == 'turn': break
                    time.sleep(.1)
                else: raise AssertionError('support notice did not close')
                assert observed['faction']['units'] == popup['faction']['units'] - 1, (popup, observed)
                print(json.dumps({'passed': True, 'classification': 'controlled running native support comparison',
                    'comparisons': rows, 'forced_disband_popup_exercised': True,
                    'information': information, 'acknowledgement': acknowledgement,
                    'unit_count_before': popup['faction']['units'],
                    'unit_count_after': observed['faction']['units']}), flush=True)
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
