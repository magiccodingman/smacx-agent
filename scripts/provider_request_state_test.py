#!/usr/bin/env python3
"""Observe pending transport without confusing headers with completion."""
import json,os,tempfile
from pathlib import Path
import httpx
from smacx_diagnostics import DiagnosticWriter,install_httpx_capture,record_provider_phase
with tempfile.TemporaryDirectory() as root:
    os.environ.update(SMACX_HARNESS_RUN_ID='run-test',SMACX_STRICT_SYSTEM_PROMPT='1',SMACX_DIAGNOSTICS_ROOT=root)
    path=Path(root)/'provider-request.json'
    read=lambda:json.loads(path.read_text())
    class Client(httpx.Client): pass
    install_httpx_capture(Client,DiagnosticWriter(Path(root),'match-test','sovereign'))
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            assert read()['phase']=='headers'
            yield b'data: [DONE]\n\n'
    def transport(request):
        assert read()['phase']=='submitted'
        return httpx.Response(200,stream=Stream())
    with Client(transport=httpx.MockTransport(transport)) as c:
        with c.stream('POST','https://example.invalid/v1/chat/completions',json={}) as response:
            assert read()['phase']=='headers'
            response.read()
            assert read()['phase']=='completed'
        with c.stream('POST','https://example.invalid/v1/chat/completions',json={}): pass
        assert read()['phase']=='closed_incomplete'
    def fail(request): raise httpx.ConnectError('not recorded')
    with Client(transport=httpx.MockTransport(fail)) as c:
        try:c.post('https://example.invalid/v1/chat/completions',json={})
        except httpx.ConnectError:pass
        else:raise AssertionError('transport exception swallowed')
    assert read()['phase']=='failed'
    record_provider_phase('new-request','submitted',123)
    record_provider_phase('old-request','completed',122)
    assert read()['request_id']=='new-request'
    before=path.read_bytes()
    os.environ['SMACX_SPECIALIST_STRICT_PROMPT']='1'
    record_provider_phase('specialist','submitted',124)
    assert path.read_bytes()==before
    assert len(before)<512
print('PASS: submitted, headers, completion, early close, failure, stale completion and specialist exclusion')
