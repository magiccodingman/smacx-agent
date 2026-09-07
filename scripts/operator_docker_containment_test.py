#!/usr/bin/env python3
"""Real owned Docker process containment; no native mechanics or production match."""
import json
import tempfile
from pathlib import Path
from smacx_store import SmacxStore, MemoryScope
from smacx_control import ControlPlane
from smacx_docker import DockerClient, DockerNotFound
from smacx_worker_manager import WorkerManager
from smacx_operator import OperatorService

with tempfile.TemporaryDirectory() as tmp:
    store=SmacxStore(Path(tmp)/'state.sqlite3');control=ControlPlane(store,Path(tmp)/'secrets')
    store.ensure_agent('agent-containment','Fixture')
    store.create_match(match_id='match-containment',display_name='Fixture',mode='solo')
    store.create_perspective('match-containment','agent-containment',perspective_id='perspective-containment')
    scope=MemoryScope('match-containment','agent-containment','perspective-containment')
    store.register_instance(instance_id='instance-containment',scope=scope)
    docker=DockerClient();manager=WorkerManager(control,docker)
    installation=store.installation_id()
    names={purpose:f'smacx-ops-test-{installation[-10:]}-{purpose}' for purpose in ('game-worker','mcp-sidecar','harness-run')}
    created=[]
    try:
        for purpose,name in names.items():
            docker.create_container(name,{'Image':'smacx-agent-control:operator-tools','Entrypoint':['/bin/sleep'],
                'Cmd':['600'],'Labels':docker.labels(installation,purpose),'HostConfig':{'NetworkMode':'none'}})
            created.append((name,purpose));docker.start_container(name)
        spec={'match_id':scope.match_id,'instance_id':'instance-containment','container_name':names['game-worker'],
              'image_ref':'smacx-agent-control:operator-tools','network':{'mcp_container_name':names['mcp-sidecar']}}
        control.list_worker_specs=lambda:[spec]
        control.get_worker_spec=lambda _:spec
        run={'run_id':'run-containment','match_id':scope.match_id,'status':'running',
             'container_name':names['harness-run'],'metadata':{}}
        control.list_harness_runs=lambda:[run]
        def update(_,**kwargs):run.update(kwargs);return run
        control.update_harness_run=update
        report=OperatorService(control,manager).pause(scope.match_id)
        assert report['containment_verified'],report
        assert docker.inspect_container(names['game-worker'])['State']['Paused']
        assert not docker.inspect_container(names['mcp-sidecar'])['State']['Running']
        try:docker.inspect_container(names['harness-run'])
        except DockerNotFound:pass
        else:raise AssertionError('sovereign process remains')
        assert not report['checkpoint_created'] and report['state_preservation']=='process_memory_only'
        assert control.get_match(scope.match_id)['metadata']['recovery_required']
        again=OperatorService(control,manager).pause(scope.match_id)
        assert again['containment_verified'] and again['incidents']==report['incidents']
        print(json.dumps({'passed':True,'real_docker':True,'native_process_frozen':True,
            'collector_stopped':True,'sovereign_process_removed':True,'idempotent_pause':True,
            'verified_checkpoint_claimed':False,'native_game_started':False}))
    finally:
        for name,purpose in reversed(created):
            try:
                item=docker.inspect_container(name);docker.require_owned(item,installation,purpose=purpose)
                if item['State'].get('Paused'):docker.unpause_container(name)
                if item['State'].get('Running'):docker.stop_container(name,timeout=1)
                docker.remove_container(name)
            except DockerNotFound:pass
