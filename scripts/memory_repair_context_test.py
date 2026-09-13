import json
import smacx_mcp as m
m._bound_scope_identity=lambda *args:args
m.smac_decision=lambda:{'ok':True,'identity':{'match_id':'match-test','session_id':'session-test','revision':'new'},'information':[{'evidence':'stale'}]}
calls=[]
def invoke(error,stage='not_started'):
 m.write_platform_memory=lambda *a,**k:calls.append(a) or {'ok':False,'error':error,'persistence':{'stage':stage}}
 return m.smac_memory_update('summary','match-test','session-test','old',json.dumps({'content':'intent'}))
r=invoke('stale_memory_observation');assert r['repair_context']['frame']['identity']['revision']=='new'
assert calls[-1][3]=='old' and not r['repair_context']['automatic_retry']
r=invoke('evidence_event_scope_mismatch');assert 'optional' in r['repair_context']['instruction']
r=invoke('stale_memory_observation','journal_committed');assert 'repair_context' not in r
m.smac_decision=lambda:(_ for _ in ()).throw(RuntimeError('failed'))
r=invoke('stale_memory_observation');assert r['error']=='stale_memory_observation' and not r['repair_context']['available']
assert len(calls)==4
print('memory repair: evidence guidance, no rebase/retry, committed exclusion and read failure passed')
