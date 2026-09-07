#!/usr/bin/env python3
"""Collector/journal/projection/attention chain for native sunspot counter ticks."""
import json
from pathlib import Path
import tempfile
from observation_collector_benchmark import (NativeFixture, SmacxStore, MemoryScope,
    CampaignJournal, WorldStore, ObservationCollector, AttentionService)
from smacx_world import WorldService


def main():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);store=SmacxStore(root/'state.sqlite3')
        store.ensure_agent('agent-ecology','Ecology')
        store.create_match(match_id='match-ecology',display_name='Ecology',mode='solo')
        store.create_perspective('match-ecology','agent-ecology',perspective_id='perspective-ecology')
        scope=MemoryScope('match-ecology','agent-ecology','perspective-ecology')
        journal=CampaignJournal(root/'campaigns',timeline_resolver=store.active_timeline_id)
        worlds=WorldStore(store,root/'snapshots');fixture=NativeFixture(8,4)
        state={'sea_level':0,'sea_level_council_pressure':0,'sunspot_duration':-5,
               'perihelion_active':False,'volcano_erupted':False}
        def bridge(operation,**kwargs):
            value=fixture(operation,**kwargs)
            if operation=='semantic_snapshot':value['snapshot']['ecology']=dict(state)
            return value
        collector=ObservationCollector(scope=scope,session_id='session-ecology',bridge_call=bridge,
            journal=journal,world_store=worlds,attention=AttentionService(store,journal,scope))
        def ecology_alerts():
            with store._connect() as c:rows=c.execute('SELECT payload_json,critical FROM attention_items').fetchall()
            return sum(r[1] for r in rows if 'global-ecology' in r[0])
        collector.collect_once()
        cases=[({'sunspot_duration':-6},False),({'sunspot_duration':-7},False),
               ({'sunspot_duration':10},True),({'sunspot_duration':9},False),
               ({'sunspot_duration':0},True),({'sunspot_duration':-1},False),
               ({'sea_level':1},True),({'sea_level_council_pressure':1},True),
               ({'perihelion_active':True},True),({'volcano_erupted':True},True),
               ({'future_ecology_field':'changed'},True)]
        for update,critical in cases:
            before=ecology_alerts();state.update(update);fixture.revision+=1
            collector.collect_once()
            assert ecology_alerts()-before==int(critical), update
            projection=WorldService(worlds,scope)._projection()[1]
            ecology=next(x for x in projection['objects'] if x['object_ref']=='global-ecology')
            assert ecology['fields']['state']['value']==state, 'attention suppression changed provider state'
        assert journal.verify(scope)['ok']
        print(json.dumps({'passed':True,'native_shaped_collector_chain':True,
            'routine_ticks_remain_projected_without_critical_attention':True,
            'sunspot_start_end_and_other_ecology_changes_remain_critical':True,'cases':len(cases)}))


if __name__=='__main__':main()
