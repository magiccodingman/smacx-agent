#!/usr/bin/env python3
"""Managed faction alias resolution preserves perspective and epistemic boundaries."""
import json,tempfile
from pathlib import Path
from unittest.mock import patch
import smacx_controller as c
from smacx_store import SmacxStore,MemoryScope
from smacx_journal import CampaignJournal
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity,WorldObject,EpistemicValue

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);store=SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-test','Test')
    store.create_match(match_id='match-test',display_name='Test',mode='solo')
    store.create_perspective('match-test','agent-test',perspective_id='perspective-test')
    scope=MemoryScope('match-test','agent-test','perspective-test')
    store.register_instance(instance_id='instance-test',scope=scope)
    store.start_session(scope,'instance-test',session_id='session-test')
    journal=CampaignJournal(root/'campaigns');worlds=WorldStore(store)
    timeline=store.active_timeline_id(scope)
    identity=WorldIdentity(scope.match_id,scope.perspective_id,timeline,'epoch-test')
    def project(status='current',revision='r35'):
        owner=EpistemicValue.from_dict({'value':None if status=='unknown' else 'faction-7','epistemic_status':status,
          'source':'direct_sight','first_known_turn':34,'last_verified_turn':34,
          'world_revision':1,'provenance_ref':'observation-1'})
        worlds.replace_projection(scope,identity,[WorldObject('faction-1','faction'),
          WorldObject('location-1040','tile',fields={'owner_ref':owner})],
          observation_cursor=1,action_revision=revision,continuity='complete',journal_head_hash='0'*64)
    project()
    # A registry identity alone must not disclose or authorize an unseen actor.
    store.upsert_actor(scope.match_id,'faction:6','Hidden Name',faction_id=6)
    record={'topic':'third-faction','content':'An unidentified faction owns an observed tile.',
            'about_actor_id':'faction-7','asserted_by_actor_id':'faction-1','status':'unverified'}
    with patch.object(c,'_store',return_value=store),patch.object(c,'_journal',return_value=journal), \
         patch.object(c,'_guard_platform_observation',return_value=(scope,{'turn':35,'year':2135})):
        def write(action,record):return c.write_platform_memory(action,scope.match_id,'session-test','r35',record)
        result=write('claim',record);assert result['ok'],result
        mapping=result['actor_references'];assert result['record']['about_actor_id']==mapping['faction-7']
        assert result['record']['status']=='unverified' and 'Hidden Name' not in json.dumps(result)
        before=c._journal_working_state(scope)
        bad=write('relationship',{'actor_id':'faction-7','trust':40,'confidence':80})
        assert bad['error']=='invalid_confidence' and bad['validation']['maximum']==1.0,bad
        assert bad['persistence']['stage']=='not_started'
        assert c._journal_working_state(scope)==before
        bad=write('relationship',{'actor_id':'faction-7','trust':101,'confidence':0.8})
        assert bad['error']=='invalid_relationship_metric' and 'ranges' in bad['validation'],bad
        assert c._journal_working_state(scope)==before
        assert write('relationship',{'actor_id':'faction-7','trust':40,'confidence':0.8})['ok']
        assert write('relationship',{'actor_id':'faction-7','trust':-10})['ok']
        assert write('commitment',{'commitment_key':'test','title':'Proposed','terms':'Untrusted terms',
          'parties':[{'actor_id':'faction-7','role':'counterparty'}]})['ok']
        project('stale');assert write('claim',record)['ok']
        project('unknown');bad=write('claim',record);assert bad['error']=='actor_scope_mismatch',bad
        bad=write('claim',{**record,'about_actor_id':mapping['faction-7']});assert bad['error']=='actor_scope_mismatch',bad
        project(revision='old');bad=write('claim',record);assert bad['error']=='memory_actor_world_observation_required',bad
        project();bad=write('claim',{**record,'about_actor_id':'faction-6'});assert bad['error']=='actor_scope_mismatch',bad
        # Generic identity projection does not overwrite existing chat identity metadata.
        store.upsert_actor(scope.match_id,'faction:7','Previously identified',faction_id=7)
        assert write('claim',record)['actor_references']==mapping
        with store._connect() as db:
            assert db.execute('select display_name from actors where actor_id=?',(mapping['faction-7'],)).fetchone()[0]=='Previously identified'
        assert 'Previously identified' not in json.dumps(write('claim',record))
        # A removed contact must not erase its observed faction identity.
        project('unknown')
        observation=journal.append(scope,'observation.world_batch',{'deltas':[{
          'object_ref':'contact-old','change':'added','current':{'kind':'foreign_contact',
          'fields':{'owner_ref':{'value':'faction-0','epistemic_status':'current'}}}}]})
        journal.append(scope,'observation.world_batch',{'deltas':[{
          'object_ref':'contact-old','change':'removed'}]})
        native_record={**record,'about_actor_id':'faction-0'}
        historical=write('claim',native_record)
        assert historical['ok'] and historical['record']['status']=='unverified',historical
        state=journal.replay(scope)
        assert 'contact-old' not in state['world_objects']
        assert len(state['observed_faction_identities'])<=8
        # Restarted replay derives the same identity from canonical events.
        fresh=CampaignJournal(root/'campaigns')
        assert fresh.replay(scope)['observed_faction_identities']==state['observed_faction_identities']
        # A rewind before observation cannot inherit later identity knowledge.
        journal.fork_timeline(scope,'timeline-before-native',native_save_sha256='a'*64,
                              from_event_hash=observation['previous_hash'])
        assert 'faction-0' not in journal.replay(scope,'timeline-before-native')['observed_faction_identities']
        other=MemoryScope(scope.match_id,scope.agent_id,'perspective-other')
        assert not journal.replay(other)['observed_faction_identities']
        # Cached replay must not authorize tampered canonical history.
        path=next(journal.perspective_root(scope).joinpath('events').glob('*-'+observation['event_id']+'.json'))
        damaged=json.loads(path.read_text());damaged['payload']['forged']=True
        path.write_text(json.dumps(damaged))
        rejected=write('claim',native_record)
        assert not rejected['ok'] and rejected['error'].startswith('journal_'),rejected
print(json.dumps({'passed':True,'claim_relationship_commitment':True,'unknown_and_unseen_rejected':True,
                  'stale_identity_not_promoted':True,'fresh_projection_required':True,'registry_names_not_leaked_or_overwritten':True}))
