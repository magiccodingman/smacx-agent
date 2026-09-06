#!/usr/bin/env python3
"""Oversized historical rows give an executable, position-preserving retry."""
import copy
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from world_model_contract_test import initialized, bundle
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity
from smacx_diagnostic_summary import summary

with tempfile.TemporaryDirectory() as temporary:
    _, _, scope, world = initialized(Path(temporary))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, 'timeline-main', 'world-test')
    projected = PerspectiveProjector(identity).project(bundle(), observation_sequence=9)
    world.replace_projection(scope, identity, projected['objects'], observation_cursor=9,
                             action_revision='a', continuity='complete', journal_head_hash='0' * 64)
    service = WorldService(world, scope)
    rows = [{'observation_cursor': 9, 'journal_event_id': 'journal-same-observation',
             'turn': 12, 'continuity': 'complete',
             'delta': {'change': 'changed', 'object_ref': f'base-{index}',
                       'current': {'fields': {'evidence': {'value': 'x' * 8500,
                                   'epistemic_status': 'stale', 'last_verified_turn': 11}}}}}
            for index in range(2)]
    frozen = copy.deepcopy(rows)
    with patch.object(world, 'changes_since', return_value=rows), \
         patch.object(world, 'temporal_events_since', return_value=[]):
        for detail in ('compact', 'standard'):
            failed = service.query(mode='changes', since_cursor=8, continuation='cursor-1',
                                   detail=detail, context_length=262144)
            assert failed['ok'] is False and failed['continuation'] is None
            assert failed['result_token_estimate'] <= {'compact': 512, 'standard': 2048}[detail]
            retry = failed['required_next']
            assert retry['arguments'] == {'mode': 'changes', 'since_cursor': 8,
                                         'detail': 'deep', 'continuation': 'cursor-1'}
            recovered = service.query(**retry['arguments'], context_length=262144)
            assert recovered['ok'] and recovered['items'] == [rows[1]]
            assert recovered['continuation'] is None
            assert recovered['items'][0]['delta']['current']['fields']['evidence']['epistemic_status'] == 'stale'
            human = summary({'kind': 'tool_returned', 'payload': {'managed_name': 'smac_world', 'result': failed}})
            # The console summary must retain the recovery instruction too.
            assert 'required_next' in human, human
    assert rows == frozen
print(json.dumps({'passed': True, 'exact_retry_preserves_sibling_position': True,
                  'historical_epistemics_unchanged': True, 'provider_inference': False}))
