#!/usr/bin/env python3
"""Stored perspective -> cached anchor calendar; no inferred or stale year."""
import json
import tempfile
from pathlib import Path
from world_model_contract_test import initialized, bundle
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity, WorldObject

with tempfile.TemporaryDirectory() as temporary:
    _, _, scope, store = initialized(Path(temporary))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, 'timeline-main', 'calendar-test')
    native = bundle()
    native['year'] = 2379  # Deliberately unrelated to conventional turn arithmetic.
    projected = PerspectiveProjector(identity).project(native, observation_sequence=1)
    rows = [obj.as_dict(provider_safe=False) for obj in projected['objects']]
    clock = next(row for row in rows if row['kind'] == 'turn_state')
    service = WorldService(store, scope)
    for sequence, (status, value, expected) in enumerate([
        ('current', 2379, 2379), ('stale', 2379, None),
        ('unknown', None, None), ('current', True, None),
        ('current', 2380, 2380),
    ], 1):
        clock['fields']['year'].update(epistemic_status=status, value=value)
        store.replace_projection(scope, identity, [WorldObject.from_dict(row) for row in rows],
            observation_cursor=sequence, action_revision=f'a-{sequence}',
            continuity='complete', journal_head_hash='0' * 64)
        anchor = service.anchor(context_length=65536)
        assert anchor['payload']['year'] == expected, (status, anchor['payload']['year'])
        assert WorldService(store, scope).anchor(context_length=65536)['payload']['year'] == expected
print(json.dumps({'passed': True, 'stored_current_calendar': True, 'stale_unknown_invalid_withheld': True,
                  'same_turn_cache_refresh': True, 'service_restart': True}))
