#!/usr/bin/env python3
"""Contract regression for honest, machine-readable platform coverage."""

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
    if any(item.get("status") != "native_live_tested" for item in profiles.values()):
        raise AssertionError("a LAN profile lacks native matrix evidence")
    launches = manifest.get("launch_modes", {})
    if launches.get("solo_scenario", {}).get("status") != "native_live_tested" \
            or launches.get("lan_scenario", {}).get("status") != "native_live_tested_local":
        raise AssertionError("scenario launch evidence is not explicit")
    if launches.get("solo_custom_rules", {}).get("status") != "native_live_tested" \
            or launches.get("lan_custom_rules", {}).get("status") != "native_live_tested_local":
        raise AssertionError("typed custom setup evidence is not explicit")
    deployment = manifest.get("deployment", {})
    gaps = manifest.get("known_fail_closed_gaps", {})
    for retired_gap in (
        "human_diplomacy_map_clause",
        "human_diplomacy_joint_attack_clause",
    ):
        if retired_gap in gaps:
            raise AssertionError(f"retired human diplomacy gap remains: {retired_gap}")
    for key in ("physical_two_machine_lan", "windows_11_wsl2"):
        if "external_certification_required" not in deployment.get(key, {}).get("status", ""):
            raise AssertionError(f"{key} falsely claims local certification")
    if "section" not in inspect.signature(smacx_mcp.smac_capabilities).parameters:
        raise AssertionError("MCP capability query is missing its compact section filter")
    if smacx_mcp.smac_capabilities("lan_profiles").get("value") != profiles:
        raise AssertionError("MCP returned a different capability ledger")
    rejected = capability_manifest("invented")
    if rejected.get("error", {}).get("code") != "unknown_capability_section":
        raise AssertionError("unknown capability section was not rejected")
    print(json.dumps({
        "event": "pass",
        "capability_schema_version": CAPABILITY_SCHEMA_VERSION,
        "lan_profiles": sorted(profiles),
        "scenario_launch": "native_live_tested",
        "external_certification_claims": False,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
