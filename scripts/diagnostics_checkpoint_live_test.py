"""Controlled native final-unit turn transition through managed intent review."""
import json
import time


def exercise_diagnostics_checkpoint(call,native,get_context,responded):
    fixture=native('test_managed_action_fixture',phase='diagnostics_turn_boundary')
    assert fixture.get('ok'),fixture
    for _ in range(32):
        context=get_context()
        assert context.get('ok'),context
        attention=context['runtime_context']['attention']
        if attention.get('items'):
            assert responded(attention['attention_lease_id']).get('ok')
            ack=call('smac_attention_ack',{'attention_lease_id':attention['attention_lease_id'],'through_cursor':attention['through_cursor']})
            assert ack.get('ok'),ack
        if not any(item.get('critical') for item in attention.get('items',[])):break
    before=native('semantic_snapshot')['snapshot']
    assert len(before['ready_unit_refs'])==1,before['ready_unit_refs']
    goal={'goal_key':'diagnostic-boundary-review','title':'Review citizen management before yielding',
          'description':'Controlled boundary acceptance; explicitly defer after verifying refusal.',
          'status':'active','trigger':{'intent_horizon':'this_turn_required'}}
    def write(record):
        identity=call('smac_decision',{})['identity']
        result=call('smac_memory_update',{'action':'goal','match_id':identity['match_id'],
            'session_id':identity['session_id'],'observed_revision':identity['revision'],'record_json':json.dumps(record)})
        assert result.get('ok'),result
        return result
    written=write(goal)
    context=get_context()['runtime_context']
    assert any(row.get('key')==goal['goal_key'] for row in context['current_turn_intent_review']['items'])
    frame=call('smac_decision',{'own_unit_ref':fixture['own_unit_ref']})
    skip=next(row for row in frame['choices'] if row['label']=='Skip unit')
    reference={'decision_id':frame['decision_id'],'choice_id':skip['choice_id']}
    blocked=call('smac_execute_choice',reference)
    assert blocked.get('error',{}).get('code')=='current_turn_intent_requires_review',blocked
    assert blocked['native_action_executed'] is False and blocked['decision_consumed'] is False
    unchanged=native('semantic_snapshot')['snapshot']
    assert unchanged['turn']==before['turn'] and unchanged['ready_unit_refs']==before['ready_unit_refs']
    goal['trigger']={**written['record']['trigger'],'reconciliation':{'turn':before['turn'],
        'disposition':'deferred','reason':'Controlled acceptance: proceed with native final-unit transition.'}}
    write(goal)
    frame=call('smac_decision',{'own_unit_ref':fixture['own_unit_ref']})
    skip=next(row for row in frame['choices'] if row['label']=='Skip unit')
    released=call('smac_execute_choice',{'decision_id':frame['decision_id'],'choice_id':skip['choice_id']})
    assert released.get('ok'),released
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        after=native('semantic_snapshot')['snapshot']
        if after['turn']!=before['turn']:break
        if after.get('protocol',{}).get('phase')=='interaction':
            frame=call('smac_choices',{'kind':'interaction'})
            advances=[row for row in frame.get('choices',[]) if any(word in str(row.get('label','')).casefold()
                for word in ('acknowledge','continue','close','advance'))]
            if advances:
                result=call('smac_execute_choice',{'decision_id':frame['decision_id'],'choice_id':advances[0]['choice_id']})
                assert result.get('ok'),result
        time.sleep(.25)
    assert after['turn']!=before['turn'],{'automatic_native_transition_not_observed':after.get('protocol')}
    return {'current_turn_intent_in_next_runtime':True,'last_ready_native_unit_blocked_without_effect':True,
            'explicit_deferral_releases_native_skip':True,'automatic_turn_preference_enabled':True,
            'turn_before':before['turn'],'turn_after_receipt':after['turn'],
            'auto_transition_observed':after['turn']!=before['turn'],
            'native_execution_status':released.get('execution_status')}
