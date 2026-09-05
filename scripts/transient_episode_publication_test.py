#!/usr/bin/env python3
"""Transient visible episodes survive private staging, publication and restart."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from publication_transaction_test import setup


def main():
    cases=[]
    for ending in ('visible_unit_lost','visible_unit_destroyed'):
        for crash in ('none','stage_page','post_head'):
            with tempfile.TemporaryDirectory() as tmp:
                f,native,collect,_=setup(Path(tmp))
                area=f.attention.create_watch('spatial_scope',['location-0'],{'type':'proximity','radius':1},current_turn=50)['watch_id']
                watch=f.attention.create_watch('region_entry',[area],{'relationship':'hostile'},current_turn=50)['watch_id']
                native.events=[{'sequence':i+1,'kind':'known_tile_changed','subject_a':0,'from_tile_id':0,'to_tile_id':0,'turn':50} for i in range(255)]
                native.events += [{'sequence':256+i,'kind':kind,'subject_a':900,'subject_b':2,'from_tile_id':5,'to_tile_id':0,
                    'turn':50,'continuous_visibility':True,'relationship_at_occurrence':'hostile'}
                    for i,kind in enumerate(('visible_unit_appeared','visible_unit_moved',ending))]
                native.revision+=1
                if crash=='stage_page':
                    c=collect(); original=c.bridge_call
                    def bridge(op,**kw):
                        if op=='observation_feed' and kw.get('after_sequence')==256:raise RuntimeError('stage_page')
                        return original(op,**kw)
                    c.bridge_call=bridge
                    try:c.collect_once()
                    except RuntimeError:pass
                    else:raise AssertionError('stage injection absent')
                elif crash=='post_head':
                    original=f.worlds.replace_projection
                    def head(*a,**kw):original(*a,**kw);raise RuntimeError('post_head')
                    with patch.object(f.worlds,'replace_projection',side_effect=head):
                        try:collect().collect_once()
                        except RuntimeError:pass
                        else:raise AssertionError('head injection absent')
                collect().collect_once();collect().collect_once()
                with f.store._connect() as db:
                    notices=[json.loads(row[0]) for row in db.execute("SELECT payload_json FROM attention_items WHERE attention_kind='watch_trigger'")]
                notices=[n for n in notices if n['watch_id']==watch]
                assert len(notices)==1,(ending,crash,notices)
                event=notices[0]['matches'][0]['temporal_event']
                assert event['contact_ref'].startswith('contact-episode-')
                assert event['current_whereabouts']==('unknown' if ending.endswith('lost') else 'confirmed_destroyed')
                assert 'vehicle-handle' not in json.dumps(event)
                assert event['contact_ref'] not in {r['object_ref'] for r in f.worlds.load(f.scope,f.attention.timeline_id)['objects']}
                cases.append({'ending':ending,'crash':crash,'passed':True})
    with tempfile.TemporaryDirectory() as tmp:
        f,native,collect,_=setup(Path(tmp));c=collect()
        prior={'object_ref':'contact-old','kind':'foreign_contact','metadata':{'native_observation_key':'vehicle-handle-9'}}
        current={**prior,'object_ref':'contact-final'}
        kinds=['visible_unit_moved','visible_unit_lost','visible_unit_appeared','visible_unit_moved','visible_unit_lost',
               'visible_unit_appeared','visible_unit_moved','visible_unit_lost','visible_unit_appeared','visible_unit_moved']
        c._pending_native_events=[{'native_kind':k,'native_sequence':i+1,'subject_a':9,'continuous_visibility':True,
            'from_tile_id':5,'to_tile_id':0,'relationship_at_occurrence':'hostile'} for i,k in enumerate(kinds)]
        def events():return c._coalesce_native_events(prior_objects=[prior],current_objects=[current],turn=50,world_epoch='epoch-review')
        moved=[r for r in events() if r['event_kind']=='contact_moved'];refs=[r['contact_ref'] for r in moved]
        assert len(refs)==len(set(refs))==4 and refs[0]=='contact-old' and refs[-1]=='contact-final'
        assert all(len(r['path'])==1 for r in moved)
        assert events()==events()
        oldscope=c.scope
        from smacx_store import MemoryScope
        c.scope=MemoryScope(oldscope.match_id,oldscope.agent_id,'other-perspective')
        other=[r['contact_ref'] for r in events() if r['event_kind']=='contact_moved']
        assert not set(refs[1:-1])&set(other[1:-1]);c.scope=oldscope
        c._pending_native_events.insert(3,{'native_kind':'contact_identity_reset'})
        reset=events();assert not any(r.get('path',[{}])[0].get('occurrence_sequence')==4 for r in reset if r.get('path'))
        c._pending_native_events=[{'native_kind':'visible_unit_moved','native_sequence':1,'subject_a':9,'continuous_visibility':False,'from_tile_id':5,'to_tile_id':0}]
        assert not any(r['event_kind']=='contact_moved' for r in events())
    print(json.dumps({'passed':True,'transient_publication_cases':cases,'four_distinct_episodes':True,
        'cross_perspective_isolation':True,'reset_and_discontinuity':True,'evidence':'native_shaped_staging_and_collector'}))
if __name__=='__main__':main()
