#!/usr/bin/env python3
"""Exact tokenizer measurements and actual compiled-prefix cache probe."""
import argparse,hashlib,json,time,uuid,re
from pathlib import Path
from urllib.request import Request,urlopen
from doctrine_content_contract_test import fixtures
from doctrine_integration_contract_test import SEAT
from smacx_doctrine import compose_managed_prompt
from smacx_prompt import compose_player_system_prompt
from smacx_context_policy import validate_managed_context,semantic_gc_ceiling_tokens


def post(base,path,body):
    with urlopen(Request(base+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json'}),timeout=180) as r:return json.load(r)


def counters(base):
    with urlopen(base+'/metrics',timeout=10) as r:text=r.read().decode()
    values={}
    for key in ('queries','hits'):
        matches=re.findall(r'^vllm:prefix_cache_'+key+r'_total\{[^\n]*\}\s+([\d.e+]+)$',text,re.M)
        if not matches:raise RuntimeError('provider_cache_metrics_unavailable')
        values[key]=sum(float(v) for v in matches)
    return values


def main():
    p=argparse.ArgumentParser();p.add_argument('--base-url',required=True);p.add_argument('--model',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    records=[]
    for name,c in fixtures().items():
        if name in ('missing-required','unknown-ruleset','material-mod-conflict'):continue
        prompt,metadata=compose_managed_prompt(c,**SEAT)
        result=post(a.base_url,'/tokenize',{'model':a.model,'prompt':prompt})
        reserve=validate_managed_context(prompt,65536)
        records.append({'case':name,'bytes':len(prompt.encode()),'exact_tokens':result['count'],'sha256':metadata['final_prompt_sha256'],
          'context_fraction_65536':round(result['count']/65536,6),'context_remaining_65536_before_tools_history_runtime_output':65536-result['count'],
          'conservative_system_tool_reserve':reserve,'semantic_history_ceiling':semantic_gc_ceiling_tokens(65536,output_reserve=8192,reasoning_reserve=8192,system_tool_reserve=reserve)})
    core=compose_player_system_prompt(**SEAT)
    base_tokens=post(a.base_url,'/tokenize',{'model':a.model,'prompt':core})['count']
    prompt,_=compose_managed_prompt(fixtures()['stock-blind'],**SEAT)
    # A unique earlier system prefix prevents attribution to previous eval requests.
    unique='Read-only compiled prefix cache probe '+uuid.uuid4().hex+'.\n'+prompt
    samples=[]
    before=counters(a.base_url)
    for tail in ('first request','second request'):
        start=time.monotonic()
        response=post(a.base_url,'/v1/chat/completions',{'model':a.model,
          'messages':[{'role':'system','content':unique},{'role':'user','content':tail+': respond OK'}],
          'max_tokens':2,'temperature':0,'seed':1729,'stream':False,'chat_template_kwargs':{'enable_thinking':False}})
        latency=round(time.monotonic()-start,3)
        # vLLM metrics may publish on a periodic tick. Do not call a latency
        # improvement alone proof of caching or attribute concurrent traffic.
        after=counters(a.base_url)
        deadline=time.monotonic()+15
        while after['queries']-before['queries']<response['usage']['prompt_tokens'] and time.monotonic()<deadline:
            time.sleep(1);after=counters(a.base_url)
        delta={k:after[k]-before[k] for k in before}
        samples.append({'tail':tail,'latency_seconds':latency,'usage':response.get('usage'),'cache_counter_delta':delta,
          'isolated_query_count_matches':delta['queries']==response['usage']['prompt_tokens']})
        before=after
    result={'passed':True,'classification':'live exact provider tokenizer and actual compiled-prefix request usage',
      'model':a.model,'operational_exact_tokens':base_tokens,'prompts':records,'cache_probe':samples,
      'budget_note':'Remaining context also must contain tools, durable history, request-local runtime and output reserve; this is not all free gameplay capacity.'}
    Path(a.output).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

if __name__=='__main__':main()
