#!/usr/bin/env python3
"""Contained regression for per-agent Hermes profile isolation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_hermes import (
    COMMUNICATION_MCP_TOOLS, HermesAdapterError, configure_from_descriptor,
    configure_profile, hermes_command,
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
        communication = config["mcp_servers"].get("smacx-communication", {})
        if communication.get("url") != profile["mcp_url"] or set(
                communication.get("tools", {}).get("include", ())) != set(
                    COMMUNICATION_MCP_TOOLS):
            raise AssertionError("communication registry was not explicitly allowlisted")
        forbidden_communication = {
            "smac_decision", "smac_choices", "smac_command", "smac_execute_choice",
            "smac_launch", "smac_new_game", "smac_new_scenario", "smac_lan",
            "smac_saves", "smac_stop", "smac_report_capability_gap",
        }
        if forbidden_communication.intersection(COMMUNICATION_MCP_TOOLS):
            raise AssertionError("gameplay mutation schema entered communication registry")
        if config["model"].get("reasoning_echo") is not False:
            raise AssertionError("generic provider profiles unexpectedly replay reasoning")
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

        for effort in ("none", "low", "medium", "high", "xhigh"):
            effort_profile = configure_profile(
                hermes_root=root, agent_id=f"agent-reasoning-{effort}-contract",
                agent_name=f"{effort} reasoning player",
                match_id=f"match-reasoning-{effort}-contract",
                mcp_url="http://127.0.0.1:48126/mcp",
                provider_base_url="http://model-box:8000/v1", model_id="generic-model",
                reasoning_effort=effort,
                profile_id=f"smacx-reasoning-{effort}",
            )
            effort_config = json.loads((
                Path(effort_profile["profile_root"]) / "config.yaml"
            ).read_text(encoding="utf-8"))
            if effort_config["agent"]["reasoning_effort"] != effort:
                raise AssertionError(f"{effort} reasoning did not reach Hermes config")
            effort_command = hermes_command(effort_profile, query="Inspect status only.")
            if effort_command[effort_command.index("--reasoning") + 1] != effort:
                raise AssertionError(f"{effort} reasoning did not reach Hermes CLI")

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
            "generation_settings": {
                "preset": "qwen38-low", "temperature": 1.0, "top_p": 0.95,
                "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
                "repetition_penalty": 1.0,
                "extra_parameters": {"chat_template_kwargs": {
                    "enable_thinking": True, "preserve_thinking": False,
                }},
            },
        }, hermes_root=root)
        if descriptor_profile["profile_id"] != "smacx-descriptor-contract":
            raise AssertionError("Control descriptor did not preserve external profile identity")
        descriptor_config = json.loads((
            Path(descriptor_profile["profile_root"]) / "config.yaml"
        ).read_text(encoding="utf-8"))
        if descriptor_config["model"].get("reasoning_echo") is not True:
            raise AssertionError("Qwen current-episode reasoning continuity was not enabled")

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
                "reasoning_ladder_reaches_hermes": True,
                "generation_settings_supported": True,
                "current_episode_reasoning_echo": True,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
