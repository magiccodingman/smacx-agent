#!/usr/bin/env python3
"""Compile the production multiplayer pod eligibility predicate and negative gates."""
import pathlib, subprocess, tempfile

source=(pathlib.Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
helper=source[source.index('bool multiplayer_supply_pod_target('):
              source.index('void append_settlement_choice_evidence(')]
code=r'''
#include <cassert>
const int TRIAD_AIR=2,TRIAD_SEA=1;
int vehicle_count=1;int* VehCount=&vehicle_count;
bool ready=true,visible=true,pod=true,ocean=false;int occupied=-1,base=-1;
int BaseOffsetX[8]={1,-1,0,0,2,-2,1,-1};int BaseOffsetY[8]={0,0,2,-2,1,1,-1,-1};
struct MAP {int owner=-1;bool is_visible(int){return visible;}} square;
struct VEH {int faction_id=1,x=4,y=4,triad_value=0;int triad(){return triad_value;}} Vehs[1];
bool semantic_unit_requires_decision(int){return ready;}
MAP* mapsq(int x,int y){return x>=0&&y>=0?&square:nullptr;}
int goody_at(int,int){return pod;}
int wrap(int x){return x;}
int base_at(int,int){return base;}
int veh_at(int,int){return occupied;}
bool is_ocean(MAP*){return ocean;}
HELPER
int main(){
 assert(multiplayer_supply_pod_target(1,0,5,4));
 ready=false;assert(!multiplayer_supply_pod_target(1,0,5,4));ready=true;
 visible=false;assert(!multiplayer_supply_pod_target(1,0,5,4));visible=true;
 pod=false;assert(!multiplayer_supply_pod_target(1,0,5,4));pod=true;
 assert(!multiplayer_supply_pod_target(1,0,8,4));
 base=0;assert(!multiplayer_supply_pod_target(1,0,5,4));base=-1;
 occupied=0;assert(!multiplayer_supply_pod_target(1,0,5,4));occupied=-1;
 square.owner=2;assert(!multiplayer_supply_pod_target(1,0,5,4));square.owner=-1;
 ocean=true;assert(!multiplayer_supply_pod_target(1,0,5,4));
 Vehs[0].triad_value=TRIAD_SEA;assert(multiplayer_supply_pod_target(1,0,5,4));
 Vehs[0].triad_value=TRIAD_AIR;assert(multiplayer_supply_pod_target(1,0,5,4));
 assert(!multiplayer_supply_pod_target(2,0,5,4));
}
'''.replace('HELPER',helper)
with tempfile.TemporaryDirectory() as raw:
    root=pathlib.Path(raw);(root/'test.cpp').write_text(code)
    subprocess.run(['c++','-std=c++17',str(root/'test.cpp'),'-o',str(root/'test')],check=True)
    subprocess.run([str(root/'test')],check=True)
print('multiplayer supply-pod production guards passed; native two-peer effect tested separately')
