#!/usr/bin/env python3
"""Real canonical replay and bounded turn-boundary policy, no native claims."""
import json
from pathlib import Path
import tempfile
from smacx_intent import pending_intents, validate_intent, may_close_turn
from smacx_journal import CampaignJournal
from smacx_store import MemoryScope

with tempfile.TemporaryDirectory() as tmp:
    scope=MemoryScope('match-intent','agent-intent','perspective-intent')
    journal=CampaignJournal(Path(tmp))
    for i in range(15):
        meta=validate_intent({'intent_horizon':'this_turn_preferred'},4)
        journal.append(scope,'memory.goal',{'record':{'goal_key':f'g{i}', 'title':f'Goal {i}',
            'status':'active','trigger':meta}})
    journal.append(scope,'memory.plan',{'record':{'plan_key':'future','title':'Future',
        'status':'active','timing':{'intent_horizon':'persistent_goal'}}})
    replay=journal.replay(scope)
    pending=pending_intents(replay,4)
    assert pending['total_pending']==15 and len(pending['items'])==8 and pending['remaining_count']==7
    reviewed=validate_intent({'intent_horizon':'this_turn_required','intent_turn':4,
        'reconciliation':{'turn':4,'disposition':'deferred','reason':'Need minerals next turn'}},4)
    journal.append(scope,'memory.goal',{'record':{'goal_key':'g0','status':'active','trigger':reviewed}})
    replay=CampaignJournal(Path(tmp)).replay(scope)
    assert pending_intents(replay,4)['total_pending']==14
    assert pending_intents(replay,5)['total_pending']==15, 'deferral silently cancelled durable intent'
    for invalid in ({'intent_horizon':'urgent'}, {'intent_horizon':'this_turn_required','intent_turn':'4'},
        {'reconciliation':{'turn':5,'disposition':'deferred','reason':'future'}},
        {'reconciliation':{'turn':4,'disposition':'blocked','reason':''}}):
        try:validate_intent(invalid,4)
        except ValueError:pass
        else:raise AssertionError(invalid)
    assert may_close_turn('end_turn',{}, {})
    assert may_close_turn('skip_all_ready_units',{'ready_unit_refs':[{},{}]}, {})
    assert may_close_turn('skip_unit',{'ready_unit_refs':[{}]}, {'unit_id':7})
    assert not may_close_turn('skip_unit',{'ready_unit_refs':[{},{}]}, {'unit_id':7})
    assert not may_close_turn('set_production',{'ready_unit_refs':[{}]}, {'unit_id':-1})
    assert not may_close_turn('respond_to_end_turn_confirmation',{}, {'response':'cancel'})
    print(json.dumps({'passed':True,'evidence':'canonical journal and deterministic boundary policy',
        'bounded_pending':True,'deferral_survives_restart':True,'long_horizon_nonblocking':True,
        'last_ready_unit_auto_boundary_considered':True,'native_boundary_acceptance_pending':True}))
