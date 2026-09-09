"""Classify provider-free waits without overriding native interaction guards."""
from typing import Mapping, Any


def foreign_turn_wait(phase: str, kind: str, faction: Any, owner: Any) -> bool:
    if phase != "wait":
        return False
    if kind == "waiting_for_turn":
        return True
    # Deferred end-turn/engine state takes precedence over foreign turn in the
    # native bridge. Only explicit valid faction IDs establish this case.
    return (kind == "waiting_for_engine" and type(faction) is int
            and type(owner) is int and 1 <= faction <= 7 and 1 <= owner <= 7
            and faction != owner)


def snapshot_foreign_turn_wait(snapshot: Mapping[str, Any]) -> bool:
    interaction = snapshot.get("interaction") or {}
    return foreign_turn_wait((snapshot.get("protocol") or {}).get("phase"),
        interaction.get("kind"), (snapshot.get("faction") or {}).get("id"),
        (interaction.get("engine_state") or {}).get("current_faction_id"))


def progress_foreign_turn_wait(progress: Mapping[str, Any]) -> bool:
    return foreign_turn_wait(progress.get("phase"), progress.get("interaction_kind"),
                             progress.get("faction_id"), progress.get("current_faction_id"))
