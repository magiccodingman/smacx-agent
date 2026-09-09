"""Locally stable LAN replicas cannot publish a mutually inconsistent checkpoint."""
import copy
from unittest.mock import patch
from checkpoint_slot_atomicity_test import manager, state, saves
from smacx_checkpoint_policy import identity_hash, validate_peer_capsules
from smacx_worker_manager import WorkerManagerError

manager.control.list_seats=lambda _: [{'instance_id':'instance-test'},{'instance_id':'instance-peer'}]
previous=copy.deepcopy(state['metadata']['recovery_checkpoint']);save_count=len(saves)
def capsule(faction, x):
    fields=[2,faction,1,1,1,x,4,0,0,0,10]
    return {'ok':True,'schema':'smacx.private-vehicle-identity.v1','turn':2,'faction_id':faction,
            'native_validation_fields':fields,'native_validation_hash':identity_hash(fields),
            'semantic_vehicle_handles':[1],'next_semantic_vehicle_handle':2}

def native(instance, operation, **kwargs):
    if operation=='semantic_snapshot':
        return {'snapshot':{'turn':2,'year':2102,'revision':'stable', 'protocol':{'phase':'turn'}}}
    if operation=='semantic_identity_state':return capsule(1,3) if instance=='instance-test' else capsule(2,5)
    raise AssertionError('A divergent pair reached native save/choices')
manager._native_request=native
with patch('smacx_worker_manager.time.sleep'):
    try:manager.checkpoint_match('match-test')
    except WorkerManagerError as e:assert str(e)=='checkpoint_peers_not_synchronized:native_vehicle_layout'
    else:raise AssertionError('divergent peer checkpoint advertised')
assert state['metadata']['recovery_checkpoint']==previous and len(saves)==save_count
validate_peer_capsules({'host':capsule(1,3),'peer':capsule(2,3)})
bad=capsule(2,3);bad['native_validation_hash']='0'
try:validate_peer_capsules({'peer':bad})
except ValueError:pass
else:raise AssertionError('forged diagnostic fields accepted')
print('PASS: local quiescence is insufficient; divergent peer rejected before save; perspective-only difference accepted')

# A native save cannot be paired using only non-host replicas' evidence.
manager.control.list_seats=lambda _: [{'instance_id':'instance-test','metadata':{'delegation_status':'active'}},{'instance_id':'instance-peer'}]
with patch('smacx_worker_manager.time.sleep'):
    try:manager.checkpoint_match('match-test')
    except WorkerManagerError as e:assert str(e)=='checkpoint_waiting_for_quiescence:host_identity_unavailable'
    else:raise AssertionError('checkpoint accepted without saved host evidence')
