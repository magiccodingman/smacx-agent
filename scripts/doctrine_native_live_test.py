#!/usr/bin/env python3
"""Isolated native receipt/adapter smoke. Does not mutate the user's running stack."""
import json
import os
from pathlib import Path
import tempfile
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import SmacxStore,MemoryScope
from smacx_worker_manager import WorkerManager
from smacx_doctrine import compile_doctrine


def main():
    source_path=os.environ.get('SMACX_TEST_GAME_SOURCE')
    if not source_path:raise RuntimeError('SMACX_TEST_GAME_SOURCE_required')
    docker=DockerClient();worker=None
    with tempfile.TemporaryDirectory(prefix='smacx-doctrine-native-') as tmp:
        control=ControlPlane(SmacxStore(Path(tmp)/'state.sqlite3'),Path(tmp)/'secrets')
        manager=WorkerManager(control,docker,worker_image='smacx-agent-worker:doctrine-review',mcp_image='smacx-agent-control:doctrine-review')
        try:
            source=manager.validate_game_source(source_path,display_name='Doctrine native source')
            runtime=manager.ensure_bundled_runtime()
            control.store.ensure_agent('agent-doctrine-live','Doctrine Live')
            created=control.create_solo_match('Doctrine native receipt','agent-doctrine-live',match_id='match-doctrine-live',faction_id=1)
            scope=MemoryScope('match-doctrine-live','agent-doctrine-live',created['perspective']['perspective_id'])
            worker=manager.provision_worker(scope,source['game_source_id'],runtime['runtime_id'],
                autostart={'enabled':True,'difficulty':3,'world_size':0,'faction_id':1},view_enabled=False)
            manager.start_worker(worker['instance_id'],timeout=300)
            receipt=manager._native_request(worker['instance_id'],'doctrine_context',timeout=20)
            print(json.dumps({'native_receipt':receipt}),flush=True)
            context=manager.confirm_gameplay_context(worker['instance_id'])
            compiled=compile_doctrine(context)
            again=manager.confirm_gameplay_context(worker['instance_id'])
            assert again==context
            print(json.dumps({'passed':True,'classification':'isolated running-game UI-thread public receipt and adapter',
                'context':context,'metadata':compiled['metadata'],'repeat_identical':True}))
        finally:
            if worker:
                manager.park_worker(worker['instance_id'])
                for name,purpose in [(worker['network']['secret_volume'],'worker-secret'),(worker['data_volume'],'worker-data')]:
                    docker.require_owned(docker.inspect_volume(name),manager.installation_id,purpose=purpose)
                    docker.remove_volume(name)

if __name__=='__main__':main()
