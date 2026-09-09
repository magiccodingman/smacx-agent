"""Replay captured provider messages without invoking a model or changing evidence."""
import copy
import glob
import gzip
import json
import statistics
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('src', 'harness')]
from smacx_continuation import preserve_continuation
from smacx_diagnostic_summary import result_object

for directory in sys.argv[1:]:
    comparisons = []
    for filename in glob.glob(directory + '/*.gz'):
        with gzip.open(filename, 'rt') as stream:
            try:
                for line in stream:
                    event = json.loads(line)
                    if event['kind'] != 'provider_request_submitted':
                        continue
                    messages = event['payload']['body']['messages']
                    original = copy.deepcopy(messages)
                    names = {}
                    protected = set()
                    last_user = max(i for i, m in enumerate(messages) if m.get('role') == 'user')
                    for i, message in enumerate(messages):
                        if message.get('role') == 'assistant':
                            calls = message.get('tool_calls') or []
                            if i >= last_user:
                                protected = {c['id'] for c in calls}
                            for call in calls:
                                names[call['id']] = call['function']['name'].removeprefix('mcp__smacx__')
                    output, metrics = preserve_continuation(messages, last_user, names, result_object, protected=protected)
                    assert messages == original
                    assert [m.get('content') for m in output if m.get('role') == 'assistant'] == [m.get('content') for m in messages if m.get('role') == 'assistant']
                    for m in messages:
                        if m.get('tool_call_id') in protected:
                            assert m in output
                    size = lambda rows: len(json.dumps(rows, ensure_ascii=False).encode())
                    comparisons.append((size(messages), size(output)))
            except EOFError:
                pass  # Only complete captured records are sampled.
    before = sum(a for a, b in comparisons)
    after = sum(b for a, b in comparisons)
    print(json.dumps({'capture': Path(directory).name, 'requests': len(comparisons),
                      'before_bytes': before, 'after_bytes': after,
                      'reduction_percent': round(100 * (1 - after / before), 2),
                      'median_before_bytes': statistics.median(a for a, b in comparisons),
                      'median_after_bytes': statistics.median(b for a, b in comparisons),
                      'assistant_prose_unchanged': True, 'latest_results_unchanged': True,
                      'durable_history_unchanged': True}))
