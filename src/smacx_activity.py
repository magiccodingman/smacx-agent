"""Read-only spectator projection of emitted sovereign diagnostics, never prompts."""
import json
import os
import re
from pathlib import Path


def public_value(value):
    if isinstance(value, dict):
        return {k: '[redacted]' if re.fullmatch(r'authorization|cookie|set-cookie|password|api[_-]?key|access[_-]?token|refresh[_-]?token|secret', k, re.I)
                else public_value(v) for k,v in value.items()}
    if isinstance(value, list): return [public_value(v) for v in value]
    if isinstance(value, str):
        for name in ('SMACX_PROVIDER_API_KEY', 'OPENAI_API_KEY'):
            secret = os.environ.get(name)
            if secret: value = value.replace(secret, '[redacted]')
        value = re.sub(r'(?i)Bearer\s+[A-Za-z0-9_.~+/-]+=*', 'Bearer [redacted]', value)
        if value.lstrip().startswith(('{','[')):
            try: return json.dumps(public_value(json.loads(value)), ensure_ascii=False)
            except ValueError: pass
    return value


def project(kind, payload):
    # Select before sanitizing: provider requests can contain very large prompt
    # histories, none of which belong in this projection.
    return public_value(_project(kind, payload))


def _project(kind, payload):
    if kind == 'provider_request_submitted':
        body = payload.get('body', {})
        if isinstance(body, str):
            try: body = json.loads(body)
            except ValueError: body = {}
        return {'type': 'response_started', 'model': body.get('model'),
                'reasoning_effort': body.get('reasoning_effort')}
    if kind == 'provider_activity_delta':
        return {'type': 'response_delta', 'chunks': payload.get('chunks', [])}
    if kind == 'provider_response_body':
        return {'type': 'response_body', 'body': payload.get('body', {})}
    if kind == 'provider_response_stream':
        return {'type': 'response_finished', 'complete': payload.get('done_marker_observed', False),
                'truncated': payload.get('capture_truncated', False)}
    if kind in ('tool_requested', 'tool_returned', 'tool_validation_rejected', 'tool_batch_finished', 'capture_gap', 'provider_transport_failed'):
        return {'type': kind, **payload}
    return None


def read_page(root, match_id, cursor='', limit=150):
    """Byte cursors only advance past complete lines; no global history rescan."""
    directory = Path(root) / match_id
    try:
        offsets = json.loads(cursor) if cursor else {}
        if not isinstance(offsets, dict) or len(offsets) > 256: raise ValueError()
        if any(not isinstance(k, str) or type(v) is not int or v < 0 for k,v in offsets.items()): raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('invalid_activity_cursor')
    events, gaps, more, advanced = [], [], False, False
    paths = sorted(directory.glob('activity-*.jsonl')) if directory.is_dir() else []
    if set(offsets) - {p.name for p in paths}: gaps.append('activity_history_no_longer_retained')
    if len(paths) > 256: gaps.append('activity_stream_limit')
    budget = 1024 * 1024
    for path in paths[:256]:
        if path.is_symlink(): continue
        start = offsets.get(path.name, 0)
        if start > path.stat().st_size:
            gaps.append('activity_stream_replaced'); start = 0
        with path.open('rb') as stream:
            stream.seek(start)
            while len(events) < limit and budget > 0:
                line = stream.readline(262145)
                if not line: break
                if not line.endswith(b'\n'):
                    if len(line) > 262144: gaps.append('activity_record_too_large')
                    else: gaps.append('partial_activity_tail')
                    break
                budget -= len(line)
                offsets[path.name] = stream.tell()
                advanced = True
                try:
                    event = json.loads(line)
                    if event.get('match_id') == match_id: events.append(event)
                except ValueError: gaps.append('invalid_activity_record')
            more |= stream.tell() < path.stat().st_size
        if len(events) >= limit or budget <= 0:
            more |= path != paths[-1]
            break
    if (directory / '.capacity-exhausted').exists(): gaps.append('diagnostic_capacity_exhausted')
    return {'events': events, 'cursor': json.dumps(offsets, separators=(',', ':')),
            'has_more': more and advanced, 'gaps': sorted(set(gaps)), 'available': bool(paths),
            'schema': 'smacx.ai-activity.v1'}
