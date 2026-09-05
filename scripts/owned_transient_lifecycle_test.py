#!/usr/bin/env python3
"""Owned native births absent from both snapshot endpoints retain lifecycle identity."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from publication_transaction_test import setup
from cross_publication_episode_test import restart, history
from smacx_temporal_episodes import advance_episodes
from smacx_world_types import WorldIdentity


def birth(sequence=1):
    return dict(sequence=sequence,kind='owned_production_completed',turn=50,subject_a=0,
                subject_b=8,from_tile_id=0,to_tile_id=0,value_before=0,value_after=777,item_name='Scout Patrol')


def event(sequence,kind):
    return dict(sequence=sequence,kind='visible_unit_'+kind,turn=50,subject_a=777,subject_b=1,
                from_tile_id=0,to_tile_id=2,continuous_visibility=True,relationship_at_occurrence='self',
                value_before=10,value_after=5)


def main():
    cases=[]
    for middle in (None,'moved','damaged','lost'):
        for split in (False,True):
            for retry in (False,True):
                with tempfile.TemporaryDirectory() as tmp:
                    f,native,collect,_=setup(Path(tmp))
                    assert all(u.get('own_unit_ref')!='own-unit-777' for u in native.units)
                    rows=[birth()]
                    if middle: rows.append(event(2,middle))
                    rows.append(event(len(rows)+1,'destroyed'))
                    if split:
                        native.events=rows[:1];native.revision+=1;collect().collect_once()
                        restart(f)
                    native.events=rows;native.revision+=1
                    from smacx_observation import ObservationCollector
                    def run():
                        return ObservationCollector(scope=f.scope,session_id='session-review',bridge_call=native,
                            journal=f.journal,world_store=f.worlds,attention=f.attention).collect_once()
                    if retry:
                        original=f.worlds.acknowledge_native_observation_publication
                        with patch.object(f.worlds,'acknowledge_native_observation_publication',side_effect=RuntimeError('retry')):
                            try:run()
                            except RuntimeError:pass
                            else:raise AssertionError('missing injected failure')
                        restart(f)
                    run();run()
                    assert not any(r.get('object_ref')=='own-unit-777' for r in f.worlds.load(f.scope,f.attention.timeline_id)['objects'])
                    canonical=[semantic for record in f.journal.events_after(f.scope,None,limit=1000)
                               if record['event_type']=='observation.semantic_batch'
                               for semantic in record['payload']['events']]
                    assert sum(r.get('event_kind')=='production_completed' and r.get('unit_ref')=='own-unit-777' for r in canonical)==1
                    assert sum(r.get('event_kind')=='unit_destroyed' and r.get('unit_ref')=='own-unit-777' for r in canonical)==1
                    assert not any(r.get('event_kind')=='contact_destroyed' for r in canonical)
                    rows_out=[r for r in history(f) if r.get('unit_ref')=='own-unit-777']
                    kinds=[r['event_kind'] for r in rows_out]
                    assert kinds.count('production_completed')==1,kinds
                    assert kinds.count('unit_destroyed')==1,kinds
                    if middle and middle != 'lost':assert kinds.count('unit_'+middle)==1,kinds
                    assert not any(r.get('event_kind')=='contact_destroyed' for r in history(f))
                    # Reuse after termination is explicitly foreign and must not inherit birth proof.
                    reuse=event(len(rows)+1,'destroyed');reuse['relationship_at_occurrence']='hostile'
                    native.events=rows+[reuse];native.revision+=1;run()
                    assert len([r for r in history(f) if r.get('event_kind')=='contact_destroyed'])==1
                    assert len([r for r in history(f) if r.get('event_kind')=='unit_destroyed' and r.get('unit_ref')=='own-unit-777'])==1
                    cases.append(dict(middle=middle,split=split,retry=retry,passed=True))
    identity=WorldIdentity('match-test','perspective-test','timeline-a','epoch-a')
    def raw(row):return {**row,'native_kind':row['kind'],'native_sequence':row['sequence']}
    state,_,_=advance_episodes(identity=identity,prior_objects=[],state={},events=[raw(birth())],gaps=[])
    for reason in ('timeline','epoch','gap','transition'):
        next_identity=WorldIdentity('match-test','perspective-test','timeline-b' if reason=='timeline' else 'timeline-a',
                                    'epoch-b' if reason=='epoch' else 'epoch-a')
        row=raw(event(2,'destroyed'))
        if reason=='transition':row['relationship_at_occurrence']='hostile'
        _,assigned,_=advance_episodes(identity=next_identity,prior_objects=[],state=state,events=[row],
            gaps=[{'before_native_sequence':2}] if reason=='gap' else [])
        assert assigned['2'].startswith('contact-episode-'),(reason,assigned)
        cases.append(dict(boundary=reason,passed=True))
    print(json.dumps({'passed':True,'cases':cases}))


if __name__=='__main__':main()
