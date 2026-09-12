#!/usr/bin/env python3
"""Native receipt semantics remain explicit through the actual MCP adapter."""
import smacx_mcp as m
for resolution,complete in [('native_terraform_work_observed',False),('native_terraform_completed',True)]:
    m._call=lambda op,**args:{'ok':True,'action':{'status':'completed','resolution':resolution,'native_call_attempted':True}}
    r=m._await_deferred_action({'ok':True,'queued':True,'action_id':1})
    assert r['completed'] is complete and r['terraform_completion_verified'] is complete
    if not complete:assert r['persistent'] is True
    if not complete:assert 'not complete' in r['completion_semantics']
    assert m._public_execution_receipt(r)['terraform_completion_verified'] is complete
m._call=lambda op,**args:{'ok':True,'action':{'status':'pending'}}
r=m._await_deferred_action({'ok':True,'queued':True,'action_id':1},timeout=.001)
assert r['queued'] and not r.get('completed') and 'terraform_completion_verified' not in r
print('development receipts distinguish work, completed terrain, and pending transport')

# A development operation rejected before native dispatch cannot be offered
# again against the identical meaningful state. Other legal unit choices remain.
from unittest.mock import patch
identity={'match_id':'match-development','session_id':'session-development','revision':'r1'}
rows=[{'command':'terraform','unit_id':7,'former_id':0,'name':'Farm'},
      {'command':'skip_unit','unit_id':7}]
focus={'kind':'unit_actions','unit':{'own_unit_ref':'own-unit-7','location_ref':'location-99',
       'moves_remaining':3,'order_name':'none'}}
m.ACTION_PROGRESS.clear();m.FAILED_CHOICE_ATTEMPTS.clear();m.RUNTIME_CIRCUITS.clear()
first_id,first_choices=m._cache_decision_choices(identity,rows,choice_kind='unit_actions',
    choice_arguments={'unit_id':7},focus=focus,turn=9,year=2109,phase='turn')
terraform_choice=next(row for row in first_choices if row['label']=='Terraform')
def rejected_development(op,**args):
    if op=='semantic_command':return {'ok':True,'command':'terraform','queued':True,'action_id':8}
    if op=='action_status':return {'ok':True,'action':{'action_id':8,'command':'terraform',
        'status':'rejected','resolution':'network_unit_lock_rejected',
        'native_call_attempted':False,'native_result':0}}
    raise AssertionError(op)
with patch.object(m,'_call',side_effect=rejected_development),\
     patch.object(m,'controller_record_campaign_action',return_value={'ok':True}),\
     patch.object(m,'_sovereign_gameplay_gate',return_value=None),\
     patch.object(m,'_match_briefing_gate',return_value=None),\
     patch.object(m,'_pending_capability_gap',return_value=None):
    rejected=m.smac_execute_choice(first_id,terraform_choice['choice_id'])
assert rejected['execution_status']=='not_dispatched' and rejected['native_action_executed'] is False,rejected
assert 'terraform' in rejected['error']['message'] and 'do not retry the same choice' in rejected['error']['message'],rejected
second_id,second_choices=m._cache_decision_choices(identity,rows,choice_kind='unit_actions',
    choice_arguments={'unit_id':7},focus=focus,turn=9,year=2109,phase='turn')
assert [row['label'] for row in second_choices]==['Skip unit'],second_choices
recovery=m.DECISION_CACHE[second_id]['choice_recovery']
assert recovery['retry_same_choice'] is False and recovery['withheld_choice_count']==1,recovery
changed_focus={**focus,'unit':{**focus['unit'],'moves_remaining':0}}
_,changed_choices=m._cache_decision_choices(identity,rows,choice_kind='unit_actions',
    choice_arguments={'unit_id':7},focus=changed_focus,turn=9,year=2109,phase='turn')
assert any(row['label']=='Terraform' for row in changed_choices),changed_choices
print('pre-dispatch development rejection is withheld only for unchanged meaningful state')

# Actual managed choice query -> opaque selection -> guarded command -> receipt.
import json
context={'reverse_units':{7:'own-unit-7'},'reverse_locations':{99:'location-99'}}
for command,resolution in [('found_base','native_base_founded'),('terraform','native_terraform_work_observed')]:
    calls=[];journals=[]
    row={'command':command,'unit_id':7}
    if command=='terraform':row['former_id']=5
    def native(op,**args):
        if op=='semantic_snapshot':return {'ok':True,'snapshot':{**identity,'turn':9,'year':2109,'protocol':{'phase':'turn'}}}
        if op=='semantic_choices':return {'ok':True,**identity,'kind':'unit_actions','ready':True,
            'movement_budget':{'movement_points':3,'moves_remaining':3,'movement_scale':3},
            'roles':{'colony':command=='found_base','former':command=='terraform'},'choices':[row]}
        if op=='semantic_command':
            assert args['command']==command and args['unit_id']==7 and args['expected_revision']=='r1'
            if command=='terraform':assert args['former_id']==5
            calls.append(args);return {'ok':True,'queued':True,'action_id':1}
        if op=='action_status':return {'ok':True,'action':{'status':'completed','resolution':resolution,'native_call_attempted':True}}
        raise AssertionError(op)
    with patch.object(m,'_call',side_effect=native),patch.object(m,'_resolve_managed_selectors',return_value=({'unit_id':7},context)),patch.object(m,'_pending_capability_gap',return_value=None),patch.object(m,'_match_briefing_gate',return_value=None),patch.object(m,'controller_record_campaign_action',side_effect=lambda *a,**kw:journals.append(kw) or {'ok':True}):
        m.ACTION_PROGRESS.clear();m.FAILED_CHOICE_ATTEMPTS.clear()
        frame=m.smac_choices(kind='unit_actions',own_unit_ref='own-unit-7')
        assert frame['ok'],frame
        assert '"unit_id"' not in json.dumps(frame),frame
        result=m.smac_execute_choice(frame['decision_id'],frame['choices'][0]['choice_id'])
        assert result['ok'] and len(calls)==1 and len(journals)==1,result
        assert result['execution_status']==('order_assigned' if command=='terraform' else 'completed'),result
        if command=='terraform':assert result['terraform_completion_verified'] is False
print('development actions pass managed query, opaque execution, journal and receipt boundary')

# Supply-pod collection keeps the random consequence unknown while carrying
# the exact selected unit/target and verified pod removal through the receipt.
calls=[];journals=[]
def pod_native(op,**args):
    if op=='semantic_snapshot':return {'ok':True,'snapshot':{**identity,'turn':9,'year':2109,'protocol':{'phase':'turn'}}}
    if op=='semantic_choices':return {'ok':True,**identity,'kind':'unit_actions','ready':True,
        'choices':[{'command':'collect_supply_pod','unit_id':7,'target_tile_id':99,
                    'consequential':True,'known_outcome':False}]}
    if op=='semantic_command':
        assert args['command']=='collect_supply_pod' and args['unit_id']==7 and args['target_tile_id']==99
        calls.append(args);return {'ok':True,'command':'collect_supply_pod','queued':True,'action_id':2}
    if op=='action_status':return {'ok':True,'action':{'action_id':2,'command':'collect_supply_pod',
        'status':'completed','resolution':'native_supply_pod_resolved','native_call_attempted':True,
        'origin_tile_id':98,'target_tile_id':99,'observed_tile_id':99,'supply_pod_removed':True}}
    raise AssertionError(op)
with patch.object(m,'_call',side_effect=pod_native),patch.object(m,'_resolve_managed_selectors',return_value=({'unit_id':7},context)),patch.object(m,'_pending_capability_gap',return_value=None),patch.object(m,'_match_briefing_gate',return_value=None),patch.object(m,'controller_record_campaign_action',side_effect=lambda *a,**kw:journals.append(kw) or {'ok':True}):
    m.ACTION_PROGRESS.clear();m.FAILED_CHOICE_ATTEMPTS.clear()
    frame=m.smac_choices(kind='unit_actions',own_unit_ref='own-unit-7')
    choice=frame['choices'][0]
    assert choice['known_outcome'] is False and choice['target_location_ref']=='location-99'
    result=m.smac_execute_choice(frame['decision_id'],choice['choice_id'])
    assert result['ok'] and result['completed'] and result['supply_pod_removed']
    assert result['execution']['resolution']=='native_supply_pod_resolved'
    assert 'random consequence is not inferred' in result['completion_semantics']
    assert len(calls)==1 and len(journals)==1
print('managed supply-pod collection preserves opaque guards and qualified native effect evidence')
