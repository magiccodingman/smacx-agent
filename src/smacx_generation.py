"""Validated provider-neutral generation settings for managed model profiles.

Common OpenAI-compatible controls are first-class. Everything else crosses
the trust boundary as an explicitly named, JSON-typed extra parameter. Model
specific defaults live only in named templates; the generic path never guesses
from a model name.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping


class GenerationSettingsError(ValueError):
    pass


_ALIASES = {
    "preset": "preset", "Preset": "preset",
    "temperature": "temperature", "Temperature": "temperature",
    "top_p": "top_p", "topP": "top_p", "TopP": "top_p",
    "presence_penalty": "presence_penalty", "presencePenalty": "presence_penalty",
    "PresencePenalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty", "frequencyPenalty": "frequency_penalty",
    "FrequencyPenalty": "frequency_penalty",
    "max_output_tokens": "max_output_tokens", "maxOutputTokens": "max_output_tokens",
    "MaxOutputTokens": "max_output_tokens",
    "seed": "seed", "Seed": "seed",
    "reasoning_continuity": "reasoning_continuity",
    "reasoningContinuity": "reasoning_continuity",
    "ReasoningContinuity": "reasoning_continuity",
    "extra_parameters": "extra_parameters", "extraParameters": "extra_parameters",
    "ExtraParameters": "extra_parameters",
    # Read-only compatibility for pre-release profile records. New profiles
    # express these through extra_parameters instead.
    "top_k": "top_k", "topK": "top_k", "TopK": "top_k",
    "min_p": "min_p", "minP": "min_p", "MinP": "min_p",
    "repetition_penalty": "repetition_penalty", "repetitionPenalty": "repetition_penalty",
    "RepetitionPenalty": "repetition_penalty",
    "enable_thinking": "enable_thinking", "enableThinking": "enable_thinking",
    "EnableThinking": "enable_thinking",
    "preserve_thinking": "preserve_thinking", "preserveThinking": "preserve_thinking",
    "PreserveThinking": "preserve_thinking",
}

_PRESETS = {
    "provider-default", "custom", "qwen38-instant", "qwen38-low",
    "qwen38-medium", "qwen38-high", "qwen38-xhigh", "qwen38-thinking", "qwen38-instruct",
}
_EXTRA_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_RESERVED = {
    "model", "messages", "stream", "tools", "tool_choice", "reasoning_effort",
    "temperature", "top_p", "presence_penalty", "frequency_penalty", "max_tokens", "seed",
}
_MAX_EXTRA_JSON = 32_768
REASONING_EFFORTS = {
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra",
}
REASONING_CONTINUITY = {"automatic", "off", "current_episode"}


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise GenerationSettingsError(f"invalid_{name}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GenerationSettingsError(f"invalid_{name}") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise GenerationSettingsError(f"invalid_{name}")
    return result


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise GenerationSettingsError(f"invalid_{name}")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise GenerationSettingsError(f"invalid_{name}") from exc
    if result != value or not minimum <= result <= maximum:
        raise GenerationSettingsError(f"invalid_{name}")
    return result


def _validate_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 8:
        raise GenerationSettingsError("extra_parameter_too_deep")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise GenerationSettingsError("invalid_extra_parameter")
        return value
    if isinstance(value, list):
        return [_validate_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise GenerationSettingsError("invalid_extra_parameter")
            result[key] = _validate_json_value(item, depth=depth + 1)
        return result
    raise GenerationSettingsError("invalid_extra_parameter")


def _extras(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > 64:
        raise GenerationSettingsError("invalid_extra_parameters")
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if not isinstance(raw_key, str) or not _EXTRA_KEY.fullmatch(raw_key):
            raise GenerationSettingsError("invalid_extra_parameter_key")
        key = raw_key.strip()
        if key in _RESERVED or key in result:
            raise GenerationSettingsError("reserved_or_duplicate_extra_parameter")
        result[key] = _validate_json_value(raw_value)
    if len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) > _MAX_EXTRA_JSON:
        raise GenerationSettingsError("extra_parameters_too_large")
    return result


def normalize_generation_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"preset": "provider-default", "reasoning_continuity": "off"}
    if not isinstance(value, Mapping):
        raise GenerationSettingsError("invalid_generation_settings")
    unknown = [key for key in value if key not in _ALIASES]
    if unknown:
        raise GenerationSettingsError("unsupported_generation_setting")
    canonical: dict[str, Any] = {}
    for key, raw in value.items():
        name = _ALIASES[key]
        if raw is not None:
            canonical[name] = raw
    preset = str(canonical.get("preset", "provider-default"))
    if preset not in _PRESETS:
        raise GenerationSettingsError("invalid_generation_preset")
    if preset == "qwen38-thinking":
        preset = "qwen38-low"
    elif preset == "qwen38-instruct":
        preset = "qwen38-instant"
    # Presets are editable starting points, not runtime macros. The values
    # saved with a profile are authoritative for every preset, including
    # provider-default and the built-in Qwen templates.
    legacy_extras = dict(_extras(canonical.get("extra_parameters")))
    for key in ("top_k", "min_p", "repetition_penalty"):
        if key in canonical:
            if key in legacy_extras:
                raise GenerationSettingsError("reserved_or_duplicate_extra_parameter")
            legacy_extras[key] = canonical[key]
    template = dict(legacy_extras.get("chat_template_kwargs") or {})
    if "enable_thinking" in canonical:
        template["enable_thinking"] = canonical["enable_thinking"]
    if "preserve_thinking" in canonical:
        template["preserve_thinking"] = canonical["preserve_thinking"]
    if template:
        legacy_extras["chat_template_kwargs"] = template
    canonical["extra_parameters"] = legacy_extras

    result: dict[str, Any] = {"preset": preset}
    continuity = canonical.get("reasoning_continuity")
    if continuity is None:
        continuity = "automatic"
    if continuity is not None:
        continuity = str(continuity).strip().lower()
        if continuity not in REASONING_CONTINUITY:
            raise GenerationSettingsError("invalid_reasoning_continuity")
        if continuity == "automatic":
            template = canonical.get("extra_parameters", {}).get("chat_template_kwargs", {})
            continuity = (
                "current_episode"
                if isinstance(template, Mapping) and template.get("enable_thinking") is True
                else "off"
            )
        result["reasoning_continuity"] = continuity
    ranges = {
        "temperature": (0.0, 2.0), "top_p": (0.0, 1.0),
        "presence_penalty": (-2.0, 2.0), "frequency_penalty": (-2.0, 2.0),
    }
    for name, (minimum, maximum) in ranges.items():
        if name in canonical:
            result[name] = _number(canonical[name], name, minimum, maximum)
    if "max_output_tokens" in canonical:
        result["max_output_tokens"] = _integer(canonical["max_output_tokens"], "max_output_tokens", 1, 262_144)
    if "seed" in canonical:
        result["seed"] = _integer(canonical["seed"], "seed", -2_147_483_648, 2_147_483_647)
    extras = _extras(canonical.get("extra_parameters"))
    if extras:
        result["extra_parameters"] = extras
    return result


def openai_extra_body(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Translate normalized settings into Hermes' OpenAI ``extra_body``."""
    normalized = normalize_generation_settings(settings)
    body = {
        key: item for key, item in normalized.items()
        if key in {"temperature", "top_p", "presence_penalty", "frequency_penalty", "seed"}
    }
    if "max_output_tokens" in normalized:
        body["max_tokens"] = normalized["max_output_tokens"]
    body.update(normalized.get("extra_parameters", {}))
    return body


def direct_reasoning_parameters(reasoning_effort: str | None) -> dict[str, str]:
    """Translate the shared profile intent for direct chat-completion callers.

    Hermes receives this intent through its own CLI/config adapter. Services
    that call an OpenAI-compatible endpoint directly (provider probes and
    Graphiti) use the provider's top-level ``reasoning_effort`` field. ``none``
    deliberately omits the field rather than inventing provider semantics.
    """
    effort = str(reasoning_effort or "none").strip().lower()
    if effort not in REASONING_EFFORTS:
        raise GenerationSettingsError("invalid_reasoning_effort")
    return {} if effort == "none" else {"reasoning_effort": effort}
