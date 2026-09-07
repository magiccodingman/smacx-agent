#!/usr/bin/env python3
"""Run the production SOCIETY handler with guarded native adapters, not game proof."""
from pathlib import Path
import subprocess
import tempfile

source = (Path(__file__).resolve().parents[1] / 'bridge/src/agent_bridge.cpp').read_text()
handler = source.split('    if (command == "defer_social_engineering") {', 1)[1].split(
    '    if (command == "acknowledge_popup") {', 1)[0]
code = r'''
#include <cassert>
#include <cstring>
#include <string>
struct BasePop {} popup;
bool active=true, present=true, accepted=true; int count=2, calls=0;
std::string label="SOCIETY";
BasePop* active_default_popup() { return active ? &popup : nullptr; }
const char* semantic_popup_label() { return label.c_str(); }
int popup_choice_count(BasePop*) { return count; }
bool popup_has_choice_id(BasePop*, int id) { assert(id==0); return present; }
bool submit_popup_choice_id(BasePop* p, int id) { assert(p==&popup && id==0); ++calls; return accepted; }
std::string error_response(const char* code, const char*) { return code; }
std::string execute() {
''' + handler + r'''
int main() {
 assert(execute().find("\"policy_changed\":false") != std::string::npos && calls==1);
 calls=0; label="WEDEVELOP"; assert(execute()=="society_prompt_changed" && !calls);
 label="SOCIETY"; active=false; assert(execute()=="society_prompt_changed" && !calls);
 active=true; count=1; assert(execute()=="society_prompt_changed" && !calls);
 count=2; present=false; assert(execute()=="society_prompt_changed" && !calls);
 present=true; accepted=false; assert(execute()=="society_prompt_changed" && calls==1);
}
'''
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp); (p/'test.cpp').write_text(code)
    subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: SOCIETY exact-label, live-object, two-choice, row-identity and refusal guards; native replay required')
