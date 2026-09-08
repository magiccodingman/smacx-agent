#!/usr/bin/env python3
"""Production serializer and fog adapter contracts; not live native proof."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

from native_event_time_contract_test import function
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'bridge/src/agent_bridge.cpp').read_text()
    helper = function(source, 'item_names(')
    enums = (root / 'bridge/src/engine_enums.h').read_text()
    constants = '\n'.join('const uint32_t '+name+' = '+re.search(
        rf'\b{name}\s*=\s*(0x[0-9A-Fa-f]+|\d+)', enums)[1]+';'
        for name in sorted(set(re.findall(r'\bBIT_\w+', helper))))
    program = '''#include <cstdint>
#include <string>
#include <sstream>
#include <cassert>
std::string json_string(const char* s){return std::string("\\\"")+s+"\\\"";}
''' + constants + '\n' + helper + '''
int main(){
 assert(item_names(BIT_SUPPLY_POD,0)=="[]");
 assert(item_names(0,1)=="[\\\"supply_pod\\\"]");
 assert(item_names(0,2)=="[\\\"supply_pod\\\"]");
 assert(item_names(BIT_SUPPLY_POD|BIT_SUPPLY_REMOVE)=="[]");
 assert(item_names(BIT_SUPPLY_POD|BIT_MONOLITH)=="[\\\"monolith\\\"]");
 assert(item_names(BIT_SUPPLY_POD)=="[\\\"supply_pod\\\"]");
 assert(item_names(BIT_ROAD,0)=="[\\\"road\\\"]");
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        cpp = Path(tmp)/'test.cpp'; cpp.write_text(program)
        subprocess.run(['g++','-std=c++11',str(cpp),'-o',tmp+'/test'],check=True)
        subprocess.run([tmp+'/test'],check=True)
    identity = WorldIdentity('match-pods','perspective-pods','timeline-pods','world-pods')
    prior = None
    for turn, visible, raw, expected, verified in [
        (1,True,['supply_pod'],['supply_pod'],1),
        (2,False,[],['supply_pod'],1),
        (3,False,None,['supply_pod'],1),
        (4,True,[],[],4),
        (5,False,['supply_pod'],[],4),
        (6,True,['supply_pod'],['supply_pod'],6),
    ]:
        tile = {'tile_id':0,'x':0,'y':0,'terrain':'land','visible_now':visible}
        if raw is not None: tile['features'] = raw
        bundle = {'turn':turn,'map':{'width':4,'height':2},'tiles':[tile]}
        projected = PerspectiveProjector(identity,prior_projection=prior).project(
            bundle,observation_sequence=turn)
        rows = [row.as_dict(provider_safe=False) for row in projected['objects']]
        feature = next(row for row in rows if row['object_ref']=='location-0')['fields']['features']
        assert feature['value'] == expected, feature
        assert feature['epistemic_status'] == ('current' if visible else 'stale'), feature
        assert feature['last_verified_turn'] == verified, feature
        prior = {**projected,'objects':rows,'world_revision':turn}
    print(json.dumps({'serializer_cases':7,'projection_transitions':6,
                      'live_native_comparison':'not covered'}))


if __name__ == '__main__':
    main()
