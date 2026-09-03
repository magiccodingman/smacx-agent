"""Shared provider-context pressure policy for Hermes and SMACX wire GC."""

from __future__ import annotations


# Hermes generic compression is intentionally secondary.  The same constant
# writes its profile config and drives request-wire semantic GC.
HERMES_COMPRESSION_THRESHOLD_RATIO = 0.50
SEMANTIC_GC_FRACTION_OF_HERMES_TRIGGER = 0.80
HERMES_COMPRESSION_TARGET_RATIO = 0.20


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
