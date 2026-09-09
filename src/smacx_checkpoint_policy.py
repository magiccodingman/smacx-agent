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
