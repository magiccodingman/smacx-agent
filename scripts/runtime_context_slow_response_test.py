#!/usr/bin/env python3
"""A real response beyond ten seconds preserves the pending tool receipt/episode."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

path = Path(os.environ.get('SMACX_TEST_STRICT_PROMPT_PATH',
    str(Path(__file__).resolve().parents[1]/'harness/smacx_strict_prompt.py')))
spec = importlib.util.spec_from_file_location('strict_slow_contract', path)
module = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {'SMACX_STRICT_SYSTEM_PROMPT':'0', 'SMACX_SPECIALIST_STRICT_PROMPT':'0'}):
    spec.loader.exec_module(module)
requests = []
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        assert self.headers.get('Authorization') == 'Bearer fixture-token'
        episode = parse_qs(urlsplit(self.path).query)['episode_id'][0]
        requests.append(episode)
        time.sleep(10.5)
        payload = {'ok':True, 'runtime_context':{
            'schema':'smacx.runtime-context.v1', 'identity':{},
            'episode':{'episode_id':episode}, 'attention':{},
            'working_cognition':{'plans':[{'plan_id':'plan-after-action'}]}}}
        body = json.dumps(payload).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
messages = [{'role':'user','content':'Continue this episode'},
            {'role':'assistant','content':None,'tool_calls':[{'id':'call-verified', 'type':'function',
                'function':{'name':'smac_execute_choice','arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'call-verified',
             'content':'{"ok":true,"execution_status":"order_assigned","journal_event_id":"journal-verified"}'}]
original = copy.deepcopy(messages)
try:
    with patch.dict(os.environ, {'SMACX_RUNTIME_CONTEXT_URL':f'http://127.0.0.1:{server.server_port}/runtime-context',
            'SMACX_AGENT_MATCH_ID':'','SMACX_PERSPECTIVE_ID':''}), \
         patch.object(module,'_runtime_token',return_value='fixture-token'):
        episode = module._episode_id(messages)
        started = time.monotonic()
        module._append_runtime_context(messages)
        elapsed = time.monotonic()-started
    assert elapsed >= 10 and elapsed < module._RUNTIME_CONTEXT_TIMEOUT_SECONDS
    assert requests == [episode]
    assert messages[:-1] == original[:-1]
    assert messages[-1]['tool_call_id'] == 'call-verified'
    assert messages[-1]['content'].split('\n\n'+module._RUNTIME_OPEN)[0] == original[-1]['content']
    assert 'plan-after-action' in messages[-1]['content']
    print(json.dumps({'passed':True,'real_http_delay_seconds':round(elapsed,3),
        'same_episode':True,'pending_receipt_preserved':True,'fresh_context_appended':True}))
finally:
    server.shutdown();server.server_close();thread.join(5)
