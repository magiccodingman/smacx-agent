"""Controlled notice drain: capture before guarded execution, bounded, no choice guessing."""
from types import SimpleNamespace
import smacx_mcp as m
m.MANAGED_ATTACHED=True
reviewed='Acknowledge this reviewed information-only game notification.'
frames=[]; events=[]; mode='normal'; count=0

def frame(*args):
 global count
 count+=1
 revision=str(count) if mode!='unchanged' else '1'
 d='d'+revision
 choices={'c':{'command':'acknowledge_popup','meaning':reviewed}}
 if mode=='generic': choices['c']['meaning']='Acknowledge an engine-confirmed information-only popup with no alternatives.'
 if mode=='choice': choices['other']={'command':'respond_to_artifact'}
 m.DECISION_CACHE[d]={'choices':choices}
 return {'ok':True,'decision_id':d,'identity':{'match_id':'m','session_id':'s','revision':revision},
         'focus':{'kind':'interaction','popup_label':'SIMULYOU'},'information':[{'pending':'not verified'}]}
def enqueue(*args,**kwargs):
 events.append('capture')
 if mode=='capture-fail': raise RuntimeError()
 return {'attention_id':'notice'}
def execute(*args):
 events.append('execute')
 return {'ok':mode!='reject','queued':mode=='queued'}
m._decision_frame_once=frame
m._execute_choice_once=execute
m._public_execution_receipt=lambda r:r
m._runtime_services=lambda:(None,SimpleNamespace(scope='scope',timeline_id='t',enqueue=enqueue,
 world_store=SimpleNamespace(load=lambda *args:{'action_revision':str(count) if mode!='unchanged' else '1','observation_cursor':3})))
for mode in ('normal','generic','choice','capture-fail','unchanged','reject','queued'):
 events.clear();count=0
 result=m.smac_decision()
 if mode=='normal': assert events==['capture','execute']*4 and count==5
 elif mode in ('generic','choice'): assert events==[]
 elif mode=='capture-fail': assert events==['capture']
 else: assert events==['capture','execute']
 for item in result.get('automatic_notifications',[]): assert item['cognitively_acknowledged'] is False
print('reviewed notice drain passed: durable capture first, four bound, unchanged/rejected/queued stop, generic and strategic choices untouched')
