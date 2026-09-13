import copy
from smacx_topology import MapShape, KnownSquare, PerspectiveTopology
from smacx_operational_context import geographic_completeness, geographic_belief_review

shape = MapShape(8, 8)
land = KnownSquare('land', 2, 2, 'land')
partial = PerspectiveTopology(shape, [land])
assert geographic_completeness(partial, ['land'])['boundary_status'] == 'incomplete'
neighbors = [KnownSquare(str(p), *p, 'ocean') for p in shape.neighbors((2, 2)).values()]
closed = PerspectiveTopology(shape, [land, *neighbors])
assert geographic_completeness(closed, ['land'])['boundary_status'] == 'observed_closed'
stale = PerspectiveTopology(shape, [land, *[KnownSquare(s.location_ref, s.x, s.y, s.terrain, False) for s in neighbors]])
assert geographic_completeness(stale, ['land'])['boundary_status'] == 'indeterminate_stale'
cognition = {'beliefs': [{'ref_id': 'belief-island', 'content': 'My island is fully revealed with no rivals.', 'confidence': .85}]}
original = copy.deepcopy(cognition)
anchor = {'physical_masses': [{'landmass_ref': 'mass-a', 'geographic_completeness': geographic_completeness(partial, ['land'])}]}
review = geographic_belief_review(cognition, anchor)
assert review[0]['belief_ref'] == 'belief-island' and cognition == original
anchor['physical_masses'][0]['geographic_completeness'] = geographic_completeness(closed, ['land'])
assert not geographic_belief_review(cognition, anchor)
print('geographic certainty: incomplete, closed, stale, heuristic review and unchanged belief passed')
