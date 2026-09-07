#!/usr/bin/env python3
import json
from smacx_observation import _provider_safe_temporal_events

def location(features, status='current'):
    return {'kind':'location','fields':{'features':{'value':features,'epistemic_status':status}}}
new=location(['fungus'])
events=_provider_safe_temporal_events([{'object_ref':f'location-{i}','change':'appeared','current':new} for i in range(1000)],[],turn=3)
assert len(events)==1 and events[0]['event_kind']=='known_extent_increased'
assert events[0]['newly_known_location_count']==1000 and len(events[0]['sample_location_refs'])==8
assert events[0]['newly_known_features_by_freshness']=={'fungus:current':1000}
assert events[0]['physical_creation_inferred'] is False
for status, expected in [('stale','knowledge_refresh'),('current','observed_values_changed')]:
    events=_provider_safe_temporal_events([{'object_ref':'location-1','change':'changed','previous':location([],status),'current':new}],[],turn=4)
    assert events[0]['change_basis']==expected and events[0]['cause']=='not_determined'
print(json.dumps({'passed':True,'discovery_aggregated':True,'stale_refresh_not_growth':True,'observed_change_not_attributed_cause':True}))
