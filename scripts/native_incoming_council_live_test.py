#!/usr/bin/env python3
"""Isolated AI-called council validation; production campaign is never resumed."""
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
        class CouncilManager(WorkerManager):
            def _worker_environment(self, spec, session_id):
                return super()._worker_environment(spec, session_id) + ["SMACX_AGENT_TEST_COUNCIL=1", "SMACX_AGENT_TEST_INCOMING_COUNCIL=1"]
        manager = CouncilManager(control, docker,
            worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        worker = None
        try:
            source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='End turn native test')
            runtime = manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-end-turn-test', 'End turn test')
            match = control.create_solo_match('End turn native test', 'agent-end-turn-test', faction_id=4)
            scope = MemoryScope(match['match']['match_id'], 'agent-end-turn-test', match['perspective']['perspective_id'])
            worker = manager.provision_worker(scope, source['game_source_id'], runtime['runtime_id'],
                autostart={'enabled': True, 'difficulty': 0, 'world_size': 0, 'faction_id': 4}, view_enabled=False)
            manager.start_worker(worker['instance_id'], timeout=300)
            def call(op, **args):
                timeout = args.pop('timeout', 20)
                return manager._native_request(worker['instance_id'], op, timeout=timeout, **args)
            play.bridge_request = call
            import incoming_council_test as council
            council.bridge_request = call
            council.new_game = lambda **kwargs: {"ok": True, "already_autostarted": True}
            assert council.main() == 0, "incoming native council acceptance failed"
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name, purpose in ((worker['network']['secret_volume'], 'worker-secret'), (worker['data_volume'], 'worker-data')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)

if __name__ == '__main__':
    main()
