#!/usr/bin/env python3
"""Adversarial known-map cuts, bounded queries, settlement and operational evidence."""
import json
import time
from smacx_geographic_context import connector_evidence, geographic_context, wider_passage_evidence
from smacx_operational_context import operational_context, order_review
from smacx_mechanics import location_affordances
from smacx_topology import KnownSquare, MapShape, MobilityProfile, PerspectiveTopology


def field(value, status='current'):
    return {'value': value, 'epistemic_status': status, 'source': 'owned_state', 'last_verified_turn': 10}

def main():
    profile = MobilityProfile('land', 'land')
    # Long chains must not exhaust Python recursion; side size is distinct from neck size.
    topo = PerspectiveTopology(MapShape(6000, 4, False),
        [KnownSquare(str(i), i*2, 2, 'land', True) for i in range(3000)])
    started = time.monotonic()
    result = connector_evidence(topo, profile, origin_ref='1500', limit=2)
    assert result['total_single_tile_connectors'] == 2998
    assert result['items'][0]['location_ref'] == '1500'
    for row in result['items']:
        assert sum(s['known_location_count'] for s in row['separated_areas']) == 2999
        assert row['passage_width'] == 1
        assert row['unknown_geography_may_provide_alternates']
    elapsed = time.monotonic()-started
    # Full wrapping ring has an alternate route, no articulation.
    ring = PerspectiveTopology(MapShape(12, 1, True),
        [KnownSquare(str(i), i*2, 0, 'land', True) for i in range(6)])
    assert not connector_evidence(ring, profile)['items']
    # Four vertices in a cycle: adjacent pair may cut it only if the graph says so.
    wide = wider_passage_evidence(ring, profile, '0')
    assert not wide['items'] and wide['candidate_coverage_complete']
    assert wider_passage_evidence(topo, profile, '0', max_nodes=8)['status'] == 'not_calculated'
    ladder = PerspectiveTopology(MapShape(8, 4, False), [KnownSquare(str(i), i, i % 2, 'land', True) for i in range(5)])
    assert wider_passage_evidence(ladder, profile, '2')['items']
    # Fog far from the neck still qualifies the entire component's cut claim.
    squares = [KnownSquare(f'{x}:{y}', x, y, 'land' if y == 4 else 'ocean', True)
               for y in range(8) for x in range(y % 2, 20, 2) if (x, y) != (1, 3)]
    distant = PerspectiveTopology(MapShape(20, 8, False), squares)
    cut = connector_evidence(distant, profile, origin_ref='10:4', limit=1)['items'][0]
    assert cut['unknown_geography_may_provide_alternates']
    closed = geographic_context(ring, ring.by_ref, {})
    assert closed['unknown_boundary_location_count'] == 0
    seam = geographic_context(ring, {'0', '5'}, {})
    assert seam['minimum_horizontal_coordinate_span'] == 2
    # Stale economy never becomes a current alert. Queue position zero is current.
    base = {'object_ref': 'base-a', 'kind': 'base', 'fields': {
        'owner_ref': field('faction-own'), 'production_queue': field([{'position': 0, 'name': 'Scout'}]),
        'production_name': field('Scout'), 'nutrient_surplus': field(-2, 'stale'),
        'energy_surplus': field(-1), 'drone_riots': field(False)}}
    review = operational_context({'base-a': base})
    reasons = review['economic_review'][0]['review_reasons']
    assert 'negative_nutrient_surplus' not in reasons
    assert 'negative_energy_surplus' in reasons and 'no_followup_queue_not_idle_production' in reasons
    # Overlap with two bases is a union interval, never summed as distinct tiles.
    obj = {'object_ref': '0', 'kind': 'location', 'fields': {'features': field([])}}
    row = location_affordances(ring, {'0': obj}, ['0'], native_receipts={'0': {
        'known_radius_location_count': 21, 'overlapping_known_bases': [
            {'overlapping_radius_location_count': 10}, {'overlapping_radius_location_count': 8}]}})[0]
    assert row['settlement_coverage']['additional_to_known_base_radii_min'] == 3
    assert row['settlement_coverage']['additional_to_known_base_radii_max'] == 11
    unit = {'object_ref': 'u', 'kind': 'own_unit', 'location_ref': '0', 'fields': {
        'order_name': field('sentry'), 'ready': field(False), 'moves_spent': field(0)}}
    projection = {'identity': {'world_epoch': 'epoch'}, 'observation_cursor': 5, 'objects': [unit]}
    events = [{'sequence': 1, 'event_id': 'action-a', 'payload': {'persistent_order_attempt': {
        'before': {'unit_ref': 'u', 'location_ref': '0'}, 'world_epoch': 'epoch', 'observation_cursor': 1, 'mode': 'sentry_unit'}}}]
    order = order_review(events, projection)['items'][0]
    assert order['location_differs_from_assignment'] is False and 'stall' not in order
    projection['identity']['world_epoch'] = 'restored'
    assert not order_review(events, projection)['items']
    print(json.dumps({'event': 'pass', 'chain_nodes': 3000, 'connector_seconds': round(elapsed, 3),
                      'cases': ['iterative_cuts', 'side_sizes', 'wrap_alternate', 'wide_bounds', 'stale_economy', 'queue_current', 'overlap_union', 'stationary_not_failure', 'epoch_isolation']}))

if __name__ == '__main__': main()
