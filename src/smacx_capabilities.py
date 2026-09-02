"""Machine-readable product availability and safety boundary.

Runtime observations say what the current game permits; this manifest says which
platform paths exist. Keeping them separate prevents an agent from treating an
unavailable operation as a legal action.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CAPABILITY_SCHEMA_VERSION = 2

_CAPABILITIES: dict[str, Any] = {
    "policy": {
        "agent_ui_input": "forbidden",
        "hidden_state": "forbidden",
        "unknown_mandatory_interaction": "report_gap_and_stop",
        "game_assets": "bring_your_own_legal_copy",
        "personality_cards": "reserved_opaque_reference_only",
    },
    "launch_modes": {
        "solo_random": {"status": "available", "operator": "control_center_or_standalone_mcp"},
        "lan_random_ai_host": {"status": "available_private_network", "operator": "control_center"},
        "lan_random_human_host": {"status": "available_private_network", "operator": "control_center"},
        "resume_solo_checkpoint": {"status": "available"},
        "resume_lan_checkpoint": {"status": "available_private_network"},
        "solo_scenario": {
            "status": "available",
            "operator": "control_center_or_mcp",
        },
        "lan_scenario": {
            "status": "available_private_network",
            "operator": "control_center_or_mcp",
        },
        "solo_custom_rules": {"status": "available"},
        "lan_custom_rules": {"status": "available_private_network"},
    },
    "lan_profiles": {
        "tiny_citizen": {"difficulty": "citizen", "map_size": "tiny", "status": "available"},
        "small_easy": {"difficulty": "citizen", "map_size": "small", "status": "available"},
        "standard_librarian": {"difficulty": "librarian", "map_size": "standard", "status": "available"},
        "large_thinker": {"difficulty": "thinker", "map_size": "large", "status": "available"},
        "huge_transcend": {"difficulty": "transcend", "map_size": "huge", "status": "available"},
    },
    "semantic_surface": {
        "core_turn_loop": {"status": "available"},
        "chat_and_faction_attribution": {"status": "available_private_network"},
        "production_research_social_engineering": {"status": "available"},
        "unit_base_and_terraforming": {"status": "available"},
        "diplomacy_and_council": {"status": "available_fail_closed"},
        "save_load_endgame": {"status": "available"},
        "persistent_scoped_memory": {"status": "available"},
        "rules_reference_bm25": {"status": "available"},
        "graphiti_projection": {
            "status": "optional",
            "default": "disabled_until_compatible_embedding_endpoint_is_configured",
        },
    },
    "known_fail_closed_gaps": {
        "accepted_ai_concessions": {
            "status": "unavailable",
            "behavior": "Consequential LAN settlement effects are rejection-only.",
        },
        "lan_destructive_or_persistent_unit_mutations": {
            "status": "unavailable",
            "behavior": (
                "Carrier recovery, bombing runs, terrain destruction, single-unit upgrades, and selected persistent "
                "policies are unavailable in LAN."
            ),
        },
        "lan_base_mutations": {
            "status": "partially_available",
            "behavior": (
                "Governor, queue, production, citizen, allocation, research, and Social Engineering changes are "
                "available; hurry, rename, recycling, nerve stapling, and obliteration are unavailable in LAN."
            ),
        },
        "unrecognized_modal": {
            "status": "unavailable",
            "behavior": "The bridge returns capability_gap; no visual fallback exists.",
        },
    },
    "deployment": {
        "linux_single_host": {"status": "available"},
        "linux_two_native_process_lan": {"status": "available_private_network"},
        "tailscale_routed_transport": {"status": "available_private_network"},
        "physical_two_machine_lan": {"status": "available_private_network"},
        "windows_11_wsl2": {
            "status": "experimental",
            "notes": "Use WSL2 with a Linux Docker engine and run the packaged preflight on the target host.",
        },
    },
}


def capability_manifest(section: str | None = None) -> dict[str, Any]:
    """Return a copy of all capabilities or one exact top-level section."""
    if section is None or section == "all":
        return {"ok": True, "schema_version": CAPABILITY_SCHEMA_VERSION, **deepcopy(_CAPABILITIES)}
    if section not in _CAPABILITIES:
        return {
            "ok": False,
            "error": {"code": "unknown_capability_section", "message": "Use one of available_sections."},
            "available_sections": sorted(_CAPABILITIES),
        }
    return {
        "ok": True,
        "schema_version": CAPABILITY_SCHEMA_VERSION,
        "section": section,
        "value": deepcopy(_CAPABILITIES[section]),
    }
