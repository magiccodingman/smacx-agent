#!/usr/bin/env python3
"""Indexed misses preserve canonical marker-loss and restart recovery."""
import hashlib,json
from pathlib import Path
import tempfile
from unittest.mock import patch
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);scope=MemoryScope('match-index','agent-index','perspective-index');j=CampaignJournal(root)
        reads=[0];original=j._load
        def load(path,*a,**kw):
            if path.parent.name=='events':reads[0]+=1
            return original(path,*a,**kw)
        with patch.object(j,'_load',side_effect=load):
            events=[j.append(scope,'observation.test',{'n':i},idempotency_key='index-'+str(i)) for i in range(100)]
        assert reads[0]<10,reads
        directory=j.perspective_root(scope)/'idempotency'
        def remove(key): (directory/(hashlib.sha256(key.encode()).hexdigest()+'.json')).unlink()
        remove('index-0')
        assert j.append(scope,'observation.test',{},idempotency_key='index-0')['event_id']==events[0]['event_id']
        remove('index-1');restarted=CampaignJournal(root)
        assert restarted.append(scope,'observation.test',{},idempotency_key='index-1')['event_id']==events[1]['event_id']
        other=CampaignJournal(root);external=other.append(scope,'observation.test',{},idempotency_key='external');remove('external')
        # Non-idempotent writes cannot mask external event-directory changes.
        j.append(scope,'observation.test',{})
        assert j.append(scope,'observation.test',{},idempotency_key='external')['event_id']==external['event_id']
        assert j.verify(scope)['ok']
    print(json.dumps({'passed':True,'new_key_event_file_reads':reads[0],'old_marker_loss':True,'restart':True,'external_writer_marker_loss':True}))
if __name__=='__main__':main()
