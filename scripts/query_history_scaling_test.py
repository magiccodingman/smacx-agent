#!/usr/bin/env python3
"""Thousands of historical query receipts reduce to recent plus explicit pins."""
import json,time
from copy import deepcopy
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture,field
from smacx_world_model import CALCULATOR_VERSION
from smacx_world_types import content_hash,canonical_json
from smacx_runtime_context import RuntimeContextAssembler


def main():
    measurements=[]
    for count in (100,1000,5000):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture(Path(tmp),width=32,height=16)
            for i in range(20):f.actor('unit-history-'+str(i),'own_unit',2,2,triad='land',movement_points=3,owner_ref='faction-1',roles={'combat':True})
            f.save();query=f.service.query(mode='route',origin_ref='unit-history-0',target_ref=f.at(8,2),detail='deep');ref=query['route']['route_ref']
            scope=f.attention.create_watch('spatial_scope',[ref],{'type':'route_corridor','radius':1},current_turn=4)['watch_id']
            watch=f.attention.create_watch('region_entry',[scope],{},current_turn=4)['watch_id']
            plan=f.store.put_plan(f.scope,'pin','Keep route','Explicit dependency',dependencies=[ref]);f.journal.append(f.scope,'memory.plan',{'record':plan})
            deps=f.attention.semantic_dependency_hashes();op=f.attention.upsert_operation(operation_id=None,kind='history',objective='Keep nominated route',referenced_world_objects=[ref],
                source_world_epoch=f.identity.world_epoch,source_world_revision=1,source_dependency_hash=content_hash({ref:deps[ref]}),current_turn=4)
            with f.store._connect() as db:template=dict(db.execute('SELECT * FROM world_query_cache WHERE query_fingerprint=?',(query['cache']['query_fingerprint'],)).fetchone())
            # Historical receipt stress: actual result envelope and dependency
            # digest, distinct legal query parameters and issued opaque handles.
            # This tests storage/resolution scaling, not new mechanics accuracy.
            columns=list(template);prepared=[]
            for i in range(count):
                row=dict(template);request=json.loads(row['request_json']);request['origin_ref']='unit-history-'+str(i//256)
                request['target_ref']='location-'+str(i%256)
                result=json.loads(row['result_json']);result['route']['route_ref']='route-history-'+str(i)
                row.update(query_fingerprint=content_hash({'history':i}),request_json=canonical_json(request),result_json=canonical_json(result),created_unix=template['created_unix']+i+1,last_hit_unix=None)
                prepared.append(tuple(row[k] for k in columns))
            with f.store.transaction() as db:db.executemany('INSERT INTO world_query_cache('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',prepared)
            f.actor('unrelated-history','economy_state',0,0,credits=20);f.save()
            from smacx_store import SmacxStore
            from smacx_world_store import WorldStore
            from smacx_world import WorldService
            from smacx_attention import AttentionService
            f.store=SmacxStore(f.root/'state.sqlite3');f.worlds=WorldStore(f.store);f.service=WorldService(f.worlds,f.scope);f.attention=AttentionService(f.store,f.journal,f.scope)
            def timed(fn):
                start=time.perf_counter();result=fn();return round((time.perf_counter()-start)*1000,3),result
            registry_ms,registry=timed(f.registry);assert ref in registry
            watch_ms,_=timed(lambda:f.attention.evaluate_watches([],observation_cursor=20,turn=4))
            inspection_ms,_=timed(lambda:f.attention.inspect_scope(scope))
            assembler=RuntimeContextAssembler(scope=f.scope,world=f.service,attention=f.attention,snapshot=lambda:{'turn':4},working_state=lambda:{'sections':{}})
            runtime_ms,_=timed(lambda:assembler.build(episode_id='history-'+str(count),episode_mode='gameplay',context_length=65536))
            with f.store._connect() as db:retained=db.execute('SELECT COUNT(*) FROM world_query_cache').fetchone()[0]
            assert retained<=65,retained
            assert ref in f.registry()
            next(r for r in f.objects if r['object_ref']==f.at(8,2))['fields']['features']=field(['fungus']);f.save()
            assert ref not in f.registry()
            # Release every explicit consumer, then the old receipt is collectable.
            with f.store.transaction() as db:
                db.execute("UPDATE world_watches SET status='closed'");db.execute("UPDATE cognitive_operations SET status='completed'")
            updated={**plan,'status':'completed'};f.journal.append(f.scope,'memory.plan',{'record':updated})
            f.worlds.prune_query_cache(f.scope,f.identity.timeline_id,f.identity.world_epoch)
            with f.store._connect() as db:assert not db.execute('SELECT 1 FROM world_query_cache WHERE query_fingerprint=?',(query['cache']['query_fingerprint'],)).fetchone()
            measurements.append({'historical_queries':count,'retained':retained,'registry_ms':registry_ms,'watch_ms':watch_ms,'scope_inspection_ms':inspection_ms,'runtime_ms':runtime_ms})
    assert measurements[-1]['registry_ms'] < max(1500,measurements[0]['registry_ms']*4),measurements
    print(json.dumps({'passed':True,'measurements':measurements,'old_pinned_restart_valid':True,'true_dependency_invalidates':True,'release_gc':True,
        'population':'distinct historical cache receipts shaped from a production query; not 5000 live provider executions'}))
if __name__=='__main__':main()
