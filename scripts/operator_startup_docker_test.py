#!/usr/bin/env python3
"""Opt-in real Docker exit/log capture. Not a native-game startup test."""
import json
import os
import time
import uuid
from types import SimpleNamespace
from smacx_docker import DockerClient
from smacx_worker_manager import WorkerManager, WorkerManagerError

image=os.environ.get('SMACX_TEST_CONTROL_IMAGE')
if not image:raise SystemExit('Set SMACX_TEST_CONTROL_IMAGE to a local control image for this opt-in Docker test.')
docker=DockerClient()
manager=object.__new__(WorkerManager);manager.docker=docker
installation='installation-startup-capture-test'
manager.store=SimpleNamespace(installation_id=lambda:installation)
identifier=docker.create_container('smacx-startup-capture-'+uuid.uuid4().hex[:12],{
 'Image':image,'Entrypoint':['python3','-c'],
 'Cmd':['import sys; print("controlled startup failure",flush=True); sys.exit(23)'],
 'Labels':docker.labels(installation,'mcp-sidecar'),
 'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'CapDrop':['ALL'],
               'SecurityOpt':['no-new-privileges']}})
try:
 docker.start_container(identifier)
 deadline=time.monotonic()+15
 while docker.inspect_container(identifier)['State']['Running']:
  if time.monotonic()>deadline:raise AssertionError('test process did not exit')
  time.sleep(.1)
 receipt=manager._capture_startup_failure(identifier,WorkerManagerError('mcp_sidecar_failed_healthcheck'))
 assert receipt['state']['ExitCode']==23 and receipt['state']['OOMKilled'] is False
 assert 'controlled startup failure' in receipt['log_tail']
 assert receipt['capture_complete']
 print(json.dumps({'passed':True,'actual_docker_exit_code':23,'log_capture':True,'image_id':receipt['image_id']}))
finally:manager._cleanup_container(identifier,'mcp-sidecar')
