#!/usr/bin/env python3
"""Deployment read-back rejects stale containers and mismatched child settings."""
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from smacx_ops import deployment

env={k:'value-'+k for k in ('SMACX_WORKER_IMAGE','SMACX_MCP_IMAGE','SMACX_HERMES_IMAGE','SMACX_DOCKER_NETWORK','SMACX_CONTROL_DATA_VOLUME')}
config={'services':{'control-api':{'image':'control:test','environment':env},'control-center':{'image':'portal:test'}}}
for fault in (None,'image','settings','project','stopped'):
 with tempfile.TemporaryDirectory() as tmp:
  def run(command, **kwargs):
   args=command[1:]
   if 'config' in args:value=config
   elif 'ps' in args:return SimpleNamespace(returncode=0,stdout=args[-1]+'\n')
   elif args[:2]==['image','inspect']:value=[{'Id':'sha256:expected'}]
   elif args[0]=='inspect':
    local=dict(env)
    if fault=='settings':local['SMACX_MCP_IMAGE']='wrong-image'
    value=[{'Image':'sha256:stale' if fault=='image' else 'sha256:expected',
     'State':{'Running':fault!='stopped','Health':{'Status':'healthy'}},
     'Config':{'Labels':{'com.docker.compose.project':'wrong' if fault=='project' else 'test'},
       'Env':[k+'='+v for k,v in local.items()]}}]
   else:raise AssertionError(args)
   return SimpleNamespace(returncode=0,stdout=json.dumps(value))
  output=Path(tmp)/'images.json'
  with patch('smacx_ops.subprocess.run',side_effect=run),redirect_stdout(io.StringIO()):
   result=deployment(SimpleNamespace(compose='fixture.json',project='test',output=str(output)))
  assert result==(0 if fault is None else 2)
  assert output.exists() is (fault is None)
print(json.dumps({'passed':True,'stale_image_rejected':True,'wrong_project_rejected':True,'child_settings_verified':True}))
