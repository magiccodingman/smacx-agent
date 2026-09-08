"""Native waiting cannot latch a gap; ready units don't override turn ownership."""
from unittest.mock import patch
import smacx_mcp as m
from smacx_runtime_context import _focus
snapshot={'match_id':'match-wait','session_id':'session-wait','revision':'r1','turn':1,
 'protocol':{'phase':'wait'}, 'interaction':{'kind':'waiting_for_turn'},
 'ready_unit_refs':[{'own_unit_ref':'own-unit-1'}]}
with patch.object(m,'_call',return_value={'ok':True,'snapshot':snapshot}), \
     patch.object(m,'_attach_chat_attention',side_effect=lambda r,i:r):
    receipt=m._wait_response({},changed=False)
    assert receipt['required_next']['ordinary_message']=='WAITING'
    assert 'sleep' in receipt
    gap=m.smac_report_capability_gap('wait','move','actions','move','unchanged for 23 seconds')
    assert not gap['recorded'] and not gap['gameplay_mutations_blocked']
    assert gap['error']['code']=='native_wait_not_capability_gap'
    assert not m.CAPABILITY_GAPS
assert _focus(snapshot)['kind']=='wait'
print('PASS: wait receipt, non-latching false gap, wait precedence over ready units')
