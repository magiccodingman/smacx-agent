#!/usr/bin/env python3
"""CLI over HTTP: cookies/CSRF, exact roster, restart-safe create/start, packet export."""
import io
import json
import tempfile
import time
import threading
import zipfile
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from smacx_ops import Client, main, OpsError

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)
    state={'lobby':None,'starts':0,'creates':0, 'ready':True}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self): self.handle_request('GET')
        def do_POST(self): self.handle_request('POST')
        def do_PUT(self): self.handle_request('PUT')
        def handle_request(self,method):
            path=self.path.split('?')[0]
            body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or b'{}')
            if method!='GET': assert self.headers.get('X-CSRF-TOKEN')=='csrf-test'
            if path=='/api/auth/csrf': value={'token':'csrf-test'}
            elif path=='/api/auth/login':
                assert body['password']=='test-only-password';value={'authenticated':True}
            else:
                assert 'operator-session=authenticated' in self.headers.get('Cookie','')
                if path=='/api/operator/preflight': value={'schema':'smacx.operator-preflight.v1','installation_id':'installation-test','prerequisites_ready':state['ready']}
                elif path=='/api/catalog/lobby': value={'controlConnected':True, **{k:[{'id':i}] for k,i in [('gameSources','source-test'),('runtimes','runtime-test'),('agents','agent-test')]}}
                elif path=='/api/lobbies' and method=='POST':
                    assert body['worldSize']=='standard' and body['difficulty']=='librarian' and body['allowSpectators']
                    if not state['lobby']:
                        state['creates']+=1
                        state['lobby']={'matchId':'match-cli-test','status':'waiting','seats':[{'seatIndex':i,'controllerKind':'open'} for i in range(7)]}
                    value=state['lobby']
                elif '/seats/' in path:
                    index=int(path.rsplit('/',1)[1]);state['lobby']['seats'][index]={**body,'seatIndex':index,
                        'requestedFactionId':body['factionId'],'requestedPersonalityId':body['personalityId']}
                    value=state['lobby']
                elif path.endswith('/start'):
                    state['starts']+=1
                    if state.get('start_gate'): state['start_gate'].wait(10)
                    state['lobby']['status']='running';value=state['lobby']
                    if state.get('start_done'):state['start_done'].set()
                elif path.endswith('/diagnostics'):
                    archive=io.BytesIO()
                    with zipfile.ZipFile(archive,'w') as z:z.writestr('manifest.json','{}')
                    self.send_response(200);self.send_header('Content-Type','application/zip');self.end_headers();self.wfile.write(archive.getvalue());return
                elif path.endswith('/health'):value={'installation_id':'installation-test','match_status':'running','live_sovereign_processes':1,'world_heads':[{}],'workers':[{'running':True,'health':'healthy','mcp':{'running':True,'health':'healthy'}}],'incidents':[]}
                elif '/operator/' in path:value={'schema':'fixture','events':[],'next_cursor':'cursor-fixture'}
                else:value=state['lobby']
            self.send_response(200);self.send_header('Content-Type','application/json')
            if path=='/api/auth/login':self.send_header('Set-Cookie','operator-session=authenticated; Path=/; HttpOnly')
            self.end_headers();self.wfile.write(json.dumps({'ok':True,'data':value}).encode())
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        session=root/'session'
        Client(url,session).login('admin','test-only-password')
        assert session.stat().st_mode & 0o077==0
        args=['--url',url,'--session-file',str(session),'--json']
        def command(*parts):
            out=io.StringIO()
            with redirect_stdout(out): code=main(args+list(parts))
            assert code==0,out.getvalue()
            return json.loads(out.getvalue())
        for _ in range(2): assert command('create','--name','Test','--request-id','cli-retry-001')['roster_verified']
        assert state['creates']==1
        assert state['lobby']['seats'][0]['controllerKind']=='agent'
        assert state['lobby']['seats'][0]['personalityId']=='none'
        assert [s['requestedFactionId'] for s in state['lobby']['seats'][:5]]==['peacekeepers','hive','university','morgan','spartans']
        for _ in range(2):assert command('start','match-cli-test')['completed']
        assert state['starts']==1
        assert command('preflight','--expect-installation','installation-test')['prerequisites_ready']
        try: command('preflight','--expect-installation','installation-wrong')
        except OpsError: pass
        else: raise AssertionError('wrong installation accepted')
        boot=['bootstrap','--expect-installation','installation-test','--state-file',str(root/'mission.json'),
              '--name','Test','--request-id','cli-retry-001','--source','source-test','--runtime','runtime-test','--agent','agent-test','--wait','2']
        assert command(*boot)['phase']=='native_ready'
        assert command(*boot)['phase']=='native_ready'
        assert state['starts']==1
        saved=json.loads((root/'mission.json').read_text())
        assert saved['match_id']=='match-cli-test'
        assert (root/'mission.json').stat().st_mode & 0o077 == 0
        state['lobby']['status']='waiting'
        saved['start_submitted']=True
        (root/'mission.json').write_text(json.dumps(saved))
        try: command(*boot)
        except OpsError as e: assert 'unresolved' in str(e)
        else: raise AssertionError('ambiguous start retried')
        assert state['starts']==1
        state['lobby']['status']='running'
        state['lobby']['status']='waiting';state['start_gate']=threading.Event();state['start_done']=threading.Event()
        slow=list(boot);slow[slow.index('--state-file')+1]=str(root/'slow.json');slow[-1]='0.05'
        out=io.StringIO();ended=threading.Event();codes=[]
        def run_slow():
            try:
                with redirect_stdout(out):codes.append(main(args+slow))
            finally:ended.set()
        caller=threading.Thread(target=run_slow);caller.start()
        try: assert ended.wait(5), 'deadline waited for background POST'
        finally:state['start_gate'].set();caller.join(5)
        assert codes==[2]
        assert json.loads(out.getvalue().splitlines()[-1])['phase']=='startup_unverified'
        assert state['start_done'].wait(5)
        state.pop('start_gate');state.pop('start_done')
        assert command(*slow)['phase']=='native_ready'
        assert state['starts']==2, 'ambiguous start was duplicated'
        packet=command('packet','match-cli-test','--output',str(root/'packet'))
        assert packet['complete'] and len(packet['files'])==4
        assert zipfile.is_zipfile(root/'packet/diagnostics.zip')
        session.chmod(0o644)
        try:Client(url,session)
        except OpsError:pass
        else:raise AssertionError('public cookie file accepted')
        print(json.dumps({'passed':True,'http_cookie_csrf':True,'preset_verified':True,
            'repeated_create_start':True,'packet_hash_manifest':True}))
    finally:server.shutdown();server.server_close();thread.join()
