#!/usr/bin/env python3
"""Long provider/tool episodes must not strand unacknowledged critical attention."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from world_model_contract_test import initialized, bundle
from smacx_attention import AttentionService, AttentionError
from smacx_world_model import PerspectiveProjector
from smacx_world_types import WorldIdentity

with tempfile.TemporaryDirectory() as tmp:
    store, journal, scope, world = initialized(Path(tmp))
    identity = WorldIdentity(scope.match_id, scope.perspective_id, journal.timeline_id(scope), 'world-expiry')
    projected = PerspectiveProjector(identity).project(bundle(), observation_sequence=9)
    world.replace_projection(scope, identity, projected['objects'], observation_cursor=9,
                             action_revision='r9', continuity='complete', journal_head_hash='0'*64)
    attention = AttentionService(store, journal, scope)
    first = attention.enqueue('world_change', {'material': 'threat'}, observation_cursor=9, critical=True)
    lease = attention.lease('episode-long', ttl_seconds=30)
    attention.placed(lease['attention_lease_id'])
    attention.responded(lease['attention_lease_id'])
    with store._connect() as c:
        expires = c.execute('SELECT expires_unix FROM attention_leases WHERE attention_lease_id=?',
                            (lease['attention_lease_id'],)).fetchone()[0]
    with patch('smacx_attention.time.time', return_value=expires+1):
        renewed = attention.lease('episode-long')
    assert renewed['attention_lease_id'] != lease['attention_lease_id']
    assert [row['attention_id'] for row in renewed['items']] == [first['attention_id']]
    assert renewed['items'][0]['redelivered']
    assert attention.unacknowledged_critical()['items']
    attention.placed(renewed['attention_lease_id'])
    second = attention.enqueue('world_change', {'material': 'new threat'}, observation_cursor=9, critical=True)
    # An in-flight provider placement is immutable even when a new item arrives.
    same = attention.lease('episode-long')
    assert same['attention_lease_id'] == renewed['attention_lease_id']
    assert len(same['items']) == 1
    attention.responded(renewed['attention_lease_id'])
    refreshed = attention.lease('episode-long')
    assert {r['attention_id'] for r in refreshed['items']} == {first['attention_id'], second['attention_id']}
    attention.placed(refreshed['attention_lease_id'])
    attention.responded(refreshed['attention_lease_id'])
    for cursor, ids, code in [
        (refreshed['through_cursor'], ['attention-not-in-lease'], 'attention_ack_scope_mismatch'),
        (refreshed['through_cursor']+1, [], 'attention_ack_cursor_out_of_range'),
    ]:
        try:
            attention.acknowledge(refreshed['attention_lease_id'], through_cursor=cursor, acknowledged_ids=ids)
            raise AssertionError('invalid acknowledgement reported success')
        except AttentionError as exc:
            assert str(exc) == code
        assert len(attention.unacknowledged_critical()['items']) == 2
    acknowledged = attention.acknowledge(refreshed['attention_lease_id'], through_cursor=refreshed['through_cursor'])
    assert len(acknowledged['acknowledged_ids']) == 2
    assert not attention.unacknowledged_critical()['items']
    empty = attention.lease('episode-long')
    attention.placed(empty['attention_lease_id'])
    attention.responded(empty['attention_lease_id'])
    third = attention.enqueue('world_change', {'material': 'later threat'}, observation_cursor=9, critical=True)
    replacement = attention.lease('episode-long')
    assert [r['attention_id'] for r in replacement['items']] == [third['attention_id']]

print(json.dumps({'pass': True, 'expired_responded_redelivery': True,
                  'inflight_placement_immutable': True, 'new_critical_after_response_delivered': True,
                  'empty_lease_does_not_hide_critical': True, 'invalid_ack_no_effect': True,
                  'reviewed_valid_ack_clears_gate': True}))

# The managed tool must expose a refresh path, not another apparently successful no-op.
import smacx_mcp
class RejectedAcknowledgement:
    def acknowledge(self, *args, **kwargs):
        raise AttentionError('attention_ack_scope_mismatch')
with patch.object(smacx_mcp, '_managed_scope_identity', return_value=('m','s','a','p')), \
     patch.object(smacx_mcp, 'controller_world_service', return_value=(None,None,RejectedAcknowledgement())):
    result = smacx_mcp.smac_attention_ack('lease-empty', 32, ['attention-old'])
assert result['ok'] is False and result['acknowledged_ids'] == []
assert result['required_next']['tool'] == 'smac_decision'
print(json.dumps({'managed_refresh_guidance': True}))
