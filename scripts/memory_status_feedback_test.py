#!/usr/bin/env python3
"""Status feedback preserves rejected-write and canonical plan retirement semantics."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import smacx_controller as c
from smacx_store import MEMORY_STATUS_VALUES, SmacxStore, MemoryScope
from smacx_journal import CampaignJournal, JournalError
from smacx_world_store import WorldStore
from smacx_world import WorldService
from smacx_world_types import WorldIdentity
from smacx_world_model import PerspectiveProjector
from smacx_attention import AttentionService
from smacx_runtime_context import RuntimeContextAssembler

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp);store=SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-delivery','Delivery')
    store.create_match(match_id='match-delivery',display_name='Delivery',mode='solo')
    store.create_perspective('match-delivery','agent-delivery',perspective_id='perspective-delivery')
    scope=MemoryScope('match-delivery','agent-delivery','perspective-delivery')
    store.register_instance(instance_id='instance-delivery',scope=scope)
    store.start_session(scope,'instance-delivery',session_id='session-delivery')
    journal=CampaignJournal(root/'campaigns')
    worlds=WorldStore(store,root/'world-snapshots')
    identity=WorldIdentity(scope.match_id,scope.perspective_id,journal.timeline_id(scope),'world-delivery')
    projected=PerspectiveProjector(identity).project({'turn':4,'map':{'width':8,'height':8},
        'tiles':[{'tile_id':0,'x':0,'y':0,'terrain':'land','visible_now':True}],
        'units':[],'bases':[]},observation_sequence=4)
    worlds.replace_projection(scope,identity,projected['objects'],observation_cursor=4,
        action_revision='r4',continuity='complete',journal_head_hash='0'*64)
    snapshot={'turn':4,'year':2104,'revision':'r4','ready_unit_refs':[]}
    with patch.object(c,'_store',return_value=store), patch.object(c,'_journal',side_effect=lambda:journal), \
         patch.object(c,'_guard_platform_observation',return_value=(scope,snapshot)):
        records={
            'claim':{'topic':'test','content':'test'},
            'commitment':{'commitment_key':'test','title':'test','terms':'test'},
            'goal':{'goal_key':'test','title':'test','description':'test'},
            'plan':{'plan_key':'test','title':'test','objective':'test'},
        }
        before=c._journal_working_state(scope)
        for action,record in records.items():
            result=c.write_platform_memory(action,scope.match_id,'session-delivery','r4',
                {**record,'status':'not-a-status'})
            assert result['error']==f'invalid_{action}_status',result
            assert result['validation']['allowed_values']==list(MEMORY_STATUS_VALUES[action])
            assert result['persistence']['stage']=='not_started'
            assert c._journal_working_state(scope)==before,'rejected status wrote cognition'
        record=records['plan']
        active=c.write_platform_memory('plan',scope.match_id,'session-delivery','r4',
            {**record,'status':'active'})
        assert active['ok'],active
        bad=c.write_platform_memory('plan',scope.match_id,'session-delivery','r4',
            {**record,'status':'cancelled'})
        assert bad['error']=='invalid_plan_status' and 'abandoned' in bad['validation']['allowed_values']
        retired=c.write_platform_memory('plan',scope.match_id,'session-delivery','r4',
            {**record,'status':'abandoned'})
        assert retired['ok'] and retired['record']['status']=='abandoned',retired
        assert retired['record']['supersedes_plan_id']==active['record']['plan_id']
        assert retired['persistence']['stage']=='runtime_projection_built'
        import smacx_mcp as m
        import asyncio
        listed=asyncio.run(m.mcp.list_tools())
        desc=next(tool.description for tool in listed if tool.name=='smac_memory_update')
        for action,values in MEMORY_STATUS_VALUES.items():
            assert action+'='+'|'.join(values) in desc
print(json.dumps({'passed':True,'classification':'guarded writer with controlled native snapshot',
 'invalid_status_does_not_write':True,'status_guidance_matches_validation':True,
 'explicit_plan_retirement_journaled_and_projected':True}))
