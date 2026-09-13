#!/usr/bin/env python3
"""Actual context functions with controlled transport; no Hermes process/game.

The nested wire compactor is compiled unchanged from production source. Only
its enclosing transport/system-builder closures are supplied by this fixture.
"""
from copy import deepcopy
from pathlib import Path
import ast
import json
import logging
import os
import unittest

os.environ.pop('SMACX_STRICT_SYSTEM_PROMPT', None)
os.environ.pop('SMACX_SPECIALIST_STRICT_PROMPT', None)
import smacx_strict_prompt as wire
import unit_directive_mcp_test as mcp_cases
from smacx_runtime_context import RuntimeContextAssembler
from smacx_world_model import estimate_tokens


def compactor():
    source = Path(wire.__file__).read_text()
    node = next(n for n in ast.walk(ast.parse(source))
                if isinstance(n, ast.FunctionDef) and n.name == 'compact_managed_context')
    namespace = dict(vars(wire))
    namespace.update(original_sanitize=deepcopy, canonical_system=lambda messages: messages,
                     logger=logging.getLogger('directive-wire-test'))
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<production wire compactor>', 'exec'), namespace)
    return namespace['compact_managed_context']


def call(cid, name, args, result):
    return [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': cid, 'type': 'function',
             'function': {'name': 'mcp__smacx__' + name, 'arguments': json.dumps(args)}}]},
            {'role': 'tool', 'tool_call_id': cid, 'content': json.dumps(result)}]


class DirectiveContextContracts(unittest.TestCase):
    def test_consumed_authorization_menu_retires_without_history_mutation(self):
        rows = [{'role': 'user', 'content': 'Play this turn.'}]
        rows += call('prepare', 'smac_directives', {'action': 'prepare'},
                     {'ok': True, 'decision_id': 'decision-authorize', 'choices': [{'choice_id': 'retire-this'}]})
        rows += call('approve', 'smac_execute_choice', {'decision_id': 'decision-authorize', 'choice_id': 'retire-this'},
                     {'ok': True, 'decision_consumed': True, 'directive_generation': 1})
        original = deepcopy(rows)
        out = compactor()(rows, include_runtime=False)
        prepared = next(r for r in out if r.get('tool_call_id') == 'prepare')
        self.assertNotIn('retire-this', prepared['content'])
        self.assertTrue(json.loads(prepared['content'])['decision_consumed'])
        self.assertEqual(rows, original)
        self.assertIn('smac_directives', wire._QUERY_TOOL_NAMES)
        self.assertIn('smac_directives', wire._DISPOSABLE_TOOL_NAMES)

    def test_latest_unreviewed_authorization_remains_executable(self):
        result = {'ok': True, 'decision_id': 'new-authority', 'choices': [{'choice_id': 'keep-this'}]}
        rows = [{'role': 'user', 'content': 'Play.'}] + call('prepare', 'smac_directives', {'action': 'prepare'}, result)
        out = compactor()(rows, include_runtime=False)
        self.assertEqual(json.loads(out[-1]['content']), result)

    def test_army_dashboard_fits_runtime_budget_and_preserves_counts(self):
        # Reuse the real journal/world/attention fixture without inheriting its
        # test methods or importing a live native session.
        fixture = mcp_cases.MCPDirectiveContracts(methodName='runTest')
        fixture.setUp()
        try:
            fixture.assign()
            record = next(iter(fixture.directives.read()['records'].values()))
            records = []
            for i in range(64):
                row = deepcopy(record)
                row.update(directive_id=record['directive_id'] if i == 0 else f'directive-{i:03}', actor_ref=f'own-unit-{i:03}',
                           actors=[f'own-unit-{i:03}'], purpose='purpose ' * 50)
                records.append(row)
            # Test presentation at the supported maximum, not actor validation.
            fixture.journal.append(fixture.scope, 'directive.transaction', {'generation': 2, 'updates': records})
            assembler = RuntimeContextAssembler(scope=fixture.scope, world=fixture.world,
                attention=fixture.attention, snapshot=lambda: fixture.port.snapshot,
                working_state=lambda: {'sections': {}}, interpretive_recall=lambda _: {'ok': False})
            envelope = assembler.build(episode_id='episode-dense', episode_mode='gameplay', context_length=65536)
            dashboard = envelope['unit_directives']
            # The original record is one of the 64 presentation rows.
            self.assertEqual(dashboard['active_count'], 64)
            self.assertEqual(len(dashboard['items']) + dashboard['omitted_count'], 64)
            self.assertLessEqual(estimate_tokens(dashboard), 1400)
            self.assertLessEqual(envelope['token_estimate'], envelope['budget']['total'])
            self.assertNotIn('native_id', json.dumps(dashboard))
            self.assertNotIn('scope_positions', json.dumps(dashboard))
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
