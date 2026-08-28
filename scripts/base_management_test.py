#!/usr/bin/env python3
"""Contained regression for guarded semantic base management."""

from __future__ import annotations

import json
import sys
import time

from semantic_playthrough import handle_interaction
from smacx_controller import bridge_request, new_game


def emit(event: str, payload: object) -> None:
    print(json.dumps({"event": event, "payload": payload}, separators=(",", ":")), flush=True)


def command(choices: dict, command_name: str, **arguments: object) -> dict:
    return bridge_request(
        "semantic_command",
        command=command_name,
        match_id=choices["match_id"],
        session_id=choices["session_id"],
        expected_revision=choices["revision"],
        **arguments,
    )


def base_choices(base_id: int) -> dict:
    return bridge_request("semantic_choices", kind="base_management", base_id=base_id)


def main() -> int:
    started = new_game(wait_seconds=60, difficulty=0, world_size=0, faction_id=1)
    emit("new_game", started)
    if not started.get("ok"):
        return 2
    deadline = time.monotonic() + 70
    while time.monotonic() < deadline:
        snapshot = bridge_request("semantic_snapshot").get("snapshot", {})
        if not snapshot:
            time.sleep(0.1)
            continue
        if snapshot["interaction"]["kind"] != "turn":
            handled, outcome = handle_interaction(snapshot)
            if not handled:
                emit("failure", {"stage": "interaction", "outcome": outcome})
                return 3
            continue
        base = bridge_request("list_bases", limit=1)["items"][0]
        base_id = int(base["id"])
        choices = base_choices(base_id)
        commands = {item.get("command") for item in choices.get("choices", [])}
        expected = {"rename_base", "set_base_governor", "queue_production",
                    "remove_queued_production", "clear_production_queue",
                    "set_governor_permission"}
        if not expected.issubset(commands):
            emit("failure", {"stage": "choices", "choices": choices})
            return 4

        renamed = command(choices, "rename_base", base_id=base_id, name="Agentic Dawn")
        choices = base_choices(base_id)
        governed = command(
            choices,
            "set_base_governor",
            base_id=base_id,
            active=1,
            manage_citizens=0,
            manage_production=1,
        )
        choices = base_choices(base_id)
        permission = next(
            (item for item in choices.get("choices", [])
             if item.get("command") == "set_governor_permission"
             and item.get("governor_permission") == "secret_projects"),
            None,
        )
        if permission is None:
            emit("failure", {"stage": "governor_permission", "choices": choices})
            return 5
        permission_changed = command(
            choices, "set_governor_permission", base_id=base_id,
            governor_permission="secret_projects", active=int(permission["active"]),
        )
        stale_permission = command(
            choices, "set_governor_permission", base_id=base_id,
            governor_permission="secret_projects", active=int(permission["active"]),
        )
        after_permission = bridge_request("list_bases", limit=1)["items"][0]
        choices = base_choices(base_id)
        governor_disabled = command(
            choices, "set_base_governor", base_id=base_id,
            active=0, manage_citizens=0, manage_production=0,
        )

        production = bridge_request("semantic_choices", kind="production", base_id=base_id)
        build = next((item for item in production.get("choices", [])
                      if item.get("command") == "set_production"), None)
        if build is None:
            emit("failure", {"stage": "production", "production": production})
            return 5
        choices = base_choices(base_id)
        queued = command(
            choices,
            "queue_production",
            base_id=base_id,
            item_id=int(build["item_id"]),
        )
        after_queue = bridge_request("list_bases", limit=1)["items"][0]
        choices = base_choices(base_id)
        removed = command(
            choices,
            "remove_queued_production",
            base_id=base_id,
            queue_position=1,
        )
        after_remove = bridge_request("list_bases", limit=1)["items"][0]

        emit("results", {
            "renamed": renamed,
            "governed": governed,
            "permission_changed": permission_changed,
            "governor_disabled": governor_disabled,
            "stale_permission": stale_permission,
            "queued": queued,
            "removed": removed,
            "after_queue": after_queue,
            "after_remove": after_remove,
        })
        passed = (
            renamed.get("ok")
            and governed.get("ok")
            and permission_changed.get("ok")
            and permission_changed.get("production_recalculated") is True
            and governor_disabled.get("ok")
            and stale_permission.get("error", {}).get("code") == "stale_state"
            and after_permission["governor"]["permissions"]["secret_projects"]
                is bool(permission["active"])
            and queued.get("ok")
            and removed.get("ok")
            and after_queue["name"] == "Agentic Dawn"
            and len(after_queue["production_queue"]) == 2
            and len(after_remove["production_queue"]) == 1
            and not after_remove["governor"]["active"]
            and not after_remove["governor"]["manage_citizens"]
            and not after_remove["governor"]["manage_production"]
        )
        if not passed:
            return 6
        emit("pass", {"base_id": base_id, "semantic_actions": 5,
                      "advanced_governor_permissions": True})
        return 0
    return 7


if __name__ == "__main__":
    sys.exit(main())
