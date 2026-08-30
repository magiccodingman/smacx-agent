#!/usr/bin/env python3
"""Source-level guard for the native human-only quit interception contract."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = (ROOT / "bridge/src/agent_bridge.cpp").read_text(encoding="utf-8")
MANAGER = (ROOT / "src/smacx_worker_manager.py").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "worker/entrypoint.py").read_text(encoding="utf-8")

assert 'op == "human_ui_control"' in BRIDGE
assert 'action != "cancel_native_quit"' in BRIDGE
assert 'strcmp(semantic_popup_label(), "REALLYQUIT")' in BRIDGE
assert 'submit_popup_choice(popup, 0)' in BRIDGE
assert 'state.get("popup_label") == "REALLYQUIT"' in MANAGER
assert '"human_ui_control", action="cancel_native_quit"' in MANAGER
assert 'def stream_bitrate_kbps(width: int, height: int)' in MANAGER
assert "SMACX_STREAM_VIDEO_BITRATE" in ENTRYPOINT

print("Human UI safety contract passed")
