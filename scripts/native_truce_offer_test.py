#!/usr/bin/env python3
"""Compile production truce label/row guards; native treaty effects need live replay."""
from pathlib import Path
import subprocess,tempfile,json
source=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
labels=source[source.index('bool unconditional_truce_offer_label('):source.index('bool relationship_offer_label(')]
helpers=source[source.index('bool reviewed_unconditional_truce_popup('):source.index('VOID CALLBACK energy_gift_timer_proc(')]
code='''#include <string>
#include <cassert>
struct BasePop {} popup;
int count=2,calls=0,selected=-1;bool zero=true,one=true,accepted=true;
int popup_choice_count(BasePop*) {return count;}
bool popup_has_choice_id(BasePop*,int id){return id==0?zero:id==1?one:false;}
bool submit_popup_choice_id(BasePop*,int id){++calls;selected=id;return accepted;}
'''+labels+helpers+'''
int main(){
 for(const char* label:{"WANTTOTRUCE0","WANTTOTRUCE1","WANTTOTRUCE2","OFFERTRUCE","MUSTTRUCE"}){
  assert(submit_unconditional_truce_response(&popup,label,"accept") && selected==0);
  assert(submit_unconditional_truce_response(&popup,label,"reject") && selected==1);
 }
 calls=0;
 for(const char* label:{"WANTTOTRUCE3","WANTTOTRUCE00","TECHTRUCE","ENERGYTRUCE","TRUCEPLEASE","MAKETRUCE"})
  assert(!submit_unconditional_truce_response(&popup,label,"accept"));
 assert(!submit_unconditional_truce_response(nullptr,"WANTTOTRUCE0","accept"));
 assert(!submit_unconditional_truce_response(&popup,"WANTTOTRUCE0","counter"));
 count=3;assert(!submit_unconditional_truce_response(&popup,"WANTTOTRUCE0","accept"));
 count=2;zero=false;assert(!submit_unconditional_truce_response(&popup,"WANTTOTRUCE0","accept"));
 zero=true;one=false;assert(!submit_unconditional_truce_response(&popup,"WANTTOTRUCE0","reject"));
 assert(calls==0);
 one=true;accepted=false;assert(!submit_unconditional_truce_response(&popup,"WANTTOTRUCE0","accept"));
 accepted=true;
 for(const char* label:{"ENERGYTRUCE","ENERGYTREATY"}){
  assert(submit_energy_peace_response(&popup,label,"accept",25,25) && selected==0);
  assert(submit_energy_peace_response(&popup,label,"reject",25,25) && selected==1);
  assert(!submit_energy_peace_response(&popup,label,"accept",25,24));
  assert(!submit_energy_peace_response(&popup,label,"accept",0,0));
  assert(!submit_energy_peace_response(&popup,label,"accept",-1,-1));
  assert(!submit_energy_peace_response(&popup,label,"counter",25,25));
  count=3;assert(!submit_energy_peace_response(&popup,label,"accept",25,25));count=2;
  zero=false;assert(!submit_energy_peace_response(&popup,label,"accept",25,25));zero=true;
  accepted=false;assert(!submit_energy_peace_response(&popup,label,"accept",25,25));accepted=true;
 }
 assert(!submit_energy_peace_response(nullptr,"ENERGYTRUCE","accept",25,25));
 assert(!submit_energy_peace_response(&popup,"TECHTRUCE","accept",25,25));
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
assert '&& !unconditional_truce_offer_label(active_label)' in source
assert '&& !energy_peace_offer_label(active_label)' in source
assert '&& (*MultiplayerActive || !reviewed_unconditional_truce_popup(active_default_popup(), label))' in source
print(json.dumps({'passed':True,'evidence':'compiled production helpers with controlled popup adapter',
 'five_exact_labels':True,'accept_row0_reject_row1':True,'changed_rows_and_conditional_offers_rejected':True,
 'multiplayer_permissions_not_broadened':True,'quoted_energy_peace_guards':True,'native_relationship_effect':'requires live replay'}))
