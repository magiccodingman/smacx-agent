"""Reject unknown MCP arguments before the SDK can discard them."""
from __future__ import annotations

import json
from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from smacx_diagnostics import record


class StrictMCPServer(MCPServer):
    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)
        def decorate(function):
            registered = register(function)
            name = kwargs.get('name') or (args[0] if args else None) or function.__name__
            tool = self._tool_manager.get_tool(name)
            tool.parameters['additionalProperties'] = False
            return registered
        return decorate

    async def call_tool(self, name, arguments, context=None):
        tool = self._tool_manager.get_tool(name)
        if tool is not None and isinstance(arguments, dict):
            allowed = set(tool.parameters.get('properties', {}))
            unknown = sorted(set(arguments) - allowed)
            if unknown:
                result = {'ok': False, 'error': {'code': 'unknown_tool_arguments',
                    'message': 'Use only the declared parameters; inspect the tool schema before retrying.',
                    'unknown_arguments': [key[:120] for key in unknown[:20]],
                    'additional_unknown_count': max(0, len(unknown)-20),
                    'allowed_arguments': sorted(allowed)},
                    'execution_status': 'not_executed', 'native_action_executed': False}
                self.record_argument_rejection({'tool': name, 'arguments': arguments, 'result': result})
                return CallToolResult(content=[TextContent(type='text', text=json.dumps(result))], is_error=True)
        return await super().call_tool(name, arguments, context)

    def record_argument_rejection(self, payload):
        record('managed_tool_validation_rejected', payload, actor='managed-mcp')
