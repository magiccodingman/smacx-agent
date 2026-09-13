#!/usr/bin/env python3
"""Guard modal postconditions and the spectator redraw handoff."""
from pathlib import Path


source = (Path(__file__).resolve().parents[1] / "bridge/src/agent_bridge.cpp").read_text()

transition = source[source.index("bool popup_transition_is_pending() {"):
                    source.index("UINT_PTR council_vote_timer_id")]
assert "agent_popup_object_is_active(pending_popup_object)" in transition
assert transition.index("pending_popup_object = NULL;") < transition.index(
    "redraw_after_popup_transition();")

redraw = source[source.index("void redraw_after_popup_transition() {"):
                source.index("bool popup_transition_is_pending() {")]
assert "GraphicWin_redraw(WorldWin);" in redraw

ack = source[source.index('if (command == "acknowledge_popup") {'):
             source.index('if (command == "respond_to_contact") {')]
assert ack.index("BasePop_on_button_clicked(active, 0);") < ack.index(
    "popup_transition_is_pending()")
assert "dismissal_verified" in ack
assert "waiting_for_engine" in ack
assert "Do not infer dismissal from command acceptance alone." in ack

print("PASS: popup dismissal is observed before redraw and receipt verification")
