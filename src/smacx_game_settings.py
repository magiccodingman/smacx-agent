"""Canonical typed setup contract shared by standalone and managed launches."""

from __future__ import annotations

from typing import Any, Mapping

from smacx_store import InvalidRecord


WORLD_FIELDS = ("ocean_coverage", "erosive_forces", "native_life", "cloud_cover")
RULE_FIELDS = (
    "victory_transcendence", "victory_conquest", "victory_diplomatic",
    "victory_economic", "victory_cooperative", "do_or_die", "look_first",
    "tech_stagnation", "spoils_of_war", "blind_research", "intense_rivalry",
    "unity_survey", "unity_scattering", "random_events", "time_warp", "ironman",
    "random_leader_personalities", "random_leader_agendas",
)
LAN_RULE_FIELDS = tuple(
    field for field in RULE_FIELDS
    if field not in {"random_leader_personalities", "random_leader_agendas"}
)

ENV_NAMES = {
    "map_generation": "SMACX_AGENT_MAP_GENERATION",
    "world_size": "SMACX_AGENT_WORLD_SIZE",
    "custom_width": "SMACX_AGENT_CUSTOM_WIDTH",
    "custom_height": "SMACX_AGENT_CUSTOM_HEIGHT",
    "ocean_coverage": "SMACX_AGENT_OCEAN_COVERAGE",
    "erosive_forces": "SMACX_AGENT_EROSIVE_FORCES",
    "native_life": "SMACX_AGENT_NATIVE_LIFE",
    "cloud_cover": "SMACX_AGENT_CLOUD_COVER",
    **{name: f"SMACX_AGENT_RULE_{name.upper()}" for name in RULE_FIELDS},
}


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise InvalidRecord(f"invalid_{field}")
    return value


def normalize_game_settings(value: Mapping[str, Any] | None, *,
                            default_blind_research: bool = True) -> dict[str, Any]:
    supplied = dict(value or {})
    unknown = set(supplied) - {
        "map_generation", "world_size", "custom_width", "custom_height",
        *WORLD_FIELDS, *RULE_FIELDS,
    }
    if unknown:
        raise InvalidRecord("unknown_game_setting:" + sorted(unknown)[0])
    map_generation = supplied.get("map_generation", "random")
    if map_generation not in ("random", "custom"):
        raise InvalidRecord("invalid_map_generation")
    world_size = _integer(supplied.get("world_size", 0), "world_size", 0, 99)
    if world_size not in (*range(5), 99):
        raise InvalidRecord("invalid_world_size")
    result: dict[str, Any] = {
        "map_generation": map_generation,
        "world_size": world_size,
    }
    custom_width = supplied.get("custom_width")
    custom_height = supplied.get("custom_height")
    if (custom_width is None) != (custom_height is None):
        raise InvalidRecord("custom_dimensions_must_be_paired")
    if custom_width is not None:
        if map_generation != "custom":
            raise InvalidRecord("custom_dimensions_require_custom_map")
        result["world_size"] = 99
        result["custom_width"] = _integer(custom_width, "custom_width", 16, 512)
        result["custom_height"] = _integer(custom_height, "custom_height", 16, 512)
    elif world_size == 99:
        raise InvalidRecord("custom_dimensions_required")
    for field in WORLD_FIELDS:
        candidate = supplied.get(field)
        if candidate is not None:
            if map_generation != "custom":
                raise InvalidRecord(f"{field}_requires_custom_map")
            result[field] = _integer(candidate, field, 0, 2)
        elif map_generation == "custom":
            result[field] = 1
    for field in RULE_FIELDS:
        candidate = supplied.get(field)
        if candidate is None:
            if field == "blind_research":
                result[field] = bool(default_blind_research)
            continue
        if not isinstance(candidate, bool):
            raise InvalidRecord(f"invalid_{field}")
        result[field] = candidate
    return result


def game_settings_environment(settings: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field, value in settings.items():
        environment_name = ENV_NAMES.get(field)
        if environment_name is None:
            continue
        if isinstance(value, bool):
            result[environment_name] = "1" if value else "0"
        else:
            result[environment_name] = str(value)
    return result


def normalize_lan_game_settings(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the presentation-free subset of the native LAN setup packet."""
    supplied = dict(value)
    allowed = {
        "difficulty", "time_control", "world_size", *WORLD_FIELDS, *LAN_RULE_FIELDS,
    }
    unknown = set(supplied) - allowed
    if unknown:
        raise InvalidRecord("unknown_lan_game_setting:" + sorted(unknown)[0])
    result: dict[str, Any] = {
        "difficulty": _integer(supplied.get("difficulty"), "lan_difficulty", 0, 5),
        # Native ids: None, Tight, Standard, Moderate, Loose. The sixth
        # "Custom" clock opens another editor and is deliberately excluded.
        "time_control": _integer(supplied.get("time_control"), "lan_time_control", 0, 4),
        "world_size": _integer(supplied.get("world_size"), "lan_world_size", 0, 4),
    }
    for field in WORLD_FIELDS:
        result[field] = _integer(supplied.get(field), f"lan_{field}", 0, 2)
    for field in LAN_RULE_FIELDS:
        if field not in supplied:
            continue
        if not isinstance(supplied[field], bool):
            raise InvalidRecord(f"invalid_lan_{field}")
        result[field] = supplied[field]
    return result
