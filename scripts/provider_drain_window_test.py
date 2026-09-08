#!/usr/bin/env python3
from smacx_provider_watchdog import provider_drain_window as check
base=dict(run_id='run',progress_since=100,stall_seconds=360)
r=dict(run_id='run',request_id='one',started_unix=400,observed_unix=400,phase='submitted')
allowed,latch=check(**base,now=465,request=r)
assert allowed and latch['hard_deadline']==640
assert check(**base,now=639,request=r,previous=latch)[0]
assert not check(**base,now=640,request=r,previous=latch)[0]
for mutation in [dict(request_id='two'),dict(run_id='other'),dict(started_unix=461),dict(started_unix=99),dict(observed_unix=999),dict(started_unix=float('nan')),dict(phase='failed'),dict(phase='closed_incomplete')]:
 assert not check(**base,now=500,request={**r,**mutation},previous=latch)[0],mutation
complete={**r,'phase':'completed','observed_unix':500}
assert check(**base,now=529,request=complete,previous=latch)[0]
assert not check(**base,now=530,request=complete,previous=latch)[0]
assert not check(**base,now=510,request=complete)[0]
assert not check(**base,now=500,request=None,previous=latch)[0]
assert not check(**base,now=450,request=r)[0]
# A new native-progress window invalidates the old latch and old request.
assert not check(run_id='run',progress_since=600,stall_seconds=360,now=965,request=r,previous=latch)[0]
print('PASS: one request, fixed hard deadline, bounded dispatch, mismatches and failures closed')

# Exercise production reconciliation, including hard-stop quarantine.
from unittest.mock import patch
from harness_continuation_contract_test import FakeControl,FakeWorkerManager,ContractHarnessManager
control=FakeControl();worker=FakeWorkerManager();manager=ContractHarnessManager(control,worker)
manager.observed_running=True
control.run['metadata']={'semantic_fingerprint':'turn-2','semantic_progress_unix':100,
 'semantic_sample_unix':400,'semantic_telemetry_unix':400,
 'semantic_baseline_telemetry':{'api_calls':0,'output_tokens':0}}
request=dict(run_id='run-continuation',request_id='one',started_unix=400,observed_unix=400,phase='submitted')
manager.telemetry=lambda _: {'telemetry':{'api_calls':3,'output_tokens':5000,'provider_request':request}}
with patch('smacx_harness_manager.time.time',return_value=465):
 assert manager.reconcile_once()['operator_required']==0
assert control.run['metadata']['semantic_progress_unix']==100
assert control.run['metadata']['provider_drain']['hard_deadline']==640
with patch('smacx_harness_manager.time.time',return_value=645):
 assert manager.reconcile_once()['operator_required']==1
assert worker.quarantines==['match-continuation']
assert control.incidents[0]['details']['provider_drain']['request_id']=='one'
print('PASS: production reconciliation preserves progress clock and quarantines at hard bound')
