#!/usr/bin/env python3
"""The production command boundary refuses both explicit and automatic closure."""
import json
import time
from types import SimpleNamespace
from unittest.mock import patch
import smacx_mcp as m

native=[]
pending={'turn':4,'total_pending':1,'items':[{'key':'citizen'}]}
critical={'items':[],'more':False}
def call(operation,**kwargs):
    if operation=='semantic_snapshot':
        return {'ok':True,'snapshot':{'turn':4,'revision':'r4','ready_unit_refs':[{'own_unit_ref':'own-unit-7'}]}}
    native.append((operation,kwargs));return {'ok':True}
with patch.object(m,'MANAGED_ATTACHED',True), patch.object(m,'_sovereign_gameplay_gate',return_value=None), \
     patch.object(m,'_pending_capability_gap',return_value=None), patch.object(m,'_match_briefing_gate',return_value=None), \
     patch.object(m,'_managed_scope_identity',return_value=('match-test','session-test','agent-test','perspective-test')), \
     patch.object(m,'current_turn_intents',side_effect=lambda *a,**k:pending), \
     patch.object(m,'_runtime_services',return_value=(None,SimpleNamespace(unacknowledged_critical=lambda:critical))), \
     patch.object(m,'_call',side_effect=call):
    for command,args in [('end_turn',{}),('skip_all_ready_units',{}),('skip_unit',{'unit_id':7}),('move_unit',{'unit_id':7,'target_tile_id':2})]:
        response=m.smac_command(command, 'match-test','session-test','r4',**args)
        assert response['error']['code']=='current_turn_intent_requires_review',response
        assert response['native_action_executed'] is False and not native
    m.DECISION_CACHE['decision-review']={'created_monotonic':time.monotonic(),
        'identity':{'match_id':'match-test','session_id':'session-test','revision':'r4'},
        'choices':{'choice-review':{'command':'end_turn'}},'consumed':False}
    for _ in range(8):
        review=m.smac_execute_choice('decision-review','choice-review')
        assert review['error']['code']=='current_turn_intent_requires_review',review
        assert review['decision_consumed'] is False and review['execution_status']=='review_required'
    assert not native and ('match-test','session-test') not in m.RUNTIME_CIRCUITS
    response=m.smac_command('set_production','match-test','session-test','r4',base_id=1,item_id=0)
    assert response['ok'] and len(native)==1,'management must remain reachable'
    pending={'total_pending':0}
    critical={'items':[{'attention_id':'critical-test'}]}
    response=m.smac_command('end_turn','match-test','session-test','r4')
    assert response['error']['code']=='critical_attention_requires_review' and len(native)==1
    critical={'items':[]}
    response=m.smac_command('end_turn','match-test','session-test','r4')
    assert response['ok'] and len(native)==2
print(json.dumps({'passed':True,'evidence':'production managed command with controlled native adapter',
    'explicit_and_last_unit_guarded':True,'management_reachable':True,'critical_attention_guarded':True}))
