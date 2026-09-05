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
            "import json; from types import SimpleNamespace; import smacx_strict_prompt; "
            "import agent.system_prompt as s; "
            "agent=SimpleNamespace(); "
            "value=s.build_system_prompt(agent, 'upstream additive text'); "
            "parts=s.build_system_prompt_parts(agent, 'upstream additive text'); "
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
    print(json.dumps({
        "event": "pass",
        "payload": {
            "deterministic_prompt": True,
            "upstream_prompt_replaced": True,
            "provider_system_message_exact": True,
            "integrity_failure_closed": True,
            "personality_seam_only": True,
        },
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
