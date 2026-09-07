#!/usr/bin/env python3
"""Client-side context failures remain fail-closed and diagnostically visible."""
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from smacx_diagnostic_summary import Metrics, summary

path = Path(os.environ.get('SMACX_TEST_STRICT_PROMPT_PATH',
    str(Path(__file__).resolve().parents[1]/'harness/smacx_strict_prompt.py')))
spec = importlib.util.spec_from_file_location('strict_fetch_contract', path)
module = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {'SMACX_STRICT_SYSTEM_PROMPT':'0', 'SMACX_SPECIALIST_STRICT_PROMPT':'0'}):
    spec.loader.exec_module(module)

cases = [(TimeoutError('private-password'), 'runtime_context_timeout'),
         (URLError(TimeoutError('private-password')), 'runtime_context_timeout'),
         (HTTPError('http://private-password/',503,'private-password',{},None), 'runtime_context_http_error'),
         (URLError('private-password'), 'runtime_context_fetch_error')]
for error, code in cases:
    records = []
    def record(kind, payload, **kwargs):
        records.append({'kind':kind, 'payload':payload, **kwargs})
    with patch.dict(os.environ, {'SMACX_RUNTIME_CONTEXT_URL':'http://private-password/runtime-context'}), \
         patch.object(module, '_runtime_token', return_value='private-password'), \
         patch.object(module, 'urlopen', side_effect=error), \
         patch('smacx_diagnostics.record', side_effect=record):
        try: module._fetch_runtime_context([{'role':'user','content':'resume'}])
        except RuntimeError as exc:
            assert str(exc) == 'smacx_runtime_context_unavailable' and exc.__cause__ is error
        else: raise AssertionError('failed context allowed continuation')
    assert len(records) == 1
    event = records[0]
    assert event['actor'] == 'sovereign' and event['correlation']['episode_id'].startswith('episode-')
    assert event['payload']['error']['code'] == code
    assert event['payload']['provider_context_issued'] is False
    assert event['payload']['elapsed_ms'] >= 0
    assert 'private-password' not in json.dumps(event)
    assert code in summary(event)
    metrics = Metrics(); metrics.add(event)
    assert metrics.failures['runtime_context_fetch_failed:'+code] == 1
print(json.dumps({'passed':True,'cases':len(cases),'fail_closed':True,
                  'before_provider_failure_captured':True,'credentials_not_captured':True}))
