"""Settled execution remains authoritative across next-frame failures."""
import copy
import smacx_mcp as m
m.MANAGED_ATTACHED=True
key=('match-post','session-post')
frame={'ok':True,'identity':dict(zip(('match_id','session_id'),key)),
       'decision_id':'fresh','choices':[{'choice_id':'next'}],
       'required_next':{'tool':'smac_execute_choice','decision_id':'fresh','execute_at_most':1}}
reads=[]
m.smac_decision=lambda: reads.append(True) or copy.deepcopy(frame)
base={'ok':True,'execution_status':'completed','execution':{'action_id':8},'decision_consumed':True}
r=m._attach_post_action_decision(copy.deepcopy(base),key)
assert r['post_action_decision']['frame']==frame and len(reads)==1
assert r['required_next']['select_choice_from']=='post_action_decision.frame.choices'
for extra in ({'queued':True},{'turn_handoff_required':{'required':True}},
              {'execution_status':'pending'},{'execution_status':'unverified'},
              {'ok':False},{'required_next':{'stop_after':True}}):
 n=len(reads);r=m._attach_post_action_decision({**base,**extra},key)
 assert len(reads)==n and 'post_action_decision' not in r
m.smac_decision=lambda: (_ for _ in ()).throw(RuntimeError('unavailable'))
r=m._attach_post_action_decision(copy.deepcopy(base),key)
assert r['ok'] and r['execution']==base['execution'] and r['required_next']['tool']=='smac_decision'
assert r['post_action_decision']['frame']['error']['code']=='post_action_observation_failed'
m.smac_decision=lambda: {**frame,'identity':{'match_id':'other','session_id':key[1]}}
r=m._attach_post_action_decision(copy.deepcopy(base),key)
assert r['ok'] and r['post_action_decision']['frame']['error']['code']=='post_action_scope_changed'
print('post-action decision: settled success, pending/handoff exclusion, scope and observation failure passed')
