#!/usr/bin/env python3
"""Compile actual multiplayer development eligibility, including negative gates."""
import pathlib,subprocess,tempfile
s=(pathlib.Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
helper=s[s.index('bool multiplayer_development_eligible('):s.index('std::string multiplayer_unit_choices_response(')]
dispatch=s[s.index('if (deferred_development_unit_id >= 0) {'):s.index('if (deferred_disband_unit_id >= 0) {')]
for receipt in ('turn_not_actionable_before_execution','development_unit_missing_before_execution',
                'development_unit_owner_changed_before_execution','development_unit_location_changed_before_execution',
                'development_unit_identity_changed_before_execution','development_choice_no_longer_legal_before_execution',
                'network_unit_lock_rejected','network_unit_lock_remapped',
                'development_unit_identity_changed_after_lock','development_choice_changed_after_lock'):
    assert receipt in dispatch,receipt
assert dispatch.rindex('deferred_action.native_call_attempted = 1;') > dispatch.index('if (lock_result) {')
code=r'''
#include <cassert>
const int TRIAD_LAND=0,FORMER_FARM=0,FORMER_MINE=2,FORMER_SOLAR=3,FORMER_FOREST=4,FORMER_ROAD=5,FORMER_SENSOR=9,FORMER_REMOVE_FUNGUS=10;
const int ORDER_HOLD=2,ORDER_SENTRY_BOARD=1,ORDER_FARM=4,VehOrderFormerLast=23,LEVEL_ROCKY=2,MaxBaseNum=512;
enum FormerItem { Dummy };
int vehicle_count=1,base_count=1;int* VehCount=&vehicle_count;int* BaseCount=&base_count;
bool ready=true,legal=true,technology=true,jail=false,ocean=false,present=true;
struct Vehicle {int faction_id=1, x=0,y=0,order=0,triad_value=0;bool colony=true,former=false;
 int triad(){return triad_value;}bool is_colony(){return colony;}bool is_former(){return former;}};
using VEH=Vehicle;VEH Vehs[1];
struct MAP {int owner=-1,items=0,base=-1,rocky=0;bool fungus=false;
 int base_who(){return base;}int rocky_level(){return rocky;}bool is_fungus(){return fungus;}} square;
struct Terrain {int bit=1;};Terrain Terraform[24];
MAP* mapsq(int,int){return present?&square:nullptr;}bool is_ocean(MAP*){return ocean;}
bool semantic_unit_requires_decision(int){return ready;}
bool can_build_base(int,int,int,int){return legal;}
bool terrain_avail(FormerItem,int,int){return technology;}
bool veh_jail(int){return jail;}
HELPER
int main(){
 assert(multiplayer_development_eligible(1,0,-1));
 assert(!multiplayer_development_eligible(1,-1,-1));assert(!multiplayer_development_eligible(1,1,-1));
 assert(!multiplayer_development_eligible(2,0,-1));
 ready=false;assert(!multiplayer_development_eligible(1,0,-1));ready=true;
 legal=false;assert(!multiplayer_development_eligible(1,0,-1));legal=true;
 base_count=MaxBaseNum;assert(!multiplayer_development_eligible(1,0,-1));base_count=1;
 square.owner=2;assert(!multiplayer_development_eligible(1,0,-1));square.owner=-1;
 ocean=true;assert(!multiplayer_development_eligible(1,0,-1));ocean=false;
 Vehs[0].triad_value=1;assert(!multiplayer_development_eligible(1,0,-1));Vehs[0].triad_value=0;
 Vehs[0].colony=false;Vehs[0].former=true;
 for(int order:{0,2,3,4,5,9})assert(multiplayer_development_eligible(1,0,order));
 for(int order:{1,6,7,8,11,12,16,17,19,999})assert(!multiplayer_development_eligible(1,0,order));
 technology=false;assert(!multiplayer_development_eligible(1,0,5));technology=true;
 square.base=0;assert(!multiplayer_development_eligible(1,0,5));square.base=-1;
 square.items=1;assert(!multiplayer_development_eligible(1,0,5));square.items=0;
 square.rocky=LEVEL_ROCKY;assert(!multiplayer_development_eligible(1,0,0));square.rocky=0;
 assert(!multiplayer_development_eligible(1,0,10));square.fungus=true;
 assert(multiplayer_development_eligible(1,0,10));assert(!multiplayer_development_eligible(1,0,5));
 Vehs[0].order=ORDER_HOLD;assert(multiplayer_activation_eligible(1,0));
 jail=true;assert(!multiplayer_activation_eligible(1,0));jail=false;
 assert(!multiplayer_activation_eligible(2,0));
}
'''.replace('HELPER',helper).replace('#include <cassert>','#include <cassert>\n#include <initializer_list>')
with tempfile.TemporaryDirectory() as t:
 p=pathlib.Path(t);(p/'test.cpp').write_text(code)
 subprocess.run(['c++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('multiplayer development production guards passed (native effects tested separately)')
