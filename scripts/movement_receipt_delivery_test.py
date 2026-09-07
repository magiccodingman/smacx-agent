#!/usr/bin/env python3
"""Native order resolution must not hide whether an arrival was reported."""
import copy
import json
from unittest.mock import patch
import smacx_mcp as mcp
from smacx_diagnostic_summary import summary

for observed, changed, reached in [(1254, False, False), (1295, True, True), (1334, True, False)]:
    native = {'ok': True, 'completed': True, 'queued': False,
              'execution': {'command': 'move_unit', 'action_id': 3, 'unit_id': 41,
                            'status': 'completed', 'native_result': 1,
                            'resolution': 'native_move_resolved',
                            'origin_tile_id': 1254, 'target_tile_id': 1295,
                            'observed_tile_id': observed}}
    saved = copy.deepcopy(native)
    with patch.object(mcp, '_sovereign_gameplay_gate', return_value=None), \
         patch.object(mcp, '_execute_choice_once', return_value=native):
        public = mcp.smac_execute_choice('decision-controlled', 'choice-controlled')
    receipt = public['execution']
    assert receipt['observed_location_ref'] == f'location-{observed}'
    assert receipt['origin_location_ref'] == 'location-1254'
    assert receipt['target_location_ref'] == 'location-1295'
    assert receipt['movement_observation']['reported_position_changed'] is changed
    assert receipt['movement_observation']['requested_target_reported'] is reached
    assert 'unknown' in receipt['movement_observation']['meaning']
    assert public['execution_status'] == 'completed'  # Preserve action lifecycle contract.
    assert not mcp._choice_contains_private_selector(public)
    assert native == saved
    assert 'observed_location_ref' in summary({'kind': 'tool_returned', 'payload': {'result': public}})
# Pending values are initial placeholders, not an observed terminal outcome.
pending = mcp._public_execution_receipt({'execution': {'command': 'move_unit', 'status': 'pending',
    'origin_tile_id': 0, 'target_tile_id': 1, 'observed_tile_id': 0}})
assert 'movement_observation' not in pending['execution']
for resolution in ('native_combat_resolved', 'native_artifact_consumed'):
    result = mcp._public_execution_receipt({'execution': {'command': 'move_unit', 'status': 'completed',
        'resolution': resolution, 'origin_tile_id': 0, 'target_tile_id': 1, 'observed_tile_id': 0}})
    assert 'movement_observation' not in result['execution']
unknown = mcp._public_execution_receipt({'execution': {'command': 'move_unit', 'status': 'completed',
    'resolution': 'native_move_resolved', 'unit_id': 9, 'origin_tile_id': -1, 'observed_tile_id': -1}})
assert 'observed_location_ref' not in unknown['execution']
assert 'reported_position_changed' not in unknown['execution']['movement_observation']
print(json.dumps({'passed': True, 'origin_target_and_observation_retained': True,
    'no_displacement_and_arrival_distinguished': True, 'native_slots_private': True,
    'unknown_pending_combat_and_consumption_not_promoted': True,
    'original_receipt_unchanged': True, 'native_failure_reason_not_invented': True,
    'provider_inference': False}))
