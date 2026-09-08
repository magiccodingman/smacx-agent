#!/usr/bin/env python3
import json
from types import SimpleNamespace
from unittest.mock import Mock
from smacx_operations import OperationsManager
from smacx_worker_manager import WorkerManagerError
m=OperationsManager.__new__(OperationsManager)
m.ingest_capability_gaps_once=Mock(return_value={})
m.harness_manager=None
m.control=SimpleNamespace(list_worker_specs=lambda:[{'instance_id':'instance-test','match_id':'match-test','desired_status':'running','updated_unix':0}],list_supervision_incidents=Mock(return_value=[{'incident_kind':'operator_pause'}]),get_match=Mock(return_value={'metadata':{'recovery_checkpoint':{'verified':True}}}))
m.worker_manager=SimpleNamespace(worker_status=Mock(return_value={'running':False}),recover_match=Mock(side_effect=WorkerManagerError('operator_pause_blocks_automatic_recovery')))
m._capture_worker_loss=Mock();m._incident=Mock()
assert m._reconcile_once()['operator_required']==0
m.worker_manager.worker_status.assert_not_called();m._incident.assert_not_called()
m.control.list_supervision_incidents.return_value=[]
assert m._reconcile_once()['operator_required']==0
m.worker_manager.recover_match.assert_called_once();m._incident.assert_not_called()
m.worker_manager.recover_match.side_effect=WorkerManagerError('real_failure')
assert m._reconcile_once()['operator_required']==1
assert m._incident.call_args.args[1]=='supervisor_error'
print(json.dumps({'pass':True,'paused_worker_untouched':True,'expected_guard_no_incident':True,'real_failure_retained':True}))
