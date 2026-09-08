#!/usr/bin/env python3
"""Run native automation regression in an isolated temporary installation."""
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
    with tempfile.TemporaryDirectory(prefix='smacx-automation-progress-') as tmp:
        control = ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'), Path(tmp)/'secrets')
        manager = WorkerManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='Automation progress native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-support-test', 'Support test')
            match = control.create_solo_match('Automation progress native test', 'agent-support-test', faction_id=1)
            scope = MemoryScope(match['match']['match_id'], 'agent-support-test', match['perspective']['perspective_id'])
            worker = manager.provision_worker(scope, source['game_source_id'], runtime['runtime_id'],
                autostart={'enabled': True, 'difficulty': 0, 'world_size': 0, 'faction_id': 1}, view_enabled=False)
            manager.start_worker(worker['instance_id'], timeout=300)
            seeded = False
            def call(op, **args):
                nonlocal seeded
                timeout = args.pop('timeout', 20)
                result = manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
                if not seeded and op == 'semantic_snapshot' and result.get('snapshot', {}).get('interaction', {}).get('kind') == 'turn':
                    fixture = manager._native_request(worker['instance_id'], 'test_managed_action_fixture')
                    assert fixture.get('ok'), fixture
                    seeded = True
                    result = manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
                return result
            play.bridge_request = call
            import smacx_controller
            import native_automation_test as regression
            smacx_controller.bridge_request = call
            regression.bridge_request = call
            # The temporary worker autostart already created this isolated game.
            regression.new_game = lambda **kwargs: {"ok": True, "source": "isolated_worker_autostart"}
            result = regression.main()
            assert result == 0, result
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
