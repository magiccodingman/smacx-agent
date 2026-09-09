#!/usr/bin/env python3
"""Recovery cannot publish collector observations before every identity import."""
import copy
import json
from types import SimpleNamespace
from smacx_worker_manager import WorkerManager, WorkerManagerError


def exercise(mode, fail_identity=False, staged_slot=False, fail_save=False):
    ids = ['instance-a'] if mode == 'singleplayer' else ['instance-a', 'instance-b']
    seats = [{'seat_index': i, 'instance_id': instance, 'metadata': {}}
             for i, instance in enumerate(ids)]
    checkpoint = {'verified': True, 'slot': 'control_recovery',
                  'native_save_sha256':'a'*64, 'native_save_bytes':42,
                  'native_semantic_identity': {i: {'capsule': i} for i in ids}}
    if staged_slot:
        checkpoint['native_save_slot'] = 'ckpt_test_a'
    expected_slot = checkpoint.get('native_save_slot', checkpoint['slot'])
    match = {'mode': mode, 'status': 'error', 'metadata': {'recovery_checkpoint': checkpoint}}
    events = []
    imported = set()
    def lifecycle(match_id, status, **kwargs):
        if status == 'running':
            assert imported == set(ids)
            assert [e[1] for e in events if e[0] == 'collector'] == ids
            assert kwargs['metadata']['last_recovered_slot'] == 'control_recovery'
        match['status'] = status
        events.append(('lifecycle', status))
        return copy.deepcopy(match)
    def clear_incidents(_match_id, *, kinds):
        assert kinds == ('harness_clean_yield_no_progress',)
        assert match['status'] == 'running' and imported == set(ids)
        events.append(('incident_recovered',))
        return [{'incident_id':'incident-clean', 'status':'recovered'}]
    def autostart(_instance, value):
        assert value['startup_save'] == expected_slot
    manager = object.__new__(WorkerManager)
    manager.control_data_volume = 'fixture'
    manager.control = SimpleNamespace(
        get_match=lambda _: copy.deepcopy(match), list_seats=lambda _: seats,
        get_worker_spec=lambda _: {'autostart': {}, 'network': {'controller_kind': 'agent'}},
        update_worker_autostart=autostart, update_match_lifecycle=lifecycle,
        recover_supervision_incidents=clear_incidents)
    manager._stop_match_harnesses_for_restore = lambda _: events.append(('stop',))
    manager.park_match = lambda _: lifecycle('match', 'parked')
    def digest(instance, slot):
        assert instance == ids[0] and slot == expected_slot
        events.append(('native_digest_verified', not fail_save))
        return {'sha256':('b' if fail_save else 'a')*64, 'bytes':42}
    manager._checkpoint_save_digest = digest
    def memory(*_args):
        assert ('native_digest_verified', True) in events
        events.append(('memory_restore',))
        return {'restored':True}
    manager._prepare_memory_restore = memory
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
        assert kwargs['resume_slot'] == expected_slot
        for instance in ids: start(instance, _defer_ready=True)
        return {'ok': True, 'match': lifecycle(match_id, 'starting')}
    manager.start_lan_match = lan
    def native(instance, operation, **kwargs):
        assert operation == 'semantic_identity_state'
        if kwargs['action'] == 'export':
            events.append(('private_diagnostic', instance))
            return {'ok': True, 'native_validation_hash': 'different'}
        assert kwargs['action'] == 'import'
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
        assert fail_identity or fail_save
        assert not any(e[0] == 'collector' for e in events)
        assert match['status'] != 'running'
        assert not any(e[0] == 'incident_recovered' for e in events)
        if fail_save:
            assert not any(e[0] in ('memory_restore','start_without_collector') for e in events)
    else:
        assert not fail_identity and not fail_save and result['match']['status'] == 'running'
        assert len(result['restored_mcp_endpoints']) == len(ids)
        assert result['recovered_incidents'][0]['status'] == 'recovered'
    return {'mode': mode, 'identity_failure': fail_identity, 'staged_slot':staged_slot,
            'save_digest_failure':fail_save, 'passed': True}


if __name__ == '__main__':
    print(json.dumps({'cases': [exercise(mode, failure, staged, save_failure)
        for mode in ['singleplayer', 'lan'] for failure,save_failure in [(False,False),(True,False),(False,True)]
        for staged in [False, True]],
        'classification': 'actual recovery orchestration with controlled native/collector adapters'}))
