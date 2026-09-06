"""Private, perspective-scoped visible episodes at a native feed cut.

An open episode is evidence of stream continuity, not current whereabouts or
an omniscient vehicle identity. Only explicit observed segments extend it.
"""
from copy import deepcopy
from smacx_world_types import content_hash


def advance_episodes(*, identity, prior_objects, state, events, gaps, owned_keys=()):
    namespace = identity.as_dict()
    valid = state.get('identity') == namespace
    opened = deepcopy(state.get('open', {})) if valid else {}
    # Bootstrap an installation without the private temporal checkpoint only.
    # Once it exists, snapshot endpoints cannot resurrect closed stream state.
    if not valid:
        for row in prior_objects:
            key = row.get('metadata', {}).get('native_observation_key')
            if key and row.get('kind') == 'foreign_contact' and row.get('status', 'active') == 'active':
                opened[key] = {'ref': row['object_ref'], 'location': row.get('location_ref')}
    own = set(owned_keys) | {str(row.get('metadata', {}).get('native_observation_key') or
               'vehicle-handle-' + str(row.get('metadata', {}).get('native_handle')))
           for row in prior_objects if row.get('kind') == 'own_unit'}
    owned_lifecycles = deepcopy(state.get('owned_lifecycles', {})) if valid else {}
    assignments, terminal, just_lost, owned_lost = {}, {}, {}, {}
    boundaries = sorted({int(gap['before_native_sequence']) for gap in gaps})
    def close_all():
        terminal.update({value['ref']: 'unknown' for value in opened.values()})
        opened.clear(); just_lost.clear(); owned_lifecycles.clear(); owned_lost.clear()
    def create(key, raw):
        return {'ref': 'contact-episode-' + content_hash({
            **namespace, 'handle': key, 'start': raw['native_sequence']})[:32], 'location': None}
    for raw in events:
        sequence = int(raw['native_sequence'])
        while boundaries and boundaries[0] <= sequence:
            close_all(); boundaries.pop(0)
        kind = raw.get('native_kind')
        if kind == 'contact_identity_reset':
            close_all(); continue
        if kind == 'owned_production_completed' and raw.get('value_before') == 0:
            handle = raw.get('value_after')
            if type(handle) is int and handle >= 0:
                key = 'vehicle-handle-' + str(handle)
                ref = 'own-unit-' + str(handle)
                previous = opened.pop(key, None)
                if previous: terminal[previous['ref']] = 'unknown'
                just_lost.pop(key, None)
                owned_lost.pop(key, None)
                owned_lifecycles[key] = {'ref': ref, 'birth_sequence': sequence}
                assignments[str(sequence)] = ref
            continue
        if not str(kind).startswith('visible_unit_'):
            continue
        key = 'vehicle-handle-' + str(raw.get('subject_a'))
        lifecycle = owned_lifecycles.get(key)
        if kind == 'visible_unit_destroyed' and lifecycle is None:
            lifecycle = owned_lost.pop(key, None)
        elif kind == 'visible_unit_appeared':
            owned_lost.pop(key, None)
        # Explicit contrary ownership ends proof; a gap/reset also clears it.
        if lifecycle and raw.get('relationship_at_occurrence') in ('hostile', 'allied', 'neutral'):
            owned_lifecycles.pop(key, None)
            own.discard(key)
            lifecycle = None
        if lifecycle:
            assignments[str(sequence)] = lifecycle['ref']
            if kind == 'visible_unit_lost':
                owned_lost[key] = lifecycle
            if kind in ('visible_unit_lost', 'visible_unit_destroyed'):
                owned_lifecycles.pop(key, None)
                own.discard(key)
            continue
        if key in own:
            continue
        current = opened.get(key)
        before = raw.get('from_tile_id'); after = raw.get('to_tile_id')
        before_ref = f'location-{before}' if type(before) is int and before >= 0 else None
        after_ref = f'location-{after}' if type(after) is int and after >= 0 else None
        if kind == 'visible_unit_appeared':
            # Appearance is a new visibility boundary even at the same square.
            if current: terminal[current['ref']] = 'unknown'
            current = create(key, raw); opened[key] = current
            just_lost.pop(key, None)
        elif kind == 'visible_unit_moved':
            if before_ref is None or after_ref is None:
                # Native sentinel coordinates cannot extend a visible episode
                # across this boundary, even if the raw visibility bit is set.
                if current is None or before_ref is None or current.get('location') != before_ref:
                    if current: terminal[current['ref']] = 'unknown'
                    current = create(key, raw)
                assignments[str(sequence)] = current['ref']
                terminal[current['ref']] = 'unknown'
                opened.pop(key, None); just_lost.pop(key, None)
                continue
            if raw.get('continuous_visibility') is not True:
                if current: terminal[current['ref']] = 'unknown'
                opened.pop(key, None); continue
            if current and current.get('location') not in (None, before_ref):
                terminal[current['ref']] = 'unknown'; current = None
            if current is None:
                current = create(key, raw); opened[key] = current
        elif current is None and kind == 'visible_unit_destroyed' and key in just_lost:
            current = just_lost.pop(key)
        elif current is None:
            # An observed loss/damage after a gap still has qualified evidence,
            # but no private handle may attach it to a pre-gap identity.
            current = create(key, raw)
        assignments[str(sequence)] = current['ref']
        if kind in ('visible_unit_lost', 'visible_unit_destroyed'):
            terminal[current['ref']] = 'confirmed_destroyed' if kind.endswith('destroyed') else 'unknown'
            opened.pop(key, None)
            if kind == 'visible_unit_lost': just_lost[key] = current
        else:
            current['location'] = after_ref or before_ref or current.get('location')
            current['last_sequence'] = sequence
    if boundaries:
        close_all()
    return {'identity': namespace, 'open': opened, 'owned_lifecycles': owned_lifecycles}, assignments, terminal
