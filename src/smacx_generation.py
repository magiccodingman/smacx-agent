"""Validated, provider-neutral generation settings for managed model profiles.

The portal stores settings as part of an immutable AI profile version.  This
module is the control-plane trust boundary: only explicitly supported fields
can reach an OpenAI-compatible request, and omitted fields remain genuine
provider defaults.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


class GenerationSettingsError(ValueError):
    pass


_ALIASES = {
    "preset": "preset",
    "Preset": "preset",
    "temperature": "temperature",
    "Temperature": "temperature",
    "top_p": "top_p",
    "topP": "top_p",
    "TopP": "top_p",
    "top_k": "top_k",
    "topK": "top_k",
    "TopK": "top_k",
    "min_p": "min_p",
    "minP": "min_p",
    "MinP": "min_p",
    "presence_penalty": "presence_penalty",
    "presencePenalty": "presence_penalty",
    "PresencePenalty": "presence_penalty",
    "frequency_penalty": "frequency_penalty",
    "frequencyPenalty": "frequency_penalty",
    "FrequencyPenalty": "frequency_penalty",
    "repetition_penalty": "repetition_penalty",
    "repetitionPenalty": "repetition_penalty",
    "RepetitionPenalty": "repetition_penalty",
    "max_output_tokens": "max_output_tokens",
    "maxOutputTokens": "max_output_tokens",
    "MaxOutputTokens": "max_output_tokens",
    "seed": "seed",
    "Seed": "seed",
    "enable_thinking": "enable_thinking",
    "enableThinking": "enable_thinking",
    "EnableThinking": "enable_thinking",
    "preserve_thinking": "preserve_thinking",
    "preserveThinking": "preserve_thinking",
    "PreserveThinking": "preserve_thinking",
}

_PRESETS = {"provider-default", "qwen38-thinking", "qwen38-instruct", "custom"}


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


def normalize_generation_settings(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"preset": "provider-default"}
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
    if preset == "provider-default":
        canonical = {"preset": preset}
    elif preset == "qwen38-thinking":
        canonical.update({
            "temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0,
            "presence_penalty": 0.0, "repetition_penalty": 1.0,
            "enable_thinking": True, "preserve_thinking": True,
        })
    elif preset == "qwen38-instruct":
        canonical.update({
            "temperature": 0.7, "top_p": 0.80, "top_k": 20, "min_p": 0.0,
            "presence_penalty": 1.5, "repetition_penalty": 1.0,
            "enable_thinking": False,
        })
        canonical.pop("preserve_thinking", None)
    result: dict[str, Any] = {"preset": preset}
    ranges = {
        "temperature": (0.0, 2.0),
        "top_p": (0.0, 1.0),
        "min_p": (0.0, 1.0),
        "presence_penalty": (-2.0, 2.0),
        "frequency_penalty": (-2.0, 2.0),
        "repetition_penalty": (0.01, 2.0),
    }
    for name, (minimum, maximum) in ranges.items():
        if name in canonical:
            result[name] = _number(canonical[name], name, minimum, maximum)
    if "top_k" in canonical:
        result["top_k"] = _integer(canonical["top_k"], "top_k", 0, 100_000)
    if "max_output_tokens" in canonical:
        result["max_output_tokens"] = _integer(
            canonical["max_output_tokens"], "max_output_tokens", 1, 262_144,
        )
    if "seed" in canonical:
        result["seed"] = _integer(
            canonical["seed"], "seed", -2_147_483_648, 2_147_483_647,
        )
    for name in ("enable_thinking", "preserve_thinking"):
        if name in canonical:
            if not isinstance(canonical[name], bool):
                raise GenerationSettingsError(f"invalid_{name}")
            result[name] = canonical[name]
    return result


def openai_extra_body(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Translate normalized settings into the final OpenAI-compatible body.

    Hermes places this mapping in the SDK's ``extra_body`` argument, whose
    fields are merged into the outgoing JSON request. This supports standard
    OpenAI fields and optional local-server extensions without patching
    upstream Hermes.
    """
    normalized = normalize_generation_settings(settings)
    if normalized["preset"] == "provider-default":
        return {}
    body = {
        key: value for key, value in normalized.items()
        if key in {
            "temperature", "top_p", "top_k", "min_p", "presence_penalty",
            "frequency_penalty", "repetition_penalty", "seed",
        }
    }
    if "max_output_tokens" in normalized:
        body["max_tokens"] = normalized["max_output_tokens"]
    template: dict[str, bool] = {}
    if "enable_thinking" in normalized:
        template["enable_thinking"] = normalized["enable_thinking"]
    if "preserve_thinking" in normalized:
        template["preserve_thinking"] = normalized["preserve_thinking"]
    if template:
        body["chat_template_kwargs"] = template
    return body
