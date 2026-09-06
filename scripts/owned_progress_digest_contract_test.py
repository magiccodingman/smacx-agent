#!/usr/bin/env python3
"""Compile the production digest against controlled owned/foreign native rows."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[1]
source = (root / 'bridge/src/agent_bridge.cpp').read_text()
start = source.index('std::string semantic_owned_progress_digest()')
end = source.index('\nstd::string semantic_snapshot_response()', start)
prefix = r'''
#include <cstdint>
#include <string>
#include <algorithm>
#include <cassert>
struct BASE {
 int faction_id=1,x=1,y=2,pop_size=2,queue_size=0,queue_items[10]={1};
 int minerals_accumulated=14,worked_tiles=3,specialist_total=0;
 int specialist_types[2]={0},governor_flags=0;
 unsigned char facilities_built[16]={0};
};
struct VEH {
 int faction_id=1,handle=1,unit_id=1,x=3,y=4,hp=10,moves_spent=0;
 int order=0,order_auto_type=0,waypoint_count=0,waypoint_x[1]={-1},waypoint_y[1]={-1};
 int cur_hitpoints() { return hp; }
};
BASE Bases[3]; VEH Vehs[3];
int faction=1,bc=3,vc=3;
int *CurrentPlayerFaction=&faction,*BaseCount=&bc,*VehCount=&vc;
int semantic_vehicle_handle(int i) { return Vehs[i].handle; }
'''
suffix = r'''
int main() {
 Bases[1].x=9; Bases[2].faction_id=2;
 Vehs[1].handle=2; Vehs[2].faction_id=2;
 const auto baseline=semantic_owned_progress_digest();
 assert(baseline==semantic_owned_progress_digest());
 Bases[2].queue_items[0]=99; Vehs[2].hp=1;
 assert(baseline==semantic_owned_progress_digest());
 std::swap(Bases[0],Bases[1]); std::swap(Vehs[0],Vehs[1]);
 assert(baseline==semantic_owned_progress_digest());
 Bases[0].queue_items[0]=64;
 assert(baseline!=semantic_owned_progress_digest());
 Bases[0].queue_items[0]=1;
 Bases[0].worked_tiles=7;
 assert(baseline!=semantic_owned_progress_digest());
 Bases[0].worked_tiles=3;
 Vehs[0].moves_spent=3;
 assert(baseline!=semantic_owned_progress_digest());
 Vehs[0].moves_spent=0;
 Vehs[0].x=7;
 assert(baseline!=semantic_owned_progress_digest());
 Vehs[0].x=3;
 assert(baseline==semantic_owned_progress_digest());
}
'''
with tempfile.TemporaryDirectory(prefix='smacx-owned-progress-') as raw:
    path = Path(raw)
    (path/'test.cpp').write_text(prefix+source[start:end]+suffix)
    subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',str(path/'test.cpp'),'-o',str(path/'test')],check=True)
    subprocess.run([str(path/'test')],check=True)
print('PASS: production C++ digest detects owned effects, ignores foreign changes and slot order')
