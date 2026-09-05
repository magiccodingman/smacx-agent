#!/usr/bin/env python3
"""Current topology and valid warm inspection are independent of stale history."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture,field
from smacx_regions import PHYSICAL_LAND_PROFILE,PHYSICAL_OCEAN_PROFILE
from smacx_milestones import evaluate_milestone
from smacx_plan_health import dependency_states
from smacx_world import WorldQueryError
from smacx_world_types import content_hash


def main():
    transitions=[]
    from itertools import product
    for kind, domain in product(('version','split','merge'), ('land','ocean')):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture(Path(tmp),width=12,height=2)
            # Three known cells; no anchor/query refresh after the mutation.
            f.objects=[r for r in f.objects if r['kind']!='location' or r['object_ref'] in {f.at(0,0),f.at(2,0),f.at(4,0)}]
            for r in f.objects:
                if r['kind']=='location':r['fields']['terrain']=field(domain)
            middle=next(r for r in f.objects if r['object_ref']==f.at(2,0))
            opposite='ocean' if domain=='land' else 'land'
            if kind=='merge':middle['fields']['terrain']=field(opposite)
            f.save();old=f.service._derived_geography(f.worlds.load(f.scope,f.identity.timeline_id))
            regions=[r for r in old['_region_projection'] if r.mobility_profile_ref in {PHYSICAL_LAND_PROFILE if domain=='land' else PHYSICAL_OCEAN_PROFILE, 'mobility-land-default' if domain=='land' else 'mobility-sea-default'} and f.at(0,0) in r.location_refs]
            refs=[r.region_ref for r in regions]
            watches=[f.attention.create_watch('region_entry',[ref],{},current_turn=4)['watch_id'] for ref in refs]
            scope=f.attention.create_watch('spatial_scope',[refs[0]],{'type':'geography'},current_turn=4)['watch_id']
            deps=f.attention.semantic_dependency_hashes()
            operation=f.attention.upsert_operation(operation_id=None,kind='topology',objective='Inspect nominated geography',referenced_world_objects=refs,
                source_world_epoch=f.identity.world_epoch,source_world_revision=1,source_dependency_hash=content_hash({r:deps[r] for r in refs}),current_turn=4)
            if kind=='split':middle['fields']['terrain']=field(opposite)
            elif kind=='merge':middle['fields']['terrain']=field(domain)
            else:f.objects.remove(next(r for r in f.objects if r['object_ref']==f.at(4,0)))
            f.save();projection=f.worlds.load(f.scope,f.identity.timeline_id)
            # Persisted region rows still contain all old refs here.
            assert refs[0] in {r.region_ref for r in f.worlds.load_regions(f.scope,f.identity.timeline_id,regions[0].mobility_profile_ref)}
            registry=f.registry();assert not set(refs)&set(registry),(kind,refs)
            assert scope not in registry
            state,_=evaluate_milestone({'requirements':[{'kind':'dependency_valid','ref':refs[0]}]}, {},registry,[])
            assert state['state']=='blocked'
            assert dependency_states([{'plan_id':'plan-topology','dependencies':[refs[0]]}],{},set(registry))['plan-topology:'+refs[0]]['state']=='unavailable'
            f.attention.gc_watches(4)
            with f.store._connect() as db:
                lifecycle=[json.loads(r[0])['watch_id'] for r in db.execute("SELECT payload_json FROM attention_items WHERE attention_kind='watch_lifecycle'")]
            assert scope in lifecycle and len(lifecycle)<=32
            if kind=='split': assert set(watches).issubset(lifecycle)
            statuses=[f.attention.inspect_watch(w)['status'] for w in watches]
            assert statuses==(['invalid']*2 if kind=='split' else ['active']*2),(kind,statuses)
            runtime=f.attention.runtime_state(current_world_revision=projection['world_revision'],current_world_epoch=f.identity.world_epoch,
                object_dependency_hashes=f.attention.semantic_dependency_hashes(projection),current_turn=4)
            assert operation['operation_id'] not in {r['operation_id'] for r in runtime['operations']}
            assert f.service.query(mode='area',origin_ref=refs[0]).get('ok') is False
            try:f.service.query(mode='area',origin_ref=scope)
            except WorldQueryError:pass
            else:raise AssertionError('superseded scope queried')
            transitions.append({'kind':kind,'domain':domain,'direct_watch_status':statuses,'old_refs_withdrawn_before_history_refresh':True})
    for mode in ('route','area'):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture(Path(tmp));f.actor('unit-inspect','own_unit',2,2,triad='land',movement_points=3,owner_ref='faction-1',roles={'combat':True});f.save()
            target=f.at(8,2);args={'mode':mode,'origin_ref':'unit-inspect' if mode=='route' else target,'target_ref':target if mode=='route' else ''}
            cold=f.service.query(**args)
            f.actor('unrelated-economy','economy_state',0,0,credits=10);f.save()
            warm=f.service.query(**args);assert warm['cache']['hit']
            assert target in f.service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
            from smacx_store import SmacxStore
            from smacx_world_store import WorldStore
            from smacx_world import WorldService
            restarted=WorldService(WorldStore(SmacxStore(f.root/'state.sqlite3')),f.scope)
            assert target in restarted.anchor(context_length=65536)['payload']['lod']['promotion_refs']
            with f.store._connect() as db:assert db.execute('SELECT world_revision FROM world_query_cache WHERE query_fingerprint=?',(warm['cache']['query_fingerprint'],)).fetchone()[0]==cold['world_revision']
            next(r for r in f.objects if r['object_ref']==target)['fields']['features']=field(['fungus']);f.save()
            assert target not in f.service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
    print(json.dumps({'passed':True,'topology_lifecycle':transitions,'automatic_warm_inspection_restart':True,'true_dependency_stops_promotion':True}))
if __name__=='__main__':main()
