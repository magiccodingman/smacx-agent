#!/usr/bin/env python3
"""Exercise the production end-turn callback against native outcome contracts."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / 'bridge/src/agent_bridge.cpp').read_text()
implementation = source[source.index('void refresh_deferred_end_turn_state() {'):
                        source.index('bool end_turn_completion_pending() {')]
code = r'''
#include <cassert>
#include <string>
#define CALLBACK
using HWND = void*; using UINT = unsigned; using UINT_PTR = unsigned long; using DWORD = unsigned long;
int turn=69, player=1, faction=1, state=0;
int *CurrentTurn=&turn, *CurrentPlayerFaction=&player, *GameState=&state;
const int STATE_UNK_2=2, NativeEndTurnCommand=0x2000D;
struct Console { int field_23BE8=1; } console;
Console* MapWin=&console;
bool active=true, modal=false, pending_end_turn_completion=false, deferred_end_turn_native_returned=false;
int pending_end_turn_source_turn=-1, deferred_end_turn_faction_id=1, deferred_end_turn_source_turn=69;
UINT_PTR deferred_end_turn_timer_id=4;
struct Action { std::string status="pending", resolution; int native_result=0, native_call_attempted=-1; } deferred_action;
bool game_active() { return active; }
bool human_turn_actionable(int f) { return active && faction==f && !modal; }
void KillTimer(HWND, UINT_PTR) {}
void refresh_deferred_end_turn_state();
int outcome=0, calls=0;
void Console_on_key_click(void*, int, int command) {
 assert(command==NativeEndTurnCommand); ++calls;
 // Nested observation must not classify a still-running native call as refusal.
 refresh_deferred_end_turn_state(); assert(deferred_action.status=="pending");
 if(outcome==1) { state|=STATE_UNK_2; console.field_23BE8=0; }
 if(outcome==2) ++turn;
 if(outcome==3) { modal=true; pending_end_turn_completion=true; }
 if(outcome==4) faction=2;
}
''' + implementation + r'''
void reset(int result) {
 turn=69; console.field_23BE8=1; faction=player=1; state=0; active=true; modal=false; calls=0;
 pending_end_turn_completion=false; pending_end_turn_source_turn=-1; deferred_end_turn_native_returned=false;
 deferred_end_turn_faction_id=1; deferred_end_turn_source_turn=69;
 deferred_end_turn_timer_id=4; deferred_action=Action{}; outcome=result;
}
int main() {
 reset(0); deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(calls==1 && deferred_end_turn_timer_id==0 && deferred_action.native_call_attempted==1 && deferred_end_turn_native_returned);
 assert(deferred_action.status=="rejected" && deferred_action.resolution=="native_turn_transition_not_accepted");
 assert(deferred_end_turn_faction_id==-1 && !pending_end_turn_completion);
 reset(0); state=STATE_UNK_2; deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(deferred_action.status=="rejected"); // completed-unit flag is not acceptance
 reset(1); deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(deferred_action.status=="pending" && deferred_end_turn_faction_id==1);
 ++turn; refresh_deferred_end_turn_state(); assert(deferred_action.status=="completed");
 reset(2); deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(deferred_action.status=="completed" && deferred_action.native_result==1);
 reset(3); deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(deferred_action.status=="pending" && pending_end_turn_completion);
 reset(4); deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(deferred_action.status=="pending");
 reset(0); modal=true; deferred_end_turn_timer_proc(nullptr,0,4,0);
 assert(calls==0 && deferred_action.status=="rejected");
}
'''
with tempfile.TemporaryDirectory() as tmp:
    path=Path(tmp); (path/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17',str(path/'test.cpp'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
print('PASS: production callback refusal, acceptance, advancement, nested poll, modal and faction transition contracts; controlled native replay remains required')
