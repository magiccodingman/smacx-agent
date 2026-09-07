#!/usr/bin/env python3
"""Actual helper: closed and retained WAL, read-only source, scoped archive."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from smacx_worker_manager import HERMES_CHECKPOINT_SCRIPT

assert os.geteuid() == 0, 'Run in the isolated MCP test container as root.'
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    root.chmod(0o755)
    for case in ('closed_wal', 'retained_wal', 'invalid_schema'):
        source = root / case / 'profiles' / 'test-profile'
        source.mkdir(parents=True)
        db = sqlite3.connect(source / 'state.db')
        db.execute('PRAGMA journal_mode=WAL')
        if case != 'invalid_schema':
            db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT)')
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,value TEXT)')
            db.executemany('INSERT INTO sessions VALUES (?,?)', [('ours','match-test'),('other','other-match')])
            db.executemany('INSERT INTO messages VALUES (?,?,?)', [(1,'ours','committed-main'),(2,'other','private-other')])
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute("INSERT INTO messages VALUES (3,'ours','committed-latest')")
            db.commit()
        else:
            db.execute('CREATE TABLE wrong_schema(secret TEXT)')
            db.commit()
        if case != 'retained_wal':
            db.close()
            assert not (source / 'state.db-wal').exists()
        else:
            assert (source / 'state.db-wal').stat().st_size > 32
            # Active, uncommitted transaction must not enter the archive.
            db.execute("INSERT INTO messages VALUES (4,'ours','uncommitted')")
        source.chmod(0o555)
        for p in source.glob('state.db*'):
            p.chmod(0o444)
        def hashes():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source.glob('state.db*')}
        before = hashes()
        output = root / (case + '-output')
        output.mkdir()
        os.chown(output,10000,10001)
        result = subprocess.run([sys.executable,'-c',HERMES_CHECKPOINT_SCRIPT],
            env={**os.environ,'SMACX_HERMES_PROFILE_ID':'test-profile','SMACX_MATCH_ID':'match-test',
                 'SMACX_SOURCE_ROOT':str(root/case),'SMACX_CONTROL_ROOT':str(output),
                 'SMACX_CHECKPOINT_RELATIVE':'checkpoint.tar.gz'},
            user=10000,group=10001,extra_groups=[],capture_output=True,text=True,timeout=30)
        assert hashes() == before, 'Read-only source or its sidecars were modified.'
        if case == 'invalid_schema':
            assert result.returncode != 0 and 'hermes_checkpoint_session_filter_failed' in result.stderr
            assert not (output/'checkpoint.tar.gz').exists()
            continue
        assert result.returncode == 0, result.stderr
        with tarfile.open(output/'checkpoint.tar.gz') as archive:
            manifest=json.load(archive.extractfile('checkpoint.json'))
            assert manifest['session_ids']==['ours']
            stable=output/'verified.db'
            stable.write_bytes(archive.extractfile('state.db').read())
        with sqlite3.connect(stable) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()==('ok',)
            assert restored.execute('SELECT value FROM messages ORDER BY id').fetchall()==[
                ('committed-main',),('committed-latest',)]
            assert restored.execute('SELECT id FROM sessions').fetchall()==[('ours',)]
        if case == 'retained_wal':
            db.rollback()
            db.close()
print(json.dumps({'passed':True,'closed_wal_on_readonly_source':True,
    'retained_wal_commits_preserved':True,'uncommitted_rows_excluded':True,
    'other_match_excluded':True,'source_files_unchanged':True,
    'session_filter_errors_fail_closed':True}))
