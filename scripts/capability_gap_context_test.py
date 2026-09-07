#!/usr/bin/env python3
"""Compact gap frames expose observed obstruction without operator-only state."""
from contextlib import ExitStack
from unittest.mock import patch
import smacx_mcp as m
s={'match_id':'gap-test','session_id':'gap-session','revision':'r1','turn':212,'year':2312,
'protocol':{'phase':'capability_gap','required_action':'report_capability_gap_and_stop'},
'interaction':{'kind':'unsupported_modal','popup_label':'','engine_state':{'last_started_popup_label':'CALLSCOUNCIL','private_test_marker':'must-not-appear'}}}
with ExitStack() as stack:
 for name,value in [('_sovereign_gameplay_gate',None),('_refresh_managed_world',{'ok':True}),('_call',{'ok':True,'snapshot':s}),('_compose_match_briefing',{'ok':True,'acknowledged':True}),('_implicit_turn_handoff',None)]:
  stack.enter_context(patch.object(m,name,return_value=value))
 for name in ['_attach_working_state','_attach_chat_attention']:
  stack.enter_context(patch.object(m,name,side_effect=lambda frame,*a,**kw:frame))
 frame=m.smac_decision()
 assert frame['phase']=='capability_gap',frame
 b=frame['blocking_interaction'];assert b['kind']=='unsupported_modal' and b['last_started_popup_label']=='CALLSCOUNCIL'
 assert 'private_test_marker' not in str(frame)
 assert 'not proof' in b['qualification'] and frame['choices']==[]
print('PASS: compact native gap attribution, qualified prior label, bounded field allowlist')
