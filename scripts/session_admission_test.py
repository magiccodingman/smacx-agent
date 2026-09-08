#!/usr/bin/env python3
"""Run-scoped admission signal, specialist exclusion, bounded replacement."""
import json, os, tempfile
from pathlib import Path
from unittest.mock import patch
from smacx_diagnostics import record_session_admission
with tempfile.TemporaryDirectory() as temporary:
    with patch.dict(os.environ, {'SMACX_DIAGNOSTICS_ROOT':temporary,
        'SMACX_STRICT_SYSTEM_PROMPT':'1','SMACX_SPECIALIST_STRICT_PROMPT':'0',
        'SMACX_HARNESS_RUN_ID':'run-test','SMACX_DIAGNOSTICS_ENABLED':'0'}):
        path=Path(temporary)/'session-admission.json'
        record_session_admission('waiting_session_lease')
        first=json.loads(path.read_text());assert first['run_id']=='run-test'
        assert first['phase']=='waiting_session_lease'
        record_session_admission('session_admitted')
        assert json.loads(path.read_text())['phase']=='session_admitted'
        original=path.read_bytes()
        with patch.dict(os.environ, {'SMACX_SPECIALIST_STRICT_PROMPT':'1'}):
            record_session_admission('waiting_session_lease')
        assert path.read_bytes()==original
        record_session_admission('invented')
        assert path.read_bytes()==original
        assert len(list(Path(temporary).iterdir()))==1
print(json.dumps({'passed':True,'run_scoped':True,'specialists_excluded':True,'bounded_replacement':True}))
