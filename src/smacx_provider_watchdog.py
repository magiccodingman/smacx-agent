"""Bounded transport drain; never declares gameplay progress."""
from math import isfinite
from typing import Mapping


def provider_drain_window(*, run_id, now, progress_since, stall_seconds, request, previous=None,
                          progress_observed_after=None):
    deadline = progress_since + stall_seconds
    prior = previous if isinstance(previous, Mapping) and previous.get('base_deadline') == deadline else None
    if not isinstance(request, Mapping) or request.get('run_id') != run_id:
        return False, prior
    started, observed = request.get('started_unix'), request.get('observed_unix')
    if any(type(x) not in (int, float) or not isfinite(x) for x in (started, observed)):
        return False, prior
    # Progress occurred between native samples; its detection time is not its
    # execution time. A request begun in that interval may follow the effect.
    earliest = progress_since if progress_observed_after is None else progress_observed_after
    if type(earliest) not in (int, float) or not isfinite(earliest) or earliest > progress_since:
        return False, prior
    if not earliest <= started <= deadline or not started <= observed <= now:
        return False, prior
    identifier, phase = request.get('request_id'), request.get('phase')
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        return False, prior
    if prior is None:
        if phase not in {'submitted', 'headers', 'streaming'} or now < deadline:
            return False, None
        prior = {'request_id': identifier, 'base_deadline': deadline,
                 'hard_deadline': deadline + 180, 'started_unix': started}
    if identifier != prior['request_id'] or started != prior['started_unix']:
        return False, prior
    # Content liveness can extend this one request, never reset gameplay progress.
    # 20 minutes from submission is absolute; keepalives cannot buy time.
    content = request.get('last_content_unix')
    if type(content) in (int, float) and isfinite(content) and started <= content <= observed:
        prior = {**prior, 'hard_deadline': max(deadline + 180, started + 1200),
                 'last_content_unix': content}
        if phase == 'streaming' and now - content >= 120:
            return False, {**prior, 'stop_reason': 'provider_stream_silent'}
    if now >= prior['hard_deadline']:
        return False, {**prior, 'stop_reason': 'provider_generation_budget_exceeded' if 'last_content_unix' in prior else 'provider_transport_budget_exceeded'}
    if phase in {'submitted', 'headers', 'streaming'}:
        return True, prior
    # Give the completed response a bounded dispatch interval, never another
    # reasoning request. Failure/early close do not earn dispatch grace.
    if phase == 'completed' and now < min(observed + 30, prior['hard_deadline']):
        return True, prior
    return False, prior
