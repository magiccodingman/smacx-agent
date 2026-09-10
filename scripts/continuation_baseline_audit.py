"""Replay retained transcript cuts through one installed Hermes history policy.

Run in the Hermes image with PYTHONPATH selecting the desired source revision.
Never invokes generation, mutates a game, or edits retained history. The same
captured system/tools/runtime are held fixed: this isolates history policy, not
the counterfactual behavior of a model playing without bundled decisions.
"""
import argparse
import copy
from collections import Counter
import gzip
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
import tempfile
from urllib.request import Request, urlopen

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('capture', type=Path, help='Directory with hermes-history.jsonl and sovereign*.gz')
p.add_argument('--tokenizer', required=True)
p.add_argument('--label', required=True)
p.add_argument('--output', type=Path, required=True)
p.add_argument('--verify-captured', action='store_true', help='Also count captured requests to validate replay of their deployed policy')
a = p.parse_args()
requests = []
events = []
for path in a.capture.glob('sovereign*.gz'):
    with gzip.open(path, 'rt') as stream:
        events.extend(json.loads(line) for line in stream)
events.sort(key=lambda r: r['recorded_unix'])
requests = [r for r in events if r['kind'] == 'provider_request_submitted']
history_path = a.capture / 'hermes-history.jsonl'
if history_path.exists():
    history = [json.loads(line)['payload'] for line in history_path.read_text().splitlines()]
    history_source = 'retained_message_export'
else:
    # Full opening-to-capture traces also retain original assistant responses
    # and tool results before wire cleanup. Recover boundaries from each new
    # episode's request, never from a guessed summary of the previous turn.
    history = []
    episode = None
    for event in events:
        kind, payload = event['kind'], event['payload']
        row = None
        if kind == 'provider_request_submitted' and event['correlation']['episode_id'] != episode:
            episode = event['correlation']['episode_id']
            row = copy.deepcopy(next(m for m in reversed(payload['body']['messages']) if m['role'] == 'user'))
            row['content'] = row['content'].split('<SMACX_RUNTIME_CONTEXT', 1)[0].rstrip()
        elif kind == 'sovereign_response':
            row = copy.deepcopy(payload['message'])
        elif kind == 'tool_returned':
            row = {'role': 'tool', 'content': payload['content'],
                   'tool_call_id': event['correlation']['call_id'], 'tool_name': payload['managed_name']}
        if row is not None:
            row['timestamp'] = event['recorded_unix']
            history.append(row)
    assert [m['role'] for m in requests[0]['payload']['body']['messages']] == ['system', 'user'], 'Trace must start at opening'
    history_source = 'original_response_and_tool_traces'
requests.sort(key=lambda r: r['recorded_unix'])
assert requests
system = next(m['content'] for m in requests[0]['payload']['body']['messages'] if m['role'] == 'system')
with tempfile.TemporaryDirectory() as temporary:
    prompt = Path(temporary) / 'SYSTEM.md'
    prompt.write_text(system)
    os.environ.update(SMACX_STRICT_SYSTEM_PROMPT='1', SMACX_SYSTEM_PROMPT_FILE=str(prompt),
                      SMACX_SYSTEM_PROMPT_SHA256=hashlib.sha256(prompt.read_bytes()).hexdigest(),
                      SMACX_CONTEXT_LENGTH='262144')
    import smacx_strict_prompt as strict
    importlib.reload(strict)
    strict._append_runtime_context = lambda rows: rows
    from run_agent import AIAgent
    output = []
    for event in requests:
        body = event['payload']['body']
        assert next(m['content'] for m in body['messages'] if m['role'] == 'system') == system
        rows = []
        for retained in history:
            if retained['timestamp'] > event['recorded_unix']:
                continue
            row = {k: retained[k] for k in ('role', 'content', 'tool_call_id', 'tool_calls')
                   if retained.get(k) is not None}
            if isinstance(row.get('tool_calls'), str):
                row['tool_calls'] = json.loads(row['tool_calls'])
            if retained.get('tool_name'):
                row['name'] = retained['tool_name']
            rows.append(row)
        original = copy.deepcopy(rows)
        wire = AIAgent._sanitize_api_messages(rows)
        assert original == rows, 'Replay mutated retained history'
        tool_names = {c['id']: c.get('function', {}).get('name', 'unknown')
                      for m in rows for c in (m.get('tool_calls') or [])}
        history_sizes = Counter()
        for m in wire:
            category = 'tool:' + tool_names.get(m.get('tool_call_id'), 'unknown') if m['role'] == 'tool' else m['role']
            history_sizes[category] += len(json.dumps(m, ensure_ascii=False).encode())
        envelopes = [m['content'].split(strict._RUNTIME_OPEN, 1)[1]
                     for m in body['messages'] if strict._RUNTIME_OPEN in str(m.get('content', ''))]
        assert len(envelopes) == 1
        tail = next(m for m in reversed(wire) if m['role'] in {'user', 'tool'})
        tail['content'] = (tail.get('content') or '') + '\n\n' + strict._RUNTIME_OPEN + envelopes[0]
        token_body = {k: body[k] for k in ('model', 'tools', 'chat_template_kwargs') if k in body}
        token_body.update(messages=wire, add_generation_prompt=True)
        with urlopen(Request(a.tokenizer, data=json.dumps(token_body).encode(),
                             headers={'Content-Type': 'application/json'}), timeout=60) as response:
            count = json.load(response)['count']
        captured_count = None
        if a.verify_captured:
            token_body['messages'] = body['messages']
            with urlopen(Request(a.tokenizer, data=json.dumps(token_body).encode(),
                                 headers={'Content-Type': 'application/json'}), timeout=60) as response:
                captured_count = json.load(response)['count']
            assert count == captured_count, f'Replay differs from deployed request {event["correlation"]["request_id"]}'
        output.append({'request_id': event['correlation']['request_id'],
                       'tokens': count, 'message_bytes': len(json.dumps(wire, ensure_ascii=False).encode()),
                       'captured_tokens': captured_count,
                       'history_bytes_by_category': dict(history_sizes),
                       'fixed_runtime_bytes': len(envelopes[0].encode()),
                       'assistant_prose_chars': sum(len(m.get('content') or '') for m in wire if m['role'] == 'assistant'),
                       'history_rows': len(rows), 'wire_rows': len(wire)})
    result = {'label': a.label, 'requests': output,
              'history_source': history_source,
              'policy_file': strict.__file__,
              'policy_sha256': hashlib.sha256(Path(strict.__file__).read_bytes()).hexdigest(),
              'continuation_sha256': hashlib.sha256(Path(sys.modules['smacx_continuation'].__file__).read_bytes()).hexdigest()
              if 'smacx_continuation' in sys.modules else None,
              'total_tokens': sum(r['tokens'] for r in output),
              'total_message_bytes': sum(r['message_bytes'] for r in output),
              'scope': 'Same retained transcript cuts and captured system/tools/runtime; history policy only. No generation or strategic equivalence claim.'}
    a.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'requests'}))
