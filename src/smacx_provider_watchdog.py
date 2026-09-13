"""Bounded transport drain; never declares gameplay progress."""
from math import isfinite
from typing import Mapping

PROVIDER_GENERATION_SECONDS = 1200
DECISION_HANDLE_SECONDS = PROVIDER_GENERATION_SECONDS + 60


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
    if started < earliest or not started <= observed <= now:
        return False, prior
    identifier, phase = request.get('request_id'), request.get('phase')
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        return False, prior
    if prior is None:
        if started > deadline or phase not in {'submitted', 'headers', 'streaming'} or now < deadline:
            return False, None
        prior = {'request_id': identifier, 'base_deadline': deadline,
                 'hard_deadline': deadline + 180, 'started_unix': started,
                 'episode_started_unix': started}
    elif identifier != prior['request_id'] or started != prior['started_unix']:
        # Hermes admits only one provider request at a time. A different
        # request from the same run therefore proves that the latched request
        # reached a terminal boundary. Permit the active continuation while
        # retaining the original episode's absolute deadline: tool dispatch
        # cannot buy another generation window.
        episode_started = prior.get('episode_started_unix', prior.get('started_unix'))
        hard_deadline = prior.get('hard_deadline')
        if (phase not in {'submitted', 'headers', 'streaming'}
                or type(episode_started) not in (int, float) or not isfinite(episode_started)
                or type(hard_deadline) not in (int, float) or not isfinite(hard_deadline)
                or started <= prior.get('started_unix', episode_started)
                or started > observed or now >= hard_deadline):
            return False, prior
        prior = {**prior, 'request_id': identifier, 'started_unix': started,
                 'episode_started_unix': episode_started}
    # Content liveness can extend this episode, never reset gameplay progress.
    # 20 minutes from its first submission is absolute; keepalives and later
    # request handoffs cannot buy time.
    content = request.get('last_content_unix')
    if type(content) in (int, float) and isfinite(content) and started <= content <= observed:
        episode_started = prior.get('episode_started_unix', started)
        prior = {**prior, 'hard_deadline': max(deadline + 180, episode_started + PROVIDER_GENERATION_SECONDS),
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
