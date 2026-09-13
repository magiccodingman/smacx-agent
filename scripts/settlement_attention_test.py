"""Real collector -> journal -> attention -> base query -> restart integration."""
from pathlib import Path
from tempfile import TemporaryDirectory
from global_world_pipeline_test import CompleteDomainFixture
from smacx_attention import AttentionService
from smacx_journal import CampaignJournal
from smacx_observation import ObservationCollector
from smacx_store import SmacxStore, MemoryScope
from smacx_world_store import WorldStore
from smacx_world import WorldService
with TemporaryDirectory() as raw:
 root=Path(raw);s=SmacxStore(root/'db');s.ensure_agent('agent-domain','test')
 s.create_match(match_id='match-domain',display_name='test',mode='solo')
 s.create_perspective('match-domain','agent-domain',perspective_id='perspective-domain')
 scope=MemoryScope('match-domain','agent-domain','perspective-domain')
 j=CampaignJournal(root/'campaigns',timeline_resolver=s.active_timeline_id);w=WorldStore(s,root/'snapshots')
 a=AttentionService(s,j,scope);f=CompleteDomainFixture()
 tile=f.bases[0]['base_radius'][1];tile['worked']=True
 tile['access']={'foreign_territory':False,'visible_occupation_constraint':False,'reserved_by_other_owned_base':False}
 def collector():return ObservationCollector(scope=scope,session_id='session-domain',bridge_call=f,journal=j,world_store=w,attention=a)
 c=collector();c.collect_once()
 tile['access']['foreign_territory']=True;tile['worked']=False;f.revision+=1
 c.collect_once()
 events=w.temporal_events_since(scope,j.timeline_id(scope),0,limit=256)
 changes=[r for r in events if r.get('event',{}).get('event_kind')=='base_resource_access_changed']
 assert len(changes)==1,changes
 c=collector();c.collect_once()
 events=w.temporal_events_since(scope,j.timeline_id(scope),0,limit=256)
 assert len([r for r in events if r.get('event',{}).get('event_kind')=='base_resource_access_changed'])==1
 page=WorldService(w,scope).query(mode='base',subject_refs=['base-home'],detail='deep')
 base=next(o for o in page['objects'] if o['object_ref']=='base-home')
 assert base['fields']['last_resource_access_change']['value']['tiles'][0]['previously_worked']
 with s._connect() as conn:
  rows=conn.execute("SELECT * FROM attention_items WHERE attention_kind='base_resource_access'").fetchall()
 assert len(rows)==1
print('PASS: observed -> journaled -> attention captured -> queryable base -> collector restart without duplicate')
