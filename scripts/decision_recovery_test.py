#!/usr/bin/env python3
"""Real decision enumeration/execution with a controlled bridge, never a game-effect claim."""
import json
import time
import smacx_mcp as m

key = ('match-recovery', 'session-recovery')
identity = dict(match_id=key[0], session_id=key[1], revision='r1')
m.MANAGED_ATTACHED = True
m._managed_scope_identity = lambda: (*key, 'agent-test', 'perspective-test')
m._sovereign_gameplay_gate = lambda operation: None
m._refresh_managed_world = lambda: {'ok': True}
m._compose_match_briefing = lambda snapshot: {'ok': True, 'acknowledged': True}
m._attach_working_state = m._attach_chat_attention = m._attach_briefing_status = lambda frame, *args: frame
m._attach_turn_boundary_notice = lambda frame: frame
m._pending_capability_gap = lambda: None
m._match_briefing_gate = lambda *args: None
m._turn_reconciliation_gate = lambda payload: None
m._implicit_turn_handoff = lambda *args: None
m.controller_record_campaign_action = lambda *args, **kwargs: {'ok': True}
reports = []
m.smac_report_capability_gap = lambda **kwargs: reports.append(kwargs) or {'gap': {}}
reads, writes = [], []
phase = 'turn'
fail_read = False

def bridge(operation, **arguments):
    if operation == 'semantic_snapshot':
        return {'ok': True, 'snapshot': {**identity, 'turn': 8, 'year': 2108,
            'protocol': {'phase': phase}, 'ready_unit_refs': []}}
    if operation == 'semantic_choices':
        reads.append(arguments)
        if fail_read:
            raise RuntimeError('controlled unavailable bridge')
        return {'ok': True, **identity, 'choices': [{'command': 'skip_unit', 'label': 'Skip unit'}]}
    if operation == 'semantic_command':
        writes.append(arguments)
        return {'ok': True, 'changed': True}
    raise AssertionError(operation)

m._call = bridge

def clear():
    m.FAILED_CHOICE_ATTEMPTS.clear()
    m.RUNTIME_CIRCUITS.clear()
    m.ACTION_PROGRESS.clear()
    m.DECISION_CACHE.clear()
    reads.clear(); writes.clear(); reports.clear()

def attempt(d='invented-decision', c='invented-choice'):
    # Round-trip matches the provider's JSON receipt boundary.
    return json.loads(json.dumps(m.smac_execute_choice(d, c)))

for failure in ('unknown_decision', 'expired_decision', 'consumed_decision', 'invalid_choice'):
    clear()
    d, c = 'invented-decision', 'invented-choice'
    if failure != 'unknown_decision':
        old = m.smac_decision(); d = old['decision_id']; c = old['choices'][0]['choice_id']
        if failure == 'expired_decision':m.DECISION_CACHE[d]['created_monotonic'] = time.monotonic() - m.DECISION_TTL_SECONDS - 1
        elif failure == 'consumed_decision':m.DECISION_CACHE[d]['consumed'] = True
        else:c = 'invented-choice'
    rejected = attempt(d, c)
    assert rejected['error']['code'] == failure, rejected
    assert rejected['native_action_executed'] is False and not writes
    assert rejected['execution_status'] == 'not_dispatched'
    assert rejected['failure_budget']['consecutive_failures'] == 1
    assert rejected['recovery']['attempted_action_replayed'] is False
    frame = rejected['recovery']['frame']
    assert frame['ok'] and frame['decision_id'] != d
    assert rejected['required_next']['decision_id'] == frame['decision_id']
    assert rejected['required_next']['select_choice_from'] == 'recovery.frame.choices'
    assert rejected['required_next']['do_not_reuse'] == {'decision_id': d, 'choice_id': c}
    assert 'command' not in frame['choices'][0], frame
    selected = attempt(frame['decision_id'], frame['choices'][0]['choice_id'])
    assert selected['ok'] and len(writes) == 1, selected
    assert key not in m.FAILED_CHOICE_ATTEMPTS

clear()
for i in range(4):
    rejected = attempt(f'invented-{i}', f'choice-{i}')
assert rejected['error']['code'] == 'failure_circuit_open'
assert len(reads) == 3 and not writes and len(reports) == 1
assert 'recovery' not in rejected and rejected['required_next']['stop_after']
assert attempt()['error']['code'] == 'repetition_circuit_open'
assert len(reads) == 3

clear(); fail_read = True
rejected = attempt()
assert rejected['error']['code'] == 'unknown_decision'
assert rejected['recovery']['frame']['error']['code'] == 'decision_refresh_failed'
assert rejected['required_next']['tool'] == 'smac_decision' and not writes
assert rejected['failure_budget']['consecutive_failures'] == 1
fail_read = False

clear(); phase = 'wait'
rejected = attempt()
assert rejected['recovery']['frame']['phase'] == 'wait'
assert rejected['required_next']['tool'] == 'smac_wait'
assert not reads and not writes
phase = 'turn'

# Scope changes must not leak a new session's frame into the old receipt.
clear(); original = m.smac_decision
m.smac_decision = lambda: {'ok': True, 'identity': {**identity, 'session_id': 'other'}, 'choices': ['private']}
rejected = attempt()
assert rejected['recovery']['frame'] == {'ok': False, 'error': {'code': 'recovery_scope_changed'}}
# Native rejection / text correction are not a reason to substitute a new frame.
m.smac_decision = lambda: (_ for _ in ()).throw(AssertionError('unexpected enumeration'))
for code in ('native_action_rejected', 'invalid_choice_text', 'unexpected_choice_text'):
    response = {'ok': False, 'error': {'code': code}}
    assert m._refresh_rejected_decision(response, key) == response and 'recovery' not in response
m.smac_decision = original
print('decision recovery passed: real frame/cache/guarded selection, four-failure bound, no replay, wait/error/scope safety')

# Bundled success uses actual enumeration/cache and a new guarded selection.
clear(); phase='turn'
original_bridge=m._call

def settled_bridge(operation, **arguments):
    result=original_bridge(operation, **arguments)
    if operation=='semantic_command':
        result.update(completed=True,execution={'status':'completed','native_call_attempted':True})
    return result

m._call=settled_bridge
first=m.smac_decision()
selected=attempt(first['decision_id'],first['choices'][0]['choice_id'])
assert selected['completed'] and len(writes)==1
next_frame=selected['post_action_decision']['frame']
assert next_frame['decision_id']!=first['decision_id']
second=attempt(next_frame['decision_id'],next_frame['choices'][0]['choice_id'])
assert second['ok'] and len(writes)==2
assert m.DECISION_CACHE[first['decision_id']]['consumed']
print('post-action chain passed: observed -> cached -> returned -> guarded next selection, exactly two selected mutations')

# One allowed long generation must not invalidate an otherwise guarded handle.
from smacx_provider_watchdog import PROVIDER_GENERATION_SECONDS
clear();phase='turn'
frame=m.smac_decision();d=frame['decision_id'];c=frame['choices'][0]['choice_id']
m.DECISION_CACHE[d]['created_monotonic']=time.monotonic()-PROVIDER_GENERATION_SECONDS-1
result=attempt(d,c)
assert result['ok'] and len(writes)==1
assert m.DECISION_CACHE[d]['consumed']
assert attempt(d,c)['error']['code']=='consumed_decision'
assert len(writes)==1
assert m.DECISION_TTL_SECONDS==PROVIDER_GENERATION_SECONDS+60
print('long generation handle passed: bounded generation survives, native dispatch once, consumed reuse rejected')
