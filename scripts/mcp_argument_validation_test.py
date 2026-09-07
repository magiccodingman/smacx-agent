#!/usr/bin/env python3
"""Real MCP SDK boundary rejects ignored arguments before any tool body runs."""
import asyncio
import json
import gzip
import os
import tempfile
from pathlib import Path
from smacx_mcp_validation import StrictMCPServer
from smacx_diagnostic_summary import Metrics


async def main():
    temporary=tempfile.TemporaryDirectory()
    os.environ.update(SMACX_DIAGNOSTICS_ENABLED='1', SMACX_AGENT_MATCH_ID='match-validation-fixture',
                      SMACX_DIAGNOSTICS_ROOT=temporary.name)
    server=StrictMCPServer('validation-fixture')
    calls=[]
    @server.tool()
    def select_unit(own_unit_ref: str = '', payload: dict | None = None) -> dict:
        calls.append(own_unit_ref)
        return {'selected':own_unit_ref, 'payload':payload}
    advertised=await server.list_tools()
    schema=advertised[0].input_schema
    assert schema['additionalProperties'] is False
    rejected=await server.call_tool('select_unit',{'focus_id':'other-unit'})
    body=json.loads(rejected.content[0].text)
    assert rejected.is_error and body['error']['code']=='unknown_tool_arguments'
    assert body['error']['allowed_arguments']==['own_unit_ref','payload']
    assert body['native_action_executed'] is False and calls==[]
    accepted=await server.call_tool('select_unit',{'own_unit_ref':'unit-2','payload':{'arbitrary_nested_data':1}})
    assert not accepted.is_error and calls==['unit-2']
    default=await server.call_tool('select_unit',{})
    assert not default.is_error and calls==['unit-2','']
    metrics=Metrics();metrics.add({'kind':'managed_tool_validation_rejected','payload':{'tool':'select_unit','result':body}})
    assert metrics.as_dict()['failure_observations_by_layer']=={'managed_tool_validation_rejected:unknown_tool_arguments':1}
    import smacx_mcp
    actual=await smacx_mcp.mcp.call_tool('smac_decision', {'focus_id':'unsupported'})
    assert actual.is_error and json.loads(actual.content[0].text)['error']['code']=='unknown_tool_arguments'
    rows=[json.loads(line) for path in Path(temporary.name).rglob('*.gz') for line in gzip.open(path,'rt')]
    assert len(rows)==2 and all(row['kind']=='managed_tool_validation_rejected' for row in rows)
    assert rows[-1]['payload']['arguments']=={'focus_id':'unsupported'}
    temporary.cleanup()
    print(json.dumps({'passed':True,'unknown_arguments_never_execute':True,'schema_advertises_rejection':True,
                     'declared_nested_payload_and_defaults_preserved':True}))


asyncio.run(main())
