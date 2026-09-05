#!/usr/bin/env python3
"""Preserve every repeated large-collector gate result, including tail failures."""
import json
from observation_collector_benchmark import run_case


def main():
    rows=[]
    for index in range(3):
        row=run_case('large_custom_quiet_repeat_'+str(index+1),320,160)
        rows.append({'run':index+1,'initial_ms':row['initial']['wall_ms'],'unchanged_ms':row['unchanged']['wall_ms'],
            'probe_ms':row['ui_probe_max_gap_ms'],'known_tiles':row['known_tiles']})
        print(json.dumps({'measurement':rows[-1]}),flush=True)
    passed=all(r['initial_ms']<30000 and r['probe_ms']<500 for r in rows)
    print(json.dumps({'passed':passed,'runs':rows,'collector_gate_ms':30000,'probe_gate_ms':500,
        'evidence':'native_shaped_production_collector_independent_probe'}),flush=True)
    assert passed,rows
if __name__=='__main__':main()
