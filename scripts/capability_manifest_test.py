#!/usr/bin/env python3
"""Contract regression for machine-readable platform availability."""

from __future__ import annotations

import inspect
import json

from smacx_capabilities import CAPABILITY_SCHEMA_VERSION, capability_manifest
import smacx_mcp
from smacx_worker_manager import LAN_PROFILES


def main() -> int:
    manifest = capability_manifest()
    if manifest.get("schema_version") != CAPABILITY_SCHEMA_VERSION:
        raise AssertionError("capability schema version drift")
    profiles = manifest.get("lan_profiles", {})
    if set(profiles) != LAN_PROFILES:
        raise AssertionError(f"capability/manager profile drift: {set(profiles)} != {LAN_PROFILES}")
    if any(item.get("status") != "available" for item in profiles.values()):
        raise AssertionError("a managed LAN profile is not available")
    launches = manifest.get("launch_modes", {})
    if launches.get("solo_scenario", {}).get("status") != "available" \
            or launches.get("lan_scenario", {}).get("status") != "available_private_network":
        raise AssertionError("scenario launch availability is not explicit")
    if launches.get("solo_custom_rules", {}).get("status") != "available" \
            or launches.get("lan_custom_rules", {}).get("status") != "available_private_network":
        raise AssertionError("typed custom setup availability is not explicit")
    deployment = manifest.get("deployment", {})
    gaps = manifest.get("known_fail_closed_gaps", {})
    for retired_gap in (
        "human_diplomacy_map_clause",
        "human_diplomacy_joint_attack_clause",
    ):
        if retired_gap in gaps:
            raise AssertionError(f"retired human diplomacy gap remains: {retired_gap}")
    if deployment.get("physical_two_machine_lan", {}).get("status") != "available_private_network":
        raise AssertionError("physical private-LAN availability is not explicit")
    if deployment.get("windows_11_wsl2", {}).get("status") != "experimental":
        raise AssertionError("WSL2 support state is not explicit")
    if "evidence" in manifest:
        raise AssertionError("capability manifest still exposes a development evidence section")
    if "section" not in inspect.signature(smacx_mcp.smac_capabilities).parameters:
        raise AssertionError("MCP capability query is missing its compact section filter")
    if smacx_mcp.smac_capabilities("lan_profiles").get("value") != profiles:
        raise AssertionError("MCP returned a different capability manifest")
    rejected = capability_manifest("invented")
    if rejected.get("error", {}).get("code") != "unknown_capability_section":
        raise AssertionError("unknown capability section was not rejected")
    print(json.dumps({
        "event": "pass",
        "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
        "lan_profiles": sorted(profiles),
        "scenario_launch": "available",
        "availability_contract": True,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
