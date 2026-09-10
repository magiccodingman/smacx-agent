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
# The next request can start after the native effect but before its sample.
between={**r,'started_unix':95,'observed_unix':96}
allowed, sampled_latch=check(**base,now=465,request=between,progress_observed_after=90)
assert allowed and sampled_latch['hard_deadline']==640
assert not check(**base,now=640,request=between,progress_observed_after=90,previous=sampled_latch)[0]
assert not check(**base,now=465,request={**between,'started_unix':89},progress_observed_after=90)[0]
assert not check(**base,now=465,request=between,progress_observed_after=float('nan'))[0]
assert not check(**base,now=465,request={**between,'request_id':'next'},progress_observed_after=90,previous=sampled_latch)[0]
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

control=FakeControl();worker=FakeWorkerManager();manager=ContractHarnessManager(control,worker)
manager.observed_running=True
control.run['metadata']={'semantic_fingerprint':'turn-1','semantic_progress_unix':50,
 'semantic_sample_unix':90}
request={**request,'started_unix':95,'observed_unix':96}
manager.telemetry=lambda _: {'telemetry':{'api_calls':1,'output_tokens':100,'provider_request':request}}
with patch('smacx_harness_manager.time.time',return_value=125):
 assert manager.reconcile_once()['operator_required']==0
assert control.run['metadata']['semantic_progress_observed_after_unix']==90
manager.telemetry=lambda _: {'telemetry':{'api_calls':3,'output_tokens':5000,'provider_request':request}}
with patch('smacx_harness_manager.time.time',return_value=490):
 assert manager.reconcile_once()['operator_required']==0
assert control.run['metadata']['provider_drain']['hard_deadline']==665
print('PASS: sampled native effect admits an intervening request without extending its deadline')

stream={**r,'phase':'streaming','last_content_unix':650,'observed_unix':650}
allowed,live=check(**base,now=660,request=stream,previous=latch)
assert allowed and live['hard_deadline']==1600
assert not check(**base,now=770,request=stream,previous=live)[0]
for tick in (800,1000,1400,1599):
 stream.update(last_content_unix=tick,observed_unix=tick)
 assert check(**base,now=tick,request=stream,previous=live)[0]
assert not check(**base,now=1600,request=stream,previous=live)[0]
assert not check(**base,now=1000,request={**stream,'request_id':'another'},previous=live)[0]
assert not check(**base,now=700,request={**r,'phase':'headers'},previous=latch)[0]
complete={**stream,'phase':'completed','observed_unix':1599}
assert check(**base,now=1599,request=complete,previous=live)[0]
print('PASS: active streaming extends one request; silence, replacement and absolute bound stop it')
control=FakeControl();worker=FakeWorkerManager();manager=ContractHarnessManager(control,worker)
manager.observed_running=True
control.run['metadata']={'semantic_fingerprint':'turn-2','semantic_progress_unix':100,
 'semantic_sample_unix':400,'semantic_telemetry_unix':400,
 'semantic_baseline_telemetry':{'api_calls':0,'output_tokens':0}}
request=dict(run_id='run-continuation',request_id='stream',started_unix=400,observed_unix=700,last_content_unix=700,phase='streaming')
manager.telemetry=lambda _: {'telemetry':{'api_calls':3,'output_tokens':5000,'provider_request':request}}
with patch('smacx_harness_manager.time.time',return_value=710):assert manager.reconcile_once()['operator_required']==0
assert control.run['metadata']['semantic_progress_unix']==100
request.update(observed_unix=1600,last_content_unix=1600)
with patch('smacx_harness_manager.time.time',return_value=1600):assert manager.reconcile_once()['operator_required']==1
assert control.incidents[0]['details']['why_blocked']=='provider_generation_budget_exceeded'
print('PASS: production streaming survives old deadline and quarantines at generation bound')
