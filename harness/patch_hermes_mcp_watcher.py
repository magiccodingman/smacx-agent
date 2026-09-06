#!/usr/bin/env python3
"""Remove a discarded coroutine in the pinned Hermes MCP liveness race."""
from pathlib import Path


def patch(source: str) -> str:
    old = '''                    _watch_ok = (
                        _watch_children is not None
                        and inspect.isawaitable(_watch_children())
                        and asyncio.iscoroutine(_call_coro)
                    )'''
    new = '''                    _watch_coro = (
                        _watch_children()
                        if callable(_watch_children) and asyncio.iscoroutine(_call_coro)
                        else None
                    )
                    _watch_ok = inspect.isawaitable(_watch_coro)'''
    task = 'watch_task = asyncio.ensure_future(_watch_children())'
    if source.count(old) != 1 or source.count(task) != 1:
        raise ValueError('unexpected Hermes MCP child watcher layout')
    return source.replace(old, new).replace(task, 'watch_task = asyncio.ensure_future(_watch_coro)')


if __name__ == '__main__':
    path = Path('/opt/hermes/tools/mcp_tool.py')
    path.write_text(patch(path.read_text()))
