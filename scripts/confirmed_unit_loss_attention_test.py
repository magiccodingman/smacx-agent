#!/usr/bin/env python3
"""Native-shaped destruction publication, upgrade backfill, and crash retry."""
import json
import tempfile
from pathlib import Path
from observation_collector_benchmark import (
    NativeFixture, SmacxStore, MemoryScope, CampaignJournal, WorldStore,
    ObservationCollector, AttentionService,
)
from smacx_runtime_context import _attention_payload


def run(count, mode):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = SmacxStore(root / 'state.sqlite3')
        store.ensure_agent('agent-loss', 'Loss')
        store.create_match(match_id='match-loss', display_name='Loss', mode='solo')
        store.create_perspective('match-loss', 'agent-loss', perspective_id='perspective-loss')
        scope = MemoryScope('match-loss', 'agent-loss', 'perspective-loss')
        journal = CampaignJournal(root / 'campaigns', timeline_resolver=store.active_timeline_id)
        worlds = WorldStore(store, root / 'snapshots')
        attention = AttentionService(store, journal, scope)
        fixture = NativeFixture(32, 16, contacts=1, ready_drop_units=count + 1)
        def collector():
            return ObservationCollector(scope=scope, session_id='session-loss', bridge_call=fixture,
                journal=journal, world_store=worlds, attention=attention)
        collector().collect_once()
        for index, unit in enumerate(fixture.units):
            fixture.events.append(dict(sequence=index + 1,
                kind='visible_unit_lost' if index == len(fixture.units) - 1 else 'visible_unit_destroyed',
                turn=50, subject_a=int(unit['native_observation_key'].split('-')[-1]),
                subject_b=1 if unit['owned'] else 2, from_tile_id=unit['tile_id'],
                to_tile_id=unit['tile_id'], continuous_visibility=True,
                item_name='support_shortage' if index % 2 == 0 else 'unreviewed_cause'))
        fixture.units = []
        fixture.revision += 1
        capture = attention.capture_confirmed_unit_losses
        if mode in {'upgrade', 'upgrade_acknowledged'}:
            attention.capture_confirmed_unit_losses = lambda *args, **kwargs: None
        elif mode == 'crash':
            def crash(*args, **kwargs):
                raise RuntimeError('injected_loss_capture_failure')
            attention.capture_confirmed_unit_losses = crash
        try:
            collector().collect_once()
            assert mode != 'crash'
        except RuntimeError as error:
            assert mode == 'crash' and str(error) == 'injected_loss_capture_failure'
        if mode == 'upgrade_acknowledged':
            old = attention.lease('episode-old')
            attention.placed(old['attention_lease_id'])
            attention.responded(old['attention_lease_id'])
            attention.acknowledge(old['attention_lease_id'], through_cursor=old['through_cursor'])
        attention.capture_confirmed_unit_losses = capture
        if mode == 'crash':
            collector().collect_once()
        lease = attention.lease('episode-loss')
        with store._connect() as connection:
            rows = connection.execute("SELECT * FROM attention_items WHERE attention_kind='unit_losses'").fetchall()
        assert len(rows) == 1, (mode, len(rows))
        row = dict(rows[0])
        payload = json.loads(row['payload_json'])
        assert row['critical'] == 1 and row['priority'] == 100
        assert payload['event_count'] == count, payload
        assert len(payload['events']) == min(count, 8)
        assert all(event['unit_ref'].startswith('own-unit-2') for event in payload['events'])
        assert f'own-unit-{2000 + count}' not in json.dumps(payload), 'ambiguous loss became destruction'
        assert 'contact-' not in json.dumps(payload)
        assert payload['source_journal_event_ids'] and payload['evidence_kind'] == 'confirmed_owned_unit_destruction'
        assert worlds.confirmed_unit_losses_at(scope, attention.timeline_id, payload['observation_cursor'] + 100)['event_count'] == 0
        assert worlds.confirmed_unit_losses_at(scope, 'timeline-other', payload['observation_cursor'])['event_count'] == 0
        compact = _attention_payload({**row, 'payload': payload})
        assert compact['event_count'] == count and compact['events']
        for event in compact['events']:
            handle = int(event['unit_ref'].split('-')[-1])
            original_index = next(i for i, raw in enumerate(fixture.events) if raw['subject_a'] == handle)
            if original_index % 2 == 0:
                assert event['removal_cause'] == 'support_shortage', event
                assert event['cause_source'] == 'native_support_disband_call', event
                assert 'not a combat loss' in event['meaning']
            else:
                assert 'removal_cause' not in event, event

        assert lease['items'][0]['attention_kind'] == 'unit_losses'
        attention.placed(lease['attention_lease_id'])
        attention.responded(lease['attention_lease_id'])
        attention.acknowledge(lease['attention_lease_id'], through_cursor=lease['through_cursor'])
        AttentionService(store, journal, scope).capture_confirmed_unit_losses(payload['observation_cursor'])
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM attention_items WHERE attention_kind='unit_losses'").fetchone()[0] == 1
        assert journal.verify(scope)['ok']
        return dict(mode=mode, confirmed=count, bounded_details=len(payload['events']), journal_verified=True)


if __name__ == '__main__':
    print(json.dumps({'passed': True, 'cases': [run(5, 'normal'), run(5, 'upgrade'), run(5, 'upgrade_acknowledged'), run(5, 'crash'), run(64, 'normal')]}))
