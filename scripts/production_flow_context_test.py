#!/usr/bin/env python3
"""Conditional production flow survives the managed and projection boundaries."""
import copy
import json
from unittest.mock import patch
import smacx_mcp as mcp
from smacx_mechanics import base_mechanics, production_flow_state
from smacx_topology import KnownSquare, MapShape, PerspectiveTopology
from geographic_semantics_contract_test import obj


def main():
    stalled = 'no_passive_progress_at_current_surplus'
    assert production_flow_state(30, 1, 0, inputs_current=True) == stalled
    assert production_flow_state(30, 1, -1, inputs_current=True) == stalled
    assert production_flow_state(30, 1, 2, inputs_current=True) == 'accumulating_at_current_surplus'
    assert production_flow_state(30, 30, 0, inputs_current=True) == 'cost_already_accumulated_pending_native_processing'
    for cost, progress, surplus in ((None,1,0),(30,None,0),(30,1,None),(True,1,0),(30,1,float('nan')),(-1,1,0),(30,-1,0)):
        assert production_flow_state(cost, progress, surplus, inputs_current=True) == 'unknown'
    assert production_flow_state(30,1,0,inputs_current=False) == 'unknown'
    identity = {'match_id':'match-flow','session_id':'session-flow','revision':'r1'}
    snapshot = {**identity,'turn':51,'protocol':{'phase':'turn'}}
    catalog = {**identity,'ok':True,'kind':'production','base_id':7,'population':1,
        'current':{'name':'Synthmetal Garrison','mineral_cost':30,'minerals_accumulated':1,'mineral_surplus':0},
        'choices':[{'command':'set_production','base_id':7,'item_id':0,'name':'Colony Pod',
                    'population_effect':{'epistemic_status':'conditional',
                        'population_at_query':1,'population_change_on_selection':0,
                        'population_cost_on_completion':1}},
                   {'command':'hurry_production','base_id':7,'energy_cost':12,'minerals_added':29,
                    'production_name':'Synthmetal Garrison','quote_scope':{
                        'single_execution':True,'revision':'r1',
                        'invalidated_by':['production switch']}}]}
    original = copy.deepcopy(catalog)
    with patch.object(mcp,'_call',side_effect=lambda op,**kw: {'ok':True,'snapshot':snapshot} if op=='semantic_snapshot' else catalog), \
         patch.object(mcp,'_resolve_managed_selectors',return_value=({'base_id':7},{'base_reverse':{7:'base-public'}})), \
         patch.object(mcp,'_pending_capability_gap',return_value=None), \
         patch.object(mcp,'_match_briefing_gate',return_value=None):
        result=mcp.smac_choices(kind='production',base_ref='base-public')
    assert result['ok'] and result['production_context']['current']['progress_state']==stalled
    assert 'while' in result['production_context']['current']['condition']
    assert catalog==original and len(result['choices'])==2
    assert result['production_context']['population']==1
    assert 'does not prove' in result['production_context']['selection_completion_boundary']
    colony=next(row for row in result['choices'] if row.get('name')=='Colony Pod')
    assert colony['population_effect']['population_change_on_selection']==0
    quote=next(row for row in result['choices'] if row.get('label')=='Hurry production')
    assert quote['quote_scope']['single_execution'] is True
    assert 'production switch' in quote['quote_scope']['invalidated_by']
    topology=PerspectiveTopology(MapShape(20,8,False),[KnownSquare('start',0,2,'land')])
    base=obj('base','base','start',production_cost=30,minerals_accumulated=1,mineral_surplus=0)
    objects={'base':base}
    p=base_mechanics(topology,objects,['base'])[0]['production']
    assert p['progress_state']==stalled and p['turns_remaining'] is None and p['inputs_current']
    for field in ('production_cost','minerals_accumulated','mineral_surplus'):
        stale=copy.deepcopy(objects);stale['base']['fields'][field]['epistemic_status']='stale'
        p=base_mechanics(topology,stale,['base'])[0]['production']
        assert p['progress_state']=='unknown' and p['turns_remaining'] is None and not p['inputs_current']
    print(json.dumps({'passed':True,'managed_zero_surplus_delivered':True,
                      'selection_completion_boundary_delivered':True,
                      'population_effect_remains_conditional':True,
                      'hurry_quote_scoped_to_one_revision':True,
                      'stale_inputs_not_promoted':True,'already_funded_not_called_stalled':True,
                      'classification':'adapter and deterministic conditional calculation'}))

if __name__=='__main__': main()
