#!/usr/bin/env python3
import json
from smacx_mcp import _production_catalog_context
for surplus,extra,expected,state in [(1,1,0,'zero'),(3,1,2,'positive'),(1,2,-1,'negative'),(0,0,0,'zero')]:
    source={'current':{'mineral_surplus':surplus},'support_projection':{'epistemic_status':'conditional','additional_support_minerals':extra,'condition':'Native uncertainty retained.'}}
    result=_production_catalog_context(source)['support_projection']
    assert result['mineral_surplus_after_one_completion']==expected
    assert result['passive_production_after_one_completion'].startswith(state)
    assert result['epistemic_status']=='conditional' and result['condition']==source['support_projection']['condition']
    assert 'mineral_surplus_after_one_completion' not in source['support_projection']
for state in ['stale','unknown']:
    source={'current':{'mineral_surplus':1,'epistemic_status':state},'support_projection':{'epistemic_status':'conditional','additional_support_minerals':1}}
    assert 'mineral_surplus_after_one_completion' not in _production_catalog_context(source)['support_projection']
for value in [None,True,'1']:
    source={'current':{'mineral_surplus':value},'support_projection':{'epistemic_status':'conditional','additional_support_minerals':1}}
    assert 'mineral_surplus_after_one_completion' not in _production_catalog_context(source)['support_projection']
print(json.dumps({'pass':True,'zero_positive_negative':True,'conditional_preserved':True,'stale_unknown_invalid_not_promoted':True}))
