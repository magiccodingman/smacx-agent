#!/usr/bin/env python3
"""Private WAL staging and explicit history gaps preserve scoped diagnostics."""
import hashlib,json,sqlite3,tempfile,shutil
from pathlib import Path
from unittest.mock import Mock,patch
from smacx_campaign_diagnostics import snapshot_hermes

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); source=root/'source'; profile=source/'profiles'/'profile-test'
    profile.mkdir(parents=True); db=profile/'state.db'
    writer=sqlite3.connect(db)
    writer.execute('PRAGMA journal_mode=WAL')
    writer.executescript('CREATE TABLE sessions(id TEXT,title TEXT); CREATE TABLE messages(id INTEGER,session_id TEXT,role TEXT,content TEXT,tool_call_id TEXT,tool_calls TEXT,tool_name TEXT,timestamp REAL);')
    writer.execute('INSERT INTO sessions VALUES (?,?)',('s','match-test'))
    writer.execute('INSERT INTO messages VALUES (1,"s","assistant","retained",NULL,NULL,NULL,1)')
    writer.commit()
    files=[db,Path(str(db)+'-wal')]
    before=[hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    unavailable=Mock();unavailable.execute.side_effect=sqlite3.OperationalError('unable to open database file')
    connect=sqlite3.connect; calls=[]
    def failing_readonly(*args,**kwargs):
        calls.append(args[0])
        return unavailable if kwargs.get('uri') else connect(*args,**kwargs)
    with patch('smacx_campaign_diagnostics.sqlite3.connect',side_effect=failing_readonly):
        snapshot_hermes(source,root/'captured','match-test','profile-test')
    assert 'retained' in (root/'captured/hermes-history.jsonl').read_text()
    assert not (root/'captured/history-gap.jsonl').exists()
    assert before==[hashlib.sha256(p.read_bytes()).hexdigest() for p in files]
    assert not list((root/'captured').glob('.history-*'))
    copyfile=shutil.copyfile
    def racing_copy(source_file, target_file):
        result=copyfile(source_file,target_file)
        if Path(source_file)==db:
            writer.execute('INSERT INTO messages VALUES (2,"s","assistant","later",NULL,NULL,NULL,2)')
            writer.commit()
        return result
    with patch('smacx_campaign_diagnostics.sqlite3.connect',side_effect=failing_readonly), patch('smacx_campaign_diagnostics.shutil.copyfile',side_effect=racing_copy):
        snapshot_hermes(source,root/'racing','match-test','profile-test')
    assert 'retained_history_unavailable' in (root/'racing/history-gap.jsonl').read_text()
    assert not list((root/'racing').glob('.history-*'))
    logs=source/'diagnostics'/'match-test'; logs.mkdir(parents=True)
    (logs/'native.jsonl').write_text('{"kind":"native_test"}\n')
    writer.close()
    db.write_bytes(b'invalid database')
    snapshot_hermes(source,root/'damaged','match-test','profile-test')
    assert 'retained_history_unavailable' in (root/'damaged/history-gap.jsonl').read_text()
    assert (root/'damaged/native.jsonl').read_text()==(logs/'native.jsonl').read_text()
print(json.dumps({'pass':True,'retained_wal_captured':True,'source_unchanged':True,
                  'staging_removed':True,'damaged_history_explicit_gap':True}))
