#!/usr/bin/env python3
"""An operator pause wins over automatic recovery queued behind its lock."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock
from smacx_worker_manager import WorkerManager, WorkerManagerError

manager = WorkerManager.__new__(WorkerManager)
manager._lifecycle_lock = threading.RLock()
pauses = []
checkpoint = {'checkpoint_id':'checkpoint-test','verified':True}
manager.worker_image='test'
manager.control = SimpleNamespace(list_supervision_incidents=lambda **kwargs: list(pauses),
    get_match=lambda _: {'metadata': {'recovery_checkpoint': checkpoint}},
    update_match_lifecycle=lambda *a,**k:{})
manager._recover_match_locked = Mock(return_value={'ok': True})
started = threading.Event()
errors = []
def queued():
    started.set()
    try: manager.recover_match('match-guard')
    except WorkerManagerError as exc: errors.append(str(exc))
with manager._lifecycle_lock:
    thread = threading.Thread(target=queued)
    thread.start()
    assert started.wait(2)
    pauses.append({'incident_id':'incident-pause','incident_kind':'operator_pause'})
thread.join(3)
assert not thread.is_alive()
assert errors == ['operator_pause_blocks_automatic_recovery'], errors
manager._recover_match_locked.assert_not_called()
try: manager.recover_match('match-guard',operator_pause_incident_id='incident-wrong')
except WorkerManagerError: pass
else: raise AssertionError('wrong incident bypassed pause')
manager._recover_match_locked.assert_not_called()
pauses.append({'incident_id':'incident-peer-pause','incident_kind':'operator_pause'})
assert manager.recover_match('match-guard',refresh_runtime=True,
    operator_pause_incident_id='incident-pause')['ok']
manager._recover_match_locked.assert_called_once_with('match-guard',refresh_runtime=True,_checkpoint=checkpoint)
pauses.clear()
assert manager.recover_match('match-guard')['ok']
print(json.dumps({'pass':True,'queued_automatic_recovery_blocked':True,
                  'wrong_incident_no_effect':True,'exact_operator_resume_allowed':True,
                  'ordinary_unpaused_recovery_allowed':True}))
