#!/usr/bin/env python3
"""Compile production base-screen ownership and command guards; native effects tested separately."""
from pathlib import Path
import subprocess
import tempfile
source = (Path(__file__).resolve().parents[1] / 'bridge/src/agent_bridge.cpp').read_text()
helper = source[source.index('int owned_base_management_window(int faction_id) {'):source.index('std::string interaction_kind(int faction_id) {')]
start = source.index('    if (command == "close_base_management") {')
command = source[start:source.index('    if (command == "acknowledge_popup") {', start)]
code = r'''
#include <cassert>
#include <string>
struct Render { int base_id=0; };
struct Window { Render oRender; } window;
Window* BaseWin=&window;
struct Base { int faction_id=1; } Bases[2];
int count=2; int* BaseCount=&count;
bool visible=true;
int calls=0, selected=0;
std::string phase="base_management_screen";
bool Win_is_visible(Window*) { return visible; }
int field_int(const std::string&, const char*, int) { return selected; }
std::string error_response(const char* code, const char*) { return std::string("error:")+code; }
std::string interaction_kind(int) { return phase; }
void BaseWin_on_button_clicked(Window* w, int button) {
 assert(w==BaseWin && button==0); ++calls; visible=false;
}
''' + helper + r'''
std::string execute() {
 const std::string command="close_base_management", request="";
 const int faction_id=1;
''' + command + r'''
 return "unhandled";
}
int main() {
 for(int id : {-1,2,9999}) { window.oRender.base_id=id; assert(owned_base_management_window(1)==-1); }
 window.oRender.base_id=0; Bases[0].faction_id=2;
 assert(owned_base_management_window(1)==-1); assert(execute().find("error:")==0 && calls==0);
 Bases[0].faction_id=1; visible=false;
 assert(owned_base_management_window(1)==-1); assert(execute().find("error:")==0 && calls==0);
 visible=true; selected=1;
 assert(execute().find("error:")==0 && calls==0);
 selected=0; phase="waiting_for_engine";
 assert(execute().find("error:")==0 && calls==0);
 phase="base_management_screen";
 auto receipt=execute(); assert(calls==1 && !visible);
 assert(receipt.find("\"base_screen_closed\":true")!=std::string::npos);
 assert(receipt.find("\"turn_completion_verified\":false")!=std::string::npos);
 assert(execute().find("error:")==0 && calls==1);
}
'''
with tempfile.TemporaryDirectory(prefix='smacx-base-screen-') as tmp:
    path=Path(tmp); (path/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17',str(path/'test.cpp'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
print('PASS: production base-screen ownership/range/phase/selector/replay guards and qualified receipt; native closure is separate evidence')
