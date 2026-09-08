"""Managed preview adapter contract, not native combat/mechanics validation."""
import json
from types import SimpleNamespace
from unittest.mock import patch
import smacx_mcp as mcp

scope = ('match-preview', 'session-preview', 'agent-preview', 'perspective-preview')
projection = {'action_revision': 'r1', 'objects': []}
world = SimpleNamespace(
    _projection=lambda: (SimpleNamespace(timeline_id='timeline', world_epoch='epoch'), projection),
    _objects=lambda p: {},
    query=lambda **kw: {'ok': True, 'items': [kw['runtime_counterfactual_receipt']]})
attention = SimpleNamespace(scope=None, journal=SimpleNamespace(projection_records=lambda *a, **kw: []))
with patch.object(mcp, '_managed_scope_identity', return_value=scope), \
     patch.object(mcp, '_refresh_managed_world', return_value={'ok': True}), \
     patch.object(mcp, 'controller_world_service', return_value=(None, world, attention)), \
     patch.object(mcp, '_semantic_selector_context', return_value={'reverse_units': {7: 'own-unit-8'}}), \
     patch.object(mcp, '_call', return_value={'ok': True, 'kind': 'action', 'epistemic_status': 'conditional',
                                            'executes_action': False, 'support_changes': []}) as native:
    decision, choices = mcp._cache_decision_choices(
        {'match_id': scope[0], 'session_id': scope[1], 'revision': 'r1'},
        [{'command': 'move_unit', 'unit_id': 7, 'target_tile_id': 20}],
        choice_kind='unit_actions', choice_arguments={})
    for kind in ('action', 'deployment'):
        ref = {'decision_id': decision, 'choice_id': choices[0]['choice_id']}
        scenario = {'kind': kind, **ref} if kind == 'action' else {'kind': kind, 'capability': 'combat', 'choice_refs': [ref]}
        result = mcp.smac_world(mode='counterfactual', scenario_json=json.dumps(scenario))
        if kind == 'deployment':
            assert result['error']['code'] == 'counterfactual_requires_matching_final_choice', result
            continue
        assert result['ok'], result
        item = result['items'][0]
        if kind == 'deployment': item = item['alternatives'][0]
        assert item['epistemic_status'] == 'conditional'
        assert item['executes_action'] is False
        assert 'combat odds' in item['not_predicted']
        assert 'movement success or arrival' in item['not_predicted']
        assert 'successful move only' in item['prediction_scope']
        assert not mcp.DECISION_CACHE[decision]['consumed']
    assert all(call.args[0] == 'semantic_counterfactual' for call in native.call_args_list)
    mcp.DECISION_CACHE.pop(decision)
print(json.dumps({'pass': True, 'action_scope_and_deployment_rejection': True, 'no_action_dispatch_or_consumption': True,
                  'scope': 'managed adapter with native-shaped receipt and query sink; native mechanics not asserted'}))
