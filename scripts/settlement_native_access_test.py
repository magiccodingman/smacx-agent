"""Compile and execute actual public-access helper against controlled native-shaped state."""
from pathlib import Path
import subprocess,tempfile
s=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
a=s.index('std::string settlement_public_access(int faction, int x, int y, int own_base) {')
helper=s[a:s.index('\nstd::string site_economy_receipt',a)]
code=r'''
#include <string>
#include <sstream>
#include <cassert>
const int ORDER_CONVOY=4,DIPLO_TREATY=1,DIPLO_PACT=2;
int bc=2,vc=1;int* BaseCount=&bc;int* VehCount=&vc;
bool visible=true,treaty=false;
struct MAP {int owner=1;bool is_visible(int){return visible;}} tile;
struct BASE {int faction_id=1,x=0,y=0,worked_tiles=0;} Bases[2];
struct VEH {int faction_id=2,x=0,y=0,order=0;bool seen=true;bool is_visible(int){return seen;}} Vehs[1];
MAP* mapsq(int,int){return &tile;}
int map_range(int,int,int,int){return 0;}
MAP* next_tile(int,int,int off,int*x,int*y){*x=off==1?0:off;*y=0;return &tile;}
bool has_treaty(int,int,int){return treaty;}
int whose_territory(int,int,int,int*,int){return tile.owner;}
HELPER
int main(){
 auto has=[](std::string s,std::string k,bool v){return s.find("\""+k+"\":"+(v?"true":"false"))!=std::string::npos;};
 assert(has(settlement_public_access(1,0,0,0),"visible_occupation_constraint",true));
 treaty=true;assert(has(settlement_public_access(1,0,0,0),"visible_occupation_constraint",false));
 Vehs[0].order=ORDER_CONVOY;assert(has(settlement_public_access(1,0,0,0),"visible_occupation_constraint",true));
 Vehs[0].seen=false;assert(has(settlement_public_access(1,0,0,0),"visible_occupation_constraint",false));
 Bases[0].worked_tiles=2;assert(has(settlement_public_access(1,0,0,0),"reserved_by_other_owned_base",false));
 Bases[1].worked_tiles=2;assert(has(settlement_public_access(1,0,0,0),"reserved_by_other_owned_base",true));
 tile.owner=2;assert(has(settlement_public_access(1,0,0,0),"foreign_territory",true));
 assert(has(settlement_public_access(1,0,0,0),"reserved_by_other_owned_base",false));
 visible=false;assert(settlement_public_access(1,0,0,0)=="null");
}
'''.replace('HELPER',helper)
with tempfile.TemporaryDirectory() as raw:
 p=Path(raw);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS: actual native helper; treaty, convoy, hidden unit, own reservation, foreign territory, fog exclusion')
