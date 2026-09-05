#!/usr/bin/env python3
"""Each explicit consumer independently protects an old query receipt."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture
from smacx_world_types import content_hash,canonical_json


def main():
    passed=[]
    for consumer in ('watch','scope','operation','plan','milestone'):
        with tempfile.TemporaryDirectory() as tmp:
            f=Fixture(Path(tmp));f.actor('unit-pinned','own_unit',2,2,triad='land',movement_points=3,owner_ref='faction-1',roles={'combat':True});f.save()
            query=f.service.query(mode='route',origin_ref='unit-pinned',target_ref=f.at(8,2));ref=query['route']['route_ref']
            if consumer=='watch':f.attention.create_watch('route_disruption',[ref],{},current_turn=4)
            if consumer=='scope':f.attention.create_watch('spatial_scope',[ref],{'type':'route_corridor','radius':1},current_turn=4)
            if consumer=='operation':
                deps=f.attention.semantic_dependency_hashes();f.attention.upsert_operation(operation_id=None,kind='pin',objective='Explicit route',referenced_world_objects=[ref],
                    source_world_epoch=f.identity.world_epoch,source_world_revision=1,source_dependency_hash=content_hash({ref:deps[ref]}),current_turn=4)
            if consumer in {'plan','milestone'}:
                plan=f.store.put_plan(f.scope,'pin-plan','Pin','Explicit dependency',dependencies=[ref] if consumer=='plan' else [])
                f.journal.append(f.scope,'memory.plan',{'record':plan})
                if consumer=='milestone':f.attention.create_watch('milestone',[ref],{'requirements':[{'kind':'dependency_valid','ref':ref}]},current_turn=4,linked_plan_id=plan['plan_id'])
            with f.store._connect() as db:row=dict(db.execute('SELECT * FROM world_query_cache').fetchone())
            columns=list(row);values=[]
            for i in range(100):
                noise=dict(row);result=json.loads(row['result_json']);result['route']['route_ref']='route-noise-'+str(i)
                noise.update(query_fingerprint='noise-'+str(i),result_json=canonical_json(result),created_unix=row['created_unix']+i+1)
                values.append(tuple(noise[k] for k in columns))
            with f.store.transaction() as db:db.executemany('INSERT INTO world_query_cache('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
            f.worlds.prune_query_cache(f.scope,f.identity.timeline_id,f.identity.world_epoch)
            assert ref in f.registry(),consumer
            passed.append(consumer)
    print(json.dumps({'passed':True,'independent_pin_consumers':passed}))
if __name__=='__main__':main()
