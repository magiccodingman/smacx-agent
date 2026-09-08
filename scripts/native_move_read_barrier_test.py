#!/usr/bin/env python3
"""Explicit disposable-worker test: adjacent ready unit plus immediate base read.

Use a restored fixture with an owned ready unit and a legal adjacent destination.
The old bridge faults in mod_base_support when list_bases enters movement's
nested message pump. No fixture mutation or gameplay bypass is used here.
"""
import argparse
import json
import time
from pathlib import Path
from smacx_controller import bridge_request_to

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--port', type=int, required=True)
p.add_argument('--token-file', required=True)
p.add_argument('--unit-id', type=int, required=True)
p.add_argument('--target-tile-id', type=int, required=True)
a = p.parse_args()
token = Path(a.token_file).read_text().strip()

def request(op, **arguments):
    return bridge_request_to('127.0.0.1', a.port, token, op, timeout=8, **arguments)

snapshot = request('semantic_snapshot')['snapshot']
queued = request('semantic_command', command='move_unit', unit_id=a.unit_id,
                 target_tile_id=a.target_tile_id,
                 expected_revision=snapshot['revision'],
                 match_id=snapshot['match_id'], session_id=snapshot['session_id'])
assert queued.get('ok') and queued.get('queued'), queued
started = time.monotonic()
bases = request('list_bases')
assert bases.get('ok'), bases
assert bases.get('items'), bases
elapsed = time.monotonic() - started
receipt = request('action_status', action_id=queued['action_id'])
assert receipt.get('ok'), receipt
action = receipt['action']
assert action['status'] == 'completed', action
assert action['observed_tile_id'] == a.target_tile_id, action
assert request('ping').get('ok')
print(json.dumps({'pass': True, 'base_read_seconds': round(elapsed, 3),
                  'action': action, 'base_count': len(bases['items'])}))
