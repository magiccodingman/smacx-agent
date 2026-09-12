#!/usr/bin/env python3
"""Compile production demand gating and payment checks; native effects tested separately."""
import pathlib,subprocess,tempfile,json
s=(pathlib.Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
gate=s.split('    bool validated_multiplayer_energy_demand_response =',1)[1].split(';',1)[0]
check=s.split('        if ((bribe_demand || bribe_ultimatum) && response != "reject") {',1)[1].split('\n        if ((joint_attack',1)[0]
notice=s.split('    bool refused_demand_notice =',1)[1].split(';',1)[0]
code=r'''
#include <string>
#include <cassert>
std::string reply; bool popup=true,human=false;int treasury=100,full=50,half=20;
const int MaxPlayerNum=8;
struct Faction {int energy_credits;};Faction Factions[8];
std::string field_string(const std::string&,const char*){return reply;}
bool is_human(int){return human;}
bool active_default_popup(){return popup;}
bool bribe_demand_label(const std::string& s){return s.compare(0,11,"DEMANDBRIBE")==0;}
int agent_popup_parse_number(int i){return i?half:full;}
std::string error_response(const char* code,const char*){return code;}
bool notice(std::string label){return NOTICE;}
bool allowed(std::string active_label,int multiplayer_contact_other=3,std::string command="respond_to_diplomatic_offer"){
 const int faction_id=1;std::string request;
 return GATE;
}
std::string payment(bool bribe_demand=true,bool bribe_ultimatum=false){
 int faction_id=1;std::string response=reply;bool counter=reply=="counter";
 Factions[1].energy_credits=treasury;
 if ((bribe_demand || bribe_ultimatum) && response != "reject") { CHECK
 return "ok";
}
int main(){
 for(auto label:{"BULLY0","BULLY5","BULLY6"}) assert(notice(label));
 for(auto label:{"BULLY","BULLY7","BULLY50","DEMANDBRIBE1"}) assert(!notice(label));
 for(auto r:{"accept","reject","counter"}){reply=r;assert(allowed("DEMANDBRIBE1"));assert(allowed("DEMANDBRIBE0"));}
 reply="counter";assert(!allowed("WEASELOUT"));
 for(auto r:{"accept","reject"}){reply=r;assert(allowed("WEASELOUT"));}
 reply="accept";assert(!allowed("DIPLO"));assert(!allowed("ENERGYLOAN"));assert(!allowed("DEMANDBRIBE1",1));assert(!allowed("DEMANDBRIBE1",-1));assert(!allowed("DEMANDBRIBE1",8));
 human=true;assert(!allowed("DEMANDBRIBE1"));human=false;popup=false;assert(!allowed("DEMANDBRIBE1"));popup=true;
 reply="invalid";assert(!allowed("DEMANDBRIBE1"));
 reply="accept";treasury=50;assert(payment()=="ok");treasury=49;assert(payment()=="energy_demand_not_affordable");
 reply="counter";treasury=20;assert(payment()=="ok");treasury=19;assert(payment()=="energy_demand_not_affordable");
 half=-1;assert(payment()=="energy_demand_not_affordable");reply="reject";assert(payment()=="ok");
 reply="accept";full=-1;assert(payment()=="energy_demand_not_affordable");
 full=50;treasury=50;assert(payment(false,true)=="ok");treasury=49;assert(payment(false,true)=="energy_demand_not_affordable");
 reply="reject";human=true;assert(!allowed("WEASELOUT"));human=false;
 popup=false;assert(!allowed("WEASELOUT"));popup=true;
 assert(!allowed("WEASELOUT",1));assert(!allowed("WEASELOUT",-1));assert(!allowed("WEASELOUT",8));
}
'''.replace('NOTICE',notice).replace('GATE',gate).replace('CHECK',check)
with tempfile.TemporaryDirectory() as t:
 p=pathlib.Path(t);(p/'test.cpp').write_text(code)
 subprocess.run(['c++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print(json.dumps({'passed':True,'evidence':'compiled production gate and affordability checks; not native synchronization proof'}))
