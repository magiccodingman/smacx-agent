#!/usr/bin/env python3
"""Recovery cannot publish collector observations before every identity import."""
import copy
import json
from types import SimpleNamespace
from smacx_worker_manager import WorkerManager, WorkerManagerError


def exercise(mode, fail_identity=False):
    ids = ['instance-a'] if mode == 'singleplayer' else ['instance-a', 'instance-b']
    seats = [{'seat_index': i, 'instance_id': instance, 'metadata': {}}
             for i, instance in enumerate(ids)]
    checkpoint = {'verified': True, 'slot': 'control_recovery',
                  'native_semantic_identity': {i: {'capsule': i} for i in ids}}
    match = {'mode': mode, 'status': 'error', 'metadata': {'recovery_checkpoint': checkpoint}}
    events = []
    imported = set()
    def lifecycle(match_id, status, **kwargs):
        if status == 'running':
            assert imported == set(ids)
            assert [e[1] for e in events if e[0] == 'collector'] == ids
        match['status'] = status
        events.append(('lifecycle', status))
        return copy.deepcopy(match)
    manager = object.__new__(WorkerManager)
    manager.control_data_volume = 'fixture'
    manager.control = SimpleNamespace(
        get_match=lambda _: copy.deepcopy(match), list_seats=lambda _: seats,
        get_worker_spec=lambda _: {'autostart': {}, 'network': {'controller_kind': 'agent'}},
        update_worker_autostart=lambda *args: None, update_match_lifecycle=lifecycle)
    manager._stop_match_harnesses_for_restore = lambda _: events.append(('stop',))
    manager.park_match = lambda _: lifecycle('match', 'parked')
    manager._prepare_memory_restore = lambda *_: {'restored': True}
    manager._refresh_match_worker_images = lambda _: []
    def start(instance, **kwargs):
        assert kwargs.get('_defer_ready') is True
        assert not imported
        events.append(('start_without_collector', instance))
        return {'ok': True}
    manager.start_worker = start
    manager._wait_native = lambda *args, **kwargs: {'ok': True, 'snapshot': {'turn': 22}}
    def lan(match_id, **kwargs):
        assert kwargs.get('_defer_ready') is True
        for instance in ids: start(instance, _defer_ready=True)
        return {'ok': True, 'match': lifecycle(match_id, 'starting')}
    manager.start_lan_match = lan
    def native(instance, operation, **kwargs):
        assert operation == 'semantic_identity_state' and kwargs['action'] == 'import'
        assert not any(e[0] == 'collector' for e in events)
        if fail_identity and instance == ids[-1]:
            return {'ok': False, 'error': 'injected_identity_failure'}
        imported.add(instance)
        events.append(('import', instance))
        return {'ok': True, 'restored': True, 'handle_count': 3}
    manager._native_request = native
    def collector(instance):
        assert imported == set(ids), 'collector observed temporary native handles'
        assert match['status'] != 'running', 'campaign ready before collector startup'
        events.append(('collector', instance))
        return {'instance_id': instance, 'ok': True}
    manager.start_mcp_sidecar = collector
    try:
        result = manager._recover_match_locked('match', refresh_runtime=True)
    except WorkerManagerError:
        assert fail_identity
        assert not any(e[0] == 'collector' for e in events)
        assert match['status'] != 'running'
    else:
        assert not fail_identity and result['match']['status'] == 'running'
        assert len(result['restored_mcp_endpoints']) == len(ids)
    return {'mode': mode, 'identity_failure': fail_identity, 'passed': True}


if __name__ == '__main__':
    print(json.dumps({'cases': [exercise(mode, failure)
        for mode in ['singleplayer', 'lan'] for failure in [False, True]],
        'classification': 'actual recovery orchestration with controlled native/collector adapters'}))
