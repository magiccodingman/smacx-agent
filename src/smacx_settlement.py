"""Bounded settlement assistance. Preferences are advisory, never native authority."""
from __future__ import annotations

import json
from collections.abc import Mapping
from smacx_counterfactual import feasible_outputs
from smacx_mechanics import field_value, object_location

PURPOSES = {'expansion', 'growth', 'production', 'coastal_access', 'strategic_outpost'}


def parse_search(raw):
    if len(raw.encode()) > 4096:
        raise ValueError('settlement_search_too_large')
    value = json.loads(raw) if raw else {}
    if not isinstance(value, dict) or set(value) - {'purpose', 'domain', 'max_travel_turns'}:
        raise ValueError('unsupported_settlement_search_parameter')
    value.setdefault('purpose', 'expansion')
    value.setdefault('domain', 'land')
    if value['purpose'] not in PURPOSES or value['domain'] not in {'land', 'sea', 'both'}:
        raise ValueError('invalid_settlement_purpose_or_domain')
    if 'max_travel_turns' in value and (type(value['max_travel_turns']) is not int
                                      or not 0 <= value['max_travel_turns'] <= 32):
        raise ValueError('invalid_settlement_travel_limit')
    return value


def candidate_locations(topology, objects, registry, area_ref, radius, definition):
    """Search known centers; the native radius probe can add fogged terrain potential."""
    item = objects.get(area_ref, {})
    center = topology.by_ref.get(object_location(item, area_ref))
    region = registry.get(area_ref, {})
    if center is None and region.get('kind') not in {'region', 'scope', 'frontier', 'theater'}:
        raise ValueError('settlement_requires_base_unit_location_or_region')
    members = set(region.get('location_refs', ())) if center is None else None
    rows = []
    for ref, square in topology.by_ref.items():
        if members is not None and ref not in members:
            continue
        distance = topology.shape.distance((center.x, center.y), (square.x, square.y)) if center else 0
        if center and distance > radius:
            continue
        if square.terrain not in {'land', 'ocean'}:
            continue
        if definition['domain'] == 'land' and square.ocean or definition['domain'] == 'sea' and not square.ocean:
            continue
        rows.append((distance, ref))
    rows.sort()
    # Spatially spread a bounded sample instead of keeping only one nearby cluster.
    selected = rows if len(rows) <= 32 else [rows[i * (len(rows)-1)//31] for i in range(32)]
    return [ref for _, ref in selected], {
        'area_ref': area_ref, 'known_centers_in_scope': len(rows),
        'centers_evaluated': len(selected), 'complete': len(rows) <= 32,
        'scope': 'known candidate centers; radius terrain potential may include fog',
        'selection': 'deterministic distance-spread sample' if len(rows) > 32 else 'all known centers in scope',
        'purpose': definition['purpose'], 'radius': radius if center else None,
    }


def shortlist(receipts, purpose, limit=4):
    """Expose distinct tradeoffs; never claim an optimal global site."""
    def metrics(row):
        tiles = row.get('known_radius', [])
        outputs = [t.get('yields', {}) for t in tiles]
        n = sum(sorted((v.get('nutrients', 0) or 0 for v in outputs), reverse=True)[:3])
        m = sum(sorted((v.get('minerals', 0) or 0 for v in outputs), reverse=True)[:3])
        overlap = sum(v.get('overlapping_radius_location_count', 0) for v in row.get('overlapping_known_bases', []))
        return n, m, -overlap, int(bool(row.get("coastal_access")))
    rows = list(receipts)
    if not rows:
        return []
    order = [3, 0, 1] if purpose == 'coastal_access' else [1, 0, 2] if purpose == 'production' else [2, 0, 1] if purpose == 'expansion' else [0, 1, 2]
    selected = []
    for axis in order:
        row = max(rows, key=lambda r: (metrics(r)[axis], str(r.get('location_ref'))))
        if row not in selected:
            selected.append(row)
    for row in rows:
        if row not in selected:
            selected.append(row)
        if len(selected) >= limit:
            break
    return selected[:limit]


def economic_assessment(receipt, populations=(1, 2, 3)):
    economy = receipt.get('site_economy') or {}
    center = economy.get('center') or {}
    if not center:
        return {'status': 'economic_preview_unavailable',
                'meaning': 'Legality and radius coverage do not establish economic suitability.'}
    squares = economy.get('squares', [])
    intake = economy.get('nutrients_per_citizen')
    levels = []
    cost = economy.get('benchmark_colony_mineral_cost')
    for pop in populations:
        frontier = feasible_outputs(squares, {**center.get('yields', {}),
            'location_ref': center.get('location_ref'), 'epistemic_status': center.get('epistemic_status')},
            pop, alternative_limit=3)
        for alternative in frontier['alternatives']:
            n = alternative['gross_output']['nutrients']
            surplus = n - intake * pop if type(intake) is int else None
            alternative['nutrient_surplus'] = surplus
            alternative['growth'] = ('unknown' if surplus is None else
                'cannot_sustain_allocation' if surplus < 0 else 'no_growth_surplus' if surplus == 0 else 'positive_growth_surplus')
            shared = [s['location_ref'] for s in squares if s.get('shared_known_base_count', 0)
                      and s['location_ref'] in alternative['worker_refs']]
            alternative['shared_worker_refs'] = shared
            minerals = alternative['gross_output']['minerals']
            if type(cost) is int and cost > 0 and minerals > 0:
                alternative['colony_mineral_accumulation_turns_zero_support'] = (cost + minerals - 1)//minerals
                alternative['production_assumptions'] = 'Zero stock/support, fixed output; ignores colony population eligibility, riots, completion timing and future changes.'
        levels.append(frontier)
    first = levels[0].get('alternatives', []) if levels else []
    sustainable = [a for a in first if (a.get('nutrient_surplus') or 0) > 0]
    descriptions = ['positive early growth available' if sustainable else
                    'early growth constrained in evaluated allocations']
    times = [a['colony_mineral_accumulation_turns_zero_support'] for a in sustainable
             if 'colony_mineral_accumulation_turns_zero_support' in a]
    if times:
        descriptions.append(('strong' if min(times) <= 6 else 'moderate' if min(times) <= 12 else 'weak')
                            + ' immediate production under zero-support benchmark')
    return {
        'summary': '; '.join(descriptions),
        'descriptor_basis': 'Heuristic: strong <=6, moderate <=12 turns of colony mineral accumulation with positive growth; weak above12. Not a site ranking or completion guarantee.',
        'status': 'conditional', 'population_alternatives': levels,
        'basis': economy.get('coverage'),
        'territorial_effects': 'Founding-induced ownership changes are not simulated. Current access is not a post-founding guarantee.',
        'production': 'Gross minerals before support; no net build-time promise.',
        'new_workable_tiles': sum(s.get('workable') is True and not s.get('shared_known_base_count') for s in squares),
        'shared_unreserved_tiles': sum(s.get('workable') is True and bool(s.get('shared_known_base_count')) for s in squares),
        'reserved_by_owned_workers': sum(s.get('reserved_by_owned_worker') is True for s in squares),
        'unavailable_tiles': sum(s.get('workable') is False for s in squares),
        'unknown_availability_tiles': sum(s.get('workable') is None for s in squares),
        'development': 'Current terrain output only. Existing counterfactual details expose conditional improvements and unlocks.',
        'heuristic_version': 'settlement-v1',
    }


def access_changes(previous, current, turn=None):
    """Only compare actually observed access; disappearance into fog is not loss."""
    old = {r['object_ref']: r for r in previous}
    events = []
    for base in current:
        if base.get('kind') != 'base':
            continue
        before = old.get(base['object_ref'], {})
        prior_field = before.get('fields', {}).get('base_radius', {})
        field = base.get('fields', {}).get('base_radius', {})
        if prior_field.get('epistemic_status') != 'current' or field.get('epistemic_status') != 'current':
            continue
        prior = {r['location_ref']: r for r in prior_field.get('value', [])}
        changed = []
        for row in field.get('value', []):
            prev = prior.get(row['location_ref'], {})
            if isinstance(prev.get('access'), Mapping) and isinstance(row.get('access'), Mapping) \
                    and any(prev['access'].get(k) != row['access'].get(k) for k in
                            ('foreign_territory', 'visible_occupation_constraint', 'reserved_by_other_owned_base')):
                changed.append({'location_ref': row['location_ref'], 'previous': prev['access'],
                    'current': row['access'], 'previously_worked': prev.get('worked', False)})
        if changed:
            events.append({'event_kind': 'base_resource_access_changed', 'base_ref': base['object_ref'],
                'turn': turn, 'tiles': changed, 'cause': 'not_established',
                'observed_economy': {name: {'before': field_value(before,name), 'after': field_value(base,name)}
                    for name in ('nutrient_surplus','mineral_surplus','energy_surplus')
                    if field_value(before,name) is not None and field_value(base,name) is not None},
                'meaning': 'Observed access changed; nearby discovery does not prove new construction.',
                'query': {'tool': 'smac_world', 'mode': 'base', 'subject_refs': [base['object_ref']]}})
    return events


def compact_assessment(assessment):
    if assessment.get('status') != 'conditional':
        return assessment
    return {k: assessment[k] for k in ('status','summary','new_workable_tiles',
        'shared_unreserved_tiles','reserved_by_owned_workers','unavailable_tiles') } | {
        'allocations': [{'population': level['population'],
            'alternatives': [{'gross_N_M_E': list(a['gross_output'].values()),
                'nutrient_surplus': a['nutrient_surplus'],
                'shared_workers': len(a['shared_worker_refs']),
                'colony_mineral_turns_zero_support': a.get('colony_mineral_accumulation_turns_zero_support')}
                for a in level.get('alternatives', [])],
            'complete': level.get('frontier_search_complete') and not level.get('alternatives_truncated')}
            for level in assessment['population_alternatives']],
        'assumptions': 'Conditional current access; zero support benchmark is not completion ETA. Borders, psych, population eligibility and future changes not simulated.'}


def terrain_summary(receipt):
    rows = receipt.get('known_radius', [])
    bonuses = {'nutrient': 0, 'mineral': 0, 'energy': 0}
    for row in rows:
        for number, name in ((1,'nutrient'),(2,'mineral'),(3,'energy')):
            if row.get('resource_bonus') == number or name+'_resource' in row.get('features', []):
                bonuses[name] += 1
    return {'radius_resource_bonuses': bonuses,
        'river_tiles': sum(t.get('river') is True or 'river' in t.get('features', []) for t in rows),
        'unobserved_tiles': sum(t.get('availability') == 'unknown' for t in rows),
        'coastal_access': receipt.get('coastal_access'),
        'meaning': 'Resource potential, not additional collectible income; allocations exclude unverified access.'}
