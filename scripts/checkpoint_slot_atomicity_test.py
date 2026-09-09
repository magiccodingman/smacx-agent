#!/usr/bin/env python3
"""Manager failure window: failed AI capture preserves verified native bytes."""
import copy
import hashlib
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from smacx_worker_manager import WorkerManager, WorkerManagerError
from smacx_checkpoint_policy import identity_hash

previous = {'verified':True, 'slot':'control_recovery', 'turn':1}
state = {'status':'running', 'host_instance_id':'instance-test',
         'metadata':{'recovery_checkpoint':previous}}
saves = {'control_recovery':b'verified-turn-1'}
fail = True
unpaused = []

def update(_match, status, **kw):
    state['status'] = status
    state['metadata'].update(copy.deepcopy(kw.get('metadata',{})))
    return copy.deepcopy(state)

def native(_instance, operation, **kw):
    if operation == 'semantic_snapshot':
        return {'snapshot':{'turn':2,'year':2102,'revision':'stable',
                            'protocol':{'phase':'turn'}}}
    if operation == 'semantic_identity_state':
        return {'ok':True,'schema':'smacx.private-vehicle-identity.v1','turn':2,'faction_id':1,
                'native_validation_fields':[2,1,0], 'native_validation_hash':identity_hash([2,1,0]),
                'semantic_vehicle_handles':[]}
    if operation == 'semantic_choices':
        return {'choices':[{'command':'save_game'}],'revision':'stable'}
    assert operation == 'semantic_command' and kw['command'] == 'save_game'
    saves[kw['slot']] = b'candidate-turn-2'
    return {'ok':True,'turn':2,'year':2102,'relative_path':kw['slot']+'.sav'}

def snapshot(*_args):
    if fail:
        raise WorkerManagerError('injected_ai_archive_failure')
    return []

manager = object.__new__(WorkerManager)
manager._lifecycle_lock = threading.RLock()
manager.control = SimpleNamespace(
    get_match=lambda _id:copy.deepcopy(state),
    list_seats=lambda _id:[{'instance_id':'instance-test','controller_kind':'agent'}],
    update_match_lifecycle=update)
manager.store = SimpleNamespace(export_chat_groups=lambda _:[],
    scopes_for_match=lambda _:[],complete_checkpoint_generation=lambda *_:1)
manager._native_request = native
manager._pause_match_harnesses = lambda _:['sovereign']
manager._unpause_harnesses = lambda names:unpaused.extend(names)
manager._snapshot_hermes_state = snapshot
manager._cleanup_recovery_snapshots = lambda *_:0
manager._checkpoint_save_digest = lambda _,slot:{
    'sha256':hashlib.sha256(saves[slot]).hexdigest(),'bytes':len(saves[slot])}

with patch('smacx_worker_manager.time.sleep'):
    for _ in range(2):
        try: manager.checkpoint_match('match-test')
        except WorkerManagerError as exc: assert str(exc) == 'injected_ai_archive_failure'
        else: raise AssertionError('failed archive was published')
        assert state['metadata']['recovery_checkpoint'] == previous
        assert saves['control_recovery'] == b'verified-turn-1'
    assert len(saves) == 2, 'failed attempts accumulated save slots'
    fail = False
    first = manager.checkpoint_match('match-test')['checkpoint']
    fail = True
    try: manager.checkpoint_match('match-test')
    except WorkerManagerError: pass
    else: raise AssertionError('failed archive was published')
    assert state['metadata']['recovery_checkpoint'] == first
    assert saves[first['native_save_slot']] == b'candidate-turn-2'
    fail = False
    second = manager.checkpoint_match('match-test')['checkpoint']
    assert first['native_save_slot'] != second['native_save_slot']
    third = manager.checkpoint_match('match-test')['checkpoint']
    assert third['native_save_slot'] not in {first['native_save_slot'], second['native_save_slot']}
    assert len(saves) == 4, 'retained candidates overwritten or unbounded staging'
    assert first in state['metadata']['recovery_checkpoint_history']
    for _ in range(12): manager.checkpoint_match('match-test')
    assert len(saves) <= 6, 'retention staging exceeds five plus legacy slot'
    assert third['slot'] == 'control_recovery'
    assert len(unpaused) == 18
print(json.dumps({'passed':True,'failed_archive_preserves_previous_native':True,
                  'candidate_slots_bounded':True,'logical_slot_preserved':True}))
