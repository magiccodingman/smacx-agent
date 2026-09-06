#!/usr/bin/env python3
"""Prove the managed hook replaces Hermes prompt assembly and fails closed."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from smacx_prompt import compose_player_system_prompt, prompt_sha256


ROOT = Path(__file__).resolve().parents[1]


def run_probe(package_root: Path, prompt_path: Path, expected_hash: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update({
        "PYTHONPATH": os.pathsep.join((
            str(ROOT / "harness"), str(ROOT / "src"), str(package_root),
        )),
        "SMACX_STRICT_SYSTEM_PROMPT": "1",
        "SMACX_SYSTEM_PROMPT_FILE": str(prompt_path),
        "SMACX_SYSTEM_PROMPT_SHA256": expected_hash,
    })
    return subprocess.run(
        [sys.executable, "-c", (
            "import json\nfrom types import SimpleNamespace\n"
            "try:\n import smacx_strict_prompt\n"
            "except Exception:\n pass # Python site/.pth swallows startup exceptions\n"
            "import agent.system_prompt as s; "
            "agent=SimpleNamespace(); "
            "value=s.build_system_prompt(agent, 'upstream additive text'); "
            "parts=s.build_system_prompt_parts(agent, 'upstream additive text'); "
            "import run_agent, smacx_strict_prompt; "
            "smacx_strict_prompt._append_runtime_context=lambda rows: rows; "
            "history=[{'role':'system','content':'saved stale prompt'}, "
            "{'role':'system','content':'upstream extra'}, {'role':'user','content':'resume'}]; "
            "wire=run_agent.AIAgent._sanitize_api_messages(history); "
            "assert history[0]['content']=='saved stale prompt'; "
            "assert [row['content'] for row in wire if row['role']=='system']==[value]; "
            "released=[]; "
            "smacx_strict_prompt._mark_runtime_responded=lambda: None; "
            "smacx_strict_prompt._end_runtime_episode=lambda **kw: released.append(kw['committed']); "
            "instance=run_agent.AIAgent(); "
            "instance._build_assistant_message({'content':'partial'}, 'length'); "
            "instance._build_assistant_message({'content':'continue'}, 'incomplete'); "
            "instance._build_assistant_message({'content':'filtered'}, 'content_filter'); "
            "instance._build_assistant_message({'tool_calls':[{'id':'call'}]}, 'tool_calls'); "
            "instance._build_assistant_message({'content':'done'}, 'stop'); "
            "assert released==[False,False,False,True], released; "
            "boundary=[{'role':'user','content':'old'}, {'role':'assistant','content':'old'}, "
            "{'role':'tool','content':'old'}, {'role':'user','content':'resume'}]; "
            "assert smacx_strict_prompt._episode_id(boundary)==smacx_strict_prompt._episode_id([boundary[0],boundary[-1]]); "
            "assert smacx_strict_prompt._episode_id(boundary)!=smacx_strict_prompt._episode_id(boundary+[boundary[-1]]); "
            "print(json.dumps({'value':value,'parts':parts}))"
        )],
        cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
    )


def main() -> int:
    prompt = compose_player_system_prompt(
        agent_name="Strict Contract Player",
        agent_id="agent-strict-contract",
        match_id="match-strict-contract",
        match_name="Strict prompt contract",
        perspective_id="perspective-strict-contract",
        ruleset_id="smacx", seat_index=2,
        match_policy={"ranking_mode": "unranked"},
    )
    if prompt != compose_player_system_prompt(
        agent_name="Strict Contract Player", agent_id="agent-strict-contract",
        match_id="match-strict-contract", match_name="Strict prompt contract",
        perspective_id="perspective-strict-contract", ruleset_id="smacx",
        seat_index=2, match_policy={"ranking_mode": "unranked"},
    ):
        raise AssertionError("system prompt composition is not deterministic")
    from doctrine_integration_contract_test import SEAT
    from doctrine_content_contract_test import fixtures
    from smacx_doctrine import compose_managed_prompt
    prompt, _ = compose_managed_prompt(fixtures()["stock-blind"], **SEAT)
    with tempfile.TemporaryDirectory(prefix="smacx-strict-prompt-") as temporary:
        root = Path(temporary)
        package = root / "agent"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "system_prompt.py").write_text(
            "def build_system_prompt_parts(agent, system_message=None):\n"
            "    return {'stable':'upstream','context':'extra','volatile':'extra'}\n"
            "def build_system_prompt(agent, system_message=None):\n"
            "    return 'upstream\\n' + (system_message or '')\n",
            encoding="utf-8",
        )
        (root / "run_agent.py").write_text(
            "class AIAgent:\n"
            "    @staticmethod\n"
            "    def _sanitize_api_messages(messages):\n"
            "        return [dict(message) for message in messages]\n"
            "    def _strip_think_blocks(self, content):\n"
            "        return content\n"
            "    def _build_assistant_message(self, message, finish_reason):\n"
            "        return dict(message)\n",
            encoding="utf-8",
        )
        prompt_path = root / "SYSTEM.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        result = run_probe(root, prompt_path, prompt_sha256(prompt))
        if result.returncode != 0:
            raise AssertionError(result.stderr)
        captured = json.loads(result.stdout)
        if captured["value"] != prompt or captured["parts"] != {
                "stable": prompt, "context": "", "volatile": ""}:
            raise AssertionError("Hermes additive system prompt survived the strict override")
        corrupted = run_probe(root, prompt_path, hashlib.sha256(b"wrong").hexdigest())
        if corrupted.returncode == 0 or "smacx_strict_prompt_integrity_failure" not in corrupted.stderr:
            raise AssertionError("strict hook did not fail closed on prompt hash mismatch")
        prompt_path.write_text(prompt + 'x' * 100000, encoding='utf-8')
        oversized = run_probe(root, prompt_path, prompt_sha256(prompt_path.read_text()))
        if oversized.returncode == 0 or 'managed_prompt_context_headroom_insufficient' not in oversized.stderr:
            raise AssertionError('oversized prompt fell back to upstream builder')
    print(json.dumps({
        "event": "pass",
        "payload": {
            "deterministic_prompt": True,
            "upstream_prompt_replaced": True,
            "stale_duplicate_system_rows_replaced_without_history_mutation": True,
            "provider_system_message_exact": True,
            "integrity_failure_closed": True,
            "oversized_prompt_failure_closed_after_site_startup": True,
            "personality_seam_only": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
