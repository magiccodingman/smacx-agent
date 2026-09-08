#!/usr/bin/env python3
import tempfile
from pathlib import Path
from world_model_contract_test import initialized, bundle
from smacx_world import WorldService
from smacx_world_model import PerspectiveProjector,estimate_tokens
from smacx_world_types import WorldIdentity,WorldObject
with tempfile.TemporaryDirectory() as tmp:
    _,_,scope,world=initialized(Path(tmp))
    identity=WorldIdentity(scope.match_id,scope.perspective_id,'timeline-main','world-test')
    objects=PerspectiveProjector(identity).project(bundle(),observation_sequence=9)['objects']
    objects=[x.as_dict() for x in objects]
    units=[x for x in objects if x['kind']=='own_unit'];assert units
    for unit in units:
        unit['fields']['large_evidence']={**unit['fields']['name'],'value':'x'*9000,'epistemic_status':'stale'}
    world.replace_projection(scope,identity,[WorldObject.from_dict(x) for x in objects],observation_cursor=9,action_revision='a',continuity='complete',journal_head_hash='0'*64)
    service=WorldService(world,scope)
    expected={x['object_ref'] for x in objects if x['kind'] in {'own_unit','foreign_contact'} and x.get('status')=='active'}
    seen=[];cursor=''
    for _ in range(len(expected)+1):
        listed=service.query(mode='forces',detail='compact',continuation=cursor,context_length=262144)
        assert listed.get('ok') and listed['items'],listed
        assert estimate_tokens(listed)<=512
        seen.extend(row['object_ref'] for row in listed['items'])
        cursor=listed['continuation']
        if not cursor:break
    assert set(seen)==expected and len(seen)==len(expected),(seen,expected)
    page=service.query(mode='forces',subject_refs=[units[0]['object_ref']],detail='compact',context_length=262144)
    assert page.get('ok') and page['items'],page
    assert estimate_tokens(page)<=512
    row=page['items'][0];assert row['omitted_field_count']>0
    deep=service.query(**row['detail_query'],context_length=262144)
    assert deep['ok'] and deep['items'][0]['fields']['large_evidence']['epistemic_status']=='stale'
    import copy
    roster_objects=[x for x in objects if x['kind'] not in {'own_unit','foreign_contact'}]
    for index in range(5):
        unit=copy.deepcopy(units[0]);unit['object_ref']=f'own-unit-roster-{index}'
        unit['fields']['hp']={**unit['fields']['name'],'value':6,'epistemic_status':'stale'}
        roster_objects.append(unit)
    world.replace_projection(scope,identity,[WorldObject.from_dict(x) for x in roster_objects],observation_cursor=10,action_revision='b',continuity='complete',journal_head_hash='0'*64)
    roster=service.query(mode='forces',detail='roster',context_length=262144)
    assert roster['ok'] and len(roster['items'])==5 and not roster['continuation'],roster
    assert estimate_tokens(roster)<=2048
    assert all(x['fields']['hp']['epistemic_status']=='stale' for x in roster['items'])
    full=service.query(**roster['items'][0]['detail_query'],context_length=262144)
    assert full['ok'] and full['items'][0]['fields']['large_evidence']['epistemic_status']=='stale'
    assert 'detail=roster' in full['query_hint']
    from smacx_world import WorldQueryError
    try:service.query(mode='base',detail='roster')
    except WorldQueryError as error:assert str(error)=='roster_detail_requires_forces'
    else:raise AssertionError('roster accepted outside forces')
    large=[x for x in roster_objects if x['kind']!='own_unit']
    for index in range(40):
        unit=copy.deepcopy(roster_objects[-1]);unit['object_ref']=f'own-unit-many-{index}';large.append(unit)
    world.replace_projection(scope,identity,[WorldObject.from_dict(x) for x in large],observation_cursor=11,action_revision='c',continuity='complete',journal_head_hash='0'*64)
    seen=[];cursor=''
    for _ in range(41):
        page=service.query(mode='forces',detail='roster',continuation=cursor,context_length=262144)
        assert page['ok'] and page['items'] and estimate_tokens(page)<=2048,page
        seen.extend(x['object_ref'] for x in page['items'])
        cursor=page['continuation']
        if not cursor:break
    assert len(seen)==40 and len(set(seen))==40,seen
    print('five-unit roster tokens:',estimate_tokens(roster))
print('compact forces and deep evidence retrieval: passed')
