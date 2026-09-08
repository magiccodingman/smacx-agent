#!/usr/bin/env python3
"""Rejected scope never reaches persistence; explicit fresh-guard retry does."""
import os
for key,value in {'SMACX_MANAGED_ATTACHED':'1','SMACX_AGENT_MATCH_ID':'match-fixture','SMACX_AGENT_SESSION_ID':'session-fixture','SMACX_AGENT_ID':'agent-fixture','SMACX_PERSPECTIVE_ID':'perspective-fixture'}.items():
    os.environ[key]=value
import smacx_mcp as m
calls=[]
m.write_platform_memory=lambda *a,**kw: calls.append((a,kw)) or {'ok':True}
base=dict(action='goal',match_id='match-fixture',session_id='session-fixture',observed_revision='revision-fresh',record_json='{}')
for key in ['match_id','session_id','agent_id','perspective_id']:
    result=m.smac_memory_update(**{**base,key:'incorrect-scope'})
    assert result['ok'] is False and not calls
    assert result['persistence']=={'stage':'not_started','journal_committed':False}
    assert result['required_next']['tool']=='smac_decision'
    assert result['error']==f'managed_{key[:-3]}_scope_mismatch'
assert m.smac_memory_update(**base)['ok']
assert len(calls)==1
assert calls[0][0][1:4]==('match-fixture','session-fixture','revision-fresh')
assert calls[0][1]=={'agent_id':'agent-fixture','perspective_id':'perspective-fixture'}
print('PASS: four scope mismatches never persist; explicit retry keeps fresh revision and bound seat')
