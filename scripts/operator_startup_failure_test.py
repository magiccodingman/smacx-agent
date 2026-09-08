#!/usr/bin/env python3
"""Exercise actual sidecar failure path and persisted evidence before removal."""
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from smacx_worker_manager import WorkerManager, WorkerManagerError
from smacx_docker import DockerNotFound

for log_error in (False, True):
    with tempfile.TemporaryDirectory() as root:
        os.environ['SMACX_DIAGNOSTICS_ROOT'] = root
        events=[]
        spec={'observed_status':'running','container_name':'worker','data_volume':'data',
            'network':{'secret_volume':'secrets'},'match_id':'match-startup-test',
            'agent_id':'agent-test','perspective_id':'perspective-test','game_source_id':'source-test'}
        secret='sensitive-fixture-value'
        class Docker:
            def inspect_container(self, name):
                if name=='worker': return {'State':{'Running':True},'Config':{'Labels':{'io.smacx.session':'session-test'}}}
                if name!='created':raise DockerNotFound('absent')
                return {'Image':'sha256:test','Config':{'Image':'control:test','Env':['API_KEY='+secret]},
                    'State':{'Status':'exited','Running':False,'ExitCode':23,'OOMKilled':False,
                    'Health':{'Status':'starting','Log':[{'ExitCode':1,'Output':'password=hidden'}]}}}
            def require_owned(self,*args,**kwargs):pass
            def inspect_image(self,*args):return {}
            def inspect_volume(self,*args):return {}
            def labels(self,*args,**kwargs):return {}
            def create_container(self,*args):return 'created'
            def start_container(self,*args):pass
            def container_logs(self,*args,**kwargs):
                events.append('logs')
                if log_error:raise OSError('unavailable')
                return 'Traceback: import failed\n'+secret+'\nAuthorization: Bearer xyz'
        manager=object.__new__(WorkerManager)
        manager.docker=Docker();manager.control_data_volume='control';manager.mcp_image='control:test'
        manager.network_name='network';manager.resource_prefix='smacx-test'
        manager.store=SimpleNamespace(installation_id=lambda:'installation-test',active_timeline_id=lambda _: 'timeline-test')
        def save(instance, network):
            events.append('persist');spec['network']=network.copy()
        manager.control=SimpleNamespace(get_worker_spec=lambda _:spec,update_worker_network=save)
        def cleanup(*args):
            assert spec['network'].get('mcp_startup_failure'), 'cleanup lost failure evidence'
            events.append('cleanup')
        manager._cleanup_container=cleanup
        try:manager._start_mcp_sidecar_locked('instance-test')
        except WorkerManagerError as e:assert str(e)=='mcp_sidecar_failed_healthcheck'
        else:raise AssertionError('failed container accepted')
        evidence=spec['network']['mcp_startup_failure']
        assert evidence['state']['ExitCode']==23 and evidence['state']['OOMKilled'] is False
        assert evidence['health_status']=='starting'  # Does not relabel this as unhealthy or OOM.
        assert secret not in json.dumps(evidence) and 'Bearer xyz' not in json.dumps(evidence)
        assert evidence['capture_complete'] is (not log_error)
        assert events.index('persist')<events.index('cleanup')
        assert spec['network']['mcp_status']=='error'
print(json.dumps({'passed':True,'pre_cleanup_persistence':True,'credential_redaction':True,'log_failure_explicit':True}))
