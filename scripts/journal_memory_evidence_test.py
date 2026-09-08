#!/usr/bin/env python3
"""Canonical evidence IDs round-trip through guarded cognition without scope bypass."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import smacx_controller as c
from smacx_store import SmacxStore, MemoryScope
from smacx_journal import CampaignJournal

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); store=SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-test','Test')
    store.create_match(match_id='match-test',display_name='Test',mode='solo')
    store.create_perspective('match-test','agent-test',perspective_id='perspective-test')
    scope=MemoryScope('match-test','agent-test','perspective-test')
    store.register_instance(instance_id='instance-test',scope=scope)
    store.start_session(scope,'instance-test',session_id='session-test')
    import sqlite3
    with sqlite3.connect(root/'state.sqlite3') as source, sqlite3.connect(root/'empty-cache.sqlite3') as target:
        source.backup(target)
    journal=CampaignJournal(root/'campaigns')
    with patch.object(c,'_store',return_value=store), patch.object(c,'_journal',return_value=journal), \
         patch.object(c,'_guard_platform_observation',return_value=(scope,{'turn':35,'year':2135})), \
         patch.object(c,'_scope_for_match',return_value=scope):
        receipt=c.record_campaign_action(scope.match_id,'session-test',{'command':'move_unit'},turn=35)
        assert receipt['ok'],receipt
        eid=receipt['journal_event_id']
        record={'topic':'observed-move','content':'Movement was attempted','source_event_id':eid}
        def write(action,record):
            return c.write_platform_memory(action,scope.match_id,'session-test','revision',record)
        result=write('claim',record)
        assert result['ok'],result
        assert result['record']['source_event_id']==eid
        assert result['record']['status']=='unverified'
        belief=write('belief',{'topic':'move-belief','content':'Tentative','confidence':0.4,
                              'evidence':[{'event_id':eid,'stance':'supports','weight':0.4}]})
        assert belief['ok'],belief
        assert journal.replay(scope)['claims']
        for action,entry,field in (
            ('goal',{'title':'Investigate','description':'Check the outcome'},'source_event_id'),
            ('plan',{'plan_key':'check','title':'Check','objective':'Inspect outcome'},'source_event_id'),
            ('commitment',{'commitment_key':'promise','title':'Promise','terms':'Review outcome'},'resolution_event_id'),
            ('summary',{'section':'recent_events','content':'A move was attempted'},'through_event_id'),
        ):
            linked=write(action,{**entry,field:eid})
            assert linked['ok'] and linked['record'][field]==eid,(action,linked)
        # Restart the reader: canonical reference survives independently of object caches.
        journal=CampaignJournal(root/'campaigns')
        with patch.object(c,'_journal',return_value=journal):
            assert write('claim',record)['ok']
            before=journal.replay(scope)
            bad=write('claim',{**record,'source_event_id':'journal-'+'0'*32})
            assert bad['error']=='evidence_event_scope_mismatch',bad
            assert journal.replay(scope)==before
            other=MemoryScope(scope.match_id,scope.agent_id,'perspective-other')
            foreign=journal.append(other,'game.action',{})['event_id']
            bad=write('claim',{**record,'source_event_id':foreign})
            assert bad['error']=='evidence_event_scope_mismatch',bad
            # A valid cached SQL reference must not grant access on a new timeline.
            with patch.dict('os.environ',{'SMACX_TIMELINE_ID':'timeline-new'}):
                bad=write('claim',record)
                assert bad['error']=='evidence_event_scope_mismatch',bad
            journal.fork_timeline(scope,'timeline-child',native_save_sha256='a'*64,
                                  from_event_hash=receipt['event_hash'])
            later=journal.append(scope,'game.action',{})['event_id']
            with patch.dict('os.environ',{'SMACX_TIMELINE_ID':'timeline-child'}):
                assert journal.evidence_event(scope,eid)['event_id']==eid
                inherited=write('claim',record)
                assert inherited['ok'] and inherited['record']['source_event_id']==eid,inherited
                rejected=write('claim',{**record,'source_event_id':later})
                assert rejected['error']=='evidence_event_scope_mismatch',rejected
            # Loss of the query cache does not lose the citation or require a DB reset.
            rebuilt=SmacxStore(root/'empty-cache.sqlite3')
            with patch.object(c,'_store',return_value=rebuilt):
                recovered=write('claim',record)
                assert recovered['ok'] and recovered['record']['source_event_id']==eid,recovered
            # Damaged canonical evidence is rejected even when a SQL row exists.
            path=next(journal.perspective_root(scope).joinpath('events').glob('*-'+eid+'.json'))
            original=path.read_text()
            damaged=json.loads(original);damaged['payload']['forged']=True
            path.write_text(json.dumps(damaged))
            fresh=CampaignJournal(root/'campaigns')
            with patch.object(c,'_journal',return_value=fresh):
                rejected=write('claim',record)
                assert not rejected['ok'] and rejected['error'].startswith('journal_'),rejected
            path.write_text(original)
            # Legacy scoped event references remain usable.
            legacy=store.append_event(scope,'observation.test',{})
            assert write('claim',{**record,'source_event_id':legacy})['ok']
print(json.dumps({'passed':True,'canonical_receipt_to_claim_and_belief':True,
 'unknown_foreign_and_abandoned_timeline_rejected':True,'legacy_scoped_event_supported':True}))
