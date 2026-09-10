"""Restored and current world repair evidence retains its mechanical scope."""
import copy
from smacx_world_types import provider_safe
from smacx_world_model import SemanticLodProjector
from smacx_world import _public_object

for status in ('current', 'stale', 'unknown'):
    item = {'object_ref': 'global-repair-rules', 'kind': 'repair_rules', 'fields': {
        'state': {'value': {'base_bonus': 1, 'base_facility_bonus': 10},
                  'epistemic_status': status, 'source': 'owned_state'}}}
    original = copy.deepcopy(item)
    visible = provider_safe({'restored': item})['restored']
    assert visible['fields'] == item['fields']
    assert 'not combat strength' in visible['mechanical_scope']
    assert provider_safe(visible) == visible
    assert item == original
    summary = provider_safe(SemanticLodProjector._strategic_summary(item))
    assert summary['mechanical_scope'] == visible['mechanical_scope']
    assert summary['fields']['state']['epistemic_status'] == status
    inspected = _public_object(item)
    assert inspected['mechanical_scope'] == visible['mechanical_scope']
    assert inspected['fields'] == item['fields']
print('repair rules: nested/restored/anchor delivery, qualification and immutable evidence passed')
