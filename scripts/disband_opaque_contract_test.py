#!/usr/bin/env python3
"""Native disband stages remain opaque, confirmed, and one-use."""
import json
from unittest.mock import patch
import smacx_mcp as m
calls=[]
def bridge(op,**args):
    calls.append((op,args))
    if op=='semantic_command':
        if args['command']=='disband_unit':return {'ok':True,'queued':True,'action_id':42}
        return {'ok':True,'command':args['command'],'response':args['response']}
    if op=='semantic_snapshot':return {'ok':True,'snapshot':{'revision':'r1','turn':4,'year':2104}}
    raise AssertionError(op)
with patch.object(m,'_await_deferred_action',side_effect=lambda result:result),patch.object(m,'_call',bridge),patch.object(m,'_match_briefing_gate',return_value=None), \
     patch.object(m,'_pending_capability_gap',return_value=None), \
     patch.object(m,'controller_record_campaign_action',return_value={'ok':True}):
    identity={'match_id':'match-disband','session_id':'session-disband','revision':'r1'}
    decision,choices=m._cache_decision_choices(identity,[{'command':'disband_unit','unit_id':7,
        'requires':{'confirm_disband':1}}],choice_kind='unit_actions',choice_arguments={'unit_id':7})
    result=m.smac_execute_choice(decision,choices[0]['choice_id'])
    assert result['ok'] and result['queued'] and not result.get('completed'),result
    native=[a for op,a in calls if op=='semantic_command'];assert native[-1]['confirm_disband']==1
    repeated=m.smac_execute_choice(decision,choices[0]['choice_id']);assert not repeated['ok']
    assert len([a for op,a in calls if op=='semantic_command'])==1
    decision,choices=m._cache_decision_choices(identity,[{'command':'respond_to_unit_disband',
        'response':'proceed','confirm_disband':1,'destructive':True}],choice_kind='interaction',choice_arguments={})
    result=m.smac_execute_choice(decision,choices[0]['choice_id']);assert result['ok'],result
    native=[a for op,a in calls if op=='semantic_command'];assert native[-1]['response']=='proceed' and native[-1]['confirm_disband']==1
print(json.dumps({'passed':True,'queued_not_completed':True,'one_use':True,'native_confirmation_preserved':True}))
