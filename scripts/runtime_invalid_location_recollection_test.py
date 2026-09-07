#!/usr/bin/env python3
"""Real collector rejects sentinel locations without publishing or losing its cut."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from observation_collector_benchmark import (
    NativeFixture, SmacxStore, MemoryScope, CampaignJournal, WorldStore,
    ObservationCollector, AttentionService,
)
import smacx_mcp as mcp


def run(owned):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = SmacxStore(root / 'state.sqlite3')
        store.ensure_agent('agent-location', 'Location')
        store.create_match(match_id='match-location', display_name='Location', mode='solo')
        store.create_perspective('match-location', 'agent-location', perspective_id='perspective-location')
        scope = MemoryScope('match-location', 'agent-location', 'perspective-location')
        journal = CampaignJournal(root / 'campaigns', timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / 'snapshots')
        attention = AttentionService(store, journal, scope)
        fixture = NativeFixture(32, 16, contacts=1, ready_drop_units=1)
        collector = ObservationCollector(scope=scope, session_id='session-location', bridge_call=fixture,
            journal=journal, world_store=worlds, attention=attention)
        collector.collect_once()
        cap = worlds.committed_cursor(scope, collector.timeline_id)
        unit = next(u for u in fixture.units if u['owned'] == owned)
        original = unit['tile_id']
        attempts = 0
        def refresh():
            nonlocal attempts
            attempts += 1
            unit['tile_id'] = -1 if attempts < 3 else original
            fixture.revision += 1
            try:
                result = collector.collect_once()
            except (ValueError, RuntimeError) as error:
                assert str(error) == 'invalid_tile_id'
                assert worlds.committed_cursor(scope, collector.timeline_id) == cap
                assert not worlds.temporal_events_since(scope, collector.timeline_id, cap)
                return {'ok': False, 'error': str(error)}
            return result
        with patch.object(mcp, '_refresh_managed_world', side_effect=refresh):
            result = mcp._refresh_request_world('episode-location')
        assert result['ok'] and attempts == 3
        assert worlds.committed_cursor(scope, collector.timeline_id) > cap
        assert journal.verify(scope)['ok']
        assert 'location--1' not in json.dumps(journal.replay(scope))
        return {'owned': owned, 'attempts': attempts, 'invalid_cut_never_published': True}


if __name__ == '__main__':
    print(json.dumps({'passed': True, 'cases': [run(True), run(False)]}))
