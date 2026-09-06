#!/usr/bin/env python3
import json
from smacx_diagnostic_summary import result_object, summary, Metrics
wrapped='Untrusted tool output\n'+json.dumps({'result':json.dumps({'ok':False,'error':{'code':'schema_missing_required'}})})+'\nEnd output'
assert result_object(wrapped)['error']['code']=='schema_missing_required'
event={'kind':'tool_returned','actor':'sovereign','payload':{'managed_name':'smac_memory','content':wrapped}}
assert 'schema_missing_required' in summary(event)
metrics=Metrics();metrics.add(event)
assert metrics.as_dict()['failure_observations_by_layer']=={'tool_returned:schema_missing_required':1}
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
