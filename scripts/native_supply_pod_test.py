#!/usr/bin/env python3
"""Run only in a disposable worker with both supply-pod fixture gates enabled."""
import json
import time
from smacx_controller import bridge_request, BridgeUnavailable

for attempt in range(120):
    try:
        state = bridge_request('status')
        if state.get('state', {}).get('in_game'):
            break
    except BridgeUnavailable:
        pass
    time.sleep(0.5)
else:
    raise AssertionError('Disposable autostart worker did not enter game')
result = bridge_request('test_supply_pod_fixture')
assert result.get('ok') and result.get('restored'), result
assert len(result['cases']) == 5, result
for case in result['cases']:
    assert ('supply_pod' in case['features']) == bool(case['native_available']), case
assert result['cases'][2]['native_available'] == 0, result
assert result['cases'][3]['native_available'] == 0, result
print(json.dumps({'event':'pass','native_comparison':result}))
