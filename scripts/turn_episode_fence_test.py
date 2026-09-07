#!/usr/bin/env python3
"""Recorded turn-122/123 boundary regression; adapters, not native mechanics."""
from types import SimpleNamespace
from unittest.mock import patch
import smacx_mcp as m

identity = {"match_id": "match-fence", "session_id": "session-fence", "revision": "r123"}
snapshot = {**identity, "turn": 123, "year": 2223, "protocol": {"phase": "turn"}}
m.TURN_HANDOFF_STATE.clear()
m._track_observed_turn(identity, 122)
# The observed marker can move without a handoff being delivered.
m._track_observed_turn(identity, 123)
with patch.object(m, "_semantic_turn_handoff_gc"):
    boundary = m._implicit_turn_handoff(snapshot, identity)
assert boundary['turn_handoff_required']['completed_turns'] == '122', boundary
assert m._implicit_turn_handoff(snapshot, identity) is None

active = {"episode_id": "episode-122", "episode_mode": "gameplay"}
attention = SimpleNamespace(sovereign_state=lambda: active)
m.TURN_HANDOFF_STATE.clear()
m.RUNTIME_EPISODE_TURNS.clear()
m.RUNTIME_EPISODE_TURNS['episode-122'] = {'initial_turn': 122}
with patch.object(m, 'MANAGED_ATTACHED', True), \
     patch.object(m, '_runtime_services', return_value=(None, attention)), \
     patch.object(m, '_refresh_managed_world', return_value={'ok': True}), \
     patch.object(m, '_semantic_turn_handoff_gc'), \
     patch.object(m, '_call', return_value={'ok': True, 'snapshot': snapshot}) as native:
    result = m._smac_choices_once('production', base_ref='base-1')
    assert result['turn_handoff_required']['completed_turns'] == '122', result
    assert [c.args[0] for c in native.call_args_list] == ['semantic_snapshot']
    # Ignoring the handoff cannot get either new choices or a native execution.
    for response in (m._smac_choices_once('research'), m.smac_execute_choice('old', 'old')):
        assert response['required_next']['stop_after'] and response['native_action_executed'] is False
    assert native.call_count == 1
    active['episode_id'] = 'episode-123'
    m.RUNTIME_EPISODE_TURNS['episode-123'] = {'initial_turn': 123}
    assert m._sovereign_gameplay_gate('test') is None
    assert m._implicit_turn_handoff(snapshot, identity) is None

# Unknown post-action observation remains unknown, including in public receipts.
receipt = {}
m._attach_turn_handoff(receipt, {'command': 'hurry_production'}, {'turn': 122}, None)
assert receipt['turn_provenance']['selected_turn'] == 122
assert receipt['turn_provenance']['observed_after_turn'] is None
assert 'turn_handoff_required' not in receipt
print('PASS: recorded missed boundary, choices/execute episode fence, next episode, unknown receipt turn')

# The private stale-rebase branch cannot dispatch the same semantic action in
# a new turn, even if the action and its native selector remain identical.
for fresh_turn in (122, 123):
    m.TURN_HANDOFF_STATE.clear()
    m.ACTION_PROGRESS.clear()
    m._track_observed_turn(identity, 122)
    selected_identity = {**identity, 'revision': 'r122'}
    raw = {'command': 'hold_unit', 'unit_id': 7}
    decision, choices = m._cache_decision_choices(selected_identity, [raw],
        choice_kind='unit_actions', choice_arguments={'unit_id': 7}, turn=122, year=2222, phase='turn',
        snapshot={'turn': 122, 'year': 2222, 'protocol': {'phase': 'turn'}})
    def bridge(operation, **kwargs):
        if operation == 'semantic_choices':
            return {'ok': True, **identity, 'choices': [raw]}
        if operation == 'semantic_snapshot':
            return {'ok': True, 'snapshot': {**snapshot, 'turn': fresh_turn}}
        raise AssertionError(operation)
    with patch.object(m, '_call', side_effect=bridge), \
         patch.object(m, '_semantic_turn_handoff_gc'), \
         patch.object(m, 'controller_record_campaign_action', return_value={'ok': True}), \
         patch.object(m, 'smac_command', autospec=True, side_effect=[
             {'ok': False, 'error': {'code': 'stale_state'}}, {'ok': True}]) as command:
        result = m._execute_choice_once(decision, choices[0]['choice_id'])
    if fresh_turn == 123:
        assert command.call_count == 1, result
        assert result['turn_handoff_required']['completed_turns'] == '122'
        assert result['native_action_executed'] is False
    else:
        assert command.call_count == 2 and result['guard_revalidated'], result
        assert result['turn_provenance']['observed_after_turn'] == 122
print('PASS: cross-turn rebase withheld; equivalent guarded same-turn rebase still works')

# All choice families must retain the native turn in the private cache, not
# merely show it as text in the public frame.
def enumerate_native(operation, **kwargs):
    if operation == 'semantic_snapshot':
        return {'ok': True, 'snapshot': snapshot}
    if operation == 'semantic_choices':
        return {'ok': True, **identity, 'choices': [{'command': 'set_research_priority', 'priority': 1}]}
    raise AssertionError(operation)
m.TURN_HANDOFF_STATE.clear()
with patch.object(m, '_call', side_effect=enumerate_native), \
     patch.object(m, '_resolve_managed_selectors', return_value=({}, {})), \
     patch.object(m.CHOICE_PREPARATIONS, 'begin', return_value=[]):
    frame = m._smac_choices_once('research')
assert frame['turn'] == 123 and frame['year'] == 2223, frame
assert m.DECISION_CACHE[frame['decision_id']]['turn'] == 123
assert m.DECISION_CACHE[frame['decision_id']]['phase'] == 'turn'
print('PASS: actual choices facade retains turn/year/phase in the execution cache')

m.TURN_HANDOFF_STATE.clear()
m._track_observed_turn(identity, 122)
with patch.object(m, 'MANAGED_ATTACHED', True), \
     patch.object(m, '_runtime_services', side_effect=RuntimeError('authority temporarily unavailable')):
    try:
        m._attach_turn_handoff({}, {'command': 'automate_former'},
            {'identity': identity, 'turn': 122}, snapshot)
        raise AssertionError('authority failure was hidden')
    except RuntimeError:
        pass
assert m.TURN_HANDOFF_STATE[('match-fence', 'session-fence')]['handed_off_through'] == 121
active['episode_id'] = 'episode-122-retry'
m.RUNTIME_EPISODE_TURNS[active['episode_id']] = {'initial_turn': 122}
with patch.object(m, 'MANAGED_ATTACHED', True), \
     patch.object(m, '_runtime_services', return_value=(None, attention)), \
     patch.object(m, '_semantic_turn_handoff_gc'):
    retried = m._implicit_turn_handoff(snapshot, identity)
assert retried['turn_handoff_required']['completed_turns'] == '122'
print('PASS: authority-read failure cannot consume an undelivered handoff')
