#!/usr/bin/env python3
"""Gameplay-level geography/cache/epistemic regressions, with sanitized timing."""
import copy
import json
import tempfile
import time
from pathlib import Path
from smacx_mechanics import logistics
from smacx_world import WorldService
from smacx_world_model import SemanticLodProjector, PerspectiveProjector
from smacx_world_types import WorldIdentity
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology
from geographic_semantics_contract_test import obj, initialized, projection


def main():
    width,height=128,32
    squares=[];objects=[];islands=[]
    for y in range(height):
        for x in range(y%2,width,2):
            ref=f'location-{(x+width*y)//2}'
            land=y%4==0 and x%8==0
            square=KnownSquare(ref,x,y,'land' if land else 'ocean')
            squares.append(square);objects.append(obj(ref,'location',terrain=square.terrain,features=[]))
            if land: islands.append(ref)
    objects += [obj('faction-1','faction',is_self=True),obj('faction-2','faction',relations={'vendetta':True}),
                obj('base-colony','base',islands[-1],owner_ref='faction-1'),
                obj('foreign-base','base',islands[-2],owner_ref='faction-2')]
    for i in range(6):
        objects.append(obj(f'landing-{i}','foreign_contact',islands[-1],owner_ref='faction-2',triad='land',relationship='hostile'))
    model=projection(squares,objects,width=width,height=height)
    anchor=SemanticLodProjector(context_tier='64k').build(model)
    colony=next(row for row in anchor['physical_masses'] if row.get('owned_base_count'))
    assert colony['owned_base_refs']==['base-colony']
    assert colony['current_visible_land_contact_counts_by_faction']=={'faction-2':6}
    assert any(row.get('current_foreign_base_count') for row in anchor['physical_masses'])
    assert anchor['physical_mass_overflow']['omitted_count']>0 and anchor['token_estimate']<=6000
    # 60 bases must not starve any global domain.
    globals_={'economy_state','research_state','social_state','council_state','victory_state','project_race_state','ecology_state'}
    mature=projection(squares[:1],[obj(f'base-{i}','base',squares[0].location_ref,name=f'Base {i}') for i in range(60)] +
                      [obj(kind,kind,state={'status':'active','value':7}) for kind in globals_],width=width,height=height)
    mature_anchor=SemanticLodProjector(context_tier='64k').build(mature)
    assert globals_ <= {row['kind'] for row in mature_anchor['strategic_objects']}
    # A late-constructed frontier must survive each explicit promotion channel.
    frontier_squares=[KnownSquare(f'edge-{i}',i*4,0,'land') for i in range(64)]
    frontier_model=projection(frontier_squares,[],width=256,height=4)
    complete=SemanticLodProjector(context_tier='64k').build(frontier_model,registry_only=True)
    late=complete['frontiers'][-1]['frontier_ref']
    for channel in ('active_plan_refs','operation_refs','triggered_watch_refs','recent_material_refs','inspection_refs'):
        promoted=SemanticLodProjector(context_tier='64k').build(frontier_model,**{channel:[late]})
        assert any(row['frontier_ref']==late for row in promoted['frontiers']),channel
    focused=SemanticLodProjector(context_tier='64k').build(frontier_model,focus_ref=late)
    assert any(row['frontier_ref']==late for row in focused['frontiers'])
    # Distant conflicts on one continent and unrelated adjacent crises stay distinct.
    mainland=[KnownSquare(f'p{i}',2*i,2,'land') for i in range(40)]
    wars=[obj('west','foreign_contact','p0',owner_ref='hive',relationship='hostile'),
          obj('east','foreign_contact','p39',owner_ref='morgan',relationship='hostile'),
          obj('adjacent','foreign_contact','p1',owner_ref='morgan',relationship='hostile')]
    theaters=SemanticLodProjector(context_tier='64k').build(projection(mainland,wars,width=80,height=4))['active_theaters']
    assert len(theaters)==3
    timings={}
    with tempfile.TemporaryDirectory() as temporary:
        store,scope,world_store=initialized(Path(temporary))
        identity=WorldIdentity('match-geo','perspective-geo','timeline-main','world-geo')
        native={'turn':40,'map':model['map_shape'],'tiles':[{'tile_id':i,'x':sq.x,'y':sq.y,'visible_now':True,'terrain':sq.terrain,'features':[]} for i,sq in enumerate(squares)],
                'units':[],'bases':[],'factions':[]}
        projected=PerspectiveProjector(identity).project(native,observation_sequence=1)
        world_store.replace_projection(scope,identity,projected['objects'],observation_cursor=1,action_revision='a',continuity='complete',journal_head_hash='0'*64)
        service=WorldService(world_store,scope)
        issued=service.anchor(context_length=65536)['payload']
        listed={row.get('landmass_ref') or row.get('ocean_mass_ref') for row in issued['physical_masses']}
        registry=[];cursor=''
        while True:
            answer=service.query(mode='area',origin_ref='world-geography',continuation=cursor,detail='deep')
            registry+=answer['items'];cursor=answer['continuation']
            if not cursor: break
        omitted=next(row['landmass_ref'] for row in registry if row.get('landmass_ref') and row['landmass_ref'] not in listed)
        assert service.query(mode='area',origin_ref=omitted)['ok']
        mobility=next(row['region_ref'] for row in registry if row.get('region_ref') and row.get('mobility_profile_ref'))
        assert service.query(mode='area',origin_ref=mobility)['geographic_object']['region_ref']==mobility
        for mode,kwargs in [('relation',{'origin_ref':islands[0],'target_ref':islands[-1]}),('area',{'origin_ref':islands[0]}),('compare',{'subject_refs':[islands[0]]}),('logistics',{})]:
            t=time.perf_counter();cold=service.query(mode=mode,**kwargs);cold_ms=(time.perf_counter()-t)*1000
            t=time.perf_counter();warm=service.query(mode=mode,**kwargs);warm_ms=(time.perf_counter()-t)*1000
            assert warm['cache']['hit']
            timings[mode]={'cold_ms':round(cold_ms,3),'warm_ms':round(warm_ms,3),'result_tokens':warm['result_token_estimate']}
            if mode=='relation':
                assert cold['relation']['physical_connectivity']['qualification']=='separation_established_by_known_geography'
        # An unmapped gap gives a different explicit relationship qualification.
        native['tiles']=[native['tiles'][0],native['tiles'][4]]
        changed=PerspectiveProjector(identity).project(native,observation_sequence=2)
        world_store.replace_projection(scope,identity,changed['objects'],observation_cursor=2,action_revision='b',continuity='complete',journal_head_hash='0'*64)
        relation=service.query(mode='relation',origin_ref='location-0',target_ref='location-4')['relation']
        assert relation['physical_connectivity']['unknown_geography_may_connect'] is True
    # Remembered Pact, no-riot status and improvements never become current repair facts.
    topology=PerspectiveTopology(MapShape(8,4,False),[KnownSquare('a',0,2,'land'),KnownSquare('b',2,2,'land')])
    repair=[obj('faction-1','faction',is_self=True),obj('faction-2','faction',relations={'pact':True}),
            obj('unit','own_unit','a',owner_ref='faction-1',triad='land',hp=2,max_hp=10,roles={'combat':True}),
            obj('home','base','a',owner_ref='faction-1',drone_riots=False),
            obj('pact','base','b',status='stale',owner_ref='faction-2',drone_riots=False)]
    for envelope in repair[-1]['fields'].values(): envelope['epistemic_status']='stale'
    answer=logistics({row['object_ref']:row for row in repair},topology,['unit'])
    rows={row.get('base_ref'):row for row in answer['repair_locations']}
    assert rows['home']['base_repair_status']=='current_usable'
    assert rows['pact']['base_repair_status']=='stale_needs_reverification'
    assert all(row['epistemic_status']!='current' for row in answer['damaged_unit_repair_options'] if row.get('base_ref')=='pact')
    current_pact=copy.deepcopy(repair)
    current_pact[-1]['status']='active'
    for envelope in current_pact[-1]['fields'].values(): envelope['epistemic_status']='current'
    def pact_status(items):
        answer=logistics({row['object_ref']:row for row in items},topology,['unit'])
        return next(row for row in answer['repair_locations'] if row.get('base_ref')=='pact')['base_repair_status']
    assert pact_status(current_pact)=='current_usable'
    current_pact[-1]['fields']['drone_riots']['epistemic_status']='stale'
    assert pact_status(current_pact)=='stale_needs_reverification'
    current_pact[-1]['fields']['drone_riots']['epistemic_status']='current'
    current_pact[1]['fields']['relations']['epistemic_status']='unknown'
    assert pact_status(current_pact)=='access_unknown'
    # Feature freshness does not certify ownership-dependent repair bonuses.
    features=PerspectiveTopology(MapShape(8,4,False),[KnownSquare('a',0,2,'land',features=frozenset({'bunker'}),owner_ref='faction-1')])
    evidence=[*repair[:3],obj('a','location',features=['bunker'],owner_ref='faction-1'),
              obj('global-repair-rules','repair_rules',state={'minimal':1,'friendly_territory_bonus':1})]
    evidence[-2]['fields']['owner_ref']['epistemic_status']='stale'
    options=logistics({row['object_ref']:row for row in evidence},features,['unit'])['damaged_unit_repair_options']
    assert 'friendly_territory_bonus' not in options[0]['known_repair_rule_modifiers']
    assert options[0]['conditional_repair_rule_modifiers']['friendly_territory_bonus']==1
    stale_airbase=PerspectiveTopology(MapShape(8,4,False),[KnownSquare('a',0,2,'land',features=frozenset({'airbase'}),owner_ref='faction-1')])
    evidence[-2]['fields']['features']['value']=['airbase']
    evidence[-2]['fields']['features']['epistemic_status']='stale'
    evidence[2]['fields']['triad']['value']='air'
    air_options=logistics({row['object_ref']:row for row in evidence},stale_airbase,['unit'])['damaged_unit_repair_options']
    assert air_options[0]['epistemic_status']=='stale'
    assert not air_options[0]['known_repair_rule_modifiers']
    archipelago=PerspectiveTopology(MapShape(8,6,False),[


        KnownSquare('port',0,2,'land',features=frozenset({'base'})),
        KnownSquare('sea',1,3,'ocean'),KnownSquare('island',2,4,'land',features=frozenset({'base'}))])
    shipping={row['object_ref']:row for row in [
        obj('faction-1','faction',is_self=True),
        obj('port-base','base','port',owner_ref='faction-1',coastal=True,drone_riots=False),
        obj('repair-base','base','island',owner_ref='faction-1',coastal=True,drone_riots=False),
        obj('passenger','own_unit','port',owner_ref='faction-1',triad='land',movement_points=2,moves_remaining=2,hp=2,max_hp=10,roles={'combat':True}),
        obj('ferry','own_unit','port',owner_ref='faction-1',triad='sea',movement_points=2,moves_remaining=2,roles={'transport':True},cargo={'capacity':4,'loaded':0})]}
    shipping_result=logistics(shipping,archipelago,['passenger'])
    assisted=next(row for row in shipping_result['damaged_unit_repair_options'] if row.get('base_ref')=='repair-base')
    assert assisted['transport_dependency']=='transport_assisted' and assisted['arrival_turns'] is not None,assisted
    staged=next(row for row in shipping_result['staging_bases'] if row['base_ref']=='repair-base')['subject_arrivals'][0]
    assert staged['transport_dependency']=='transport_assisted' and staged['reachable']
    del shipping['ferry']
    stranded=logistics(shipping,archipelago,['passenger'])
    candidate=next(row for row in stranded['damaged_unit_repair_options'] if row.get('base_ref')=='repair-base')
    assert candidate['transport_dependency']=='no_known_transport_path'
    print(json.dumps({'passed':True,'fragmented_anchor_tokens':anchor['token_estimate'],
                      'mature_empire_anchor_tokens':mature_anchor['token_estimate'],'geography_queries':timings},sort_keys=True))

if __name__=='__main__':main()
