#!/usr/bin/env python3
"""Real write/journal/runtime/sanitizer/HTTP serialization chain; controlled provider."""
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import httpx
import smacx_controller as c
from smacx_store import SmacxStore, MemoryScope
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
        result=c.write_platform_memory('goal',scope.match_id,'session-delivery','r4',
            {'goal_key':'citizen','title':'Improve minerals','description':'Reassign citizen after inspecting yield.',
             'trigger':{'intent_horizon':'this_turn_preferred'},'status':'active'})
        assert result['ok'] and result['persistence']['stage']=='runtime_projection_built',result
        prompt=root/'SYSTEM.md';prompt.write_text('Controlled cognition delivery test\n')
        os.environ.update(SMACX_STRICT_SYSTEM_PROMPT='1',SMACX_SYSTEM_PROMPT_FILE=str(prompt),
            SMACX_SYSTEM_PROMPT_SHA256=hashlib.sha256(prompt.read_bytes()).hexdigest(),
            SMACX_DIAGNOSTICS_ENABLED='1',SMACX_DIAGNOSTICS_ROOT=str(root/'diagnostics'),
            SMACX_AGENT_MATCH_ID=scope.match_id,SMACX_CONTEXT_LENGTH='65536')
        import smacx_strict_prompt as strict
        importlib.reload(strict)
        from run_agent import AIAgent
        def runtime(messages):
            episode=strict._episode_id(messages)
            attention=AttentionService(store,journal,scope)
            assembler=RuntimeContextAssembler(scope=scope,world=WorldService(worlds,scope),attention=attention,
                snapshot=lambda:snapshot,working_state=lambda:c._journal_working_state(scope))
            return assembler.build(episode_id=episode,episode_mode='gameplay',context_length=65536),episode
        strict._fetch_runtime_context=runtime
        captured=[]
        def transport(request):
            captured.append(json.loads(request.content));return httpx.Response(200,json={'id':'controlled-provider'})
        histories=[('next-request',[{'role':'user','content':'continue'}]),
            ('handoff-resume',[{'role':'user','content':'prior turn'},
                {'role':'assistant','content':'TURN HANDOFF\nNext intent: improve minerals'},
                {'role':'user','content':'[SMACX_EPISODE_BOUNDARY kind=resume] continue'}])]
        heavy=[{'role':'user','content':'long episode'}]
        for i in range(180):
            heavy.extend([{'role':'assistant','content':'','tool_calls':[{'id':f'call-{i}','type':'function',
                'function':{'name':'tool_call','arguments':json.dumps({'name':'mcp__smacx__smac_memory_update',
                'arguments':{'action':'belief','record_json':'x'*4096}})}}]},
                {'role':'tool','tool_call_id':f'call-{i}','content':json.dumps({'ok':True,'journal_event_id':f'event-{i}','payload':'x'*4096})}])
        histories.append(('semantic-gc',heavy))
        with httpx.Client(transport=httpx.MockTransport(transport)) as client:
            for label,history in histories:
                before=copy.deepcopy(history)
                wire=AIAgent._sanitize_api_messages(history)
                assert history==before,'request-only context polluted durable history'
                client.post('https://controlled.invalid/v1/chat/completions',json={'model':'fixture','messages':wire})
                encoded=json.dumps(captured[-1])
                assert 'citizen' in encoded and 'Improve minerals' in encoded,label
                assert sum(str(row.get('content','')).count(strict._RUNTIME_OPEN) for row in wire)==1
            assert strict._RUNTIME_STATE.gc_metrics['removed_rows']>0
            journal=CampaignJournal(root/'campaigns')
            wire=AIAgent._sanitize_api_messages([{'role':'user','content':'[SMACX_EPISODE_BOUNDARY kind=resume] recover'}])
            client.post('https://controlled.invalid/v1/chat/completions',json={'model':'fixture','messages':wire})
            assert 'Improve minerals' in json.dumps(captured[-1])
        with patch.object(journal,'project_state',side_effect=JournalError('controlled_projection_failure')):
            failed=c.write_platform_memory('goal',scope.match_id,'session-delivery','r4',
                {'goal_key':'after-failure','title':'Committed before projection failure','description':'Inspect before retry.'})
        assert not failed['ok'] and failed['persistence']['stage']=='journal_committed',failed
        assert any(r['goal_key']=='after-failure' for r in c._journal_working_state(scope)['sections']['goals'])
        assert journal.verify(scope)['ok']
    print(json.dumps({'passed':True,'evidence':'guarded writer with controlled observation; real journal/runtime/Hermes sanitizer/HTTP transport',
        'next_request':True,'handoff_resume':True,'semantic_gc':True,'journal_restart':True,
        'request_only_history_unchanged':True,'ambiguous_projection_failure_exposed':True,
        'native_checkpoint_recovery_acceptance_pending':True}))
