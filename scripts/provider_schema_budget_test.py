#!/usr/bin/env python3
"""Provider-footprint gates for the stable prompt and managed MCP surface.

Run with the control image's MCP virtualenv.  ``SMACX_QWEN_TOKENIZE_URL`` may
point at a compatible tokenizer endpoint for the exact live gate; the
deterministic fallback is deliberately conservative and is recorded as such.
"""

from __future__ import annotations

import asyncio
import json
import os
from urllib.request import Request, urlopen

os.environ.setdefault("SMACX_MANAGED_ATTACHED", "1")

import smacx_mcp
from smacx_prompt import compose_player_system_prompt, prompt_sha256


def serialized(tool) -> str:
    value = tool.model_dump(mode="json") if hasattr(tool, "model_dump") else tool.dict()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def exact_tokens(text: str) -> tuple[int | None, str]:
    endpoint = os.environ.get("SMACX_QWEN_TOKENIZE_URL", "").strip()
    if not endpoint:
        return None, "conservative_utf8_proxy"
    payload = {"prompt": text}
    if os.environ.get("SMACX_QWEN_TOKENIZE_MODEL"):
        payload["model"] = os.environ["SMACX_QWEN_TOKENIZE_MODEL"]
    request = Request(endpoint, data=json.dumps(payload).encode(), headers={
        "Content-Type": "application/json",
        **({"Authorization": "Bearer " + os.environ["SMACX_QWEN_TOKENIZE_KEY"]}
           if os.environ.get("SMACX_QWEN_TOKENIZE_KEY") else {}),
    })
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    count = payload.get("count")
    if not isinstance(count, int):
        tokens = payload.get("tokens")
        count = len(tokens) if isinstance(tokens, list) else None
    return count, "provider_tokenizer"


def main() -> int:
    tools = asyncio.run(smacx_mcp.mcp.list_tools())
    by_name = {tool.name: serialized(tool) for tool in tools}
    world_bytes = len(by_name["smac_world"].encode())
    # Three bytes/token is a conservative deterministic proxy for this compact
    # English/JSON schema. The exact Qwen gate may be supplied by CI/live QA.
    world_proxy = (world_bytes + 2) // 3
    if world_proxy > 900:
        raise AssertionError(f"smac_world schema exceeds hard gate: {world_proxy}")
    prompt = compose_player_system_prompt(
        agent_name="Test Sovereign", agent_id="agent-test",
        match_id="match-test", match_name="Provider Budget",
        perspective_id="perspective-test", ruleset_id="smacx",
        seat_index=0, match_policy={"timer": "none"},
    )
    count, tokenizer = exact_tokens(prompt)
    prompt_proxy = (len(prompt.encode()) + 2) // 3
    tool_text = "\n".join(by_name[name] for name in sorted(by_name))
    tool_tokens, _ = exact_tokens(tool_text)
    world_tokens, _ = exact_tokens(by_name["smac_world"])
    tool_proxy = (len(tool_text.encode()) + 2) // 3
    if tool_proxy > 8192:
        raise AssertionError("managed schemas exceed doctrine tool reserve")
    if count is not None and count > 1600:
        raise AssertionError(f"v6 system prompt exceeds exact Qwen hard gate: {count}")
    # Without the tokenizer, retain a visible proxy rather than falsely
    # claiming an exact token count. It is diagnostic, not the exact gate.
    print(json.dumps({"event": "pass", "payload": {
        "managed_tool_count": len(tools),
        "smac_world_schema_bytes": world_bytes,
        "smac_world_conservative_token_proxy": world_proxy,
        "smac_world_target": 700, "smac_world_hard_gate": 900,
        "system_prompt_bytes": len(prompt.encode()),
        "system_prompt_conservative_token_proxy": prompt_proxy,
        "system_prompt_exact_tokens": count, "tokenizer": tokenizer,
        "system_prompt_sha256": prompt_sha256(prompt),
        "total_tool_schema_bytes": sum(len(value.encode()) for value in by_name.values()),
        "total_tool_schema_exact_tokens": tool_tokens,
        "total_tool_schema_conservative_token_proxy": tool_proxy,
        "smac_world_schema_exact_tokens": world_tokens,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
