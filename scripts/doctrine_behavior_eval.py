#!/usr/bin/env python3
"""Frozen read-only decision ablation. No native actions or model-authored rules."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.request import Request,urlopen
from doctrine_content_contract_test import fixtures
from doctrine_integration_contract_test import SEAT
from smacx_doctrine import compile_doctrine,TEMPLATE
from smacx_prompt import compose_player_system_prompt

CORPUS=Path(__file__).resolve().parents[1]/'docs/doctrine/evaluation-corpus.json'


def call(base,path,payload):
    req=Request(base.rstrip('/')+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=180) as r:return json.load(r)


def prompts(config):
    # Same identity and no personality override throughout; only literacy layers vary.
    core=compose_player_system_prompt(**SEAT)
    static=re.sub(r'\{\{[A-Z_]+\}\}','',TEMPLATE.read_text()).strip()
    return {'A':core,'B':core+'\n\n'+static,'C':compose_player_system_prompt(**SEAT,gameplay_doctrine=compile_doctrine(fixtures()[config])['text'])}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--base-url',required=True);ap.add_argument('--model',required=True);ap.add_argument('--output',required=True);ap.add_argument('--corpus',type=Path,default=CORPUS)
    args=ap.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    corpus=json.loads(args.corpus.read_text());jobs=[(case,arm) for case in corpus['cases'] for arm in ('A','B','C')]
    def run(job):
        case,arm=job;target=out/f"{case['id']}-{arm}.json"
        system=prompts(case['configuration'])[arm]
        user='Read-only evaluation of a frozen gameplay decision. Do not execute an action or claim an effect. Recommend one offered choice or request investigation. Return JSON with choice_id (or null), rationale, investigate (list), and uncertainties (list).\n'+json.dumps(case['evidence'],ensure_ascii=False)
        request={'model':args.model,'messages':[{'role':'system','content':system},{'role':'user','content':user}],
                 'max_tokens':900,'temperature':0,'seed':1729,'stream':False,
                 'chat_template_kwargs':{'enable_thinking':False,'preserve_thinking':False}}
        signature=hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest()
        if target.exists():
            stored=json.loads(target.read_text())
            if stored.get('request_sha256')==signature and not stored.get('error'):return stored
        start=time.monotonic()
        try:
            response=call(args.base_url,'/v1/chat/completions',request)
            content=response['choices'][0]['message'].get('content','')
            try:parsed=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip()))
            except (ValueError,TypeError):parsed=None
            offered={c['id'] for c in case['evidence']['choices']}
            result={'case':case['id'],'arm':arm,'request_sha256':signature,'system_sha256':hashlib.sha256(system.encode()).hexdigest(),
              'model':args.model,'latency_seconds':round(time.monotonic()-start,3),'usage':response.get('usage'),
              'finish_reason':response['choices'][0].get('finish_reason'),'response':content,'parsed':parsed,
              'offered_choice_or_investigation':isinstance(parsed,dict) and (parsed.get('choice_id') in offered or parsed.get('choice_id') is None and bool(parsed.get('investigate')))}
        except Exception as e:result={'case':case['id'],'arm':arm,'request_sha256':signature,'error':type(e).__name__+': '+str(e)}
        target.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print(case['id'],arm,'error' if result.get('error') else 'done',flush=True);return result
    with ThreadPoolExecutor(max_workers=3) as executor:results=list(executor.map(run,jobs))
    report={'classification':'live-provider frozen advisory-decision ablation; no native execution or tool loop',
       'corpus_sha256':hashlib.sha256(args.corpus.read_bytes()).hexdigest(),'model':args.model,'seed':1729,'temperature':0,
       'results':results,'errors':sum(bool(r.get('error')) for r in results)}
    (out/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')

if __name__=='__main__':main()
