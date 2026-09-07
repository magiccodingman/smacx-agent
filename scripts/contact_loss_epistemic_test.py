#!/usr/bin/env python3
"""Sentinel native coordinates remain gaps, including durable collector restart."""
import json
from pathlib import Path
import tempfile
from publication_transaction_test import setup
from cross_publication_episode_test import foreign, active, history, raw, restart
from smacx_runtime_context import _attention_payload
from smacx_world_types import provider_safe
import smacx_mcp as mcp
from unittest.mock import patch

assert provider_safe({'event_kind':{},'path':7})=={'event_kind':{},'path':7}
assert provider_safe({'event_kind':'contact_moved','path':7})=={'event_kind':'contact_moved','path':7}
historical={'event_kind':'contact_moved','contact_ref':'contact-test','from_location_ref':'location-1856',
            'to_location_ref':'location--1','path':[{'from_location_ref':'location-1856','to_location_ref':'location--1','continuous_visibility':True}]}
original=json.dumps(historical,sort_keys=True)
projected=provider_safe(historical)
assert projected['event_kind']=='movement_observation_incomplete' and projected['to_location_ref'] is None
assert 'location--1' not in json.dumps(projected) and projected['outcome']=='not_established'
assert provider_safe(projected)==projected and json.dumps(historical,sort_keys=True)==original
combat={'action_id':3,'status':'completed','resolution':'native_combat_resolved','native_result':0}
with patch.object(mcp,'_call',return_value={'ok':True,'action':combat}):
    receipt=mcp._await_deferred_action({'ok':True,'queued':True,'action_id':3})
    assert receipt['completed'] and receipt['execution']==combat
    assert 'does not establish attacker survival' in receipt['completion_semantics']
for before, after in ((5,-1),(-1,5),(-1,-1)):
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp))
        foreign(native,5);native.revision+=1;collect().collect_once()
        ref=active(f)
        native.events=[raw(1,'visible_unit_moved',before,after),raw(2,'visible_unit_lost',-1,-1)]
        foreign(native,None);native.revision+=1
        collect().collect_once();restart(f);collect().collect_once()
        events=history(f)
        assert 'location--1' not in json.dumps(events), events
        incomplete=[e for e in events if e['event_kind']=='movement_observation_incomplete']
        assert len(incomplete)==1, events
        if before == 5: assert incomplete[0]['contact_ref']==ref
        else: assert incomplete[0]['contact_ref']!=ref, 'unknown origin must not stitch identity'
        assert incomplete[0]['outcome']=='not_established' and not incomplete[0]['continuous_visibility']
        assert any(e['event_kind']=='contact_lost' and e['contact_ref']==ref for e in events)
        assert not any(e['event_kind'] in ('contact_destroyed','contact_moved') for e in events)
with tempfile.TemporaryDirectory() as tmp:
    f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1;collect().collect_once()
    native.events=[raw(1,'visible_unit_moved',5,0)];foreign(native,0);native.revision+=1
    collect().collect_once()
    assert any(e['event_kind']=='contact_moved' and e['to_location_ref']=='location-0' for e in history(f))
with tempfile.TemporaryDirectory() as tmp:
    f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1;collect().collect_once()
    original=active(f)
    native.events=[raw(1,'visible_unit_moved',5,-1)];foreign(native,None);native.revision+=1
    collect().collect_once();restart(f)
    native.events.append(raw(2,'visible_unit_moved',5,6));foreign(native,6);native.revision+=1
    collect().collect_once()
    assert active(f)!=original, 'unavailable endpoint must break cross-publication identity proof'
    assert 'location--1' not in json.dumps(history(f))
for kind, payload in (
    ('world_change',{'delta':{'object_ref':'contact-test','change':'removed'}}),
    ('world_changes',{'deltas':[{'object_ref':'contact-test','change':'removed'}]}),
    ('world_changes',{'deltas':[{'object_ref':'contact-test','change':'removed'}, {'change':'changed','detail':'x'*5000}]}),
):
    compact=_attention_payload({'attention_kind':kind,'payload':payload})
    assert 'does not prove destruction' in compact['removal_semantics'], compact
    assert 'removal_semantics' not in payload, 'runtime projection mutated durable attention'
assert 'removal_semantics' not in _attention_payload({'attention_kind':'world_change','payload':{'delta':{'change':'changed'}}})
print(json.dumps({'passed':True,'evidence':'native-shaped durable collector replay and runtime projection',
 'negative_endpoints_are_explicit_gaps':True,'loss_not_promoted_to_destruction':True,'zero_tile_preserved':True,
 'restart_preserves_semantics':True,'old_and_new_removal_attention_qualified':True}))
