#!/usr/bin/env python3
"""Compile the production event/POD helpers with controlled diplomacy inputs."""
import json
from pathlib import Path
import subprocess
import tempfile


def function(source,name):
    start=source.index(name)
    start=source.rfind('\n',0,start)+1
    brace=source.index('{',start); depth=1; end=brace+1
    while depth:
        depth+=(source[end]=='{')-(source[end]=='}');end+=1
    return source[start:end]


def main():
    source=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
    assert 'observed_event_relationship(faction_id, current.faction_id)' in source
    assert 'json_string(event.relationship)' in source
    declarations=source[source.index('const size_t MaxObservationEvents'):source.index('std::string last_observed_action_revision')]
    program='''#include <cassert>
#include <cstdint>
#include <cstring>
#include <cstddef>
using std::size_t;
const int DIPLO_VENDETTA=1, DIPLO_PACT=2;
struct Faction {int diplo_status[8];} Factions[8] = {};
void lstrcpynA(char* out,const char* in,int n){std::strncpy(out,in,n);out[n-1]=0;}
'''+declarations+function(source,'observed_event_relationship(')+'\n'+function(source,'append_observation_event(')+'''
int main(){
  Factions[1].diplo_status[2]=DIPLO_VENDETTA;
  append_observation_event("visible_unit_moved",4,9,2,1,2,-1,-1,true,nullptr,observed_event_relationship(1,2));
  Factions[1].diplo_status[2]=DIPLO_PACT;
  assert(!strcmp(observation_events[0].relationship,"hostile"));
  assert(observation_events[0].continuous_visibility && observation_events[0].sequence==1);
  append_observation_event("visible_unit_moved",4,9,2,2,3,-1,-1,true,nullptr,observed_event_relationship(1,2));
  assert(!strcmp(observation_events[1].relationship,"allied"));
  assert(!strcmp(observed_event_relationship(1,1),"self"));
  assert(!strcmp(observed_event_relationship(1,0),"hostile"));
  Factions[1].diplo_status[2]=0;
  assert(!strcmp(observed_event_relationship(1,2),"neutral"));
  for(int i=0;i<1024;i++) append_observation_event("unrelated",4);
  assert(observation_event_count==1024 && lost_after_observation_sequence==2);
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp);(path/'test.cpp').write_text(program)
        subprocess.run(['g++','-std=c++17',str(path/'test.cpp'),'-o',str(path/'test')],check=True)
        subprocess.run([str(path/'test')],check=True)
    print(json.dumps({'passed':True,'evidence':'compiled_production_adapter_with_controlled_native_shaped_inputs',
                      'event_relationship_is_frozen':True,'bounded_ring_preserved':True,'running_game_comparison':False}))


if __name__=='__main__':main()
