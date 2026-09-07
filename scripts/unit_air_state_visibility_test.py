#!/usr/bin/env python3
"""Shared native byte semantics and private aircraft-state projection boundary."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity, provider_safe

source=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
helper=source[source.index('std::string semantic_owned_air_state('):source.index('bool semantic_bombing_run_unit_eligible(')]
code=r'''
#include <string>
#include <sstream>
#include <cassert>
const int TRIAD_AIR=2;
struct VEH { int faction_id=1,type=0,movement_turns=6,x=0,y=0,fuel_range=2;
 bool missile=false;int triad(){return type;}bool is_missile(){return missile;}
 int range(){return fuel_range;} } Vehs[1];
int calls=0;
int semantic_air_safe_range(int){++calls;return 4;}
int semantic_air_full_safe_range(int){++calls;return 8;}
bool semantic_friendly_air_refuel_tile(int,int,int){++calls;return true;}
'''+helper+r'''
int main(){
 for(int type:{0,1}) for(int work:{0,4,6,255}) {
  Vehs[0].type=type;Vehs[0].movement_turns=work;
  assert(semantic_owned_air_state(1,0).empty());assert(calls==0);
 }
 Vehs[0].type=2;
 for(int fuel:{0,1,255}) {Vehs[0].movement_turns=fuel;
  assert(semantic_owned_air_state(2,0).empty());assert(calls==0);}
 Vehs[0].movement_turns=1;
 assert(semantic_owned_air_state(1,0).find("\"air_fuel_turns_used\":1,")!=std::string::npos);
 assert(calls==3);
 Vehs[0].fuel_range=0;
 assert(semantic_owned_air_state(1,0).find("\"air_fuel_turns_used\":-1,")!=std::string::npos);
 Vehs[0].fuel_range=2;Vehs[0].missile=true;
 assert(semantic_owned_air_state(1,0).find("\"air_fuel_turns_used\":-1,")!=std::string::npos);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
assert '<< semantic_owned_air_state(faction_id, i)' in source
assert '<< semantic_owned_air_state(faction_id, index)' in source

identity=WorldIdentity('match','perspective','timeline','world')
def project(owned,triad,counter):
 bundle={'turn':118,'map':{'width':8,'height':8},'tiles':[], 'bases':[], 'factions':[],
  'units':[{'id':1,'own_unit_ref':'own-unit-1','native_observation_key':'handle-1',
    'owned':owned,'tile_id':0,'triad':triad,'air_fuel_turns_used':counter,
    'air_safe_range':counter,'air_full_safe_range':counter,'air_origin_refuels':bool(counter),
    'roles':{'airdrop_used':bool(counter),'combat':True}}]}
 return [x.as_dict() for x in PerspectiveProjector(identity).project(bundle,observation_sequence=1)['objects']
         if x.kind in ('own_unit','foreign_contact')][0]
assert project(False,'air',1)==project(False,'air',6),'foreign private counter affects projection'
assert 'air_fuel_turns_used' not in project(True,'land',6)['fields']
assert project(True,'air',1)['fields']['air_fuel_turns_used']['value']==1

def evidence(value,source='owned_state',status='current'):
 return {'value':value,'source':source,'epistemic_status':status}
legacy={'triad':evidence('land'),'air_fuel_turns_used':evidence(6),'air_safe_range':evidence(-1)}
original=copy.deepcopy(legacy)
assert 'air_fuel_turns_used' not in provider_safe(legacy)
assert legacy==original,'provider repair mutated retained evidence'
foreign={'triad':evidence('air','direct_sight'),'air_fuel_turns_used':evidence(2,'direct_sight'),
 'air_safe_range':evidence(4,'direct_sight'),'roles':evidence({'airdrop_used':True},'direct_sight')}
assert 'air_safe_range' not in provider_safe(foreign)
assert 'airdrop_used' not in provider_safe(foreign)['roles']['value']
air={'triad':evidence('air',status='stale'),'air_fuel_turns_used':evidence(1,status='stale')}
assert provider_safe(air)==air,'stale aircraft evidence was promoted or lost'
assert provider_safe({'air_safe_range':4})=={'air_safe_range':4},'mechanical result changed'
old_projection={'objects':[{'object_ref':'contact-1','kind':'foreign_contact','fields':foreign,
                            'metadata':{'native_x':2,'native_y':0}}]}
original=copy.deepcopy(old_projection)
objects=WorldService._objects(old_projection)
assert 'air_safe_range' not in objects['contact-1']['fields']
assert objects['contact-1']['metadata']==original['objects'][0]['metadata']
assert old_projection==original,'calculation sanitation mutated checkpoint'

print(json.dumps({'passed':True,'compiled_production_output_helper':True,
 'foreign_private_state_invariance':True,'non_air_counter_not_fuel':True,
 'legacy_projection_sanitized_without_journal_mutation':True,
 'native_aircraft_fuel_prediction_comparison':'not asserted by this adapter test'}))
