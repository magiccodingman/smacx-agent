#!/usr/bin/env python3
"""Compile actual raid hook against controlled ownership inputs."""
from pathlib import Path
import tempfile
import subprocess
from native_event_time_contract_test import function
source=Path('bridge/src/agent_bridge.cpp').read_text()
program='''
#include <cassert>
#include <string>
struct BASE { int faction_id, x, y; } Bases[2]={{1,2,4},{2,3,5}};
int count=2, faction=1, turn=32;
int *BaseCount=&count,*CurrentPlayerFaction=&faction,*CurrentTurn=&turn;
const int MaxPlayerNum=8; bool lock_initialized=true, active=true;
bool game_active(){return active;}
int semantic_tile_id(int x,int y){return x+y*80;}
int calls=0,before_seen=-1,after_seen=-1;std::string effect_seen;
void append_observation_event(const char*,int,int,int,int,int,int before,int after,bool,const char* effect){
 ++calls;before_seen=before;after_seen=after;effect_seen=effect;
}
'''+function(source,'void agent_observe_native_raid_effect(')+'''
int main(){
 agent_observe_native_raid_effect(0,"stored_minerals",9,0);
 assert(calls==1 && before_seen==9 && after_seen==0 && effect_seen=="stored_minerals");
 agent_observe_native_raid_effect(1,"population",3,2); assert(calls==1);
 agent_observe_native_raid_effect(-1,"population",3,2); assert(calls==1);
 agent_observe_native_raid_effect(2,"population",3,2); assert(calls==1);
 faction=0;agent_observe_native_raid_effect(0,"population",3,2); assert(calls==1);
 faction=1;active=false;agent_observe_native_raid_effect(0,"population",3,2);assert(calls==1);
 active=true;lock_initialized=false;agent_observe_native_raid_effect(0,"population",3,2);assert(calls==1);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(program)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('Native raid hook: owned-only capture and effect values pass; controlled inputs')
