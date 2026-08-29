"""Machine-readable product capability and certification ledger.

Runtime observations say what the current game permits; this reviewed ledger says
which platform paths exist and how they were tested. Keeping them separate prevents
an agent from treating an aspirational roadmap item as a legal action.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CAPABILITY_SCHEMA_VERSION = 1

_CAPABILITIES: dict[str, Any] = {
    "policy": {
        "agent_ui_input": "forbidden",
        "hidden_state": "forbidden",
        "unknown_mandatory_interaction": "report_gap_and_stop",
        "game_assets": "bring_your_own_legal_copy",
        "personality_cards": "reserved_opaque_reference_only",
    },
    "launch_modes": {
        "solo_random": {"status": "native_live_tested", "operator": "control_center_or_standalone_mcp"},
        "lan_random_ai_host": {"status": "native_live_tested_local", "operator": "control_center"},
        "lan_random_human_host": {"status": "native_live_tested_local", "operator": "control_center"},
        "resume_solo_checkpoint": {"status": "native_live_tested"},
        "resume_lan_checkpoint": {"status": "native_live_tested_local"},
        "solo_scenario": {
            "status": "native_live_tested",
            "operator": "control_center_or_mcp",
        },
        "lan_scenario": {
            "status": "native_live_tested_local",
            "operator": "control_center_or_mcp",
        },
        "solo_custom_rules": {"status": "native_live_tested"},
        "lan_custom_rules": {"status": "native_live_tested_local"},
    },
    "lan_profiles": {
        "tiny_citizen": {"difficulty": "citizen", "map_size": "tiny", "status": "native_live_tested"},
        "small_easy": {"difficulty": "citizen", "map_size": "small", "status": "native_live_tested"},
        "standard_librarian": {"difficulty": "librarian", "map_size": "standard", "status": "native_live_tested"},
        "large_thinker": {"difficulty": "thinker", "map_size": "large", "status": "native_live_tested"},
        "huge_transcend": {"difficulty": "transcend", "map_size": "huge", "status": "native_live_tested"},
    },
    "semantic_surface": {
        "core_turn_loop": {"status": "native_live_tested", "soak_turns": 100},
        "chat_and_faction_attribution": {"status": "native_live_tested_lan"},
        "production_research_social_engineering": {"status": "native_live_tested"},
        "unit_base_and_terraforming": {"status": "native_live_tested"},
        "diplomacy_and_council": {"status": "broad_fail_closed_coverage"},
        "save_load_endgame": {"status": "native_live_tested"},
        "persistent_scoped_memory": {"status": "contained_and_live_tested"},
        "rules_reference_bm25": {"status": "contained_tested"},
        "graphiti_projection": {
            "status": "optional_backend_live_tested",
            "default": "disabled_until_compatible_embedding_endpoint_is_configured",
        },
    },
    "known_fail_closed_gaps": {
        "accepted_ai_concessions": "Consequential LAN settlement effects remain rejection-only where unverified.",
        "lan_destructive_or_persistent_unit_mutations": (
            "Carrier recovery, bombing runs, terrain destruction, single-unit upgrades, and selected persistent policies "
            "remain unavailable in LAN until two-client packet/effect tests pass."
        ),
        "lan_base_mutations": (
            "Governor, queue, production, citizen, allocation, research, and Social Engineering changes are synchronized; "
            "hurry, rename, recycling, nerve stapling, and obliteration remain unavailable until exact two-client effect tests pass."
        ),
        "unrecognized_modal": "The bridge returns capability_gap; no visual fallback exists.",
    },
    "deployment": {
        "linux_single_host": {"status": "certified_local"},
        "linux_two_native_process_lan": {"status": "certified_local"},
        "tailscale_routed_transport": {"status": "route_live_tested_local"},
        "physical_two_machine_lan": {
            "status": "external_certification_required",
            "blocker": "A second physical machine and operator network are not available in the development environment.",
        },
        "windows_11_wsl2": {
            "status": "implementation_complete_external_certification_required",
            "blocker": "A Windows 11/WSL2 host is not available in the development environment.",
        },
    },
    "evidence": {
        "coverage": "docs/coverage.md",
        "testing": "docs/testing.md",
        "windows_wsl2": "docs/windows-wsl2.md",
        "virtual_lan": "docs/virtual-lan.md",
        "certification_record": "docs/certification-record.example.md",
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
