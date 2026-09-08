#!/usr/bin/env python3
"""Production constraint context: epistemic boundaries and bounded live-shaped evidence."""
from copy import deepcopy
from smacx_runtime_context import _force_summary


def base(ref, surplus=0, cost=10, accumulated=1):
    values = dict(owner_ref='faction-1', production_name='Scout Patrol',
                  mineral_surplus=surplus, production_cost=cost, minerals_accumulated=accumulated)
    return dict(kind='base', status='active', object_ref=ref, fields={
        k: dict(value=v, epistemic_status='current', source='owned_state',
                last_verified_turn=27, provenance_ref='observation-168') for k,v in values.items()})


def summary(rows):
    return _force_summary({'world_revision':11, 'objects':rows})['production_constraints']


def main():
    current=base('base-home')
    result=summary([current])
    assert result['no_positive_mineral_surplus_count']==1
    assert result['bases'][0]['base_ref']=='base-home'
    assert result['bases'][0]['last_verified_turn']==27
    assert 'before the final ready unit' in result['review']
    assert summary([base('negative',-1)])['no_positive_mineral_surplus_count']==1
    for row in [base('positive',1),base('complete',0,10,10),base('non-mineral',0,0,0)]:
        assert summary([row])['no_positive_mineral_surplus_count']==0
    for field in ['mineral_surplus','production_cost','minerals_accumulated']:
        for state in ['stale','conditional','unknown']:
            row=deepcopy(current);row['fields'][field]['epistemic_status']=state
            result=summary([row]);assert not result['bases']
            assert result['missing_or_noncurrent_evidence_count']==1
        for value in [None,False,'0']:
            row=deepcopy(current);row['fields'][field]['value']=value
            assert not summary([row])['bases']
    row=deepcopy(current);row['fields']['owner_ref']['source']='observed'
    assert not summary([row])['bases']
    row=deepcopy(current);row['status']='removed'
    assert not summary([row])['bases']
    result=summary([base(f'base-{i:03}') for i in range(100)])
    assert len(result['bases'])==8 and result['details_truncated']
    assert result['no_positive_mineral_surplus_count']==100
    print('production constraint context: passed')

if __name__=='__main__':main()
