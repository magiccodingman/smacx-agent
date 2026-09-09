"""Bounded paired-checkpoint retention; never overwrite a retained native save."""
import hashlib


def retained_checkpoints(metadata):
    candidates = [metadata.get('recovery_checkpoint'), *metadata.get('recovery_checkpoint_history', [])]
    result, seen = [], set()
    for item in candidates:
        if not isinstance(item, dict) or not item.get('verified'):
            continue
        key = item.get('checkpoint_id') or item.get('native_save_slot') or item.get('slot')
        if key not in seen:
            seen.add(key)
            result.append(item)
    # Keep the latest three plus the newest proven restore, if older.
    kept = result[:3]
    tested = next((x for x in result if x.get('verification', {}).get('status') == 'restore_tested'), None)
    if tested is not None and tested not in kept:
        kept.append(tested)
    return kept


def staging_slot(slot, checkpoints):
    prefix = 'ckpt_' + hashlib.sha256(slot.encode()).hexdigest()[:16]
    occupied = {x.get('native_save_slot') or x.get('slot') for x in checkpoints}
    for suffix in 'abcde':
        candidate = prefix + '_' + suffix
        if candidate not in occupied:
            return candidate
    raise ValueError('checkpoint_staging_slots_exhausted')


def identity_hash(fields):
    value = 1469598103934665603
    for item in fields:
        value = ((value ^ (item & 0xffffffff)) * 1099511628211) & 0xffffffffffffffff
    return str(value)


def canonical_vehicle_fields(capsule):
    fields = capsule.get('native_validation_fields')
    if (not isinstance(fields, list) or len(fields) < 3
            or any(type(x) is not int for x in fields)
            or not 0 <= fields[2] <= 2048 or len(fields) != 3 + 8 * fields[2]
            or fields[0] != capsule.get('turn') or fields[1] != capsule.get('faction_id')
            or len(capsule.get('semantic_vehicle_handles', [])) != fields[2]
            or identity_hash(fields) != capsule.get('native_validation_hash')):
        raise ValueError('native_identity_evidence_invalid')
    # Perspective is the only expected cross-replica difference. Every native
    # vehicle field, including unseen rows, remains part of the private proof.
    return [fields[0], *fields[2:]]


def validate_peer_capsules(capsules):
    canonical = None
    for capsule in capsules.values():
        fields = canonical_vehicle_fields(capsule)
        if canonical is not None and fields != canonical:
            raise ValueError('native_vehicle_layout')
        canonical = fields
    if canonical is None:
        raise ValueError('native_identity_evidence_missing')


def identity_differences(expected, actual, *, ignore_perspective=False):
    if not isinstance(expected, list) or not isinstance(actual, list):
        return []
    names=['faction','design','x','y','home_base_plus_one','order','moves_spent','health']
    result=[]
    if len(expected)!=len(actual):
        result.append({'field':'field_count','expected':len(expected),'actual':len(actual)})
    for index,(before,after) in enumerate(zip(expected,actual)):
        if before==after or (ignore_perspective and index==1):continue
        result.append({'row':(index-3)//8 if index>=3 else None,
            'field':names[(index-3)%8] if index>=3 else ['turn','perspective','vehicle_count'][index],
            'expected':before,'actual':after})
        if len(result)>=32:break
    return result
