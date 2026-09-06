#!/usr/bin/env python3
"""A deployment must refresh persisted summary formats without a world mutation."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from world_model_contract_test import initialized, bundle
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector, SemanticLodProjector
from smacx_world_types import WorldIdentity

with tempfile.TemporaryDirectory() as tmp:
    _, journal, scope, world = initialized(Path(tmp))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, journal.timeline_id(scope), 'world-format')
    observed = bundle()
    observed['bases'][0]['nutrient_surplus'] = -1
    projection = PerspectiveProjector(identity).project(observed, observation_sequence=9)
    world.replace_projection(scope, identity, projection['objects'], observation_cursor=9,
                             action_revision='r9', continuity='complete', journal_head_hash='0'*64)
    service = WorldService(world, scope)
    summarize = SemanticLodProjector._strategic_summary
    def old_summary(item):
        result = summarize(item)
        result['fields'].pop('nutrient_surplus', None)
        return result
    with patch.object(SemanticLodProjector, 'FORMAT_VERSION', 1), \
         patch.object(SemanticLodProjector, '_strategic_summary', staticmethod(old_summary)):
        old = service.anchor(context_length=262144)
        old_query = service.query(mode='overview', detail='deep', context_length=262144)
    new_query = service.query(mode='overview', detail='deep', context_length=262144)
    assert new_query['cache']['hit'] is False
    new = service.anchor(context_length=262144)
    assert old['world_anchor_id'] != new['world_anchor_id']
    assert old['anchor_observation_cursor'] == new['anchor_observation_cursor'] == 9
    assert new['payload']['projector_version'] == SemanticLodProjector.FORMAT_VERSION
    base = next(row for row in new['payload']['strategic_objects'] if row.get('kind') == 'base')
    assert base['fields']['nutrient_surplus']['value'] == -1
    assert base['fields']['nutrient_surplus']['epistemic_status'] == 'current'
    same = service.anchor(context_length=262144)
    assert same['world_anchor_id'] == new['world_anchor_id'], 'current format regenerated unnecessarily'
    assert service.query(mode='overview', detail='deep', context_length=262144)['cache']['hit'] is True
print(json.dumps({'pass': True, 'persisted_anchor_format_invalidated': True,
                  'overview_cache_invalidated': True, 'no_world_mutation_needed': True,
                  'qualified_food_visible': True, 'current_format_reused': True}))
