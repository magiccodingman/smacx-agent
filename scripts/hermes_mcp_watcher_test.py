#!/usr/bin/env python3
"""Exercise the installed Hermes MCP race with controlled RPC/child lifetimes."""
import asyncio
import gc
import inspect
import json
from pathlib import Path
import textwrap
from types import SimpleNamespace
import warnings

source = Path('/opt/hermes/tools/mcp_tool.py').read_text()
start = source.index('                    _call_coro = server.session.call_tool(tool_name, arguments=args)')
end = source.index('                finally:\n                    server._pending_call_context = None', start)
body = textwrap.dedent(source[start:end])
namespace = {'asyncio': asyncio, 'inspect': inspect, 'tool_name': 'fixture',
             'args': {}, 'server_name': 'fixture', 'tool_timeout': 60,
             '_signal_reconnect': lambda server: setattr(server, 'reconnected', True)}
exec('async def invoke(server):\n' + textwrap.indent(body, '    ') + '\n    return result\n', namespace)


async def scenario(child_exits=False, synchronous=False):
    events=[]
    async def rpc():
        events.append('rpc_started')
        try:
            await asyncio.sleep(10 if child_exits else .01)
            return 'ok'
        finally: events.append('rpc_finished')
    async def watch():
        events.append('watch_started')
        try: await asyncio.sleep(.001 if child_exits else 10)
        finally: events.append('watch_finished')
    def make_watch():
        events.append('watch_created')
        return watch()
    server=SimpleNamespace(session=SimpleNamespace(call_tool=lambda *a,**k: 'sync' if synchronous else rpc()),
                           _watch_stdio_children=make_watch, reconnected=False)
    try:
        result=await namespace['invoke'](server)
        assert not child_exits and result == ('sync' if synchronous else 'ok')
    except TimeoutError:
        assert child_exits and server.reconnected
    if synchronous: assert not events
    else:
        assert events.count('watch_created')==1, events
        assert events.count('rpc_started')==1 and events.count('rpc_finished')==1
        assert events.count('watch_finished')==1
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]


with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    asyncio.run(scenario())
    asyncio.run(scenario(child_exits=True))
    asyncio.run(scenario(synchronous=True))
    gc.collect()
    assert not [w for w in caught if 'never awaited' in str(w.message)], caught
print(json.dumps({'passed': True, 'single_rpc_and_watcher': True,
                  'dead_child_cancels_rpc_and_requests_reconnect': True,
                  'no_orphan_tasks_or_coroutines': True}))
