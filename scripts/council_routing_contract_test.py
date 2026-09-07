#!/usr/bin/env python3
"""Compile the production interaction classifier; native ballot effects are separate."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
f=s[s.index('std::string interaction_kind(int faction_id) {'):s.index('const char* semantic_victory_type_name')]
code=r'''
#include <string>
#include <vector>
#include <cassert>
int modal=1,popup=0,multi=0,rules=0,halted=0,current=1;
int *WinModalState=&modal,*PopupDialogState=&popup,*MultiplayerActive=&multi,*GameRules=&rules,*GameHalted=&halted,*CurrentFaction=&current;
int RULES_BLIND_RESEARCH=1,deferred_end_turn_faction_id=-1;
bool pending_endgame_presentation_advance=false;
std::string endgame_presentation_phase;
std::vector<int> pending_multiplayer_technology_presentations;
struct F {int tech_research_id=1;} Factions[8];
bool transition=false,pending=false; int proposal=0; const char* label="";
void refresh_deferred_end_turn_state(){} void update_human_diplomacy_lifecycle(){}
bool popup_transition_is_pending(){return transition;} bool deferred_native_action_pending(){return pending;}
bool human_diplomacy_window_active(){return false;} bool first_base_name_modal(int){return false;}
bool technology_presentation_active(){return false;} int project_information_id(){return -1;}
const char* semantic_popup_label(){return label;} bool active_default_popup(){return false;}
int active_council_window_proposal(){return proposal;} bool human_diplomacy_settling(){return false;}
int BaseWin=0; bool Win_is_visible(int){return false;} int owned_base_management_window(int){return -1;}
'''+f+r'''
int main(){
 assert(interaction_kind(1)=="council_vote");
 proposal=-1;assert(interaction_kind(1)=="unsupported_modal");proposal=0;
 multi=1;assert(interaction_kind(1)=="unsupported_modal");multi=0;
 label="CALLSCOUNCIL";assert(interaction_kind(1)=="popup");label="";
 pending=true;assert(interaction_kind(1)=="waiting_for_engine");pending=false;
 transition=true;assert(interaction_kind(1)=="waiting_for_engine");transition=false;
 modal=0;assert(interaction_kind(1)=="turn");
}
'''
with tempfile.TemporaryDirectory() as t:
 p=Path(t);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS: production council routing; unknown modal, multiplayer, popup and pending-action guards retained. Native effects untested here.')
