#!/usr/bin/env python3
"""Compile the actual disband dispatch block: objectives must never be deleted."""
from pathlib import Path
import subprocess,tempfile
from native_event_time_contract_test import function
source=Path('bridge/src/agent_bridge.cpp').read_text()
block=function(source,'    if (command == "disband_unit") {')
program=r'''
#include <cassert>
#include <string>
constexpr int VFLAG_IS_OBJECTIVE=0x20;
struct Unit {int flags=0,unit_id=1; const char* name(){return "Scout";}} veh;
int deleted=0,confirmation=1;
int field_int(std::string,const char*,int){return confirmation;}
void veh_kill(int){++deleted;}
std::string error_response(const char* code,const char*){return code;}
std::string json_string(const char* s){return s;}
std::string dispatch(){std::string command="disband_unit",request;int veh_id=0;
'''+block+r'''
return "unexpected";}
int main(){
veh.flags=VFLAG_IS_OBJECTIVE;assert(dispatch()=="objective_unit_disband_forbidden");assert(deleted==0);
veh.flags=0;confirmation=0;assert(dispatch()=="disband_confirmation_required");assert(deleted==0);
confirmation=1;assert(dispatch().find("deleted_unit_id")!=std::string::npos);assert(deleted==1);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(program)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('Objective disband protection passes actual dispatch block; native recycling remains separate acceptance.')
