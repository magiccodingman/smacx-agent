#!/usr/bin/env python3
"""Canonical journal -> bounded working state -> runtime omission audit."""
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope
from smacx_runtime_context import _cognition, cognition_selection_audit

with tempfile.TemporaryDirectory() as temporary:
    journal=CampaignJournal(Path(temporary))
    scope=MemoryScope('match-omission-audit','agent-omission','perspective-omission')
    for index in range(131):
        journal.append(scope,'memory.goal',{'record':{
            'goal_id':f'goal-{index}', 'goal_key':f'goal-{index}',
            'title':f'Goal {index}', 'description':'bounded test intention',
            'status':'completed' if index==130 else 'active',
            'priority':index,'created_unix':index}})
    before=journal.replay(scope)
    captured=[]
    with patch('smacx_diagnostics.record',side_effect=lambda kind,payload,**kw:captured.append((kind,payload,kw))):
        working=journal.working_state(scope,token_budgets={'goals':1_000_000})
    audit=next(row for row in captured if row[0]=='journal_cognition_selection')
    upstream=audit[1]['selection']['goals']
    assert upstream['source_count']==131
    assert len(upstream['included_ids'])==100
    omissions={row['record_id']:row['reason'] for row in upstream['omitted']}
    assert omissions['goal-130']=='status_filter'
    assert len(omissions)==31
    assert set(omissions.values())=={'status_filter','section_count_or_token_budget'}
    assert audit[2]['correlation']['journal_head_hash']==working['journal_head_hash']==before['manifest']['head_hash']
    selected=_cognition(working,token_budget=1_000_000,current_turn=1)
    assert selected['evidence_semantics']['authority'] == \
        'sovereign_interpretation_and_intent_not_current_mechanical_truth'
    assert 'does not prove' in selected['evidence_semantics']['completion']
    runtime_audit=cognition_selection_audit(working,selected)
    assert runtime_audit['working_projection']['journal_head_hash']==audit[2]['correlation']['journal_head_hash']
    assert runtime_audit['working_projection']['scope']==working['scope']
    assert runtime_audit['working_projection']['token_budgets']=={'goals':1_000_000}
    assert runtime_audit['working_projection']['selected_token_estimates']==working['token_estimates']
    downstream=runtime_audit['sections']['goals']
    assert downstream['source_count']==100 and len(downstream['included_ids'])==12
    assert len(downstream['omitted'])==88
    assert all(row['reason']=='section_count_or_token_budget' for row in downstream['omitted'])
    assert set(downstream['included_ids']) <= set(upstream['included_ids'])
    budget_capture=[]
    with patch('smacx_diagnostics.record',side_effect=lambda kind,payload,**kw:budget_capture.append((kind,payload,kw))):
        small=journal.working_state(scope,token_budgets={'goals':256})
    limited=next(row[1]['selection']['goals'] for row in budget_capture if row[0]=='journal_cognition_selection')
    assert 0<len(limited['included_ids'])<100
    assert len(limited['omitted'])+len(limited['included_ids'])==131
    after=CampaignJournal(Path(temporary)).replay(scope)
    assert after['goals']==before['goals'] and after['manifest']['head_hash']==before['manifest']['head_hash']
print(json.dumps({'passed':True,'canonical_source_records':131,'working_count_limit':100,
    'runtime_count_limit':12,'status_and_budget_omissions_accounted':True,
    'cognition_evidence_boundary_delivered':True,
    'journal_unchanged_after_projection_and_reopen':True,
    'classification':'Production journal and runtime selection, controlled cognition; not actual provider delivery'}))
