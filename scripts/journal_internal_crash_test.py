#!/usr/bin/env python3
"""Real canonical writes: orphan recovery and fail-closed suffix adversaries."""
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import smacx_journal as module
from smacx_journal import CampaignJournal, JournalError, _canonical
from smacx_store import MemoryScope


def main():
    results=[]
    for boundary, keyed in [('event',True),('event',False),('manifest',True),('marker',True)]:
        with tempfile.TemporaryDirectory() as tmp:
            scope=MemoryScope('match-internal','agent-internal','perspective-internal')
            j=CampaignJournal(Path(tmp));j.append(scope,'test.start',{})
            atomic=module._atomic_json;installed=[]
            def crash(path,value):
                atomic(path,value)
                if (boundary=='event' and path.parent.name=='events' or
                    boundary=='manifest' and path.name=='manifest.json' or
                    boundary=='marker' and path.parent.name=='idempotency'):
                    installed.append(value);raise RuntimeError('power-loss')
            with patch.object(module,'_atomic_json',side_effect=crash):
                try:j.append(scope,'test.orphan',{},idempotency_key='orphan' if keyed else '')
                except RuntimeError:pass
                else:raise AssertionError('no injection')
            restarted=CampaignJournal(Path(tmp))
            if keyed:
                event=restarted.append(scope,'test.orphan',{},idempotency_key='orphan')
                assert event['sequence']==2
            else:
                assert restarted.replay(scope)['manifest']['sequence']==2
            next_event=restarted.append(scope,'test.next',{})
            assert next_event['sequence']==3
            files=sorted((restarted.perspective_root(scope)/'events').glob('*.json'))
            assert next_event['previous_hash']==json.loads(files[1].read_text())['event_hash']
            assert restarted.verify(scope)['ok']
            results.append({'boundary':boundary,'idempotent':keyed,'passed':True})
    for fault in ('hash','scope','noncontiguous','conflict'):
        with tempfile.TemporaryDirectory() as tmp:
            scope=MemoryScope('match-internal','agent-internal','perspective-internal');j=CampaignJournal(Path(tmp))
            j.append(scope,'test.start',{});path=j.perspective_root(scope);manifest=(path/'manifest.json').read_text()
            j.append(scope,'test.orphan',{},idempotency_key='orphan')
            (path/'manifest.json').write_text(manifest)
            file=sorted((path/'events').glob('*.json'))[-1];event=json.loads(file.read_text())
            if fault=='hash':event['event_hash']='f'*64
            if fault=='scope':event['perspective_id']='perspective-wrong'
            if fault=='noncontiguous':event['sequence']=3
            if fault=='conflict':event['event_id']='journal-'+'a'*32
            if fault!='hash':
                event.pop('event_hash');event['event_hash']=hashlib.sha256(event['previous_hash'].encode()+_canonical(event)).hexdigest()
            if fault in ('noncontiguous','conflict'):
                if fault=='noncontiguous':file.unlink()
                file=path/'events'/f"{event['sequence']:012d}-{event['event_id']}.json"
            file.write_text(json.dumps(event))
            restarted=CampaignJournal(Path(tmp))
            for key in ('orphan','different'):
                try:restarted.append(scope,'test.retry',{},idempotency_key=key)
                except JournalError:pass
                else:raise AssertionError(fault)
            assert (path/'manifest.json').read_text()==manifest
            results.append({'fault':fault,'failed_closed':True})
    print(json.dumps({'passed':True,'classification':'deterministic real journal persistence','cases':results}))
if __name__=='__main__':main()
