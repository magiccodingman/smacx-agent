#!/usr/bin/env python3
"""Wire-only terminal receipt restoration; never imports a live Hermes session."""
import ast, copy, json, re
from pathlib import Path
source=Path('harness/smacx_strict_prompt.py').read_text()
functions=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef)
           and node.name in {'_managed_tool_result','_restore_terminal_receipts'}]
namespace={'copy':copy,'json':json,'re':re}
exec(compile(ast.Module(body=functions,type_ignores=[]),'<production helpers>','exec'),namespace)
restore=namespace['_restore_terminal_receipts']
receipt=json.dumps({'ok':True,'turn_handoff_required':{'required':True},
                    'required_next':{'stop_after':True,'ordinary_message':'TURN HANDOFF'}})
note='[hermes note: this result is byte-identical to the result earlier this turn (tool_call_id a). Refer to that result. Args: {}] Change arguments or use another tool.'
rows=[{'role':'user','content':'play'},{'role':'tool','tool_call_id':'a','content':receipt},
      {'role':'tool','tool_call_id':'b','content':note}]
saved=copy.deepcopy(rows);out=restore(rows)
assert out[-1]['content']==receipt and rows==saved
assert restore([*rows,{'role':'user','content':'new episode'},{'role':'tool','tool_call_id':'c','content':note}])[-1]['content']==note
rows[1]['content']=json.dumps({'ok':True,'choices':[]})
assert restore(rows)[-1]['content']==note
assert restore([{'role':'tool','tool_call_id':'b','content':note}])[0]['content']==note
print('PASS: terminal repeat receipts restored only within current invocation; history and nonterminal notes unchanged')
