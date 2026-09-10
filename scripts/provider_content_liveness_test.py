import os,json,tempfile
from pathlib import Path
from unittest.mock import patch
from smacx_diagnostics import record_provider_phase
with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'SMACX_DIAGNOSTICS_ROOT':tmp,'SMACX_STRICT_SYSTEM_PROMPT':'1','SMACX_HARNESS_RUN_ID':'run-test'}):
 p=Path(tmp)/'provider-request.json'
 with patch('smacx_diagnostics.time.time',return_value=100):record_provider_phase('request-one','submitted',100)
 with patch('smacx_diagnostics.time.time',return_value=110):record_provider_phase('request-one','headers',100)
 assert 'last_content_unix' not in json.loads(p.read_text())
 with patch('smacx_diagnostics.time.time',return_value=120):record_provider_phase('request-one','streaming',100)
 assert json.loads(p.read_text())['last_content_unix']==120
 with patch('smacx_diagnostics.time.time',return_value=120.2):record_provider_phase('request-one','streaming',100)
 assert json.loads(p.read_text())['last_content_unix']==120
 with patch('smacx_diagnostics.time.time',return_value=130):record_provider_phase('request-one','completed',100)
 assert json.loads(p.read_text())['last_content_unix']==120
 record_provider_phase('request-one','streaming',100)
 assert json.loads(p.read_text())['phase']=='completed'
 record_provider_phase('request-two','submitted',140)
 record_provider_phase('request-one','streaming',100)
 assert json.loads(p.read_text())['request_id']=='request-two'
print('PASS: content watermark isolated, throttled, preserved on completion, never revived')

from smacx_diagnostics import provider_chunk_has_content as content
for delta in ({'role':'assistant'}, {'content':''}, {'tool_calls':[{'id':'x'}]}):
 assert not content({'choices':[{'delta':delta}]})
for delta in ({'content':'hi'}, {'reasoning':'consider'}, {'reasoning_content':'think'}, {'tool_calls':[{'function':{'arguments':'{}'}}]}):
 assert content({'choices':[{'delta':delta}]})
for malformed in (None, [], {'choices':None}, {'choices':[{'delta':[]}]}):assert not content(malformed)
print('PASS: generated SSE content recognized; keepalives, empty deltas and malformed chunks excluded')
