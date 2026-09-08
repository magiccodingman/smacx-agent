#!/usr/bin/env python3
"""Interrupted owner helper recovery preserves installation and live-process isolation."""
from types import SimpleNamespace
from smacx_harness_manager import HarnessManager, HarnessManagerError
from smacx_docker import DockerNotFound

class Docker:
    def __init__(self, running=False, identity='profile', owned=True):
        self.running,self.identity,self.owned=running,identity,owned
        self.removed=[];self.created=False
    def inspect_container(self,name):
        return {'State':{'Running':self.running},'Config':{'Labels':{'io.smacx.harness':self.identity}}}
    def require_owned(self,*args,**kwargs):
        if not self.owned:raise RuntimeError('foreign installation')
    def remove_container(self,name):self.removed.append(name)
    def create_container(self,name,config):self.created=True;return 'new-helper'
    def start_container(self,name):pass
    def wait_container(self,*args,**kwargs):return {'State':{'ExitCode':0}}

for running,identity,owned,success in [(False,'profile',True,True),(True,'profile',True,False),(False,'other',True,False),(False,'profile',False,False)]:
    d=Docker(running,identity,owned)
    manager=object.__new__(HarnessManager);manager.docker=d
    manager.installation_id='installation';manager.worker_manager=SimpleNamespace(mcp_image='test',resource_prefix='smacx-test')
    manager._labels=lambda *a,**kw:kw
    try:manager._claim_data_volume('volume',identity='profile')
    except (HarnessManagerError,RuntimeError):assert not success
    else:assert success
    assert d.created is success
    assert bool(d.removed) is success
print('harness owner orphan: passed')
