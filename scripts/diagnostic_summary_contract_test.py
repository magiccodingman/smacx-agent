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
print(json.dumps({'passed':True,'hermes_envelope_decoded':True,'pre_mcp_failure_named':True,'queued_not_completed':True}))
