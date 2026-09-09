import copy,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'harness'))
from smacx_continuation import preserve_continuation
rows=[{'role':'user','content':'old'},
 {'role':'assistant','content':'Site B because river; Site C conditional fallback.','tool_calls':[{'id':'done'},{'id':'failed'}]},
 {'role':'tool','tool_call_id':'done','content':json.dumps({'ok':True,'superseded_runtime_state':True})},
 {'role':'tool','tool_call_id':'failed','content':json.dumps({'ok':False,'error':'unverified'})},
 {'role':'assistant','content':'Treaty promise stands; enemy sighting stale.'},
 {'role':'assistant','content':'Treaty promise stands; enemy sighting stale.'},
 {'role':'user','content':'recent'},
 {'role':'assistant','content':'Pod effect not verified.','tool_calls':[{'id':'recent'}]},
 {'role':'tool','tool_call_id':'recent','content':json.dumps({'ok':True,'effect_verified':False})},
 {'role':'user','content':'current'}]
frozen=copy.deepcopy(rows)
out,metrics=preserve_continuation(rows,9,{'done':'smac_decision'},json.loads,threshold=0)
assert rows==frozen
assert out[1]['content']==rows[1]['content'] and out[1]['tool_calls']==[{'id':'failed'}]
assert any(m.get('tool_call_id')=='failed' for m in out)
assert any(m.get('tool_call_id')=='recent' for m in out)
assert sum(m.get('content')=='Treaty promise stands; enemy sighting stale.' for m in out)==1
assert metrics['exact_prose_duplicates_removed']==1 and not metrics['semantic_summary_inferred']
print('continuation retention: strategic rationale, stale evidence, promise, unresolved result, recent episode, pair validity, immutable audit passed')

receipt = {'ok': True, 'completed': True, 'execution_status': 'completed',
           'unrecognized_effect_obligation': {'effect_verified': False},
           'executed_choice': {'label': 'Move'}, 'execution': {'status': 'completed'},
           'journal': {'journal_event_id': 'event-1'},
           'post_action_decision': {'frame': {'decision_consumed': True}},
           'required_next': {'decision_id': 'expired'}}
history = [{'role': 'user', 'content': 'old'},
           {'role': 'assistant', 'content': 'Chosen for river access.', 'tool_calls': [{'id': 'move'}]},
           {'role': 'tool', 'tool_call_id': 'move', 'content': json.dumps(receipt)},
           {'role': 'user', 'content': 'next'}]
out, _ = preserve_continuation(history, 3, {'move': 'smac_execute_choice'}, json.loads)
retained = json.loads(out[2]['content'])
assert retained['journal'] == receipt['journal'] and retained['execution'] == receipt['execution']
assert retained['unrecognized_effect_obligation'] == receipt['unrecognized_effect_obligation']
assert 'post_action_decision' not in retained and 'required_next' not in retained
assert out[1]['content'] == history[1]['content']
out, _ = preserve_continuation(history, 3, {'move': 'smac_execute_choice'}, json.loads, protected={'move'})
assert out[2] == history[2]
print('settled receipt compacted with outcome, provenance and prose retained; unseen receipt protected')
