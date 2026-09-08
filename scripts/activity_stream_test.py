#!/usr/bin/env python3
"""HTTP transport emits visible activity before completion, without changing bytes."""
import json
import tempfile
from pathlib import Path
import httpx
from smacx_diagnostics import DiagnosticWriter, install_httpx_capture
from smacx_activity import read_page

with tempfile.TemporaryDirectory() as root:
    class Client(httpx.Client): pass
    install_httpx_capture(Client, DiagnosticWriter(Path(root),'match-live','sovereign',compress=True))
    chunk=('data: '+json.dumps({'choices':[{'delta':{'reasoning_content':'thinking '*2500}}]})+'\n\n').encode()
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield chunk
            page=read_page(root,'match-live')
            assert any(e['payload']['type']=='response_delta' for e in page['events'])
            assert not any(e['payload']['type']=='response_finished' for e in page['events'])
            yield b'data: [DONE]\n\n'
    with Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,stream=Stream()))) as client:
        with client.stream('POST','https://example.invalid/v1/chat/completions',json={'model':'test','messages':[{'content':'private prompt'}]}) as response:
            assert response.read()==chunk+b'data: [DONE]\n\n'
    page=read_page(root,'match-live')
    assert page['events'][-1]['payload']['complete']
    assert 'private prompt' not in json.dumps(page)
print('PASS: reasoning captured before stream completion; bytes unchanged; final completion once')
