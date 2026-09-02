#!/usr/bin/env python3
"""Apply the managed-game streaming repetition fuse to pinned Hermes."""

from pathlib import Path


path = Path("/opt/hermes/agent/chat_completion_helpers.py")
text = path.read_text(encoding="utf-8")

needle = "from agent.reasoning_summaries import separate_glued_reasoning_blocks\n"
replacement = needle + "from agent.repetition_guard import is_repetition_dominated\n"
if text.count(needle) != 1:
    raise SystemExit("unexpected Hermes reasoning import layout")
text = text.replace(needle, replacement)

needle = """        reasoning_parts: list = []
        usage_obj = None
"""
replacement = """        reasoning_parts: list = []
        # Managed autonomous play must not wait for the full output budget
        # before stopping a degenerate reasoning/content loop. The ordinary
        # Hermes guard still owns the final fail-open user response.
        repetition_probe_at = 4096
        repetition_fused = False
        usage_obj = None
"""
if text.count(needle) != 1:
    raise SystemExit("unexpected Hermes stream accumulator layout")
text = text.replace(needle, replacement)

needle = """                reasoning_parts.append(reasoning_text)
                _fire_first_delta()
                agent._fire_reasoning_delta(reasoning_text)

            # Accumulate text content"""
replacement = """                reasoning_parts.append(reasoning_text)
                _fire_first_delta()
                agent._fire_reasoning_delta(reasoning_text)

                combined_size = sum(map(len, reasoning_parts)) + sum(map(len, content_parts))
                if combined_size >= repetition_probe_at:
                    probe = \"\".join(reasoning_parts + content_parts)[-24000:]
                    repetition_probe_at = combined_size + 2048
                    if is_repetition_dominated(probe):
                        logger.warning(
                            \"SMACX streaming repetition fuse stopped output after %d characters\",
                            combined_size,
                        )
                        # Present the bounded dominated sample as truncated
                        # visible content. Hermes's normal truncation guard
                        # then discards it and returns a typed repetition error.
                        content_parts[:] = [probe]
                        reasoning_parts.clear()
                        finish_reason = FINISH_REASON_LENGTH
                        repetition_fused = True
                        try:
                            stream.close()
                        except Exception:
                            pass
                        break

            # Accumulate text content"""
if text.count(needle) != 1:
    raise SystemExit("unexpected Hermes reasoning stream layout")
text = text.replace(needle, replacement)

needle = """            # Accumulate tool call deltas — notify display on first name
            delta_tool_calls = getattr(delta, \"tool_calls\", None)
"""
replacement = """            if delta_content:
                combined_size = sum(map(len, reasoning_parts)) + sum(map(len, content_parts))
                if combined_size >= repetition_probe_at:
                    probe = \"\".join(reasoning_parts + content_parts)[-24000:]
                    repetition_probe_at = combined_size + 2048
                    if is_repetition_dominated(probe):
                        logger.warning(
                            \"SMACX streaming repetition fuse stopped output after %d characters\",
                            combined_size,
                        )
                        content_parts[:] = [probe]
                        reasoning_parts.clear()
                        finish_reason = FINISH_REASON_LENGTH
                        repetition_fused = True
                        try:
                            stream.close()
                        except Exception:
                            pass
                        break

            # Accumulate tool call deltas — notify display on first name
            delta_tool_calls = getattr(delta, \"tool_calls\", None)
"""
if text.count(needle) != 1:
    raise SystemExit("unexpected Hermes content stream layout")
text = text.replace(needle, replacement)

path.write_text(text, encoding="utf-8")
