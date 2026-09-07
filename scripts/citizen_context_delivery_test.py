#!/usr/bin/env python3
"""Managed citizen evidence explains the existing guarded two-step reassignment."""
import copy
import json
from unittest.mock import patch
import smacx_mcp as mcp
from smacx_world_model import estimate_tokens
from smacx_diagnostic_summary import summary

state = {'revision': 'r1', 'specialist': False, 'worked': 1}
commands, journaled = [], []
identity = {'match_id': 'match-citizen-context', 'session_id': 'session-citizen'}
context = {'reverse_bases': {7: 'base-public'},
           'reverse_locations': {101: 'location-101', 102: 'location-102'}}

def catalog():
    return {**identity, 'revision': state['revision'], 'ok': True, 'kind': 'base_citizens',
        'base_id': 7, 'population': 1, 'governor_manages_citizens': False,
        'specialists': [{'specialist_index': 0, 'citizen_id': 0, 'name': 'Doctor'}] if state['specialist'] else [],
        'available_specialist_types': [{'citizen_id': 0, 'name': 'Doctor', 'economy': 0, 'psych': 2, 'labs': 0}],
        'tiles': [{'tile_index': i, 'tile_id': 100+i, 'worked': state['worked'] == i,
                   'assignable': True, 'yields': {'nutrients': i-1, 'minerals': 3 if i == 1 else 1, 'energy': 0},
                   'private_extra': 'must-not-leak'} for i in (1, 2)],
        'choices': ([{'command': 'assign_specialist_to_tile', 'base_id': 7, 'tile_index': 2,
                      'tile_id': 102, 'specialist_index': 0}] if state['specialist'] else
                    [{'command': 'convert_worker_to_specialist', 'base_id': 7, 'tile_index': 1,
                      'citizen_id': 0, 'specialist_name': 'Doctor'}])}

def native(operation, **arguments):
    if operation == 'semantic_snapshot':
        return {'ok': True, 'snapshot': {**identity, 'revision': state['revision'], 'turn': 33,
                                        'year': 2133, 'protocol': {'phase': 'turn'}}}
    if operation == 'semantic_choices':
        return catalog()
    assert operation == 'semantic_command', operation
    assert arguments['expected_revision'] == state['revision'], arguments
    commands.append(dict(arguments))
    if arguments['command'] == 'convert_worker_to_specialist':
        assert arguments['tile_index'] == 1 and not state['specialist']
        state.update(revision='r2', specialist=True, worked=None)
    else:
        assert arguments['command'] == 'assign_specialist_to_tile'
        assert arguments['specialist_index'] == 0 and arguments['tile_index'] == 2
        assert state['specialist']
        state.update(revision='r3', specialist=False, worked=2)
    return {'ok': True, 'command': arguments['command'], 'base_id': 7, 'revision': state['revision']}

with patch.object(mcp, '_call', side_effect=native), \
     patch.object(mcp, '_resolve_managed_selectors', return_value=({'base_id': 7}, context)), \
     patch.object(mcp, '_pending_capability_gap', return_value=None), \
     patch.object(mcp, '_match_briefing_gate', return_value=None), \
     patch.object(mcp, 'controller_record_campaign_action', side_effect=lambda *a, **kw: journaled.append((a, kw)) or {'ok': True}):
    first = mcp.smac_choices(kind='base_citizens', base_ref='base-public')
    assert first['ok'], first
    evidence = first['citizen_context']
    assert evidence['tiles'][0]['yields']['nutrients'] == 0
    assert evidence['tiles'][1]['yields']['nutrients'] == 1
    assert evidence['available_specialist_types'][0]['psych'] == 2
    assert evidence['tile_reassignment']['next_query']['arguments']['base_ref'] == 'base-public'
    assert 'conditional' in evidence['tile_reassignment']['guard']
    choice = first['choices'][0]
    assert choice['allocation_location_ref'] == 'location-101'
    assert 'reassign' in choice['meaning']
    for key in ('base_id', 'tile_id', 'tile_index', 'specialist_index', 'citizen_id', 'private_extra'):
        assert '"'+key+'"' not in json.dumps(first), key
    converted = mcp.smac_execute_choice(first['decision_id'], choice['choice_id'])
    assert converted['ok'], converted
    second = mcp.smac_choices(kind='base_citizens', base_ref='base-public')
    assert second['citizen_context']['specialists'][0]['name'] == 'Doctor'
    assigned = mcp.smac_execute_choice(second['decision_id'], second['choices'][0]['choice_id'])
    assert assigned['ok'], assigned
    assert len(commands) == 2 and len(journaled) == 2
    assert state['worked'] == 2 and not state['specialist']
    assert mcp.smac_execute_choice(first['decision_id'], choice['choice_id'])['ok'] is False
    assert len(commands) == 2
    raw = catalog(); saved = copy.deepcopy(raw)
    mcp._citizen_catalog_context(raw, context, 'base-public')
    assert raw == saved
    assert mcp._citizen_catalog_context({}, {}, '') == {}
    assert estimate_tokens(first) < 2048
    human = summary({'kind': 'tool_returned', 'payload': {'managed_name': 'smac_choices', 'result': first}})
    assert 'reassignment' in human and 'currently_assignable_tiles' in human
print(json.dumps({'passed': True, 'native_shaped_guarded_two_step': True,
    'both_actions_journaled': True, 'single_use_choices': True,
    'tile_yields_and_conditional_workflow_delivered': True,
    'private_selectors_absent': True, 'missing_facts_not_invented': True,
    'native_live_comparison': False, 'provider_inference': False}))
