"""Current owned endpoint evidence for persistent orders; no inferred native cause."""

COMMANDS = frozenset({'return_to_base', 'go_to_base', 'go_to', 'patrol_unit',
                      'hold_unit', 'sentry_unit', 'auto_explore_unit', 'set_unit_on_alert'})


def unit_state(projection, unit_ref):
    unit = next((u for u in projection.get('objects', ()) if u.get('object_ref') == unit_ref), {})
    if unit.get('kind') != 'own_unit' or unit.get('status', 'active') != 'active':
        return None
    fields = unit.get('fields', {})
    values = {}
    for key in ('order_name', 'ready', 'moves_spent'):
        field = fields.get(key, {})
        if field.get('epistemic_status') != 'current':
            return None
        values[key] = field.get('value')
    if not unit.get('location_ref'):
        return None
    return {'unit_ref': unit_ref, 'location_ref': unit['location_ref'], **values}


def classify_attempt(before, after):
    if not before or not after:
        return 'unknown'
    if after['order_name'] != 'none' or after['ready'] is not True:
        return 'pending'
    return 'cleared'
