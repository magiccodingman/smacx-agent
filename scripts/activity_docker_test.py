#!/usr/bin/env python3
"""Opt-in real Docker helper validation; only creates/removes its own fixture volume."""
import os
import uuid
from types import SimpleNamespace
from smacx_docker import DockerClient
from smacx_harness_manager import HarnessManager

image=os.environ.get('SMACX_ACTIVITY_TEST_IMAGE','smacx-agent-control:activity-review')
docker=DockerClient()
identity='activity-test-'+uuid.uuid4().hex
volume=identity+'-data'
docker.create_volume(volume,docker.labels(identity,'harness-data'))
seed=None
try:
    script="""from pathlib import Path
import os
from smacx_diagnostics import DiagnosticWriter
w=DiagnosticWriter(Path('/data/diagnostics'),'match-docker-test','sovereign')
w.emit('provider_request_submitted',{'body':{'model':'test','messages':[{'content':'private'}]}},correlation={'request_id':'one'})
w.emit('provider_activity_delta',{'chunks':[{'choices':[{'delta':{'content':'visible'}}]}]},correlation={'request_id':'one'})
for root,dirs,files in os.walk('/data'):
 os.chown(root,10000,10000)
 for name in files:os.chown(os.path.join(root,name),10000,10000)
"""
    seed=docker.create_container(identity+'-seed',{'Image':image,'User':'0:0','Entrypoint':['python3','-c'],'Cmd':[script],
        'Labels':docker.labels(identity,'activity-test'),
        'HostConfig':{'NetworkMode':'none','Mounts':[{'Type':'volume','Source':volume,'Target':'/data'}]}})
    docker.start_container(seed)
    assert docker.wait_container(seed,timeout=20)['State']['ExitCode']==0, docker.container_logs(seed)
    manager=HarnessManager.__new__(HarnessManager)
    manager.installation_id=identity;manager.docker=docker
    manager.worker_manager=SimpleNamespace(mcp_image=image,resource_prefix='smacx-activity-test')
    manager.control=SimpleNamespace(list_harness_runs=lambda:[{'match_id':'match-docker-test','agent_id':'agent-docker-test','harness_profile_id':'profile-test','status':'stopped'}],
        get_harness_runtime_spec=lambda _: {'data_volume':volume})
    page=manager.activity('match-docker-test','agent-docker-test')
    assert len(page['events'])==2 and 'private' not in str(page)
    assert not manager.activity('match-docker-test','agent-docker-test',page['cursor'])['events']
    assert not manager.activity('match-docker-test','other-agent')['events']
    print('PASS: installed read-only owned Docker helper, stopped-run history, cursor resume and seat scope')
finally:
    if seed:docker.remove_container(seed)
    docker.remove_volume(volume)
