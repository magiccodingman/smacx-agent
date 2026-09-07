#!/usr/bin/env python3
"""Provider wire retains complementary queries and every just-returned batch result."""
import copy
import hashlib
import importlib
import json
import os
import tempfile
from pathlib import Path
import httpx
from harness_context_policy_test import dispatched_call

with tempfile.TemporaryDirectory() as temporary:
    prompt=Path(temporary)/'SYSTEM.md';prompt.write_text('Controlled complementary evidence test\n')
    os.environ.update(SMACX_STRICT_SYSTEM_PROMPT='1',SMACX_SYSTEM_PROMPT_FILE=str(prompt),
        SMACX_SYSTEM_PROMPT_SHA256=hashlib.sha256(prompt.read_bytes()).hexdigest(),SMACX_CONTEXT_LENGTH='65536')
    import smacx_strict_prompt as strict
    importlib.reload(strict)
    strict._append_runtime_context=lambda rows:rows
    from run_agent import AIAgent
    def batch(rows):
        return [{'role':'assistant','content':'','tool_calls':[dispatched_call(i,n,a) for i,n,a,v in rows]},
                *[{'role':'tool','tool_call_id':i,'content':json.dumps(v)} for i,n,a,v in rows]]
    production={'ok':True,'identity':{'revision':'r29'},'production_context':{'hurry':{'legal':True,'affordable':True,'energy_cost':37,'available_energy':79}},
                'choices':[{'choice_id':'choice-hurry','label':'Hurry production'}]}
    original=[{'role':'user','content':'Review strategic management.'},*batch([
        ('production','smac_choices',{'kind':'production','base_ref':'base-alpha'},production),
        ('citizens','smac_choices',{'kind':'base_citizens','base_ref':'base-alpha'},{'ok':True,'choices':[{'label':'Convert worker to specialist'}]}),
        ('research','smac_choices',{'kind':'research'},{'ok':True,'choices':[{'label':'Explore'}]}),
        ('decision','smac_decision',{}, {'ok':True,'identity':{'revision':'r29'},'choices':[{'label':'End turn'}]})])]
    frozen=copy.deepcopy(original);captured=[]
    def receive(request):captured.append(json.loads(request.content));return httpx.Response(200,json={'id':'controlled'})
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        wire=AIAgent._sanitize_api_messages(original)
        client.post('https://controlled.invalid/v1/chat/completions',json={'messages':wire})
    def result(rows,ident):return json.loads(next(m['content'] for m in rows if m.get('tool_call_id')==ident))
    assert result(captured[0]['messages'],'production')==production, 'affordable hurry vanished before provider'
    assert result(wire,'research')['choices'][0]['label']=='Explore'
    assert original==frozen
    later=[*original,*batch([('decision2','smac_decision',{}, {'ok':True,'newest_state':True})])]
    assert result(AIAgent._sanitize_api_messages(later),'production')==production
    repeated=[*later,*batch([('production2','smac_choices',{'kind':'production','base_ref':'base-alpha'}, {'ok':True,'replacement_quote':True})])]
    updated=AIAgent._sanitize_api_messages(repeated)
    assert result(updated,'production')['semantic_gc']=='superseded_query_evidence'
    assert result(updated,'production2')['replacement_quote']
    assert result(updated,'research')['choices'][0]['label']=='Explore'
    receipt={'ok':True,'execution_status':'completed','turn_handoff_required':{'required':True}}
    executed=[{'role':'user','content':'Execute and observe.'},*batch([
        ('execute','smac_execute_choice',{},receipt),('observe','smac_decision',{}, {'ok':True,'state':'new'})])]
    assert result(AIAgent._sanitize_api_messages(executed),'execute')==receipt
    # Emergency pressure must not silently prune/replace unseen results from
    # the latest batch. Old complete protocol groups remain disposable.
    old=[{'role':'user','content':'Long episode'}]
    for i in range(30):old+=batch([(f'old-{i}','smac_world',{'mode':'area','origin_ref':f'location-{i}'},{'ok':True,'data':'x'*9000})])
    pressured=[*old,*batch([(f'new-{i}','smac_choices',{'kind':'production','base_ref':f'base-{i}'},{'ok':True,'new_evidence':str(i)*4000}) for i in range(3)])]
    retained=AIAgent._sanitize_api_messages(pressured)
    assert all(result(retained,f'new-{i}')['new_evidence']==str(i)*4000 for i in range(3))
    huge=[{'role':'user','content':'Oversized single batch'},*batch([(f'large-{i}','smac_choices',{'kind':'production','base_ref':f'base-{i}'},{'ok':True,'data':'z'*15000}) for i in range(16)])]
    untouched=copy.deepcopy(huge)
    try:AIAgent._sanitize_api_messages(huge)
    except RuntimeError as exc:assert str(exc).startswith('context_budget_exhausted:'),str(exc)
    else:raise AssertionError('oversized unseen batch silently discarded evidence')
    assert huge==untouched
print(json.dumps({'passed':True,'actual_hermes_sanitizer_and_http_wire':True,'complementary_choices_preserved':True,
    'receipt_and_handoff_preserved':True,'only_identical_older_query_superseded':True,
    'latest_batch_preserved_under_pressure':True,'oversized_unseen_batch_fails_closed':True,'durable_history_unchanged':True}))
