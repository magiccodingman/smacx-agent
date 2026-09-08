#!/usr/bin/env python3
"""Native-shaped raid occurrences survive publication and enter leased attention."""
import json
import tempfile
from pathlib import Path
from publication_transaction_test import setup
from smacx_runtime_context import _attention_payload

with tempfile.TemporaryDirectory() as tmp:
    f, native, collect, _ = setup(Path(tmp))
    native.events = [dict(sequence=i, kind='owned_native_raid_effect', turn=50,
        subject_a=0, subject_b=-1, from_tile_id=0, to_tile_id=0,
        value_before=before, value_after=after, item_name=effect)
        for i, (effect, before, after) in enumerate((('population',3,2),('stored_minerals',9,0),('Recreation Commons',1,0)),1)]
    native.revision += 1
    collect().collect_once()
    lease = f.attention.lease('episode-raid')
    rows = [i for i in lease['items'] if i['attention_kind']=='native_raid']
    assert len(rows)==3, rows
    payloads = [_attention_payload(i) for i in rows]
    assert {p['effect'] for p in payloads} == {'population','stored_minerals','Recreation Commons'}
    assert all(p['base_ref']=='base-location-0' and p['turn']==50 for p in payloads)
    assert all(i['critical'] for i in rows)
    mineral = next(p for p in payloads if p['effect']=='stored_minerals')
    assert mineral['observed_before']==9 and mineral['observed_after']==0
    f.attention.placed(lease['attention_lease_id']);f.attention.responded(lease['attention_lease_id'])
    f.attention.acknowledge(lease['attention_lease_id'], through_cursor=lease['through_cursor'])
    collect().collect_once()
    with f.store._connect() as c:
        assert c.execute("select count(*) from attention_items where attention_kind='native_raid'").fetchone()[0]==3
    assert f.journal.verify(f.scope)['ok']
    print(json.dumps({'passed':True,'raid_effects':payloads,'scope':'native-shaped adapter/publication/attention; not running-game comparison'}))
