#!/usr/bin/env python3
"""An unchanged modal is a decision boundary, not evidence of engine processing."""
import json
from unittest.mock import patch
import smacx_mcp as mcp
from smacx_diagnostic_summary import summary

observation={'ok':True,'observation':{'screen':'game','turn':72,'ui':{'modal':True,'can_act':False}}}
for phase,label,next_tool in [('interaction','BEGINPROJECT','smac_decision'),
                              ('interaction','SOCIETY','smac_decision'),
                              ('capability_gap','UNKNOWN','smac_decision'),
                              ('wait','','smac_wait'),('turn','','smac_decision')]:
    snapshot={'ok':True,'snapshot':{'match_id':'match-test','session_id':'session-test','turn':72,
        'protocol':{'phase':phase,'required_action':'resolve_interaction' if phase=='interaction' else 'wait_then_observe'},
        'interaction':{'kind':'popup' if label else 'waiting_for_engine','popup_label':label}}}
    calls=[]
    def call(operation,**kwargs):
        calls.append(operation)
        return snapshot if operation=='semantic_snapshot' else observation
    with patch.object(mcp,'_call',side_effect=call), patch.object(mcp,'_attach_chat_attention',side_effect=lambda r,i:r), \
         patch.object(mcp.time,'sleep') as sleep:
        result=mcp.smac_wait(0 if phase=='wait' else 30)
        assert result['required_next']['tool']==next_tool and result['gameplay']['popup_label']==label
        trace=summary({'kind':'tool_returned','payload':{'managed_name':'smac_wait','result':result}})
        assert next_tool in trace and (not label or label in trace)
        assert result['changed'] is False and not sleep.called
        assert 'semantic_command' not in calls
        if phase!='wait': assert calls==['observe','semantic_snapshot'],calls
with patch.object(mcp,'_call',side_effect=[observation,{'ok':False,'error':'game_not_connected'}]):
    result=mcp.smac_wait(30)
    assert not result['ok'] and result['wait_stage']=='semantic_observation'
    assert 'STOP issuing gameplay actions' in result['instruction']
print(json.dumps({'passed':True,'classification':'managed wait adapter',
 'unchanged_blocking_modal_returns_immediate_decision':True,'native_processing_remains_wait':True,
 'actionable_turn_and_unknown_modal_require_fresh_decision':True,'bridge_failure_visible':True,'no_native_mutation':True}))
