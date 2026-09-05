"""Shared provider-context pressure policy for Hermes and SMACX wire GC."""

from __future__ import annotations


# Hermes generic compression is intentionally secondary.  The same constant
# writes its profile config and drives request-wire semantic GC.
HERMES_COMPRESSION_THRESHOLD_RATIO = 0.50
SEMANTIC_GC_FRACTION_OF_HERMES_TRIGGER = 0.80
HERMES_COMPRESSION_TARGET_RATIO = 0.20


def managed_system_tool_reserve(system_prompt: str) -> int:
    """Conservative capacity planning, not an exact provider token measurement.

    The 8192 tool/framing allowance exceeds the managed schema budget. Keep the
    historic minimum, but let substantive doctrine/personality increase it.
    """
    return max(12000, (len(system_prompt.encode("utf-8")) + 2) // 3 + 8192)


def validate_managed_context(system_prompt: str, context_length: int) -> int:
    reserve = managed_system_tool_reserve(system_prompt)
    reasoning = 8192 if context_length < 131072 else 32768
    if context_length - reserve - reasoning - 8192 < 8192:
        raise ValueError("managed_prompt_context_headroom_insufficient")
    return reserve


def hermes_compression_trigger_tokens(context_length: int) -> int:
    return int(int(context_length) * HERMES_COMPRESSION_THRESHOLD_RATIO)


def semantic_gc_ceiling_tokens(
    context_length: int, *, output_reserve: int, reasoning_reserve: int,
    system_tool_reserve: int,
) -> int:
    generic_trigger = hermes_compression_trigger_tokens(context_length)
    return max(8192, min(
        int(generic_trigger * SEMANTIC_GC_FRACTION_OF_HERMES_TRIGGER),
        int(context_length) - int(output_reserve) - int(reasoning_reserve)
        - int(system_tool_reserve),
    ))
