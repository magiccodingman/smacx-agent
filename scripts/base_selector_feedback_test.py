#!/usr/bin/env python3
"""Missing conditional base parameters must recover through public references."""
import json
from unittest.mock import patch
import smacx_mcp as mcp

with patch.object(mcp,'_sovereign_gameplay_gate',return_value=None), \
     patch.object(mcp,'_call') as native, \
     patch.object(mcp,'_refresh_managed_world') as refresh:
    for kind in ('production','base_management','base_citizens'):
        result=mcp.smac_choices(kind)
        assert result['error']['code']=='base_ref_required'
        assert result['required_next']['tool']=='smac_choices'
        assert result['required_next']['kind']==kind
        assert result['required_next']['required_arguments']==['base_ref']
        assert result['required_next']['reference_source']['arguments']['mode']=='overview'
        assert 'base_id' not in json.dumps(result)
    native.assert_not_called()
    refresh.assert_not_called()

identity={'match_id':'match-test','session_id':'session-test','revision':'r1'}
with patch.object(mcp,'_sovereign_gameplay_gate',return_value=None), \
     patch.object(mcp,'_refresh_managed_world',return_value={'ok':True}), \
     patch.object(mcp,'_resolve_managed_selectors',return_value=({'base_id':3},{})) as resolve, \
     patch.object(mcp,'_call',side_effect=[{'ok':True,'snapshot':identity},
         {'ok':False,'error':{'code':'invalid_base','message':'base_id must identify a base owned by the human faction.'}}]) as native:
    result=mcp.smac_choices('production',base_ref='base-location-10')
    assert result['error']['code']=='invalid_base'
    assert 'base_ref' in result['error']['message'] and 'base_id' not in result['error']['message']
    assert native.call_args.kwargs=={'kind':'production','base_id':3}
    assert resolve.call_args.kwargs['base_ref']=='base-location-10'
# Existing preparations must reach their normal validation without a repeated actor.
with patch.object(mcp,'_sovereign_gameplay_gate',return_value=None), \
     patch.object(mcp,'_refresh_managed_world',return_value={'ok':True}), \
     patch.object(mcp,'_call',return_value={'ok':False,'error':{'code':'snapshot-test'}}):
    assert mcp.smac_choices('production',preparation_ref='preparation-existing')['error']['code']=='snapshot-test'
print(json.dumps({'passed':True,'missing_base_families_recover_semantically':True,
    'no_native_enumeration_without_required_actor':True,'native_invalid_base_message_public':True,
    'preparation_actor_not_repeated':True}))
