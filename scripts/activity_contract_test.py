#!/usr/bin/env python3
"""Projection paging cannot expose prompts or replay completed stream chunks."""
import json
import tempfile
from pathlib import Path
from smacx_activity import read_page, public_value
from smacx_diagnostics import DiagnosticWriter

with tempfile.TemporaryDirectory() as root:
    w=DiagnosticWriter(Path(root),'match-test','sovereign')
    c={'request_id':'request-one'}
    w.emit('provider_request_submitted',{'body':{'model':'Qwen','reasoning_effort':'low',
        'messages':[{'content':'PRIVATE PROMPT'}],'api_key':'SECRET'}},correlation=c)
    w.emit('provider_activity_delta',{'chunks':[{'choices':[{'delta':{'content':'Hello','reasoning_content':'Emitted thought'}}]}]},correlation=c)
    first=read_page(root,'match-test',limit=1)
    assert first['has_more'] and len(first['events'])==1
    second=read_page(root,'match-test',first['cursor'])
    assert len(second['events'])==1
    assert not read_page(root,'match-test',second['cursor'])['events']
    w.emit('provider_response_stream',{'chunks':[{'content':'Hello'}],'done_marker_observed':True},correlation=c)
    third=read_page(root,'match-test',second['cursor'])
    assert third['events'][0]['payload']=={'type':'response_finished','complete':True,'truncated':False}
    assert 'PRIVATE PROMPT' not in json.dumps(first) and 'SECRET' not in json.dumps(first)
    assert not read_page(root,'match-other')['events']
    path=next((Path(root)/'match-test').glob('activity-*.jsonl'))
    with path.open('ab') as f:f.write(b'{"unfinished":')
    tail=read_page(root,'match-test',third['cursor'])
    assert not tail['events'] and tail['cursor']==third['cursor']
    try:read_page(root,'match-test','{"x":-1}')
    except ValueError:pass
    else:raise AssertionError('invalid cursor accepted')
print('PASS: scoped incremental capture, prompts excluded, final receipt not duplicated, partial tail retry')
assert 'SECRET' not in public_value('{"api_key":"SECRET"}')
assert 'abc123' not in public_value('Bearer abc123')
