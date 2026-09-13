#!/usr/bin/env python3
"""Run directive and affected regression suites without a live game/provider.

Install the repository's pinned mcp==2.0.0 dependency first. This runner writes
only its requested result directory and temporary files owned by test fixtures.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DIRECTIVES = ('unit_directive_test', 'unit_directive_mcp_test', 'unit_directive_context_test')
REGRESSIONS = (
    'campaign_journal_test', 'journal_internal_crash_test', 'journal_idempotency_index_test',
    'rollback_world_contract_test', 'movement_mechanics_contract_test',
    'attention_communication_contract_test', 'opaque_choice_execution_test',
    'decision_frame_test', 'decision_recovery_test', 'automatic_notification_test',
    'managed_action_path_contract_test', 'managed_mcp_contract_test', 'managed_tool_surface_test',
    'strict_prompt_contract_test', 'runtime_context_contract_test', 'post_action_decision_test',
    'intent_managed_guard_test', 'intent_reconciliation_contract_test',
    'persistent_order_attention_test', 'plan_health_contract_test', 'memory_contract_test',
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--quick', action='store_true', help='Run only the three new directive suites.')
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime/directive-validation')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tests = DIRECTIVES if args.quick else DIRECTIVES + REGRESSIONS
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(str(ROOT / p) for p in ('src', 'harness', 'scripts'))}

    def run(name: str) -> dict:
        start = time.monotonic()
        try:
            process = subprocess.run([sys.executable, str(ROOT / 'scripts' / (name + '.py'))],
                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=180, check=False)
            code, output = process.returncode, process.stdout
        except subprocess.TimeoutExpired as exc:
            code, output = 124, str(exc)
        (args.output / (name + '.log')).write_text(output, encoding='utf-8')
        return {'test': name, 'exit_code': code, 'seconds': round(time.monotonic() - start, 3)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, tests))
    for result in results:
        print(('PASS' if result['exit_code'] == 0 else 'FAIL'), result['test'], f"{result['seconds']:.3f}s")
    passed = all(row['exit_code'] == 0 for row in results)
    report = {'passed': passed, 'suites': results, 'python': sys.version,
              'live_game_tested': False, 'live_multiplayer_tested': False,
              'provider_calls': 0, 'campaign_quality_or_speedup_claimed': False}
    (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"{sum(row['exit_code'] == 0 for row in results)}/{len(results)} suites passed")
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
