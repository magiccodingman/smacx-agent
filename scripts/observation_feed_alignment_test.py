#!/usr/bin/env python3
"""A late non-contact native event must not manufacture contact identity churn."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from publication_transaction_test import setup
from cross_publication_episode_test import foreign, active, history, raw, restart

cases=[]
for broken in (False, True):
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp))
        foreign(native,5);native.revision+=1;collect().collect_once();before=active(f)
        collector=collect();original=collector._bundle;fired=[False]
        def late_bundle():
            if not fired[0]:
                fired[0]=True
                if broken:
                    native.events=[raw(1,'visible_unit_lost',5,5),raw(2,'visible_unit_appeared',5,5)]
                else:
                    native.events=[{'sequence':1,'kind':'turn_started','turn':50}]
                native.revision+=1
            return original()
        native.revision+=1
        with patch.object(collector,'_bundle',side_effect=late_bundle):
            receipt=collector.collect_once()
        after=active(f)
        assert (after!=before) if broken else (after==before), (broken,before,after)
        assert receipt['collector_metrics'].get('snapshot_feed_alignment_retries')==1
        restart(f);collect().collect_once()
        assert active(f)==after, 'stable followup changed identity again'
        if broken:
            assert any(e.get('event_kind')=='contact_lost' and e.get('contact_ref')==before for e in history(f))
        cases.append({'native_loss_and_reappearance':broken,'passed':True})
with tempfile.TemporaryDirectory() as tmp:
    from smacx_observation import ObservationCollectorError
    f,native,collect,_=setup(Path(tmp))
    foreign(native,5);native.revision+=1;collect().collect_once();before=active(f)
    collector=collect();original=collector._bundle;attempts=[0]
    def late_after_page_races():
        attempts[0]+=1
        if attempts[0]<3:
            raise ObservationCollectorError('world_changed_during_pagination')
        native.events=[{'sequence':1,'kind':'turn_started','turn':50}]
        native.revision+=1
        return original()
    native.revision+=1
    with patch.object(collector,'_bundle',side_effect=late_after_page_races):
        receipt=collector.collect_once()
    assert active(f)==before, 'pagination retries consumed the final feed catch-up opportunity'
    assert attempts[0]==3
    assert receipt['collector_metrics']['snapshot_bundle_failures']==['world_changed_during_pagination']*2
    assert receipt['collector_metrics']['snapshot_feed_alignment_retries']==1
    restart(f);collect().collect_once()
    assert active(f)==before
    cases.append({'final_snapshot_after_two_page_races_preserves_contact':True,'passed':True})
with tempfile.TemporaryDirectory() as tmp:
    f,native,collect,_=setup(Path(tmp))
    foreign(native,5);native.revision+=1;collect().collect_once();before=active(f)
    collector=collect();bridge=collector.bridge_call;probes=[0]
    def moving_probe(operation, **arguments):
        if operation=='observation_feed' and arguments.get('limit')==1:
            probes[0]+=1
            native.events.append({'sequence':probes[0], 'kind':'turn_started', 'turn':50})
            native.revision+=1
        return bridge(operation, **arguments)
    native.revision+=1;collector.bridge_call=moving_probe
    receipt=collector.collect_once()
    assert receipt['collector_metrics']['snapshot_feed_alignment_retries']==3
    assert probes[0]==6, probes
    assert active(f)!=before, 'unmatched cut incorrectly claimed continuous identity'
    cases.append({'continually_changing_cut_bounded_and_conservative':True,'passed':True})
print(json.dumps({'passed':True,'classification':'native-shaped collector and durable publication','cases':cases}))
