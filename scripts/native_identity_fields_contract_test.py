"""Compile native hash and private diagnostic flattening against controlled VEH rows."""
from pathlib import Path
import re, subprocess, tempfile
s=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
functions='\n'.join(re.search(pattern,s,re.S).group() for pattern in (
 r'uint64_t semantic_vehicle_layout_hash\(\) \{.*?\n}',
 r'std::vector<int> semantic_vehicle_layout_fields\(\) \{.*?\n}'))
program='''#include <vector>
#include <cstdint>
#include <cassert>
int turn=9, faction=2, count=2;
int *CurrentTurn=&turn,*CurrentPlayerFaction=&faction,*VehCount=&count;
struct VEH {int faction_id,unit_id,x,y,home_base_id,order,moves_spent,hp; int cur_hitpoints(){return hp;}};
VEH Vehs[2]={{1,2,3,4,-1,5,6,7},{2,3,4,5,0,6,7,8}};
'''+functions+'''
int main(){
 auto fields=semantic_vehicle_layout_fields();
 assert(fields.size()==19);
 uint64_t h=1469598103934665603ULL;
 for(int value:fields){h^=uint32_t(value);h*=1099511628211ULL;}
 assert(h==semantic_vehicle_layout_hash());
 auto old=h; Vehs[1].moves_spent++;
 assert(semantic_vehicle_layout_hash()!=old);
 assert(semantic_vehicle_layout_fields()[17]==8);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d)/'test.cpp';p.write_text(program)
 subprocess.run(['g++','-std=c++17',str(p),'-o',str(Path(d)/'test')],check=True)
 subprocess.run([str(Path(d)/'test')],check=True)
print('PASS: native private fields exactly reproduce strict vehicle hash')
