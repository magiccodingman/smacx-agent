#!/usr/bin/env python3
"""Authenticated-receipt adapter and byte-persistence integration adversaries."""
import copy
import json
from pathlib import Path
import tempfile
from smacx_doctrine import *
from smacx_doctrine_native import confirmed_context, VERIFIED_RULE_FILES
from smacx_prompt import prompt_sha256
from smacx_context_policy import validate_managed_context, semantic_gc_ceiling_tokens
from doctrine_content_contract_test import fixtures

SEAT=dict(agent_name='Doctrine player',agent_id='agent-doctrine',match_id='match-doctrine',match_name='Doctrine test',
          perspective_id='perspective-doctrine',ruleset_id='smacx',seat_index=1,personality_id='none')


def receipt():
    c=fixtures()['stock-blind']
    return {'ok':True,'schema':'smacx.native-doctrine.v1','match_id':'match-doctrine','session_id':'session-doctrine','faction_id':1,
        'engine_contract':COMPATIBILITY,'engine_source_sha256':json.loads((TEMPLATE.parent/'engine-compatibility.json').read_text())['engine_source_sha256'],'rules_file_sha256':next(iter(VERIFIED_RULE_FILES)),'config_supported':True,'scenario_supported':True,
        'self_faction':{'name':'Resolved actual faction','leader':'Public leader','progenitor':False,'flags':0,'selected_technologies':0,
            'modifiers':[0,0,0,0,0,0,0,100,100,0,0,0],
            'bonuses':[{'rule':3,'a':1,'b':2,'name':''},{'rule':0,'a':1,'b':0,'name':'Centauri Ecology'}],
            'prohibited_model':'Free Market'},
        'difficulty':c['difficulty'],'rules':{**c['rules'],'intense_rivalry':True,'random_leader_agendas':True},
        'ending_year':2500,'generators':6,'generator_population':10,'world':c['world'],
        'planetfall':True,'initial_pod_placement':True,'participants':[],'roster_complete':True}


def resolve(raw,previous=None):return confirmed_context(raw,match_id='match-doctrine',session_id=raw['session_id'],faction_id=1,previous=previous)


def main():
    raw=receipt();c=resolve(raw)
    text,metadata=compose_managed_prompt(c,**SEAT)
    assert 'Resolved actual faction' in text and 'random_leader_agendas' not in text
    assert text.index('# SMACX sovereign player contract')<text.index('# Sovereign Gameplay Doctrine')
    assert '{{' not in text and metadata['final_prompt_sha256']==prompt_sha256(text)
    previous={'system_prompt':text,'metadata':{'gameplay_doctrine':metadata}}
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/'profile.json';p.write_text(json.dumps(previous));previous=json.loads(p.read_text())
        assert compose_managed_prompt(c,previous=previous,**SEAT)==(text,metadata)
    changed=copy.deepcopy(raw);changed.update(session_id='session-restart',planetfall=False,initial_pod_placement=False,
        current_turn=80,current_techs=['SECRET_RUNTIME'],diplomacy={'Pact':True},units=[999],AI_fight='HIDDEN_SENTINEL',controller_type='HIDDEN_SENTINEL')
    assert resolve(changed,previous=c)==c
    assert compose_managed_prompt(resolve(changed,previous=c),previous=previous,**SEAT)[0]==text
    for label_,mutate in [
        ('wrong-scope',lambda r:r.update(match_id='match-other')),
        ('unknown-rules',lambda r:r.update(rules_file_sha256='0'*64)),
        ('unsupported-config',lambda r:r.update(config_supported=False)),
        ('unsupported-scenario',lambda r:r.update(scenario_supported=False)),
        ('missing-research',lambda r:r['rules'].pop('blind_research')),
        ('unmapped-bonus',lambda r:r['self_faction']['bonuses'].append({'rule':999,'a':0,'b':0,'name':'HIDDEN_SENTINEL'})),
    ]:
        r=copy.deepcopy(raw);mutate(r)
        try:resolve(r)
        except DoctrineError:pass
        else:raise AssertionError(label_)
    changed=copy.deepcopy(c);changed['rules']['victory_cooperative']=True
    try:compose_managed_prompt(changed,previous=previous,**SEAT)
    except DoctrineError as e:assert str(e)=='doctrine_explicit_recompile_required'
    else:raise AssertionError('silent regeneration')
    updated,newmeta=compose_managed_prompt(changed,previous=previous,recompile=True,**SEAT)
    assert updated!=text and newmeta['assembly_sha256']!=metadata['assembly_sha256']
    corrupt=copy.deepcopy(previous);corrupt['system_prompt']+=' tampered'
    try:compose_managed_prompt(c,previous=corrupt,**SEAT)
    except DoctrineError:pass
    else:raise AssertionError('hash corruption ignored')
    personality,_=compose_managed_prompt(c,**{**SEAT,'personality_id':'careful','personality_prompt':'Value reliable cooperation, without surrendering judgment.'})
    assert personality.index('# Sovereign Gameplay Doctrine')<personality.index('## Personality layer')
    reserve=validate_managed_context(personality,65536)
    ceiling=semantic_gc_ceiling_tokens(65536,output_reserve=8192,reasoning_reserve=8192,system_tool_reserve=reserve)
    assert reserve>12000 and ceiling+reserve+16384<=65536
    try:validate_managed_context(personality+'x'*100000,65536)
    except ValueError as e:assert str(e)=='managed_prompt_context_headroom_insufficient'
    else:raise AssertionError('oversized prefix accepted')
    print(json.dumps({'passed':True,'adapter_scope_rules_and_exclusions':True,'explicit_recompile':True,
        'restart_exact_bytes':True,'runtime_and_session_invariant':True,'personality_order':True,'prompt_bytes':len(text.encode()),
        'classification':'deterministic native-shaped adapter + persisted-profile seam'}))

if __name__=='__main__':main()
