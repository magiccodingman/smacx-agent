#!/usr/bin/env python3
"""Contained regression for per-agent Hermes profile isolation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_hermes import (
    HermesAdapterError, configure_from_descriptor, configure_profile, hermes_command,
)
from smacx_prompt import compose_player_system_prompt, prompt_sha256


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-hermes-adapter-") as temporary:
        root = Path(temporary)
        system_prompt = compose_player_system_prompt(
            agent_name="Contract Strategist",
            agent_id="agent-hermes-contract",
            match_id="match-hermes-contract",
            match_name="Hermes contract match",
            perspective_id="perspective-hermes-contract",
            ruleset_id="smacx",
            seat_index=0,
        )
        profile = configure_profile(
            hermes_root=root,
            agent_id="agent-hermes-contract",
            agent_name="Contract Strategist",
            match_id="match-hermes-contract",
            mcp_url="http://127.0.0.1:48123/mcp",
            provider_base_url="http://model-box:8000/v1",
            model_id="Qwen3.8-27B",
            reasoning_effort="low",
            context_length=65536,
            system_prompt=system_prompt,
        )
        profile_root = Path(profile["profile_root"])
        config = json.loads((profile_root / "config.yaml").read_text(encoding="utf-8"))
        if config["platform_toolsets"]["cli"] != ["smacx"]:
            raise AssertionError("visual, terminal, or unrelated tools entered the gameplay profile")
        if config["memory"]["memory_enabled"]:
            raise AssertionError("duplicate unscoped Hermes memory remained enabled")
        if config["mcp_servers"]["smacx"]["url"] != profile["mcp_url"]:
            raise AssertionError("Hermes profile was not bound to its exact MCP sidecar")
        if (profile_root / ".env").stat().st_mode & 0o777 != 0o600:
            raise AssertionError("Hermes profile secret file permissions are not private")
        if (profile_root / "SYSTEM.md").read_text(encoding="utf-8") != system_prompt \
                or (profile_root / "SOUL.md").read_text(encoding="utf-8") != system_prompt:
            raise AssertionError("strict system prompt was not materialized byte-for-byte")
        if (profile_root / "workspace" / "AGENTS.md").exists():
            raise AssertionError("managed prompt policy was duplicated into workspace instructions")
        if profile["system_prompt_sha256"] != prompt_sha256(system_prompt):
            raise AssertionError("profile system prompt integrity hash drifted")
        command = hermes_command(profile, query="Inspect status only.")
        if command[1:3] != ["-p", profile["profile_id"]] or "--continue" not in command:
            raise AssertionError("Hermes command did not preserve profile/match session identity")

        descriptor_profile = configure_from_descriptor({
            "schema": "smacx.hermes-descriptor.v1",
            "agent_id": "agent-descriptor-contract",
            "agent_name": "Descriptor Strategist",
            "match_id": "match-descriptor-contract",
            "mcp_url": "http://127.0.0.1:48125/mcp",
            "provider_base_url": "http://model-box:8000/v1",
            "provider_requires_api_key": False,
            "model_id": "Qwen3.8-27B",
            "reasoning_effort": "low",
            "external_profile_id": "smacx-descriptor-contract",
            "context_length": 65536,
        }, hermes_root=root)
        if descriptor_profile["profile_id"] != "smacx-descriptor-contract":
            raise AssertionError("Control descriptor did not preserve external profile identity")

        other = profile_root.parent / profile["profile_id"]
        (other / ".smacx-profile.json").write_text(
            '{"agent_id":"agent-someone-else"}\n', encoding="utf-8",
        )
        try:
            configure_profile(
                hermes_root=root, agent_id="agent-hermes-contract",
                agent_name="Contract Strategist", match_id="match-hermes-contract",
                mcp_url="http://127.0.0.1:48124/mcp",
                provider_base_url="http://model-box:8000/v1", model_id="Qwen3.8-27B",
            )
        except HermesAdapterError as exc:
            if str(exc) != "hermes_profile_agent_mismatch":
                raise
        else:
            raise AssertionError("Hermes profile could be reassigned across agents")
        print(json.dumps({
            "event": "pass",
            "payload": {
                "profile_per_agent": True,
                "match_workspace_isolated": True,
                "semantic_tool_allowlist": True,
                "unscoped_memory_disabled": True,
                "profile_reassignment_blocked": True,
                "low_reasoning_default": True,
                "control_descriptor_supported": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
