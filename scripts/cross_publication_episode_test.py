#!/usr/bin/env python3
"""Visible episode continuity is owned by the feed, across cuts and missing pages."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from publication_transaction_test import setup


def raw(sequence,kind,before=5,after=0):
    return {'sequence':sequence,'kind':kind,'subject_a':900,'subject_b':2,'from_tile_id':before,
            'to_tile_id':after,'turn':50,'continuous_visibility':True,'relationship_at_occurrence':'hostile'}

def foreign(native,tile):
    native.units=[u for u in native.units if u.get('native_observation_key')!='vehicle-handle-900']
    if tile is not None:
        native.units.append({'id':900,'native_observation_key':'vehicle-handle-900','tile_id':tile,
            'owned':False,'owner_ref':'faction-2','name':'Observed unit','hp':10,'max_hp':10,'triad':'land'})

def history(f):return [row['event'] for row in f.worlds.temporal_events_since(f.scope,f.attention.timeline_id,0,limit=1024)]
def active(f):return next((r['object_ref'] for r in f.worlds.load(f.scope,f.attention.timeline_id)['objects']
    if r.get('kind')=='foreign_contact' and r.get('status','active')=='active'
    and r.get('metadata',{}).get('native_observation_key')=='vehicle-handle-900'),None)
def restart(f):
    from smacx_store import SmacxStore
    from smacx_world_store import WorldStore
    from smacx_journal import CampaignJournal
    from smacx_attention import AttentionService
    f.store=SmacxStore(f.root/'state.sqlite3');f.worlds=WorldStore(f.store)
    f.journal=CampaignJournal(f.root/'campaigns',timeline_resolver=f.store.active_timeline_id)
    f.attention=AttentionService(f.store,f.journal,f.scope)

def main():
    cases=[]
    for ending in ('visible_unit_lost','visible_unit_destroyed'):
        with tempfile.TemporaryDirectory() as tmp:
            f,native,collect,_=setup(Path(tmp));native.events=[raw(1,'visible_unit_appeared',5,5)];native.revision+=1
            collect().collect_once();episode=[e['contact_ref'] for e in history(f) if e['event_kind']=='contact_appeared'][-1]
            assert active(f) is None
            restart(f);native.events += [raw(2,'visible_unit_moved'),raw(3,ending,0,0)];native.revision+=1
            collect().collect_once();rows=[e for e in history(f) if e['event_kind']=='contact_moved']
            assert len(rows)==1 and rows[0]['contact_ref']==episode,rows
            assert any(e['event_kind']==('contact_lost' if ending.endswith('lost') else 'contact_destroyed') and e['contact_ref']==episode for e in history(f))
            assert not f.worlds.load_native_observation_stage(f.scope,f.attention.timeline_id)['episode_state']['open'].get('vehicle-handle-900')
            cases.append({'case':'cross_publication_'+ending,'passed':True})
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp));native.events=[raw(1,'visible_unit_appeared',5,5)];native.revision+=1
        c=collect();bundle=c._bundle
        def racing_bundle():
            native.events.extend([raw(2,'visible_unit_lost',5,5),raw(3,'visible_unit_appeared',7,7)])
            foreign(native,7);return bundle()
        with patch.object(c,'_bundle',side_effect=racing_bundle):c.collect_once()
        appeared=[e for e in history(f) if e['event_kind']=='contact_appeared' and e.get('location_ref')=='location-5'][-1]
        assert appeared['contact_ref']!=active(f)
        first=appeared['contact_ref'];collect().collect_once()
        assert any(e['event_kind']=='contact_lost' and e['contact_ref']==first for e in history(f))
        assert first!=active(f);cases.append({'case':'feed_cut_before_snapshot_reappearance','passed':True})
    for appearance in (False,True):
        for endpoint in (False,True):
            for failure in ('none','stage','frozen'):
                with tempfile.TemporaryDirectory() as tmp:
                    f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1;collect().collect_once();old=active(f)
                    native.events=([raw(10,'visible_unit_appeared',5,5)] if appearance else [])+[raw(11,'visible_unit_moved')]
                    if not endpoint:native.events.append(raw(12,'visible_unit_lost',0,0))
                    foreign(native,0 if endpoint else None);native.revision+=1
                    c=collect();original=c.bridge_call
                    def bridge(op,**kw):
                        value=original(op,**kw)
                        if op=='observation_feed' and value.get('events') and kw.get('after_sequence',0)<10:
                            value={**value,'continuity':'incomplete','reconciliation_required':True}
                        return value
                    c.bridge_call=bridge
                    if failure=='stage':
                        with patch.object(c,'_bundle',side_effect=RuntimeError('stage')):
                            try:c.collect_once()
                            except RuntimeError:pass
                        restart(f);c=collect();c.bridge_call=bridge
                    if failure=='frozen':
                        with patch.object(f.worlds,'replace_projection',side_effect=RuntimeError('frozen')):
                            try:c.collect_once()
                            except RuntimeError:pass
                        restart(f);c=collect()
                    c.collect_once()
                    moves=[e for e in history(f) if e['event_kind']=='contact_moved' and any(p['occurrence_sequence']==11 for p in e['path'])]
                    assert len(moves)==1,(appearance,endpoint,failure,moves)
                    assert moves[0]['contact_ref']!=old
                    if endpoint:assert moves[0]['contact_ref']==active(f),(moves,active(f))
                    assert moves[0]['path'][0]['relationship']['value']=='hostile'
                    assert 'vehicle-handle' not in json.dumps(history(f))
                    assert f.journal.verify(f.scope)['ok']
                    cases.append({'case':'gap','appearance':appearance,'endpoint':endpoint,'failure':failure,'passed':True})
    # Preserve pre-gap staged movement, then a gap on the next native page.
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1;collect().collect_once();old=active(f)
        native.events=[raw(1,'visible_unit_moved',5,6)]+[raw(i,'known_tile_changed',0,0) for i in range(2,257)]
        c=collect();bridge=native
        def page_crash(op,**kw):
            if op=='observation_feed' and kw.get('after_sequence')==256:raise RuntimeError('page')
            result=bridge(op,**kw)
            if op=='observation_feed':result['has_more']=True
            return result
        c.bridge_call=page_crash
        try:c.collect_once()
        except RuntimeError:pass
        else:raise AssertionError('page crash not reached')
        restart(f);native.events=[raw(600,'visible_unit_moved',8,0)];foreign(native,0)
        c=collect()
        def gap_page(op,**kw):
            result=native(op,**kw)
            if op=='observation_feed' and kw.get('after_sequence')==256:result['continuity']='incomplete'
            return result
        c.bridge_call=gap_page;c.collect_once()
        moves=[e for e in history(f) if e['event_kind']=='contact_moved']
        refs={p['occurrence_sequence']:e['contact_ref'] for e in moves for p in e['path']}
        assert refs[1]==old and refs[600]!=old and refs[600]==active(f),refs
        # A second publication gap must close the first post-gap identity too.
        previous=active(f);native.events=[raw(900,'visible_unit_moved',2,0)];c=collect()
        def second_gap(op,**kw):
            result=native(op,**kw)
            if op=='observation_feed' and kw.get('after_sequence')==600:result['continuity']='incomplete'
            return result
        c.bridge_call=second_gap;c.collect_once();assert active(f)!=previous
        cases.append({'case':'page_boundary_restart_two_gaps_pre_gap_preserved','passed':True})
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp));foreign(native,5);native.revision+=1;collect().collect_once()
        kinds=['visible_unit_moved','visible_unit_lost','visible_unit_appeared','visible_unit_moved',
            'visible_unit_lost','visible_unit_appeared','visible_unit_moved','contact_identity_reset',
            'visible_unit_moved','visible_unit_lost']
        native.events=[raw(i+1,k,5 if k=='visible_unit_moved' else 5,0 if k=='visible_unit_moved' else 5)
                       for i,k in enumerate(kinds)]
        foreign(native,None);native.revision+=1;collect().collect_once()
        rows=[e for e in history(f) if e['event_kind']=='contact_moved']
        assert len(rows)==4 and len({e['contact_ref'] for e in rows})==4,rows
        assert {p['occurrence_sequence'] for e in rows for p in e['path']}=={1,4,7,9}
        cases.append({'case':'four_episodes_reset_retains_observed_segment','passed':True})
    print(json.dumps({'passed':True,'classification':'native-shaped production collector/projector','cases':cases}))
if __name__=='__main__':main()
