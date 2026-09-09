"""Bounded paired retention and conservative multi-candidate recovery."""
import copy
import threading
from types import SimpleNamespace
from smacx_checkpoint_policy import retained_checkpoints, staging_slot
from smacx_worker_manager import WorkerManager, WorkerManagerError


def checkpoint(i, tested=False):
    return {'checkpoint_id':f'checkpoint-{i}', 'verified':True,
            'native_save_slot':f'save-{i}',
            'verification':{'status':'restore_tested' if tested else 'save_verified'}}

history = [checkpoint(i, i==0) for i in range(8, -1, -1)]
kept = retained_checkpoints({'recovery_checkpoint':history[0], 'recovery_checkpoint_history':history[1:]})
assert [x['checkpoint_id'] for x in kept] == ['checkpoint-8','checkpoint-7','checkpoint-6','checkpoint-0']
assert staging_slot('control_recovery',kept) not in {x['native_save_slot'] for x in kept}

for failures, failure_reason in ((n, reason) for n in (1, 2) for reason in (
        'checkpoint_semantic_identity_restore_failed:peer', 'native_checkpoint_digest_mismatch',
        'checkpoint-save-digest_failed:missing', 'hermes_checkpoint_integrity_failure')):
    candidates = [checkpoint(2),checkpoint(1,True)]
    state = {'metadata':{'recovery_checkpoint':candidates[0], 'recovery_checkpoint_history':candidates[1:]}}
    events=[]
    m=object.__new__(WorkerManager);m._lifecycle_lock=threading.RLock();m.worker_image='tested-image'
    def update(_id,status,metadata):
        state['status']=status;state['metadata'].update(copy.deepcopy(metadata));return copy.deepcopy(state)
    m.control=SimpleNamespace(get_match=lambda _:copy.deepcopy(state),list_supervision_incidents=lambda **_:[],update_match_lifecycle=update)
    def restore(_id,refresh_runtime,_checkpoint):
        assert not events or events[-1]=='quarantine'
        events.append(_checkpoint['checkpoint_id'])
        if len([e for e in events if e!='quarantine']) <= failures:
            raise WorkerManagerError(failure_reason)
        return {'native_semantic_identity_restore':[{},{}]}
    m._recover_match_locked=restore
    def quarantine(*args,**kwargs):events.append('quarantine');return {'frozen':True}
    m.quarantine_match=quarantine
    try:r=m.recover_match('match')
    except WorkerManagerError as e:
        assert failures==2 and str(e).startswith('all_retained_checkpoints_failed:')
        assert state['status']=='error' and events[-1]=='quarantine'
        assert state['metadata']['recovery_checkpoint']==candidates[0]
    else:
        assert failures==1
        assert state['metadata']['recovery_checkpoint']['checkpoint_id']=='checkpoint-1'
        assert state['metadata']['recovery_checkpoint']['verification']['status']=='restore_tested'
        assert len(state['metadata']['recovery_attempts'])==1
print('PASS: bounded retention protects last restore-tested; fallback freezes each failure; all failures remain stopped')
