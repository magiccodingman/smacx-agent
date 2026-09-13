"""Bounded, read-only Qwen captured-request experiment; never executes tools.

The explicit tokenizer alias applies only to the tested Qwen endpoint. Retain
raw output outside source control; the report contains only IDs and metrics.
Recorded versus fresh generation is descriptive, not a causal speed benchmark.
"""
import argparse
import copy
import gzip
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('capture', type=Path)
p.add_argument('--request-id', action='append', required=True)
p.add_argument('--provider', required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--generate', action='store_true')
p.add_argument('--raw-output', type=Path)
a = p.parse_args()
assert not a.generate or a.raw_output
events = []
for path in a.capture.rglob('sovereign*.gz'):
    with gzip.open(path, 'rt') as stream:
        events.extend(json.loads(line) for line in stream)

def post(path, body):
    with urlopen(Request(a.provider.rstrip('/') + path,
                        data=json.dumps(body).encode(),
                        headers={'Content-Type': 'application/json'}), timeout=600) as response:
        return json.load(response)

reports, raw = [], []
for identifier in a.request_id:
    event = next(e for e in events if e['kind'] == 'provider_request_submitted'
                 and e['correlation']['request_id'] == identifier)
    body = event['payload']['body']
    changed = copy.deepcopy(body)
    last_user = max(i for i, m in enumerate(changed['messages']) if m['role'] == 'user')
    latest = max((i for i, m in enumerate(changed['messages'])
                  if i >= last_user and m['role'] == 'assistant'
                  and any(str(m.get(k) or '').strip() for k in
                          ('reasoning', 'reasoning_content', 'reasoning_details'))), default=-1)
    for i, m in enumerate(changed['messages']):
        if m['role'] == 'assistant' and i != latest:
            for key in ('reasoning', 'reasoning_content', 'reasoning_details'):
                m.pop(key, None)
    # All ordinary content and exact tool protocol must remain byte-equivalent.
    assert [{k:v for k,v in m.items() if k not in ('reasoning', 'reasoning_content', 'reasoning_details')}
            for m in body['messages']] == [
            {k:v for k,v in m.items() if k not in ('reasoning', 'reasoning_content', 'reasoning_details')}
            for m in changed['messages']]
    def count(request, alias=True):
        measured = {k: copy.deepcopy(request[k]) for k in ('model','messages','tools','chat_template_kwargs') if k in request}
        measured['add_generation_prompt'] = True
        if alias:
            for row in measured['messages']:
                if row.get('reasoning_content'):
                    assert not row.get('reasoning') or row['reasoning'] == row['reasoning_content']
                    row['reasoning'] = row.pop('reasoning_content')
        return post('/tokenize', measured)['count']
    before, after = count(body), count(changed)
    report = {'request_id': identifier, 'uncorrected_tokenizer': count(body, False),
              'reasoning_aware_before': before, 'latest_only_after': after,
              'input_reduction_percent': round(100 * (before-after)/before, 2),
              'ordinary_content_and_tools_unchanged': True}
    if a.generate:
        changed.update(stream=False, max_tokens=8192)
        changed.pop('stream_options', None)
        started = time.monotonic()
        response = post('/v1/chat/completions', changed)
        report.update(fresh_elapsed_seconds=round(time.monotonic()-started, 2),
                      fresh_usage=response.get('usage'),
                      finish_reason=response['choices'][0]['finish_reason'])
        raw.append({'request_id': identifier, 'response': response})
        a.raw_output.write_text(json.dumps(raw, indent=2))
    reports.append(report)
    a.output.write_text(json.dumps({'scope': 'Two-request reasoning-only counterfactual; no tool execution, no live speed or strategic-equivalence claim',
                                  'requests': reports}, indent=2) + '\n')
    print(json.dumps(report), flush=True)
