#!/usr/bin/env python3
"""Two-client saved-state demand replay. Requires the private turn-7 fixture.

Only creates isolated workers; never imports the production control database.
Tests actual native dialogue processing and peer synchronization, not sovereign
strategy. The checked save is intentionally not distributed with the source.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager

SAVE_SHA256 = '2a9b12d39a13e8a51698254d2ad9743f962b43cc5b7bc04f2e6205e8bbc6371f'


def exercise(manager, ids, response):
    def call(i, op, **args):
        return manager._native_request(i, op, timeout=25, **args)

    def command(i, frame, name, **args):
        return call(i, 'semantic_command', command=name, match_id=frame['match_id'],
                    session_id=frame['session_id'], expected_revision=frame['revision'], **args)

    def energy(state):
        return {f['id']: f['energy'] for f in state['factions']}

    moves = 0
    target = None
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and target is None:
        for n, instance in enumerate(ids):
            s = call(instance, 'semantic_snapshot')['snapshot']
            kind = s['interaction']['kind']
            label = s['interaction'].get('popup_label', '')
            if label.startswith('DEMANDBRIBE'):
                assert n == 0
                target = call(instance, 'semantic_choices', kind='interaction')
                break
            if kind in ('waiting_for_engine', 'waiting_for_turn'):
                continue
            f = call(instance, 'semantic_choices', kind='interaction' if kind != 'turn' else 'game_management')
            if kind == 'turn' and n == 0 and moves < 2:
                unit, tile = [(0, 1235), (1, 1275)][moves]
                result = command(instance, f, 'move_unit', unit_id=unit, target_tile_id=tile)
                moves += 1
            else:
                row = next((r for name in ('acknowledge_popup', 'continue_diplomacy', 'respond_to_contact')
                            for r in f.get('choices', []) if r.get('command') == name), None)
                assert row, (label, f)
                result = command(instance, f, row['command'], **({'response': 'accept'}
                                 if row['command'] == 'respond_to_contact' else {}))
            assert result.get('ok'), result
        time.sleep(.2)
    assert target is not None, 'saved contact did not reach the demand'
    terms = next(r for r in target['choices'] if r.get('offer_type') == 'energy_demand')
    assert terms['requester_faction_id'] == 4
    assert (terms['full_amount'], terms['counter_amount']) == (50, 20)
    before = [call(i, 'test_network_sync_status') for i in ids]
    assert all(s.get('ok') for s in before), before
    assert energy(before[0]) == energy(before[1])
    invalid = command(ids[0], target, 'respond_to_diplomatic_offer', response='invalid')
    assert not invalid.get('ok')
    assert call(ids[0], 'semantic_choices', kind='interaction')['revision'] == target['revision']
    receipt = command(ids[0], target, 'respond_to_diplomatic_offer', response=response)
    assert receipt.get('ok') and receipt['energy_change_verified'] is False, receipt
    assert receipt['relationship_change_verified'] is False
    duplicate = command(ids[0], target, 'respond_to_diplomatic_offer', response=response)
    assert not duplicate.get('ok'), duplicate
    labels = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        s = call(ids[0], 'semantic_snapshot')['snapshot']
        kind = s['interaction']['kind']
        label = s['interaction'].get('popup_label', '')
        if kind == 'turn':
            break
        if kind == 'waiting_for_engine':
            time.sleep(.1)
            continue
        labels.append(label)
        f = call(ids[0], 'semantic_choices', kind='interaction')
        if label == 'DIPLO':
            result = command(ids[0], f, 'choose_diplomacy_option', option='finish')
        elif any(r.get('command') == 'acknowledge_popup' for r in f.get('choices', [])):
            result = command(ids[0], f, 'acknowledge_popup')
        else:
            raise AssertionError(('unreviewed follow-on', label, f))
        if result.get('error', {}).get('code') == 'stale_state':
            # Re-observe the native continuation; never replay the old frame.
            continue
        assert result.get('ok'), (label, result)
        time.sleep(.2)
    else:
        raise AssertionError('demand conversation did not close')
    expected = energy(before[0])
    price = {'accept': 50, 'counter': 20, 'reject': 0}[response]
    expected[1] -= price
    expected[4] += price
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        after = [call(i, 'test_network_sync_status') for i in ids]
        if all(energy(s) == expected for s in after) and after[0]['factions'] == after[1]['factions']:
            break
        time.sleep(.2)
    else:
        raise AssertionError(('energy did not converge', expected, after))
    relations = {f['id']: f['diplomatic_status'] for f in after[0]['factions']}
    assert bool(relations[1][4] & 0x10) == (response == 'reject')
    assert bool(relations[4][1] & 0x10) == (response == 'reject')
    # No unrelated unit/base mutation was required to answer the demand.
    for key in ('vehicles', 'bases'):
        assert after[0][key] == after[1][key] == before[0][key] == before[1][key], key
    assert call(ids[1], 'semantic_snapshot')['snapshot']['interaction']['kind'] == 'waiting_for_turn'
    return {'response': response, 'label': target['popup_label'], 'follow_on': labels,
            'energy_before': energy(before[0]), 'energy_after': expected,
            'both_clients_converged': True, 'diplomatic_status': [f['diplomatic_status'] for f in after[0]['factions']], 'unrelated_units_and_bases_unchanged': True,
            'invalid_and_duplicate_rejected': True, 'receipt': receipt}


def main():
    saved = Path(os.environ['SMACX_DEMAND_TEST_SAVE']).read_bytes()
    assert hashlib.sha256(saved).hexdigest() == SAVE_SHA256
    os.environ['SMACX_AGENT_TEST_MODE'] = '1'
    os.environ['SMACX_AGENT_TEST_LAN_HOST'] = '1'
    docker = DockerClient()
    with tempfile.TemporaryDirectory(prefix='smacx-energy-demand-') as tmp:
        root = Path(tmp)
        control = ControlPlane(SmacxStore(root / 'state.sqlite3'), root / 'secrets')
        manager = WorkerManager(control, docker, worker_image=os.environ['SMACX_TEST_WORKER_IMAGE'])
        source = manager.validate_game_source(os.environ['SMACX_TEST_GAME_SOURCE'], display_name='Isolated energy demand')
        runtime = manager.ensure_bundled_runtime()
        agents = ['agent-demand-host', 'agent-demand-peer']
        for agent in agents:
            control.store.ensure_agent(agent, agent)
        created = control.create_lan_match('Isolated energy demand regression', agents)
        mid = created['match']['match_id']
        workers = []
        try:
            for n, seat in enumerate(created['seats']):
                control.update_lan_seat(mid, n, faction_id=n + 1)
                worker = manager.provision_worker(MemoryScope(mid, seat['agent_id'], seat['perspective_id']),
                            source['game_source_id'], runtime['runtime_id'], autostart={'enabled': False}, view_enabled=False)
                workers.append(worker)
                seed = ('from pathlib import Path;import sys,os;p=Path(sys.argv[1]);'
                        'p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(sys.stdin.buffer.read());'
                        '[os.chown(x,10001,10001) for x in [p,*list(p.parents)[:5]]]')
                subprocess.run(['docker', 'run', '--rm', '-i', '--user', '0', '--entrypoint', 'python3',
                                '-v', worker['data_volume'] + ':/data', os.environ['SMACX_TEST_CONTROL_IMAGE'],
                                '-c', seed, '/data/game/saves/agent/' + mid + '/replay.sav'], input=saved, check=True)
            results = []
            for response in ('counter', 'accept', 'reject'):
                if results:
                    manager.park_match(mid)
                manager.start_lan_match(mid, session_name='Energy demand regression', resume_slot='replay', _defer_ready=True)
                result = exercise(manager, [w['instance_id'] for w in workers], response)
                results.append(result)
                print(json.dumps({'case_passed': result}), flush=True)
            print(json.dumps({'passed': True, 'save_sha256': SAVE_SHA256, 'cases': results}), flush=True)
        finally:
            for w in reversed(workers):
                manager.park_worker(w['instance_id'])
                for name, purpose in ((w['data_volume'], 'worker-data'), (w['network']['secret_volume'], 'worker-secret')):
                    docker.require_owned(docker.inspect_volume(name), manager.installation_id, purpose=purpose)
                    docker.remove_volume(name)


if __name__ == '__main__':
    main()
