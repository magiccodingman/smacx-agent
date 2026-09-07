"""Qualified observations for native Former automation, never strategy selection."""
from collections.abc import Mapping
from smacx_world_types import content_hash


def former_state(projection, unit_ref):
    objects = {x['object_ref']: x for x in projection.get('objects', ())}
    unit = objects.get(unit_ref, {})
    if unit.get('kind') != 'own_unit' or unit.get('status', 'active') != 'active':
        return None
    def value(obj, key):
        field = obj.get('fields', {}).get(key, {})
        return field.get('value') if field.get('epistemic_status') == 'current' else None
    roles = value(unit, 'roles')
    if not isinstance(roles, Mapping) or not roles.get('former'):
        return None
    task = value(unit, 'terraform_task')
    if not isinstance(task, Mapping):
        return None
    location = unit.get('location_ref')
    features = value(objects.get(location, {}), 'features')
    return {'unit_ref': unit_ref, 'location_ref': location, 'task': dict(task),
            'moves_spent': value(unit, 'moves_spent'), 'order_name': value(unit, 'order_name'),
            'features_hash': content_hash(features) if features is not None else None}


def classify_attempt(before, after):
    """Endpoint evidence cannot prove every intervening activity or its cause."""
    if not before or not after:
        return 'unknown'
    if after['task'].get('state') == 'active_terraform_order':
        return 'work_observed'
    if before['location_ref'] != after['location_ref']:
        return 'movement_observed'
    if before.get('features_hash') and after.get('features_hash') \
            and before['features_hash'] != after['features_hash']:
        return 'tile_change_observed'
    if after['task'].get('automation_active'):
        return 'pending'
    if before['task'].get('state') == 'active_terraform_order':
        return 'prior_work_outcome_unknown'
    if (after['task'].get('state') != 'no_active_terraform_order'
            or after['task'].get('automation_active') is not False):
        return 'unknown'
    if after.get('order_name') != 'none':
        return 'order_outcome_unknown'
    return 'stopped'
