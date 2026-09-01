#!/usr/bin/env python3
"""Contained contract for provider-neutral generation settings and Qwen templates."""

from __future__ import annotations

import json

from smacx_generation import GenerationSettingsError, normalize_generation_settings, openai_extra_body


def rejected(value: dict, expected: str) -> None:
    try:
        normalize_generation_settings(value)
    except GenerationSettingsError as exc:
        if str(exc) != expected:
            raise
    else:
        raise AssertionError(f"generation settings unexpectedly accepted: {value}")


def main() -> int:
    expected = {
        "qwen38-instant": (False, 0.7, 0.80, 1.5),
        "qwen38-low": (True, 1.0, 0.95, 0.0),
        "qwen38-medium": (True, 1.0, 0.95, 0.0),
        "qwen38-high": (True, 1.0, 0.95, 0.0),
        "qwen38-xhigh": (True, 1.0, 0.95, 0.0),
    }
    for preset, (thinking, temperature, top_p, presence) in expected.items():
        body = openai_extra_body({
            "preset": preset,
            "temperature": temperature,
            "top_p": top_p,
            "presence_penalty": presence,
            "top_k": 20,
            "min_p": 0.0,
            "repetition_penalty": 1.0,
            "extra_parameters": {"chat_template_kwargs": {
                "enable_thinking": thinking, "preserve_thinking": False,
            }},
        })
        template = body.get("chat_template_kwargs", {})
        if body.get("temperature") != temperature or body.get("top_p") != top_p \
                or body.get("presence_penalty") != presence:
            raise AssertionError(f"{preset} sampling drifted: {body}")
        if template != {"enable_thinking": thinking, "preserve_thinking": False}:
            raise AssertionError(f"{preset} thinking history contract drifted: {template}")
        if body.get("top_k") != 20 or body.get("min_p") != 0.0 \
                or body.get("repetition_penalty") != 1.0:
            raise AssertionError(f"{preset} extension defaults drifted: {body}")

    custom = openai_extra_body({
        "preset": "custom", "temperature": 0.4,
        "extra_parameters": {
            "top_k": 14,
            "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": False},
            "provider_extension": ["one", {"nested": True}],
        },
    })
    if custom["top_k"] != 14 or custom["provider_extension"][1]["nested"] is not True:
        raise AssertionError("JSON-typed provider extensions were not preserved")
    if openai_extra_body({"preset": "provider-default"}) != {}:
        raise AssertionError("provider defaults injected model-specific parameters")
    editable = openai_extra_body({
        "preset": "qwen38-low", "temperature": 0.42,
        "extra_parameters": {"chat_template_kwargs": {
            "enable_thinking": True, "preserve_thinking": False,
        }},
    })
    if editable["temperature"] != 0.42:
        raise AssertionError("template metadata overwrote an explicit user value")
    provider_override = openai_extra_body({"preset": "provider-default", "temperature": 0.3})
    if provider_override != {"temperature": 0.3}:
        raise AssertionError("provider-default discarded an explicit override")
    rejected({"preset": "custom", "extra_parameters": {"model": "override"}},
             "reserved_or_duplicate_extra_parameter")
    rejected({"preset": "custom", "temperature": float("nan")}, "invalid_temperature")
    print(json.dumps({"event": "pass", "payload": {
        "qwen_templates_exact": True,
        "historical_thinking_disabled": True,
        "provider_defaults_clean": True,
        "custom_json_extensions": True,
        "templates_are_editable": True,
        "provider_default_overrides_reach_provider": True,
        "reserved_fields_rejected": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
