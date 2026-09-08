"""Bounded scoped spatial context: seam, stale, unknown, and omission contracts."""
import copy
import json
from smacx_runtime_context import _nearby_base_defense, _spatial_context
from smacx_world import WorldService
from smacx_world_model import estimate_tokens


def evidence(value, status='current'):
    return dict(value=value, epistemic_status=status, source='owned_state',
                provenance_ref='observation-test', last_verified_turn=38)


def entity(ref, kind, location, status='active'):
    return dict(object_ref=ref, kind=kind, location_ref=location, status=status,
                fields={'owner_ref': evidence('faction-1', 'stale' if status == 'lost' else 'current')})


def location(ref, x, y):
    return dict(object_ref=ref, kind='location', status='active',
                metadata=dict(native_x=x, native_y=y))


projection = {'world_revision': 7, 'objects': [
    dict(object_ref='world-map', kind='map_state', fields={
        'width': evidence(80), 'height': evidence(80), 'horizontal_wrap': evidence(True)}),
    location('location-arbitrary-a', 78, 20), location('location-arbitrary-b', 0, 20),
    entity('own-unit-2', 'own_unit', 'location-arbitrary-a'),
    entity('base-example', 'base', 'location-arbitrary-b'),
    entity('contact-old', 'foreign_contact', 'location-arbitrary-a', 'lost'),
]}
focus = {'unit': {'own_unit_ref': 'own-unit-2'}}
r = _spatial_context(projection, focus)
assert len(r['relations']) == 2
assert r['relations'][0]['geometric_distance'] == 1
assert r['relations'][0]['bearing'] == 'E'
assert r['relations'][1]['epistemic_status'] == 'stale'
assert r['relations'][1]['origin']['provenance_ref'] == 'observation-test'
assert not any(key in json.dumps(r) for key in ('native_x', 'native_y'))
flat = copy.deepcopy(projection)
flat['objects'][0]['fields']['horizontal_wrap']['value'] = False
assert _spatial_context(flat, focus)['relations'][0]['geometric_distance'] == 39
unknown = copy.deepcopy(projection)
unknown['objects'][0]['fields']['width']['epistemic_status'] = 'stale'
assert _spatial_context(unknown, focus)['relations'] == []
unknown = copy.deepcopy(projection)
unknown['objects'][3]['fields']['owner_ref']['epistemic_status'] = 'unknown'
assert all(row['origin']['object_ref'] != 'own-unit-2' for row in _spatial_context(unknown, focus)['relations'])
large = copy.deepcopy(projection)
large['objects'] += [entity(f'contact-{n:05}', 'foreign_contact', 'location-arbitrary-a') for n in range(10000)]
large['objects'] += [entity(f'base-{n:05}', 'base', 'location-arbitrary-b') for n in range(1000)]
r = _spatial_context(large, focus)
assert len(r['relations']) == 10
assert r['contact_sample']['omitted'] == 9997
assert estimate_tokens(r) < 2600

def fields_entity(ref, kind, location_ref, fields):
    return {'object_ref': ref, 'kind': kind, 'location_ref': location_ref,
            'status': 'active', 'fields': fields}

def terrain_location(ref, x, y):
    return {'object_ref': ref, 'kind': 'location', 'status': 'active',
            'metadata': {'native_x': x, 'native_y': y},
            'fields': {'terrain': evidence('land'), 'features': evidence([])}}

defense_projection = {'world_revision': 8, 'map_shape': {
    'width': 12, 'height': 4, 'horizontal_wrap': False}, 'objects': [
    terrain_location('location-home', 2, 2),
    terrain_location('location-reserve', 0, 2),
    terrain_location('location-contact', 4, 2),
    fields_entity('base-home', 'base', 'location-home', {
        'owner_ref': evidence('faction-1'), 'production_cost': evidence(20),
        'minerals_accumulated': evidence(4), 'mineral_surplus': evidence(2)}),
    fields_entity('own-unit-garrison', 'own_unit', 'location-home', {
        'roles': evidence({'combat': True}), 'triad': evidence('land'),
        'movement_points': evidence(1), 'moves_remaining': evidence(1)}),
    fields_entity('own-unit-reserve', 'own_unit', 'location-reserve', {
        'roles': evidence({'combat': True}), 'triad': evidence('land'),
        'movement_points': evidence(1), 'moves_remaining': evidence(1)}),
    fields_entity('foreign-contact', 'foreign_contact', 'location-contact', {
        'owner_ref': evidence('faction-2'), 'roles': evidence({'combat': True}),
        'triad': evidence('land'), 'movement_points': evidence(1),
        'relationship': evidence('neutral')}),
    fields_entity('faction-2', 'faction', '', {
        'relations': evidence({'pact': False, 'treaty': True, 'truce': False,
                               'vendetta': False})}),
]}

class ProjectedWorld:
    _objects = staticmethod(WorldService._objects)
    _topology = staticmethod(WorldService._topology)

defense = _nearby_base_defense(ProjectedWorld(), defense_projection,
                                turn=15, year=2135)
assert len(defense['bases']) == 1
base = defense['bases'][0]
assert base['observed_defender_count'] == 1 and base['friendly_response']
foreign = base['visible_foreign_response'][0]
assert foreign['formal_relationship'] == {
    'epistemic_status': 'current', 'pact': False, 'treaty': True,
    'truce': False, 'vendetta': False}
assert foreign['foreign_movement_zoc_constraint'] is True
assert foreign['inferred_intent'] == 'unknown_not_mechanically_observed'
assert defense['evidence_boundaries']['visible_forces'] == 'lower_bound_only'
assert 'do not prove equal resources' in defense['shared_era_context']['meaning']
print(json.dumps({'ok': True, 'large_relations': len(r['relations']), 'large_tokens': estimate_tokens(r),
                  'nearby_defense_surfaced': True,
                  'scope': 'projection/context behavior; live provider delivery and improved decisions not yet verified'}))
