#!/usr/bin/env python3
"""Instrument the pinned Hermes validation path before tool executor dispatch."""
from pathlib import Path
path = Path("/opt/hermes/agent/conversation_loop.py")
source = path.read_text()
needle = """                invalid_tool_calls = [
                    tc.function.name for tc in assistant_message.tool_calls
                    if tc.function.name not in agent.valid_tool_names
                ]
"""
if source.count(needle) != 1:
    raise SystemExit("unexpected Hermes tool name validation layout")
source = source.replace(needle, needle + """                if invalid_tool_calls:
                    from smacx_diagnostics import record_unknown_tool_calls
                    record_unknown_tool_calls(assistant_message.tool_calls, agent.valid_tool_names)
""")
path.write_text(source)

path = Path('/opt/hermes/run_agent.py')
source = path.read_text()
for needle, replacement in [
    ('                    nonlocal _lease_waited\n',
     '                    nonlocal _lease_waited\n                    from smacx_diagnostics import record_session_admission\n                    record_session_admission("waiting_session_lease")\n'),
    ('                # Assign only after admission so finally release cannot target a\n',
     '                from smacx_diagnostics import record_session_admission\n                record_session_admission("session_admitted")\n\n                # Assign only after admission so finally release cannot target a\n'),
]:
    if source.count(needle) != 1:
        raise SystemExit('unexpected Hermes session admission layout')
    source = source.replace(needle, replacement)
path.write_text(source)
