#!/usr/bin/env python3
"""Private heartbeat lifecycle and deterministic authority-loss exit."""
import io
import json
import os
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

with patch.dict(os.environ, {'SMACX_STRICT_SYSTEM_PROMPT':'0','SMACX_SPECIALIST_STRICT_PROMPT':'0'}):
    import smacx_strict_prompt as s

class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self,*args): self.close()

receipt={'episode_id':'episode-test','token':'private-owner-token'}
with patch.dict(os.environ, {'SMACX_RUNTIME_CONTEXT_URL':'http://local/runtime-context',
        'SMACX_HARNESS_RUN_ID':'run-test','SMACX_AGENT_SESSION_ID':'session-test'}), \
     patch.object(s,'_runtime_token',return_value='private-auth-token'):
    def send(req,**kwargs):
        assert req.full_url.endswith('/runtime-context/heartbeat')
        body=json.loads(req.data)
        assert body=={**receipt,'run_id':'run-test','session_id':'session-test'}
        return Response(b'{"ok":true}')
    h=s._AuthorityHeartbeat(receipt)
    with patch.object(s,'urlopen',side_effect=send) as transport:
        h.beat()
        assert transport.call_count==1
    with patch.object(h.stopped,'wait',side_effect=[False,True]),patch.object(h,'beat') as beat:
        h._run();beat.assert_called_once();assert not h.lost.is_set()
    with patch.object(h.stopped,'wait',return_value=False),patch.object(h,'beat',side_effect=HTTPError('http://local',409,'lost',{},None)):
        h._run();assert h.lost.is_set()
    s._RUNTIME_STATE.heartbeat=h
    with patch.object(s,'urlopen',side_effect=AssertionError('provider or runtime call after lost authority')), \
         patch.object(s,'_end_runtime_episode') as end:
        try:s._fetch_runtime_context([{'role':'user','content':'play'}]);raise AssertionError('did not yield')
        except SystemExit as exc:assert exc.code==0
        end.assert_called_once_with(committed=False)
    s._RUNTIME_STATE.heartbeat=None
    with patch.object(s,'urlopen',side_effect=HTTPError('http://local',409,'lost',{},Response(b'{"error":"sovereign_episode_authority_lost"}'))), \
         patch.object(s,'_end_runtime_episode') as end:
        try:s._fetch_runtime_context([{'role':'user','content':'play'}]);raise AssertionError('did not yield')
        except SystemExit as exc:assert exc.code==0
        end.assert_called_once_with(committed=False)
    s._RUNTIME_STATE.heartbeat=h
    s._RUNTIME_STATE.episode_id=''
    s._end_runtime_episode(committed=False)
    assert h.stopped.is_set() and s._RUNTIME_STATE.heartbeat is None
print(json.dumps({'passed':True,'private_transport':True,'heartbeat_runs_without_provider_call':True,
 'lost_authority_clean_exit_before_next_request':True,'episode_end_stops_heartbeat':True}))
