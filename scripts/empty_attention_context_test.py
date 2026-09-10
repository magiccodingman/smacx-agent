"""Empty batches never invite acknowledgement; real evidence keeps its receipt."""
import copy
from smacx_runtime_context import _bounded_attention
lease={'attention_lease_id':'lease-test','through_cursor':0,'items':[],
       'acknowledgement':{'tool_arguments':{'attention_lease_id':'lease-test'}}}
frozen=copy.deepcopy(lease)
assert 'acknowledgement' not in _bounded_attention(lease,token_budget=1000)
assert lease==frozen
lease['items']=[{'attention_id':'item-test','attention_kind':'notice',
                 'payload':{'meaning':'Enemy sighting is stale'}}]
result=_bounded_attention(lease,token_budget=1000)
assert result['acknowledgement']==lease['acknowledgement']
assert result['items'][0]['payload']==lease['items'][0]['payload']
print('empty acknowledgement suppressed; nonempty receipt and epistemic evidence retained')

assert 'acknowledgement' not in _bounded_attention(lease,token_budget=1)
