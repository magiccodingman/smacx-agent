#!/usr/bin/env python3
"""Exercise real helper scripts with a DB larger than helper tmpfs capacity.

Run in a disposable container with /tmp capped at128MiB and
SMACX_TEST_SCRATCH on a separate writable filesystem.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile

from smacx_worker_manager import HERMES_CHECKPOINT_SCRIPT, HERMES_RESTORE_SCRIPT

assert os.statvfs('/tmp').f_blocks * os.statvfs('/tmp').f_frsize <= 128 * 1024**2
with tempfile.TemporaryDirectory(dir=os.environ['SMACX_TEST_SCRATCH']) as tmp:
    root = Path(tmp)
    profile = root / 'source/profiles/test-profile'
    profile.mkdir(parents=True)
    database = profile / 'state.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT)')
        db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,session_id TEXT,value BLOB)')
        db.executemany('INSERT INTO sessions VALUES (?,?)', [('ours','match-large'),('other','other-match')])
        for i in range(144):
            db.execute('INSERT INTO messages VALUES (?,?,zeroblob(1048576))', (i, 'ours' if i < 136 else 'other'))
    source_hash = hashlib.sha256(database.read_bytes()).hexdigest()
    out = root / 'control'
    out.mkdir()
    env = {**os.environ, 'SMACX_HERMES_PROFILE_ID':'test-profile',
           'SMACX_MATCH_ID':'match-large', 'SMACX_SOURCE_ROOT':str(root/'source'),
           'SMACX_CONTROL_ROOT':str(out), 'SMACX_CHECKPOINT_RELATIVE':'checkpoint.tar.gz',
           'SMACX_TARGET_ROOT':str(root/'target')}
    def run(script):
        result = subprocess.run([sys.executable, '-c', script], env=env,
                                text=True, capture_output=True, timeout=90)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    run(HERMES_CHECKPOINT_SCRIPT)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == source_hash
    assert not list(out.glob('.hermes-checkpoint-*'))
    with tarfile.open(out/'checkpoint.tar.gz') as archive:
        assert json.load(archive.extractfile('checkpoint.json'))['session_ids'] == ['ours']
        assert archive.getmember('state.db').size > 128 * 1024**2
    run(HERMES_RESTORE_SCRIPT)
    target = root/'target/profiles/test-profile'
    assert not list(target.glob('.hermes-restore-*'))
    with sqlite3.connect(target/'state.db') as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('SELECT id FROM sessions').fetchall() == [('ours',)]
        assert db.execute('SELECT count(*),sum(length(value)) FROM messages').fetchone() == (136,136*1024**2)
    print(json.dumps({'passed':True,'tmpfs_limit_bytes':128*1024**2,
                      'restored_payload_bytes':136*1024**2,'source_unchanged':True,
                      'foreign_session_excluded':True,'private_staging_removed':True}))
