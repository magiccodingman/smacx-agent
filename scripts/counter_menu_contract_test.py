#!/usr/bin/env python3
"""Compile production diplomacy option mapping and menu rendering."""
from pathlib import Path
import subprocess,tempfile
source=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
production=source[source.index('struct NamedDiplomacyOption {'):source.index('struct NamedCouncilProposal {')]
production+=source[source.index('const NamedDiplomacyOption DiplomacyMenuOptions[]'):source.index('void append_trade_technology_context')]
code=r'''
#include <string>
#include <sstream>
#include <vector>
#include <set>
#include <cassert>
#include <algorithm>
using std::max;
struct BasePop {std::set<int> ids;};
bool popup_has_choice_id(BasePop* p,int id){return p->ids.count(id);}
std::string json_string(const std::string& s){return "\""+s+"\"";}
int player=1,*CurrentPlayerFaction=&player;
struct F {int energy_credits=500;} Factions[8];
int DiploCounterEnergyPayment=5;
std::vector<int> native_energy_gift_options(int){return {500,250,125,50,25};}
'''+production+r'''
int main(){
 BasePop p{{0,1,2,3,4,5,6,7,8}};
 assert(diplomacy_option("COUNTER0","all_research_data")->id==7);
 assert(diplomacy_option("COUNTER1","all_research_data")->id==7);
 assert(diplomacy_option("COUNTER0","threaten")->id==3);
 assert(!diplomacy_option("COUNTER0","cancel_pact"));
 assert(!diplomacy_option("COUNTER2","energy_payment"));
 std::ostringstream counter,gift;
 append_diplomacy_popup_choices(counter,&p,"COUNTER0");
 append_diplomacy_popup_choices(gift,&p,"COUNTER1");
 assert(counter.str().find("give_energy_gift")==std::string::npos);
 assert(counter.str().find("this selection alone transfers no energy")!=std::string::npos);
 assert(gift.str().find("give_energy_gift")!=std::string::npos);
 p.ids={0,2};std::ostringstream limited;
 append_diplomacy_popup_choices(limited,&p,"COUNTER0");
 assert(limited.str().find("energy_payment")==std::string::npos);
 assert(limited.str().find("name_price")!=std::string::npos);
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
print('PASS: production counter mapping, native-row filtering, unknown-label rejection and counter/gift energy separation; native return/effects require isolated test.')
