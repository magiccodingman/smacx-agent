#!/usr/bin/env python3
"""Read-only, single-perspective activity-window report; never infer turn completeness."""
import argparse
import collections
import json
from pathlib import Path
import statistics
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'src'),
                str(Path(__file__).resolve().parents[1] / 'harness')]
from smacx_strict_prompt import _managed_tool_result
from smacx_diagnostics import provider_chunk_has_content


def report(events, start, end):
    requests, calls, seen = {}, {}, set()
    for e in sorted(events, key=lambda x: x.get('recorded_unix', 0)):
        if e.get('event_id') and e['event_id'] in seen:
            continue
        seen.add(e.get('event_id'))
        t = e.get('recorded_unix', 0)
        if not start <= t <= end:
            continue
        p, c = e.get('payload', {}), e.get('correlation', {})
        kind = p.get('type')
        if kind in {'response_started', 'response_delta', 'response_finished'}:
            key = (c.get('run_id'), c.get('request_id'))
            r = requests.setdefault(key, {})
            if kind == 'response_started': r['start'] = t
            if kind == 'response_finished': r.update(end=t, complete=p.get('complete') is True)
            for chunk in p.get('chunks', []):
                if provider_chunk_has_content(chunk): r.setdefault('first_content', t)
                if isinstance(chunk.get('usage'), dict): r['usage'] = chunk['usage']
        if kind in {'tool_requested', 'tool_returned'}:
            key = (c.get('run_id'), c.get('call_id'))
            call = calls.setdefault(key, {})
            if kind == 'tool_requested':
                call.update(start=t, name=p.get('managed_name', '').split('__')[-1])
            else:
                call.update(end=t, result=_managed_tool_result(p.get('content')) or {})
    complete = [r for r in requests.values() if r.get('complete') and 'start' in r and 'end' in r]
    def stats(values):
        return {'samples': len(values), 'sum_seconds': round(sum(values), 3),
                'median_seconds': round(statistics.median(values), 3) if values else None}
    outcomes = collections.Counter()
    per_tool = collections.defaultdict(list)
    for c in calls.values():
        if 'start' not in c or 'end' not in c:
            outcomes['incomplete_call'] += 1
            continue
        per_tool[c['name']].append(c['end']-c['start'])
        r = c['result']
        if not r:
            outcomes['undecoded_result'] += 1
        elif not r.get('ok'):
            error = r.get('error')
            outcomes['rejected:' + str(error.get('code') if isinstance(error, dict) else error)] += 1
        elif c['name'] == 'smac_execute_choice':
            outcomes['execution:' + str(r.get('execution_status', 'unspecified'))] += 1
        else:
            outcomes['successful:' + c['name']] += 1
    usage = [r['usage'] for r in complete if 'usage' in r]
    cached = [(u.get('prompt_tokens_details') or {}).get('cached_tokens') for u in usage]
    cached = [v for v in cached if isinstance(v, int)]
    return {
        'schema': 'smacx.turn-latency-window.v1', 'start_unix': start, 'end_unix': end,
        'wall_seconds': end-start, 'complete_faction_turn_verified': False,
        'completed_responses': len(complete), 'partial_responses': len(requests)-len(complete),
        'provider': stats([r['end']-r['start'] for r in complete]),
        'time_to_first_content': stats([r['first_content']-r['start'] for r in complete if 'first_content' in r]),
        'content_to_finish': stats([r['end']-r['first_content'] for r in complete if 'first_content' in r]),
        'tools': {name: stats(values) for name, values in sorted(per_tool.items())},
        'outcomes': dict(outcomes), 'usage_samples': len(usage),
        'prompt_tokens': sum(u.get('prompt_tokens', 0) for u in usage) if usage else None,
        'cache_usage_samples': len(cached),
        'cached_tokens': sum(cached) if cached else None,
        'limits': ['Single-perspective input required; verify turn boundaries independently.',
                   'Cumulative request/tool durations may overlap; do not sum as wall time.',
                   'TTFT combines server queue, prefill and transport; no server attribution.',
                   'Accepted execution is not effect verification. Missing usage is unavailable.',
                   'Context assembly is outside this activity stream; not measured here.']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--start', required=True, type=float)
    p.add_argument('--end', required=True, type=float)
    a=p.parse_args()
    if a.end <= a.start: p.error('--end must follow --start')
    text=a.input.read_text()
    events=json.loads(text) if text.lstrip().startswith('[') else [json.loads(line) for line in text.splitlines() if line.strip()]
    print(json.dumps(report(events,a.start,a.end),indent=2))

if __name__ == '__main__': main()
