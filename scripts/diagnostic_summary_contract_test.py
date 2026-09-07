#!/usr/bin/env python3
import json
from smacx_diagnostic_summary import result_object, summary, Metrics
wrapped='Untrusted tool output\n'+json.dumps({'result':json.dumps({'ok':False,'error':{'code':'schema_missing_required'}})})+'\nEnd output'
assert result_object(wrapped)['error']['code']=='schema_missing_required'
event={'kind':'tool_returned','actor':'sovereign','payload':{'managed_name':'smac_memory','content':wrapped}}
assert 'schema_missing_required' in summary(event)
metrics=Metrics();metrics.add(event)
assert metrics.as_dict()['failure_observations_by_layer']=={'tool_returned:schema_missing_required':1}
sdk_error={'error':json.dumps({'ok':False,'error':{'code':'unknown_tool_arguments',
    'unknown_arguments':['detail']},'execution_status':'not_executed','native_action_executed':False})}
sdk_event={'kind':'tool_returned','payload':{'managed_name':'smac_choices','content':json.dumps(sdk_error)}}
assert result_object(sdk_error)['error']['code']=='unknown_tool_arguments'
assert '"native_action_executed":false' in summary(sdk_event)
metrics.add(sdk_event)
assert metrics.as_dict()['failure_observations_by_layer']['tool_returned:unknown_tool_arguments']==1
assert result_object({'error':json.dumps({'ok':True})}).get('error')
assert result_object({'error':'plain failure'})=={'error':'plain failure'}
rejected={'kind':'tool_validation_rejected','actor':'sovereign','payload':{
    'managed_name':'unknown_diagnostic_tool','arguments':{'purpose':'fixture'},
    'error':{'code':'unknown_tool_name'},'native_action_executed':False}}
metrics.add(rejected)
assert 'rejected before execution' in summary(rejected)
assert metrics.as_dict()['failure_observations_by_layer']['tool_validation_rejected:unknown_tool_name']==1
assert metrics.as_dict()['sovereign_requested_tool_counts']['unknown_diagnostic_tool']==1
queued={'kind':'managed_tool_returned','payload':{'tool':'smac_execute_choice','result':{'ok':True,'queued':True,'action_id':'action-test'}}}
rendered=summary(queued)
assert 'queued' in rendered and 'completed' not in rendered and 'effect verified' not in rendered
queued['payload']['result']={'ok':True,'execution_status':'queued','decision_consumed':True}
rendered=summary(queued)
assert '"execution_status":"queued"' in rendered and '"decision_consumed":true' in rendered
metrics.add({'kind':'provider_request_submitted','correlation':{'request_id':'request-open'}})
metrics.add({'kind':'provider_response_headers','correlation':{'request_id':'request-open'}})
assert metrics.as_dict()['provider_requests_without_terminal_capture']['count']==1
metrics.add({'kind':'provider_transport_failed','correlation':{'request_id':'request-open'}})
assert metrics.as_dict()['provider_requests_without_terminal_capture']['count']==0
taxonomy=Metrics()
for name, fields in [('smac_attention_ack','attention_lease_id, through_cursor'),
                     ('smac_choices','kind')]:
    error=f"tool_call to 'mcp__smacx__{name}' is missing required argument(s): {fields}. The tool was NOT invoked. Parameters schema: {{}}"
    event={'kind':'tool_returned','payload':{'content':json.dumps({'error':error})}}
    taxonomy.add(event)
    assert error in result_object(event['payload']['content'])['error']
taxonomy.add({'kind':'tool_returned','payload':{'result':{'error':"tool_call cannot invoke 'tool_describe' (it is itself a bridge tool)"}}})
for error in ['invalid_tile_id','sovereign_invocation_already_active','native_observation_feed_failed']:
    taxonomy.add({'kind':'runtime_context_failed','payload':{'error':error}})
taxonomy.add({'kind':'runtime_context_failed','payload':{'error':'Unexpected problem: invalid_tile_id'}})
assert taxonomy.as_dict()['failure_observations_by_layer']=={
    'tool_returned:schema_missing_required':2,
    'tool_returned:bridge_tool_not_invocable':1,
    'runtime_context_failed:invalid_tile_id':1,
    'runtime_context_failed:sovereign_invocation_already_active':1,
    'runtime_context_failed:native_observation_feed_failed':1,
    'runtime_context_failed:unclassified_error_text':1,
}
diplomacy={'kind':'managed_tool_returned','payload':{'tool':'smac_decision','result':{
    'ok':True,'choices':[{'choice_id':'decline','label':'Respond to diplomatic offer','response':'reject'},
                        {'choice_id':'agree','label':'Respond to diplomatic offer','response':'accept','energy_credits':15}],
    'information':[{'offer_type':'introduced_commlink','target_faction_name':'Spartan Federation','energy_credits':15}]}}}
text=summary(diplomacy)
assert '"response":"accept"' in text and '"response":"reject"' in text
assert 'Spartan Federation' in text and '"energy_credits":15' in text
diplomacy['payload']['result']['information'][0]['meaning']='x'*10000
assert len(summary(diplomacy))<1500
diplomacy['payload']['result']['information']=[{'offer_type':'technology_or_map_exchange','terms':{
    'player_gives':{'kind':'technology','name':'Centauri Ecology'},
    'player_receives':{'kind':'technology','name':'Planetary Networks'},
    'unrelated_details':{'body':'x'*10000}}}]
text=summary(diplomacy)
assert 'Centauri Ecology' in text and 'Planetary Networks' in text
assert len(text)<1500 and 'unrelated_details' not in text
print(json.dumps({'passed':True,'hermes_envelope_decoded':True,'pre_mcp_failure_named':True,'queued_not_completed':True}))
# Repeated choices must not hide later action families past the CLI's 2000-char cut.
import copy
menu = {'ok': True, 'kind': 'decision_frame', 'decision_id': 'decision-controlled',
        'focus': {'kind': 'unit_actions', 'unit': {'own_unit_ref': 'own-unit-1',
            'name': 'Scout Patrol', 'hp': 8, 'max_hp': 10, 'moves_remaining': 3,
            'movement_scale': 3, 'roles': {'combat': True}}},
        'choice_scope': {'family': 'unit_actions', 'all_management_actions_enumerated': False,
            'meaning': 'instruction ' * 100,
            'other_management_queries': {'tool': 'smac_choices', 'kinds': ['production', 'base_citizens', 'research']}},
        'choices': [{'choice_id': f'choice-{i:032}', 'label': 'Move unit',
                     'target_location_ref': f'location-{i}', 'may_close_turn': True}
                    for i in range(40)] + [
            {'choice_id': 'hurry-issued', 'label': 'Hurry production', 'energy_cost': 13, 'affordable': True},
            {'choice_id': 'skip-issued', 'label': 'Skip unit'},
            {'choice_id': 'end-issued', 'label': 'End turn', 'may_close_turn': True}]}
original = copy.deepcopy(menu)
text = summary({'kind': 'tool_returned', 'payload': {'managed_name': 'smac_decision', 'result': menu}})
assert len(text) < 2000 and 'Hurry production' in text and 'Skip unit' in text and 'End turn' in text
assert '"count":40' in text and 'decision-controlled' in text and '"energy_cost":13' in text
assert 'base_citizens' in text and 'structured diagnostic record' in text
assert menu == original  # Rendering cannot change the actual issued menu.
print(json.dumps({'compact_menu_passed': True, 'rendered_characters': len(text),
                  'late_action_families_retained': True, 'provider_response_unchanged': True}))

empty_ack = summary({'kind':'tool_returned','payload':{'managed_name':'smac_attention_ack',
    'result':{'ok':True,'acknowledged_ids':[],'attention_cursor':28,'attention_lease_id':'lease-empty'}}})
assert '"acknowledged_count":0' in empty_ack and '"attention_cursor":28' in empty_ack

# Operators must see whether a successful plan write contains mechanical bindings.
plan_result={'ok':True,'record':{'plan_id':'plan-example','status':'active','plan_revision':2,
    'objective':'unverified narrative '*1000,'participants':[],'target_refs':[],'dependencies':[]}}
plan_before=json.dumps(plan_result,sort_keys=True)
rendered=summary({'kind':'tool_returned','payload':{'managed_name':'smac_memory_update','result':plan_result}})
assert '"participants_count":0' in rendered and '"target_refs_count":0' in rendered
assert 'unverified narrative' not in rendered and len(rendered)<500
assert json.dumps(plan_result,sort_keys=True)==plan_before
health={'active_plan_count':1,'intent_coverage_complete':True,'plans_without_world_bindings_count':1,
    'assessment_scope':'Objective prose is not interpreted or verified.','assigned_owned_unit_count':0}
rendered=summary({'kind':'tool_returned','payload':{'managed_name':'smac_cognition','result':{'ok':True,'plan_health':health}}})
assert '"plans_without_world_bindings_count":1' in rendered and 'not interpreted or verified' in rendered
print(json.dumps({'plan_binding_counts_visible':True,'narrative_not_dumped_or_promoted':True}))
