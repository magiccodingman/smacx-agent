#!/usr/bin/env python3
"""The issued decision identity is a usable native memory guard, never a DB revision."""
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import smacx_mcp as m
import smacx_controller as c
from smacx_store import SmacxStore, MemoryScope
from smacx_journal import CampaignJournal
from smacx_world_store import WorldStore
from smacx_world_types import WorldIdentity
from smacx_world_model import PerspectiveProjector

with tempfile.TemporaryDirectory() as temporary:
    root=Path(temporary);store=SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-guard','Guard')
    store.create_match(match_id='match-guard',display_name='Guard',mode='solo')
    store.create_perspective('match-guard','agent-guard',perspective_id='perspective-guard')
    scope=MemoryScope('match-guard','agent-guard','perspective-guard')
    store.register_instance(instance_id='instance-guard',scope=scope)
    store.start_session(scope,'instance-guard',session_id='session-guard')
    journal=CampaignJournal(root/'campaigns');worlds=WorldStore(store)
    wi=WorldIdentity(scope.match_id,scope.perspective_id,journal.timeline_id(scope),'world-guard')
    state=PerspectiveProjector(wi).project({'turn':4,'map':{'width':8,'height':8},
        'tiles':[{'tile_id':0,'x':0,'y':0,'terrain':'land','visible_now':True}],
        'units':[],'bases':[]},observation_sequence=4)
    worlds.replace_projection(scope,wi,state['objects'],observation_cursor=4,
        action_revision='r4',continuity='complete',journal_head_hash='0'*64)
    native={'match_id':'match-guard','session_id':'session-guard','revision':'r4',
            'turn':4,'year':2104,'ready_unit_refs':[],'protocol':{'phase':'turn'},'interaction':{}}
    def bridge(operation,**arguments):
        if operation=='semantic_snapshot':return {'ok':True,'snapshot':dict(native)}
        if operation=='semantic_choices':return {'ok':True,**{k:native[k] for k in ('match_id','session_id','revision')},'choices':[]}
        raise AssertionError(operation)
    with patch.object(c,'_store',return_value=store),patch.object(c,'_journal',return_value=journal), \
         patch.object(c,'bridge_request',side_effect=bridge),patch.object(m,'_call',side_effect=bridge), \
         patch.object(m,'_sovereign_gameplay_gate',return_value=None), \
         patch.object(m,'_refresh_managed_world',return_value={'ok':True}), \
         patch.object(m,'_compose_match_briefing',return_value={'ok':True,'acknowledged':True}), \
         patch.object(m,'controller_chat_attention',return_value={}):
        frame=m.smac_decision();assert frame['ok'],frame
        guard=frame['identity']
        def write(key):
            return m.smac_memory_update('goal',guard['match_id'],guard['session_id'],guard['revision'],
                json.dumps({'goal_key':key,'title':'Establish a second base','description':'Inspect a safe colony site.'}),
                agent_id=scope.agent_id,perspective_id=scope.perspective_id)
        first=write('expansion');assert first['ok'],first
        native['revision']='r5'
        stale=write('stale-attempt');assert not stale['ok'] and stale['error']=='stale_memory_observation',stale
        goals=c._journal_working_state(scope)['sections']['goals']
        assert any(g['goal_key']=='expansion' for g in goals)
        assert not any(g['goal_key']=='stale-attempt' for g in goals)
        assert journal.verify(scope)['ok']
    tools={t.name:t for t in asyncio.run(m.mcp.list_tools())}
    description=tools['smac_memory_update'].description
    assert 'smac_decision.identity' in description and 'observed_revision to its revision' in description
    assert 'memory has no separate write revision' in tools['smac_memory'].description
print(json.dumps({'passed':True,'actual_decision_identity_to_guarded_writer':True,
    'synchronous_journal_visibility':True,'stale_native_revision_rejected':True,
    'schema_maps_guard_source':True,'classification':'production tools and guard with controlled native adapter'}))
