#!/usr/bin/env python3
"""Complete committed world/semantic history across storage and token pages."""
import json
import tempfile
from pathlib import Path
from world_model_contract_test import initialized, bundle
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity

with tempfile.TemporaryDirectory() as temporary:
    _, _, scope, world = initialized(Path(temporary))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, 'timeline-main', 'world-test')
    projected = PerspectiveProjector(identity).project(bundle(), observation_sequence=9)
    world.replace_projection(scope, identity, projected['objects'], observation_cursor=9,
                             action_revision='a', continuity='complete', journal_head_hash='0' * 64)
    deltas = [{'object_ref': f'location-{i}', 'change': 'changed',
               'epistemic_status': 'stale', 'last_verified_turn': 8} for i in range(700)]
    events = [{'event_id': f'event-{i}', 'kind': 'controlled_event',
               'epistemic_status': 'conditional'} for i in range(400)]
    world.record_observation_projections(scope, identity.timeline_id, [
        ({'sequence': 9, 'kind': 'world_batch', 'turn': 9, 'payload': {'deltas': deltas}}, 'journal-world'),
        ({'sequence': 9, 'kind': 'semantic_batch', 'turn': 9, 'payload': {'events': events}}, 'journal-events'),
        ({'sequence': 10, 'kind': 'world_object', 'turn': 10, 'payload': {'object_ref': 'uncommitted'}}, 'journal-future')])
    service = WorldService(world, scope)
    continuation = ''
    seen_deltas, seen_events, cursors = [], [], set()
    for page in range(100):
        result = service.query(mode='changes', since_cursor=8, detail='deep',
                               continuation=continuation, context_length=262144)
        assert result['ok'], result
        assert result['result_token_estimate'] <= 8192
        seen_deltas.extend(r['delta'] for r in result['items'])
        seen_events.extend(r['event'] for r in result['temporal_events'])
        continuation = result['continuation']
        if not continuation:
            break
        assert continuation not in cursors, 'continuation made no progress'
        cursors.add(continuation)
    else:
        raise AssertionError('history pagination did not terminate')
    assert seen_deltas == deltas, f'world history lost/repeated: {len(seen_deltas)}/700'
    assert seen_events == events, f'temporal history lost/repeated: {len(seen_events)}/400'
    # A temporal-only oversized row must produce an actionable error, not a
    # successful empty page whose continuation repeats forever.
    world.record_observation_projection(scope, identity.timeline_id,
        {'sequence': 9, 'kind': 'semantic_event', 'turn': 9,
         'payload': {'event_id': 'large-event', 'evidence': 'x' * 8500}}, 'journal-large')
    too_small = service.query(mode='changes', since_cursor=8, detail='standard',
                              continuation='cursor-700-400', context_length=262144)
    assert too_small['ok'] is False
    assert too_small['error']['code'] == 'single_world_item_exceeds_budget'
    retry = too_small['required_next']['arguments']
    assert retry['continuation'] == 'cursor-700-400'
    recovered = service.query(**retry, context_length=262144)
    assert recovered['ok'] and recovered['continuation'] is None
    assert recovered['temporal_events'][0]['event']['event_id'] == 'large-event'

print(json.dumps({'passed': True, 'world_deltas': 700, 'semantic_events': 400,
                  'same_observation_siblings_preserved': True,
                  'uncommitted_future_excluded': True, 'epistemics_preserved': True,
                  'oversized_temporal_retry_preserves_position': True, 'pages': page + 1, 'provider_inference': False}))
