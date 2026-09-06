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
print(json.dumps({'passed':True,'hermes_envelope_decoded':True,'pre_mcp_failure_named':True,'queued_not_completed':True}))
