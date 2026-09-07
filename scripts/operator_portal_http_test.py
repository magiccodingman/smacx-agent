#!/usr/bin/env python3
"""Real Kestrel/Identity/CSRF/SQLite smoke; isolated data, no production or native game."""
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from smacx_ops import Client, OpsError

repo=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='smacx-operator-portal-') as tmp:
    root=Path(tmp)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env={**os.environ,'ASPNETCORE_ENVIRONMENT':'Testing','ASPNETCORE_URLS':url,
        'SMACX_PORTAL_DATA':str(root/'data'),'ControlPlane__BaseUrl':'http://127.0.0.1:1/',
        'ControlPlane__ServiceTokenFile':str(root/'no-production-token')}
    with (root/'portal.log').open('w') as log:
        process=subprocess.Popen(['dotnet',str(repo/'portal/Smacx.Portal/bin/Debug/net10.0/Smacx.Portal.dll')],
            cwd=repo/'portal/Smacx.Portal',env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            client=Client(url,root/'session',timeout=5)
            for _ in range(60):
                if process.poll() is not None:raise AssertionError('isolated portal exited')
                try:client.request('api/auth/setup');break
                except OpsError:time.sleep(.25)
            else:raise AssertionError('isolated portal did not become ready')
            token=(root/'data/secrets/bootstrap-token').read_text().strip()
            client.request('api/auth/bootstrap','POST',{'token':token,'password':'FixtureP9!','confirmPassword':'FixtureP9!'})
            # Exercise a fresh CLI login, not the bootstrap session.
            client=Client(url,root/'session',timeout=5)
            client.login('admin','FixtureP9!')
            client=Client(url,root/'session',timeout=5)
            body={'displayName':'Operator HTTP smoke','gameSourceId':'source-fixture','runtimeId':'runtime-fixture',
                'profile':'alien-crossfire','mode':'standard','worldSize':'standard','difficulty':'librarian',
                'randomMap':True,'doOrDie':False,'allowSpectators':True,'managedClientsOnly':False,
                'graphitiEnabled':True,'requestId':'operator-http-001'}
            first=client.request('api/lobbies','POST',body)
            again=client.request('api/lobbies','POST',body)
            assert first['matchId']==again['matchId']
            assert len(client.request('api/lobbies'))==1
            result=subprocess.run([str(repo/'scripts/smacx-ops'),'--url',url,'--session-file',str(root/'session'),
                '--json','status',first['matchId']],capture_output=True,text=True,check=True)
            assert json.loads(result.stdout)['status']=='waiting'
            try:client.request('api/lobbies','POST',{**body,'displayName':'Conflict'})
            except OpsError as error:assert 'request_id_conflict' in str(error)
            else:raise AssertionError('idempotency conflict accepted')
            print(json.dumps({'passed':True,'real_kestrel':True,'real_identity_cookie_csrf':True,
                'cli_process_status':True,'creation_retry_and_conflict':True,'native_game_started':False}))
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
