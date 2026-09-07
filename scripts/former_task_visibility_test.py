#!/usr/bin/env python3
"""Distinguish automation assignment, selected work, and unknown completion."""
import copy,json,subprocess,tempfile
from pathlib import Path
from smacx_diagnostic_summary import summary as trace_summary
from smacx_runtime_context import _force_summary
from smacx_world_types import provider_safe
source=(Path(__file__).resolve().parents[1]/'bridge/src/agent_bridge.cpp').read_text()
helper=source[source.index('std::string semantic_owned_terraform_task('):source.index('int former_automation_mode_id(')]
code=r'''
#include <string>
#include <sstream>
#include <iostream>
#include <cassert>
const int VehOrderFormerFirst=4,VehOrderFormerLast=23;
struct VEH {int faction_id=1,order=0,movement_turns=6;bool former=true,automated=true;
 bool is_former(){return former;}};
struct Terra {const char* name="work";} Terraform[20];
bool semantic_native_automation_active(const VEH& v){return v.automated;}
const char* unit_order_name(int order){return order>=4 && order<=23?"terraform":order==24?"go_to":"none";}
std::string json_string(const char* s){return std::string("\"")+s+"\"";}
'''+helper+r'''
int main(){VEH v;
 assert(semantic_owned_terraform_task(2,v).empty());
 v.former=false;assert(semantic_owned_terraform_task(1,v).empty());v.former=true;
 for(int order:{0,4,6,9,13,23,24}) {v.order=order;
  std::cout<<"{\"order\":"<<order<<semantic_owned_terraform_task(1,v)<<"}\n";}
}
'''
with tempfile.TemporaryDirectory() as tmp:
 p=Path(tmp);(p/'test.cpp').write_text(code)
 subprocess.run(['g++','-std=c++17',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
 rows=[json.loads(x) for x in subprocess.check_output([str(p/'test')],text=True).splitlines()]
for row in rows:
 task=row['terraform_task'];active=4<=row['order']<=23
 assert task['automation_active'] and task['completion_verified'] is False
 assert task['state']==('active_terraform_order' if active else 'no_active_terraform_order')
 assert ('accumulated_work_points' in task)==active
 assert 'eta' not in task and 'remaining_turns' not in task
 if active:assert task['accumulated_work_points']==6
assert source.count('<< semantic_owned_terraform_task(faction_id, veh)')==4

def unit(task,status='current',kind='own_unit'):
 return {'kind':kind,'status':'active','fields':{
  'roles':{'value':{'former':True},'epistemic_status':'current'},
  'order_name':{'value':'auto_former_full','epistemic_status':'current'},
  'terraform_task':{'value':task,'epistemic_status':status,'source':'owned_state'}}}
active={'state':'active_terraform_order','name':'Build Road','accumulated_work_points':6,'completion_verified':False}
idle={'state':'no_active_terraform_order','automation_active':True,'completion_verified':False}
projection={'objects':[unit(active),unit(idle),unit(active,'stale'),unit(active,kind='foreign_contact')]}
original=copy.deepcopy(projection);summary=_force_summary(projection)
assert summary['former_tasks']['active_task_names']['counts']=={'Build Road':1}
assert summary['former_tasks']['no_active_terraform_order']==1
assert summary['former_tasks']['missing_or_noncurrent_task']==1
assert summary['observed_orders']['counts']=={'auto_former_full':3}
assert projection==original
stale={'terraform_task':{'value':active,'source':'owned_state','epistemic_status':'stale'}}
assert provider_safe(stale)==stale
foreign={'terraform_task':{'value':active,'source':'direct_sight','epistemic_status':'current'}}
assert provider_safe(foreign)=={}
trace = trace_summary({'kind':'tool_returned','payload':{
 'managed_name':'smac_execute_choice','content':{'ok':True,'execution_status':'order_assigned',
 'terraform_completion_verified':False,'follow_up':'Inspect current terraform_task.'}}})
assert 'terraform_completion_verified' in trace and 'terraform_task' in trace
trace = trace_summary({'kind':'tool_returned','payload':{
 'managed_name':'smac_decision','content':{'ok':True,'choices':[],
 'focus':{'kind':'unit_actions','unit':{'terraform_task':idle}}}}})
assert 'no_active_terraform_order' in trace
print(json.dumps({'passed':True,'compiled_production_task_output':True,'ownership_guard':True,
 'automation_without_task_not_counted_as_work':True,'stale_task_not_promoted':True,
 'completion_and_eta_not_inferred':True,'native_deployed_task_comparison':'separate gate'}))
