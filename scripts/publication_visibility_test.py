#!/usr/bin/env python3
"""Concurrent normal readers cannot see pre-head frozen-publication evidence."""
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch
from publication_transaction_test import setup
from smacx_runtime_context import RuntimeContextAssembler
from smacx_world import WorldService


def main():
    cases=[]
    for phase in ('pre_head','post_head'):
        for crash in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                f,native,collect,plan=setup(Path(tmp));world=WorldService(f.worlds,f.scope)
                f.attention.create_watch('milestone',['base-location-0'],{'requirements':[{'ref':'base-location-0','kind':'current_field','field':'mineral_surplus','value':9}]},current_turn=50,linked_plan_id=plan['plan_id'])
                old=f.worlds.load(f.scope,f.attention.timeline_id)['observation_cursor']
                native.events=[{'sequence':1,'kind':'known_tile_changed','turn':50,'subject_a':0,'subject_b':0,
                    'from_tile_id':0,'to_tile_id':0,'value_before':0,'value_after':1}]
                native.bases[0]['mineral_surplus']=9;native.revision+=1
                paused=threading.Event();resume=threading.Event();errors=[]
                obj,name=(f.worlds,'replace_projection') if phase=='pre_head' else (f.attention,'capture_current_plan_dependencies')
                original=getattr(obj,name)
                def pause(*a,**kw):
                    paused.set();assert resume.wait(30)
                    if crash:raise RuntimeError('injected')
                    return original(*a,**kw)
                def publish():
                    try:collect().collect_once()
                    except Exception as e:errors.append(e)
                with patch.object(obj,name,side_effect=pause):
                    t=threading.Thread(target=publish);t.start();assert paused.wait(30)
                    try:
                        cap=f.worlds.committed_cursor(f.scope,f.attention.timeline_id)
                        assert (cap==old) if phase=='pre_head' else (cap>old)
                        with f.store._connect() as c:
                            assert c.execute('SELECT MAX(observation_sequence) FROM world_observation_projection').fetchone()[0]>old
                        # A later high-priority cursor-zero item must not cross the barrier.
                        late=f.attention.enqueue('chat',{'message':{'content':'later'}},observation_cursor=0,priority=100)
                        result=world.query(mode='changes',since_cursor=0)
                        histories=[*f.worlds.changes_since(f.scope,f.attention.timeline_id,0),
                                   *f.worlds.temporal_events_since(f.scope,f.attention.timeline_id,0)]
                        assert all(row['observation_cursor']<=cap for row in histories)
                        assert all(row['observation_cursor']<=cap for row in result.get('items',[]))
                        identity,projection=world._projection()
                        frozen=f.worlds.snapshot(f.scope,identity,journal_head_hash=projection['journal_head_hash'],
                            journal_sequence=0,calculator_versions={})
                        assert all(row['observation_cursor']<=cap for row in json.loads(Path(frozen['path']).read_text())['temporal_events'])
                        runtime=RuntimeContextAssembler(scope=f.scope,world=world,attention=f.attention,
                            snapshot=lambda:{'turn':50,'revision':str(native.revision)},working_state=lambda:{'sections':{}})
                        runtime.build(episode_id='episode-visibility',episode_mode='gameplay',context_length=65536)
                        lease=f.attention.lease('episode-visibility')
                        assert all(row['observation_cursor']<=cap for row in lease['items'])
                        if phase=='pre_head':assert late['attention_id'] not in {row['attention_id'] for row in lease['items']}
                        f.attention.abandon(lease['attention_lease_id'])
                    finally:resume.set();t.join(30)
                assert not t.is_alive()
                assert bool(errors)==crash,errors
                if crash:
                    from smacx_store import SmacxStore
                    from smacx_world_store import WorldStore
                    from smacx_journal import CampaignJournal
                    from smacx_attention import AttentionService
                    f.store=SmacxStore(f.root/'state.sqlite3');f.worlds=WorldStore(f.store)
                    f.journal=CampaignJournal(f.root/'campaigns',timeline_resolver=f.store.active_timeline_id)
                    f.attention=AttentionService(f.store,f.journal,f.scope)
                    collect().collect_once()
                new=f.worlds.temporal_events_since(f.scope,f.attention.timeline_id,old)
                assert len([row for row in new if row['event']['event_kind']=='terrain_or_improvement_changed'])==1,new
                lease=f.attention.lease('episode-after')
                assert any(row['observation_cursor']>old for row in lease['items'])
                delivered={row['attention_id'] for row in lease['items']}
                f.attention.placed(lease['attention_lease_id']);f.attention.responded(lease['attention_lease_id'])
                f.attention.acknowledge(lease['attention_lease_id'],through_cursor=lease['through_cursor'])
                assert not delivered.intersection(row['attention_id'] for row in f.attention.lease('episode-after-ack')['items'])
                assert f.journal.verify(f.scope)['ok']
                cases.append({'phase':phase,'crash_restart':crash,'passed':True})
    print(json.dumps({'passed':True,'classification':'concurrent native-shaped production collector','cases':cases}))
if __name__=='__main__':main()
