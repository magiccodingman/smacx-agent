#!/usr/bin/env python3
"""Real journal idempotence and facade acknowledgement/repetition contracts."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
import smacx_controller as c
import smacx_mcp as m
from smacx_store import SmacxStore, MemoryScope
from smacx_journal import CampaignJournal
from smacx_runtime_context import _attention_payload

with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp); store=SmacxStore(root/'state.sqlite3')
    store.ensure_agent('agent-test','Test')
    store.create_match(match_id='match-test',display_name='Test',mode='solo')
    store.create_perspective('match-test','agent-test',perspective_id='perspective-test')
    scope=MemoryScope('match-test','agent-test','perspective-test')
    store.register_instance(instance_id='instance-test',scope=scope)
    store.start_session(scope,'instance-test',session_id='session-test')
    journal=CampaignJournal(root/'campaigns')
    with patch.object(c,'_store',return_value=store), patch.object(c,'_journal',return_value=journal), patch.object(c,'_guard_platform_observation',return_value=(scope,{'turn':3,'year':2103})), patch.object(c,'_journal_working_state',return_value={}):
        def write(record):
            return c.write_platform_memory('summary',scope.match_id,'session-test','revision',record,agent_id=scope.agent_id,perspective_id=scope.perspective_id)
        record={'section':'situation','content':'Explore the northern coast to locate settlement access.'}
        first=write(record); assert first['ok'],first
        head=journal.replay(scope)['manifest']['head_hash']
        for _ in range(4):
            same=write(dict(reversed(list(record.items())))); assert same['ok'] and same['changed'] is False,same
            assert same['journal_event_id']==first['journal_event_id']
            assert journal.replay(scope)['manifest']['head_hash']==head
        changed=write({**record,'content':'Explore south instead because the northern passage is blocked.'})
        assert changed['changed'] and changed['journal_event_id']!=first['journal_event_id']
        restarted=CampaignJournal(root/'campaigns')
        with patch.object(c,'_journal',return_value=restarted):
            assert write({**record,'content':'Explore south instead because the northern passage is blocked.'})['changed'] is False
        evidence=journal.append(scope,'game.action',{})['event_id']
        assert write({**record,'through_event_id':evidence})['changed']
        bad=write({**record,'through_event_id':'journal-'+'0'*32}); assert not bad['ok']

payload={'popup_label':'SIMULYOU','turn':3,'information':['Research completed'], 'state':{'protocol':{'required_action':'resolve_interaction'},'faction':{'ready_units':4}}}
projected=_attention_payload({'attention_kind':'game_notification','payload':payload})
assert 'state' not in projected and projected['information']==payload['information']
assert payload['state']['faction']['ready_units']==4

m.MEMORY_REPETITION.clear();m.RUNTIME_CIRCUITS.clear()
base={'ok':True,'record':{'section':'situation','summary_id':'summary-one'},'journal_event_id':'journal-one','persistence':{'journal_committed':True}}
with patch.object(m,'_bound_scope_identity',return_value=('match-test','session-test','agent-test','perspective-test')), patch.object(m,'_sovereign_memory_gate',return_value=None), patch.object(m,'write_platform_memory',return_value={**base,'changed':False}), patch.object(m,'smac_report_capability_gap',return_value={}) as gap:
    for i in range(4):
        receipt=m.smac_memory_update('summary','match-test','session-test','r',json.dumps(record))
        assert receipt['memory_receipt']['status']=='already_persisted'
    assert receipt['error']=='repeated_unchanged_memory' and gap.call_count==1
    m.RUNTIME_CIRCUITS.clear() # explicit operator recovery; evidence change starts a new sequence
    receipt=m.smac_memory_update('summary','match-test','session-test','r2',json.dumps(record))
    assert receipt['ok'] # different evidence revision is a distinct sequence
m.RUNTIME_CIRCUITS.clear()
with patch.object(m,'MANAGED_ATTACHED',False), patch.object(m,'_sovereign_gameplay_gate',return_value=None), patch.object(m,'smac_attention_ack',return_value={'ok':False,'error':'invalid_lease'}), patch.object(m,'_execute_choice_once') as execute:
    receipt=m.smac_execute_choice('missing','missing',attention_lease_id='bad')
    assert not receipt['ok'] and not receipt['native_action_executed'] and not execute.called
with patch.object(m,'MANAGED_ATTACHED',False), patch.object(m,'_sovereign_gameplay_gate',return_value=None), patch.object(m,'smac_attention_ack',return_value={'ok':True,'acknowledged_ids':['attention-one']}), patch.object(m,'_execute_choice_once',return_value={'ok':False,'error':{'code':'invalid_choice'}}):
    receipt=m.smac_execute_choice('missing','missing',attention_lease_id='reviewed')
    assert not receipt['ok'] and receipt['attention_acknowledgement']['ok']
print(json.dumps({'passed':True,'journal_noop_restart_and_evidence':True,'bounded_repetition':True,'notification_readiness_separated':True,'acknowledgement_independent_of_execution':True}))
