#!/usr/bin/env python3
"""Durable briefing transition counts once without weakening native stall checks."""
import json
from pathlib import Path
import tempfile
import time
from smacx_store import SmacxStore, MemoryScope
from smacx_worker_manager import WorkerManager
from harness_continuation_contract_test import FakeControl, FakeWorkerManager, ContractHarnessManager

with tempfile.TemporaryDirectory() as root:
    store = SmacxStore(Path(root) / 'test.db')
    store.ensure_agent('agent-briefing', 'Briefing')
    store.create_match(match_id='match-briefing', display_name='Briefing', mode='singleplayer')
    p = store.create_perspective('match-briefing', 'agent-briefing')
    scope = MemoryScope('match-briefing', 'agent-briefing', p['perspective_id'])
    store.register_instance(instance_id='instance-briefing', scope=scope)
    store.start_session(scope, 'instance-briefing', session_id='session-briefing')
    snapshot = {'match_id': scope.match_id, 'session_id': 'session-briefing', 'revision': 'same',
                'turn': 0, 'year': 2100, 'protocol': {'phase': 'interaction'}}
    worker = WorkerManager.__new__(WorkerManager)
    worker.store = store
    worker.worker_status = lambda _: {'running': True, 'health': 'healthy', 'paused': False}
    worker._native_request = lambda *_: {'ok': True, 'snapshot': snapshot}
    before = worker.semantic_progress('instance-briefing')
    assert worker.semantic_progress('instance-briefing') == before
    store.acknowledge_match_briefing(scope, 'session-briefing', 'a' * 64)
    after = worker.semantic_progress('instance-briefing')
    assert before['meaningful_fingerprint'] != after['meaningful_fingerprint']
    assert before['native_fingerprint'] == after['native_fingerprint']
    store.acknowledge_match_briefing(scope, 'session-briefing', 'a' * 64)
    assert worker.semantic_progress('instance-briefing') == after
    assert worker.semantic_progress('instance-other')['briefing_acknowledgement'] is None
    store.start_session(scope, 'instance-briefing', session_id='session-next')
    snapshot['session_id'] = 'session-next'
    assert worker.semantic_progress('instance-briefing')['briefing_acknowledgement'] is None

    control, observed = FakeControl(), FakeWorkerManager()
    manager = ContractHarnessManager(control, observed)
    manager.observed_running = True
    observed.progress = after
    control.run['metadata'] = {
        'semantic_sample_unix': time.time() - 61,
        'semantic_telemetry_unix': time.time() - 61,
        'semantic_fingerprint': before['meaningful_fingerprint'],
        'semantic_progress_unix': time.time() - 700,
        'semantic_baseline_telemetry': {'api_calls': 0, 'output_tokens': 0},
    }
    assert manager.reconcile_once()['operator_required'] == 0
    assert not control.incidents and not observed.quarantines
    control.run['metadata'].update(semantic_sample_unix=time.time()-61,
        semantic_telemetry_unix=time.time()-61, semantic_progress_unix=time.time()-700)
    manager.telemetry = lambda _: {'telemetry': {'api_calls': 5, 'output_tokens': 5000}}
    assert manager.reconcile_once()['operator_required'] == 1
    assert observed.quarantines
print(json.dumps({'passed': True, 'acknowledgement_credits_once': True,
                  'session_and_instance_isolation': True, 'unchanged_native_still_stops': True}))
