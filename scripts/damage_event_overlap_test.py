#!/usr/bin/env python3
"""Native step history and snapshot intervals must not imply additive damage."""
import json
from smacx_observation import _provider_safe_temporal_events

def damage(before, after, ref='contact-test', turn=86):
    return dict(event_kind='contact_damaged', contact_ref=ref, turn=turn,
                observed_hp_before=before, observed_hp_after=after)

def reconcile(steps):
    delta=dict(change='changed', object_ref='contact-test',
               previous=dict(kind='foreign_contact', fields={'hp': {'value': 7}}),
               current=dict(kind='foreign_contact', fields={'hp': {'value': 1}}))
    original=json.dumps(steps, sort_keys=True)
    result=_provider_safe_temporal_events([delta], steps, turn=86)
    assert json.dumps(steps, sort_keys=True)==original
    return result

steps=[damage(7,4), damage(4,1)]
assert reconcile(steps)==steps, 'recorded turn-86 overlap must retain only its two native steps'
assert reconcile([damage(7,1)])==[damage(7,1)]
for steps in ([], [damage(4,1)], [damage(7,4)], [damage(4,1),damage(7,4)],
              [damage(7,1,ref='other')], [damage(7,1,turn=85)],
              [damage(7,None)], [damage(7,8),damage(8,1)]):
    result=reconcile(steps)
    summary=result[-1]
    assert summary['evidence_kind']=='snapshot_interval_change'
    assert 'must not be added' in summary['aggregation_semantics']
    assert summary['observed_hp_before']==7 and summary['observed_hp_after']==1
    assert result[:-1]==steps, 'partial or unrelated evidence must not disappear'
print(json.dumps({'passed':True,'evidence':'recorded native-shaped interval reconciliation',
                  'complete_chain_not_double_counted':True,'incomplete_evidence_preserved_and_qualified':True}))
