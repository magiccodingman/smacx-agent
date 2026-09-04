#!/usr/bin/env python3
"""Adversarial gameplay seams, through production services where applicable."""
import copy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector
from smacx_world_types import WorldIdentity
from smacx_entitlements import sanitize_bundle, PerspectiveEntitlements
from geographic_semantics_contract_test import initialized, projection
from smacx_attention import AttentionService
from smacx_mechanics import base_mechanics
from smacx_regions import RegionBuilder
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology
from geographic_semantics_contract_test import obj, field


def main():
    pred = AttentionService._watch_predicate_matches
    def delta(before, after):
        return {'change': 'changed', 'previous': obj('base-a','base', production=before),
                'current': obj('base-a','base', production=after, minerals=10)}
    for value in ({'item':'Rover'}, None, 'Rover', 3):
        assert pred({'field':'production','operator':'eq','value':value},delta(value,value))
    assert not pred({'field':'production','operator':'gt','value':{}},delta({},{}))
    assert pred({'field':'production','operator':'gte','value':3},delta(2,3))
    assert not pred({'field':'production','operator':'gt','value':3},delta(2,3))
    assert not pred({'field':'production','operator':'changed'},delta('Rover','Rover'))
    assert pred({'field':'production','operator':'changed'},delta('Rover','Scout'))
    assert pred({'field':'production','operator':'changed'},delta('Scout','Rover'))
    squares=[KnownSquare('a',0,2,'land'), KnownSquare('b',2,2,'land'),KnownSquare('c',4,2,'land')]
    topology=PerspectiveTopology(MapShape(8,4,False),squares)
    objects={r['object_ref']:r for r in [obj('base','base','a'),
        obj('u0','own_unit','a',roles={'combat':True}),
        obj('u1','own_unit','b',roles={'combat':True}),
        obj('u2','own_unit','c',roles={'combat':True})]}
    assert [r['eta_turns'] for r in base_mechanics(topology,objects)[0]['friendly_response']]==[0,1,2]
    regions,_=RegionBuilder().build_physical(topology,'land',world_revision=1)
    location=obj('a','location',terrain='land')
    location['fields']['terrain']=field('land','stale',turn=30)
    contact=obj('c1','foreign_contact','a',triad='land')
    contact['fields']['triad']=field('land',turn=40)
    frontiers=RegionBuilder().frontiers(topology,regions,objects_by_location={'a':[location,contact]})
    row=frontiers[0].as_dict()
    assert row['map_information']['newest_last_verified_turn']==30,row
    # A changed owned responder invalidates the cached base answer; unrelated
    # economic state does not. Warm relation/area/compare avoid rich construction.
    with tempfile.TemporaryDirectory() as temporary:
        store,scope,world_store=initialized(Path(temporary))
        identity=WorldIdentity('match-geo','perspective-geo','timeline-main','world-geo')
        native={'turn':40,'year':2240,'map':{'width':8,'height':4,'horizontal_wrap':False},
                'tiles':[{'tile_id':8+i,'x':i*2,'y':2,'visible_now':True,'terrain':'land','features':[]} for i in range(3)],
                'bases':[{'id':0,'base_ref':'base-a','tile_id':8,'owned':True,'owner_ref':'faction-1'}],
                'units':[{'id':1,'own_unit_ref':'own-unit-1','tile_id':9,'owned':True,'owner_ref':'faction-1','triad':'land','roles':{'combat':True}}],
                'factions':[{'id':1,'faction_ref':'faction-1','owned':True}]}
        def publish(n):
            projected=PerspectiveProjector(identity).project(native,observation_sequence=n)
            world_store.replace_projection(scope,identity,projected['objects'],observation_cursor=n,
                action_revision=str(n),continuity='complete',journal_head_hash='0'*64)
        publish(1);service=WorldService(world_store,scope)
        first=service.query(mode='base',subject_refs=['base-a'])
        assert service.query(mode='base',subject_refs=['base-a'])['cache']['hit']
        native['units'][0]['tile_id']=8;publish(2)
        second=service.query(mode='base',subject_refs=['base-a'])
        assert not second['cache']['hit'] and second['items'][0]['garrison_refs']==['own-unit-1']
        native['units'].append({'id':2,'own_unit_ref':'own-unit-2','tile_id':10,'owned':True,
                                'owner_ref':'faction-1','triad':'land','roles':{'combat':False},'hp':10})
        publish(3)
        assert service.query(mode='base',subject_refs=['base-a'])['cache']['hit']
        native['units'][1]['hp']=8;publish(4)
        assert service.query(mode='base',subject_refs=['base-a'])['cache']['hit']
        for mode,kwargs in [('relation',{'origin_ref':'location-8','target_ref':'location-10'}),
                            ('area',{'origin_ref':'location-8'}),
                            ('compare',{'subject_refs':['location-8']}),('logistics',{})]:
            assert service.query(mode=mode,**kwargs)['ok']
            with patch.object(service,'_derived_geography',side_effect=AssertionError('warm rich rebuild')):
                assert service.query(mode=mode,**kwargs)['cache']['hit']
        registry=service.query(mode='area',origin_ref='world-geography',detail='deep')
        for row in registry['items']:
            ref=row.get('landmass_ref') or row.get('ocean_mass_ref') or row.get('region_ref') or row.get('frontier_ref')
            if ref:
                assert service.query(mode='area',origin_ref=ref)['ok']
        nominated=service.query(mode='area',origin_ref='world-geography',subject_refs=['base-a'],detail='deep')
        assert nominated['ok'] and any(row.get('landmass_ref') for row in nominated['items'])
        assert nominated['coverage']['subject_filter_applied']
        addresses=service.query(mode='area',origin_ref='world-map',detail='deep')
        assert addresses['ok'] and addresses['items']
        assert all(row['epistemic_status']=='unknown' and 'fields' not in row for row in addresses['items'])
        issued=service.anchor(context_length=65536,focus_ref='base-a')['payload']
        theater=next(row['theater_ref'] for row in issued['active_theaters'] if 'base-a' in row['subject_refs'])
        quiet=service.anchor(context_length=65536,active_plan_refs=[theater])['payload']
        assert any(row['theater_ref']==theater for row in quiet['active_theaters'])
        # Exercise real selector reconstruction against the persisted shape,
        # retaining a non-numeric alias to rule out semantic-ref decoding.
        import smacx_mcp
        stored_identity,stored_projection=service._projection()
        assert 'map_shape' not in stored_projection
        location=next(row for row in stored_projection['objects'] if row['object_ref']=='location-8')
        location['object_ref']='opaque-coast-address'
        with patch.object(smacx_mcp,'MANAGED_ATTACHED',True), \
             patch.object(smacx_mcp,'_managed_scope_identity',return_value=('match-geo','session',scope.agent_id,'perspective-geo')), \
             patch.object(smacx_mcp,'controller_world_service',return_value=(None,service,None)), \
             patch.object(service,'_projection',return_value=(stored_identity,stored_projection)), \
             patch.object(smacx_mcp,'_native_pages',return_value=[]), \
             patch.object(smacx_mcp,'_call',return_value={'ok':True,'action_revision':'4'}):
            assert smacx_mcp._resolve_managed_selectors('4',target_location_ref='opaque-coast-address')[0]['target_tile_id']==8
        relation=service.query(mode='relation',origin_ref='location-8',target_ref='location-10')
        assert relation['relation']['physical_connectivity']['qualification']=='same_known_physical_mass'
    # Survey channel carries only topography and never promotes vision.
    survey_native={'turn':1,'map':{'width':8,'height':4,'horizontal_wrap':False},
                  'tiles':[{'tile_id':0,'x':0,'y':0,'visible_now':False,'features':[],
                     'entitled_fields':{'terrain':{'channel':'unity_survey','value':'land'},
                                         'altitude':{'channel':'unity_survey','value':2}}}]}
    for enabled in (False,True):
        safe=sanitize_bundle(survey_native,PerspectiveEntitlements('faction-1',unity_survey=enabled))
        projected=PerspectiveProjector(identity).project(safe,observation_sequence=1)
        tile=next(o for o in projected['objects'] if o.object_ref=='location-0')
        assert tile.fields['terrain'].value==('land' if enabled else None)
        if enabled:
            assert tile.fields['terrain'].source.value=='survey'
            assert tile.fields['terrain'].status.value!='current'
        assert 'owner_ref' not in tile.fields and not tile.fields['features'].value
    print(json.dumps({'typed_watch_fields':True,'zero_turn_arrival':True,'map_freshness':True}))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
