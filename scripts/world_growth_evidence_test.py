#!/usr/bin/env python3
"""Native-shaped growth receipt retains epistemic state through storage and reads."""
import tempfile
from pathlib import Path
from world_model_contract_test import initialized, bundle
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity, WorldObject

with tempfile.TemporaryDirectory() as temporary:
    _, _, scope, store = initialized(Path(temporary))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, 'timeline-main', 'growth-test')
    native = bundle()
    receipt = {'nutrient_threshold': 30, 'growth_rating': 0, 'population_room': True,
               'ordinary_growth_inhibited': False, 'population_boom_conditions_met': False,
               'timing_semantics': 'Current inputs, not a promised growth date.'}
    native['bases'][0]['growth'] = receipt
    projected = PerspectiveProjector(identity).project(native, observation_sequence=1)
    rows = [obj.as_dict(provider_safe=False) for obj in projected['objects']]
    base = next(row for row in rows if row['kind'] == 'base' and 'growth' in row['fields'])
    assert base['fields']['growth']['epistemic_status'] == 'current'
    for sequence, status in enumerate(('current', 'stale', 'unknown'), 1):
        base['fields']['growth'].update(epistemic_status=status, value=receipt if status != 'unknown' else None)
        store.replace_projection(scope, identity, [WorldObject.from_dict(row) for row in rows],
            observation_cursor=sequence, action_revision=f'g-{sequence}',
            continuity='complete', journal_head_hash='0' * 64)
        loaded = store.load(scope, 'timeline-main')
        found = next(o for o in loaded['objects'] if o['object_ref'] == base['object_ref'])
        assert found['fields']['growth'] == base['fields']['growth']
print('PASS: growth receipt current/stale/unknown storage propagation; no timing inference')
