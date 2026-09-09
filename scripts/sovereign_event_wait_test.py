"""Provider-free multiplayer waiting, durable wake cursors, and liveness."""
import copy
import json
import time
from unittest.mock import patch
from harness_continuation_contract_test import FakeControl, FakeWorkerManager, ContractHarnessManager, marker
from smacx_harness_manager import HarnessManager

class WaitingManager(ContractHarnessManager):
    def __init__(self, control, worker):
        super().__init__(control, worker)
        self.chat_cursor = 0
        self.journal_events = []
    def _chat_wake_cursor(self, run): return self.chat_cursor
    def _cancel_sovereign_after_process_stop(self, *args): pass
    def _journal_run_event(self, run, kind, payload, **kwargs):
        self.journal_events.append((kind, payload))

control, worker = FakeControl(), FakeWorkerManager()
manager = WaitingManager(control, worker)
worker.progress.update(phase='wait', interaction_kind='waiting_for_turn', faction_id=2, current_faction_id=1)
for _ in range(12): manager.reconcile_once()
assert manager.start_count == 0 and not control.incidents
assert control.run['metadata']['sleep']['reason'] == 'waiting_for_turn'
assert len([e for e in manager.journal_events if e[0] == 'agent.sleeping']) == 1
# Recreated manager preserves waiting across reconciliation/process restart.
manager = WaitingManager(control, worker)
manager.reconcile_once()
assert manager.start_count == 0
manager.chat_cursor = 10
manager.reconcile_once()
assert manager.start_count == 1 and control.run['metadata']['wake_reason'] == 'new_chat'
# Same unacknowledged message does not repeatedly restart the model.
for _ in range(6): manager.reconcile_once()
assert manager.start_count == 1
manager.chat_cursor = 11
manager.reconcile_once()
assert manager.start_count == 2
manager.reconcile_once()
worker.progress.update(phase='interaction', interaction_kind='popup')
manager.reconcile_once()
assert manager.start_count == 3 and control.run['metadata']['sleep'] is None
worker.progress.update(phase='wait', interaction_kind='waiting_for_turn')
manager.reconcile_once()
worker.progress['session_id'] = 'session-restored'
manager.reconcile_once()
assert manager.start_count == 4 and control.run['metadata']['wake_reason'] == 'session_changed'

# Match-wide deadline: native progress from an active peer renews the marker,
# then both waiting on a managed owner without change trips exactly at deadline.
control, worker = FakeControl(), FakeWorkerManager()
manager = WaitingManager(control, worker)
peer = copy.deepcopy(control.run)
peer.update(run_id='run-peer', instance_id='instance-peer')
control.list_harness_runs = lambda: [copy.deepcopy(control.run), peer]
own = dict(marker('r2',2), phase='wait', interaction_kind='waiting_for_turn',
           faction_id=2, current_faction_id=1, native_fingerprint='own')
peer_progress = dict(marker('p1',2), faction_id=1, current_faction_id=1, native_fingerprint='p1')
worker.semantic_progress = lambda instance: dict(peer_progress if instance == 'instance-peer' else own)
with patch('smacx_harness_manager.time.time', return_value=1000):
    assert not manager._waiting_match_stalled(control.run, own)
with patch('smacx_harness_manager.time.time', return_value=1500):
    # Acting peer owns its original provider-aware watchdog, not our sleep timer.
    assert not manager._waiting_match_stalled(control.run, own)
peer_progress.update(native_fingerprint='p2', phase='wait', interaction_kind='waiting_for_turn')
with patch('smacx_harness_manager.time.time', return_value=1600):
    assert not manager._waiting_match_stalled(control.run, own)
with patch('smacx_harness_manager.time.time', return_value=1959):
    assert not manager._waiting_match_stalled(control.run, own)
with patch('smacx_harness_manager.time.time', return_value=1960):
    assert manager._waiting_match_stalled(control.run, own)
assert control.incidents[-1]['kind'] == 'managed_turn_wait_stalled'
assert worker.quarantines == ['match-continuation']
print(json.dumps({'passed':True,'sleep_without_provider_restarts':True,
 'chat_cursor_deduplicated':True,'durable_restart':True,'interaction_and_session_wake':True,
 'peer_progress_and_real_stall':True}))

# A model ignoring WAITING is put to sleep rather than stopping its peer.
control, worker = FakeControl(), FakeWorkerManager()
manager = WaitingManager(control, worker)
manager.observed_running = True
worker.progress.update(phase='wait', interaction_kind='waiting_for_turn', faction_id=2, current_faction_id=1)
control.run['metadata'] = {'semantic_sample_unix':time.time()-61,
 'semantic_telemetry_unix':time.time()-61,'semantic_fingerprint':'turn-2',
 'semantic_progress_unix':time.time()-400,
 'semantic_baseline_telemetry':{'api_calls':0,'output_tokens':0}}
control.get_harness_run = lambda _: copy.deepcopy(control.run)
def stop(_):
    manager.observed_running = False
    return control.update_harness_run(_, status='stopped',desired_status='stopped')
manager.stop_run = stop
manager.reconcile_once()
assert not manager.observed_running and control.run['metadata']['sleep']
assert not control.incidents and not worker.quarantines
print('PASS: active polling is bounded by sleep without quarantining the other player')

# Chat arriving while a runaway waiting episode is stopped must get a new
# communication invocation, not be marked seen and stranded in sleep.
control, worker = FakeControl(), FakeWorkerManager()
manager = WaitingManager(control, worker)
manager.observed_running = True
manager.chat_cursor = 42
worker.progress.update(phase='wait', interaction_kind='waiting_for_turn', faction_id=2, current_faction_id=1)
control.run['metadata'] = {'semantic_sample_unix':time.time()-61,
 'semantic_telemetry_unix':time.time()-61,'semantic_fingerprint':'turn-2',
 'semantic_progress_unix':time.time()-400,
 'semantic_baseline_telemetry':{'api_calls':0,'output_tokens':0}}
control.get_harness_run = lambda _: copy.deepcopy(control.run)
def stop_with_chat(_):
    manager.observed_running = False
    return control.update_harness_run(_, status='stopped',desired_status='stopped')
manager.stop_run = stop_with_chat
manager.reconcile_once()
assert manager.start_count == 1 and control.run['metadata']['wake_reason']=='new_chat'
print('PASS: new chat survives forced-suspension race')

# A native deferred end-turn guard precedes waiting_for_turn classification.
# Foreign-owner engine wait must sleep too, then wake when ownership returns.
control, worker = FakeControl(), FakeWorkerManager()
manager = WaitingManager(control, worker)
worker.progress.update(phase='wait', interaction_kind='waiting_for_engine', faction_id=1, current_faction_id=2)
for _ in range(12): manager.reconcile_once()
assert manager.start_count == 0 and not control.incidents
assert control.run['metadata']['sleep']
worker.progress.update(current_faction_id=1)
manager.reconcile_once()
assert manager.start_count == 1 and not control.run['metadata']['sleep']
from smacx_turn_wait import progress_foreign_turn_wait
for owner in (None, 0, -1, True, '2', 8, 1):
    worker.progress['current_faction_id'] = owner
    assert not progress_foreign_turn_wait(worker.progress), owner
print('PASS: foreign-owner engine wait sleeps; own/unknown engine wait retains watchdog')
