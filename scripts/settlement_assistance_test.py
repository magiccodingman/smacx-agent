#!/usr/bin/env python3
"""Settlement economics and material access events: no impossible joint output."""
import copy
from smacx_settlement import economic_assessment, access_changes, parse_search, shortlist
r={'site_economy':{'coverage':'fixed_current_territory_and_owned_worker_assignments',
 'nutrients_per_citizen':2,'benchmark_colony_mineral_cost':30,
 'center':{'location_ref':'location-0','epistemic_status':'conditional','yields':{'nutrients':2,'minerals':1,'energy':1}},
 'squares':[{'location_ref':'location-1','epistemic_status':'conditional','workable':True,'yields':{'nutrients':3,'minerals':0,'energy':0}},
 {'location_ref':'location-2','epistemic_status':'conditional','workable':True,'shared_known_base_count':1,'yields':{'nutrients':0,'minerals':4,'energy':0}},
 {'location_ref':'location-3','epistemic_status':'conditional','workable':False,'reserved_by_owned_worker':True,'yields':{'nutrients':9,'minerals':9,'energy':9}}]}}
original=copy.deepcopy(r);a=economic_assessment(r)
assert r==original
assert a['reserved_by_owned_workers']==1 and a['shared_unreserved_tiles']==1
for alt in a['population_alternatives'][0]['alternatives']:
 assert len(alt['worker_refs'])==1 and 'location-3' not in alt['worker_refs']
 assert alt['gross_output']!={'nutrients':5,'minerals':5,'energy':1}
assert economic_assessment({})['status']=='economic_preview_unavailable'
assert parse_search('{}')['purpose']=='expansion'
for raw in ('{"purpose":"best"}', '{"max_travel_turns":true}', '{"hidden_units":true}'):
 try:parse_search(raw);raise AssertionError(raw)
 except ValueError:pass
base={'object_ref':'base-location-0','kind':'base','fields':{'base_radius':{'epistemic_status':'current','value':[
 {'location_ref':'location-1','worked':True,'access':{'foreign_territory':False,'visible_occupation_constraint':False}}]}}}
after=copy.deepcopy(base);after['fields']['base_radius']['value'][0]['access']['foreign_territory']=True
assert len(access_changes([base],[after]))==1
assert not access_changes([after],[after])
assert not access_changes([], [after])
after['fields']['base_radius']['epistemic_status']='stale';assert not access_changes([base],[after])
print('PASS: feasible allocations, reserved/shared coverage, heuristic qualifications, access transitions, unchanged/discovery/stale suppression')
