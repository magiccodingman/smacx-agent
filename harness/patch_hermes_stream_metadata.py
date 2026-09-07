"""Keep display buffering from skipping terminal metadata or tool deltas."""
from pathlib import Path

path = Path("/opt/hermes/agent/chat_completion_helpers.py")
source = path.read_text()
metadata = '''            chunk_finish_reason = getattr(chunk.choices[0], "finish_reason", None)
            if chunk_finish_reason:
                finish_reason = chunk_finish_reason

            # Usage in the final chunk
            if hasattr(chunk, "usage") and chunk.usage:
                usage_obj = chunk.usage
'''
anchor = '            delta = chunk.choices[0].delta\n'
if source.count(metadata) != 1 or source.count(anchor) != 1:
    raise SystemExit("unexpected Hermes terminal stream metadata layout")
source = source.replace(metadata, "")
source = source.replace(anchor,
    "            # Text buffering can continue the loop; completion and usage\n"
    "            # are transport metadata and must not be skipped with text.\n"
    + metadata + "\n" + anchor)
display = '''                        if _provider_stream_text_may_be_sse(pending_text):
                            continue
                        _flush_pending_stream_text()
                        continue
                    _fire_first_delta()
                    agent._fire_stream_delta(delta_content)
                    deltas_were_sent["yes"] = True
'''
replacement = '''                        if not _provider_stream_text_may_be_sse(pending_text):
                            _flush_pending_stream_text()
                    else:
                        _fire_first_delta()
                        agent._fire_stream_delta(delta_content)
                        deltas_were_sent["yes"] = True
'''
if source.count(display) != 1:
    raise SystemExit("unexpected Hermes SSE-like display buffering layout")
source = source.replace(display, replacement)
path.write_text(source)
