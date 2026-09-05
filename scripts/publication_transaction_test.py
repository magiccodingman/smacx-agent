#!/usr/bin/env python3
"""Frozen N owns pre-head watches and recoverable post-head attention."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from observation_collector_benchmark import NativeFixture
from semantic_consumer_contract_test import Fixture
from smacx_observation import ObservationCollector


def setup(root):
    f=Fixture(root,width=32,height=16); native=NativeFixture(32,16,ready_drop_units=1)
    native.bases[0]['base_ref']='base-location-0'; native.units[0]['tile_id']=0
    def collect(): return ObservationCollector(scope=f.scope,session_id='session-review',bridge_call=native,
        journal=f.journal,world_store=f.worlds,attention=f.attention)
    collect().collect_once()
    plan=f.store.put_plan(f.scope,'publication','Publication','Wait for explicit dependency',dependencies=['base-location-0'],
        last_confirmation={'dependency_values':[{'ref':'base-location-0','field':'mineral_surplus','value':8}]})
    f.journal.append(f.scope,'memory.plan',{'record':plan}); f.attention.capture_current_plan_dependencies()
    return f,native,collect,plan


def main():
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,plan=setup(Path(tmp))
        def milestone(req):
            return f.attention.create_watch('milestone',['base-location-0'],{'requirements':[{'ref':'base-location-0',**req}]},
                current_turn=50,linked_plan_id=plan['plan_id'])['watch_id']
        g=milestone({'kind':'garrison_count','count':1})
        m=milestone({'kind':'current_field','field':'mineral_surplus','value':8})
        birth=milestone({'kind':'production_completed','value':'Scout Patrol'})
        collect().collect_once()
        native.units[0]['tile_id']=1; native.bases[0]['mineral_surplus']=8
        newborn={**native.units[0],'id':1,'own_unit_ref':'own-unit-777','native_observation_key':'vehicle-handle-777','tile_id':2}
        native.units.append(newborn)
        native.events=[{'sequence':1,'kind':'owned_production_completed','turn':50,'subject_a':0,'subject_b':8,
            'from_tile_id':0,'to_tile_id':0,'value_before':0,'value_after':777,'item_name':'Scout Patrol'}]
        native.revision+=1
        seen=[]; original=f.attention.evaluate_watches
        def evaluate(*args,**kwargs):
            assert f.worlds.load(f.scope,f.attention.timeline_id)['action_revision'] != kwargs['publication_projection']['action_revision']
            result=original(*args,**kwargs);seen.extend(result);return result
        with patch.object(f.attention,'evaluate_watches',side_effect=evaluate): collect().collect_once()
        states={row['watch_id']:row['matches'][0]['milestone']['state'] for row in seen}
        assert states[birth]=='ready' and states[m]=='ready',states
        assert f.attention.inspect_watch(g)['milestone']['state']=='pending'
        native.units[0]['tile_id']=0;native.revision+=1; collect().collect_once()
        assert f.attention.inspect_watch(g)['milestone']['state']=='ready'
        area=f.attention.create_watch('spatial_scope',['location-0'],{'type':'proximity','radius':1},current_turn=50)['watch_id']
        native.tiles[0]['terrain']='ocean';native.revision+=1
        def invalidated(*a,**kw):
            assert area not in f.attention._semantic_registry(kw['publication_projection'],[])
            return original(*a,**kw)
        with patch.object(f.attention,'evaluate_watches',side_effect=invalidated):collect().collect_once()
    cases=[]
    for boundary in ('before_watch','after_watch_enqueue','after_head','before_dependency','before_available_enqueue',
                     'after_available_enqueue','before_dependency_state','before_ack','after_ack'):
        for reverse in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                f,native,collect,plan=setup(Path(tmp))
                f.attention.create_watch('milestone',['base-location-0'],{'requirements':[{'ref':'base-location-0',
                    'kind':'current_field','field':'mineral_surplus','value':8}]},current_turn=50,linked_plan_id=plan['plan_id'])
                native.bases[0]['mineral_surplus']=8;native.revision+=1
                fired=[False]
                def fail(): fired[0]=True;raise RuntimeError('injected:'+boundary)
                originals={name:getattr(obj,name) for obj,name in [(f.attention,'evaluate_watches'),(f.attention,'enqueue'),
                    (f.attention,'capture_current_plan_dependencies'),(f.worlds,'replace_projection'),
                    (f.journal,'append'),(f.worlds,'acknowledge_native_observation_publication')]}
                def wrap(name):
                    def call(*a,**kw):
                        kind=a[0] if name=='enqueue' and a else a[1] if name=='append' and len(a)>1 else ''
                        before=(boundary=='before_watch' and name=='evaluate_watches' or
                            boundary=='before_dependency' and name=='capture_current_plan_dependencies' or
                            boundary=='before_available_enqueue' and name=='enqueue' and kind=='plan_dependency_available' or
                            boundary=='before_dependency_state' and name=='append' and kind=='attention.plan_dependency_state' or
                            boundary=='before_ack' and name=='acknowledge_native_observation_publication')
                        if before and not fired[0]:fail()
                        result=originals[name](*a,**kw)
                        after=(boundary=='after_watch_enqueue' and name=='enqueue' and kind=='watch_trigger' or
                            boundary=='after_available_enqueue' and name=='enqueue' and kind=='plan_dependency_available' or
                            boundary=='after_head' and name=='replace_projection' or
                            boundary=='after_ack' and name=='acknowledge_native_observation_publication')
                        if after and not fired[0]:fail()
                        return result
                    return call
                from contextlib import ExitStack
                with ExitStack() as stack:
                    for obj,name in [(f.attention,'evaluate_watches'),(f.attention,'enqueue'),(f.attention,'capture_current_plan_dependencies'),
                                     (f.worlds,'replace_projection'),(f.journal,'append'),(f.worlds,'acknowledge_native_observation_publication')]:
                        stack.enter_context(patch.object(obj,name,side_effect=wrap(name)))
                    try:collect().collect_once()
                    except RuntimeError as e:assert str(e).startswith('injected:')
                    else:raise AssertionError(boundary)
                if reverse:native.bases[0]['mineral_surplus']=4;native.revision+=1
                # New collector restores the durable stage before consulting native N+1.
                collect().collect_once();collect().collect_once()
                with f.store._connect() as c:
                    assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='plan_dependency_available'").fetchone()[0]==1,(boundary,reverse)
                    assert c.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='watch_trigger'").fetchone()[0] in ({1,2} if reverse else {1}),(boundary,reverse)
                assert f.journal.verify(f.scope)['ok']
                cases.append({'boundary':boundary,'native_reversed':reverse,'passed':True})
    print(json.dumps({'passed':True,'candidate_first_evaluation':True,'newborn_no_false_block':True,'garrison_departure_arrival':True,
        'current_field':True,'crash_matrix':cases,'evidence':'native_shaped_production_collector'}))
if __name__=='__main__':main()
