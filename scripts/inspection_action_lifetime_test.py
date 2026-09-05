#!/usr/bin/env python3
"""Inspection promotion inherits ordinary vs native-receipt authority."""
import json
from pathlib import Path
import tempfile
from semantic_consumer_contract_test import Fixture,field
from smacx_world import WorldService
from smacx_world_store import WorldStore
from smacx_store import SmacxStore


def main():
    cases=[]
    for mode in ('route','area','geography','native_route'):
        for warm in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                f=Fixture(Path(tmp));f.actor('unit-inspection','own_unit',2,2,triad='land',movement_points=3,
                    owner_ref='faction-1',roles={'combat':True,'airdrop_capable':True},airdrop_ready=True);f.save()
                target=f.at(8,2)
                args={'mode':'route' if 'route' in mode else 'area',
                    'origin_ref':'unit-inspection' if 'route' in mode else 'world-geography' if mode=='geography' else target,
                    'target_ref':target if 'route' in mode else '', 'subject_refs':[target] if mode=='geography' else []}
                if mode=='native_route':args['runtime_airdrop_receipt']={'action_revision':'r1','targets':[{'target_tile_id':28}]}
                result=f.service.query(**args);assert result['ok'],result
                if warm:assert f.service.query(**args)['cache']['hit']
                assert target in f.service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
                with f.store._connect() as db:
                    inspected_at=db.execute("SELECT json_extract(result_json,'$._inspection.validated_unix') FROM world_query_cache").fetchone()[0]
                before=f.worlds.load(f.scope,f.identity.timeline_id)
                f.save();after=f.worlds.load(f.scope,f.identity.timeline_id)
                assert before['world_revision']==after['world_revision'] and before['action_revision']!=after['action_revision']
                for service in (f.service,WorldService(WorldStore(SmacxStore(f.root/'state.sqlite3')),f.scope)):
                    refs=service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
                    assert (target in refs)==(mode!='native_route'),(mode,warm,refs)
                # Unrelated material churn must not shorten query authority either.
                f.actor('unrelated-economy','economy_state',0,0,credits=10);f.save()
                for service in (f.service,WorldService(WorldStore(SmacxStore(f.root/'state.sqlite3')),f.scope)):
                    refs=service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
                    assert (target in refs)==(mode!='native_route'),(mode,refs)
                changed_rules=WorldService(f.worlds,f.scope,ruleset_hash='different-rules')
                assert target not in changed_rules.anchor(context_length=65536)['payload']['lod']['promotion_refs']
                with f.store._connect() as db:
                    assert db.execute("SELECT json_extract(result_json,'$._inspection.validated_unix') FROM world_query_cache").fetchone()[0]==inspected_at
                # A true movement/geography dependency change withdraws the inspection.
                next(row for row in f.objects if row['object_ref']==target)['fields']['terrain']=field('ocean');f.save()
                assert target not in f.service.anchor(context_length=65536)['payload']['lod']['promotion_refs']
                cases.append({'mode':mode,'warm':warm,'action_churn_restart':True,'unrelated_material_churn':True,'ruleset_change_invalidates':True,'automatic_validation_does_not_renew_time':True,'dependency_change_invalidates':True})
    print(json.dumps({'passed':True,'classification':'deterministic managed query, native-shaped receipt','cases':cases}))
if __name__=='__main__':main()
