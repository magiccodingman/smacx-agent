#!/usr/bin/env python3
"""Contained two-process DirectPlay discovery/join regression.

This test drives two independent bridge ports on one nested display.  It uses
only semantic bridge requests; screenshots, pointer input, and keyboard input
are deliberately absent.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import uuid

from smacx_controller import (
    COMPAT_DATA,
    GAME,
    PRESSURE_VESSEL,
    PROTON,
    STEAM_ROOT,
    TOKEN_FILE,
)


HOST_PORT = 47913
JOIN_PORT = 47914
JOIN_DISPLAY = ":100"
PROJECT = Path(__file__).resolve().parents[1]


def request(port: int, operation: str, timeout: float = 10, **arguments: object) -> dict:
    payload = {
        "op": operation,
        "token": TOKEN_FILE.read_text(encoding="ascii").strip(),
        **arguments,
    }
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as connection:
        connection.settimeout(timeout)
        connection.sendall(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(65536)
            if not chunk:
                raise RuntimeError(f"bridge {port} closed before responding")
            data.extend(chunk)
    return json.loads(bytes(data).split(b"\n", 1)[0])


def required_snapshot(port: int, context: str) -> dict:
    """Return a semantic snapshot while preserving bridge errors in failures."""
    envelope = request(port, "semantic_snapshot")
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict):
        raise AssertionError(
            f"bridge {port} omitted a snapshot during {context}: {envelope}"
        )
    return snapshot


def wait_bridge(port: int, seconds: float = 50) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if request(port, "ping", timeout=1).get("ok"):
                return
        except (OSError, RuntimeError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise AssertionError(f"bridge {port} did not start")


def launch_join_display() -> subprocess.Popen[bytes]:
    socket_path = Path("/tmp/.X11-unix/X100")
    if socket_path.exists():
        raise AssertionError(f"join display {JOIN_DISPLAY} is already in use")
    log = (PROJECT / "runtime" / "xephyr-lan-join.log").open("wb")
    process = subprocess.Popen(
        ["Xephyr", JOIN_DISPLAY, "-screen", "1280x800", "-resizeable", "-nolisten", "tcp"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    environment = os.environ.copy()
    environment["DISPLAY"] = JOIN_DISPLAY
    while time.monotonic() < deadline:
        if subprocess.run(
            ["xdpyinfo"], env=environment, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, check=False,
        ).returncode == 0:
            return process
        if process.poll() is not None:
            break
        time.sleep(0.25)
    process.terminate()
    raise AssertionError(f"join display {JOIN_DISPLAY} did not start")


def launch_pair() -> subprocess.Popen[bytes]:
    join_game = Path(os.environ.get(
        "SMACX_TEST_JOIN_GAME_PATH", PROJECT / "runtime" / "game-lan-join",
    ))
    if not (join_game / "thinker.exe").is_file() \
            or not (join_game / "thinker.dll").is_file():
        raise AssertionError("runtime/game-lan-join is missing")
    wine = PROTON.parent / "files" / "bin" / "wine"
    wineserver = PROTON.parent / "files" / "bin" / "wineserver"
    environment = os.environ.copy()
    environment.update({
        "DISPLAY": environment.get("DISPLAY", ":99"),
        "SMACX_TEST_HOST_DISPLAY": environment.get("DISPLAY", ":99"),
        "SMACX_TEST_JOIN_DISPLAY": JOIN_DISPLAY,
        "WINE": str(wine),
        "WINESERVER": str(wineserver),
        "SMACX_TEST_WINEPREFIX": str(COMPAT_DATA / "pfx"),
        "SMACX_TEST_HOST_GAME": str(GAME),
        "SMACX_TEST_JOIN_GAME": str(join_game),
        "SMACX_TEST_TOKEN": TOKEN_FILE.read_text(encoding="ascii").strip(),
        "SMACX_TEST_HOST_PORT": str(HOST_PORT),
        "SMACX_TEST_JOIN_PORT": str(JOIN_PORT),
        "SMACX_TEST_HOST_MATCH_ID": f"match-lan-host-{uuid.uuid4().hex}",
        "SMACX_TEST_HOST_SESSION_ID": f"session-lan-host-{uuid.uuid4().hex}",
        "SMACX_TEST_JOIN_MATCH_ID": f"match-lan-join-{uuid.uuid4().hex}",
        "SMACX_TEST_JOIN_SESSION_ID": f"session-lan-join-{uuid.uuid4().hex}",
        "SMACX_TEST_HOST_LOG": str(PROJECT / "runtime" / "lan-two-client-host.log"),
        "SMACX_TEST_JOIN_LOG": str(PROJECT / "runtime" / "lan-two-client-join.log"),
    })
    command = [
        str(PRESSURE_VESSEL), "--",
        str(PROJECT / "scripts" / "run_lan_two_clients.sh"),
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    wait_bridge(HOST_PORT)
    wait_bridge(JOIN_PORT)
    return process


def terminate_test_instances() -> None:
    roots = {GAME.resolve(), Path(os.environ.get(
        "SMACX_TEST_JOIN_GAME_PATH", PROJECT / "runtime" / "game-lan-join",
    )).resolve()}
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "cwd").resolve() not in roots:
                continue
            command = (entry / "cmdline").read_bytes().decode(errors="ignore").lower()
            if "terranx.exe" in command or "thinker.exe" in command:
                pids.append(int(entry.name))
        except (FileNotFoundError, PermissionError, OSError):
            continue
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def wait_lobby(port: int, seconds: float = 25) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "semantic_lan", action="status")
        if latest.get("lifecycle") == "lobby":
            return latest
        time.sleep(0.25)
    raise AssertionError(f"bridge {port} did not reach lobby: {latest}")


def wait_lobby_predicate(port: int, predicate, seconds: float = 25) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "semantic_lan", action="status")
        if latest.get("lifecycle") == "lobby" and predicate(latest.get("lobby", {})):
            return latest
        time.sleep(0.25)
    raise AssertionError(f"bridge {port} lobby condition timed out: {latest}")


def wait_game(port: int, seconds: float = 90) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "semantic_lan", action="status", timeout=5)
        if latest.get("lifecycle") == "game":
            return latest
        time.sleep(0.5)
    raise AssertionError(f"bridge {port} did not enter game: {latest}")


def wait_snapshot_predicate(port: int, predicate, seconds: float = 25) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    latest_envelope: dict = {}
    while time.monotonic() < deadline:
        latest_envelope = request(port, "semantic_snapshot")
        latest = latest_envelope.get("snapshot", {})
        if predicate(latest):
            return latest
        time.sleep(0.25)
    raise AssertionError(
        f"bridge {port} snapshot condition timed out: "
        f"snapshot={latest}, envelope={latest_envelope}"
    )


def drain_opening_planetfall(port: int, label: str, maximum: int = 8) -> tuple[dict, int]:
    acknowledged_count = 0
    snapshot = request(port, "semantic_snapshot")["snapshot"]
    while snapshot.get("interaction", {}).get("popup_label", "").startswith("PLANETFALL"):
        if acknowledged_count >= maximum:
            raise AssertionError(
                f"{label} exceeded {maximum} distinct PLANETFALL instances: {snapshot}"
            )
        previous_instance = snapshot.get("interaction", {}).get("instance_id")
        choices = request(port, "semantic_choices", kind="interaction")
        commands = [
            choice.get("command") for choice in choices.get("choices", [])
            if choice.get("kind") != "information"
        ]
        if not choices.get("ok") or not choices.get("popup_label", "").startswith("PLANETFALL") \
                or choices.get("instance_id") != previous_instance \
                or commands != ["acknowledge_popup"]:
            raise AssertionError(
                f"{label} opening interaction was not an exact fresh PLANETFALL "
                f"acknowledgment: {choices}"
            )
        acknowledged = request(
            port,
            "semantic_command",
            command="acknowledge_popup",
            match_id=choices["match_id"],
            session_id=choices["session_id"],
            expected_revision=choices["revision"],
        )
        if not acknowledged.get("ok") \
                or not acknowledged.get("popup_label", "").startswith("PLANETFALL"):
            raise AssertionError(
                f"{label} could not acknowledge PLANETFALL semantically: {acknowledged}"
            )
        acknowledged_count += 1
        snapshot = wait_snapshot_predicate(
            port,
            lambda state: state.get("interaction", {}).get("popup_label")
            is None
            or not state.get("interaction", {}).get("popup_label", "").startswith("PLANETFALL")
            or state.get("interaction", {}).get("instance_id") != previous_instance,
        )
    return snapshot, acknowledged_count


def drain_opening_planetfall_pair(maximum: int = 8) -> tuple[dict, dict, int, int]:
    sides = {"host": HOST_PORT, "join": JOIN_PORT}
    snapshots = {
        side: request(port, "semantic_snapshot")["snapshot"]
        for side, port in sides.items()
    }
    counts = {"host": 0, "join": 0}
    while True:
        active_sides = [
            side for side, snapshot in snapshots.items()
            if snapshot.get("interaction", {}).get("popup_label", "").startswith("PLANETFALL")
        ]
        if not active_sides:
            return snapshots["host"], snapshots["join"], counts["host"], counts["join"]
        choices_by_side: dict[str, dict] = {}
        previous_instances: dict[str, object] = {}
        for side in active_sides:
            if counts[side] >= maximum:
                raise AssertionError(
                    f"{side} exceeded {maximum} distinct PLANETFALL instances: "
                    f"{snapshots[side]}"
                )
            port = sides[side]
            previous_instance = snapshots[side].get("interaction", {}).get("instance_id")
            choices = request(port, "semantic_choices", kind="interaction")
            commands = [
                choice.get("command") for choice in choices.get("choices", [])
                if choice.get("kind") != "information"
            ]
            if not choices.get("ok") or not choices.get("popup_label", "").startswith("PLANETFALL") \
                    or choices.get("instance_id") != previous_instance \
                    or commands != ["acknowledge_popup"]:
                raise AssertionError(
                    f"{side} opening interaction was not an exact fresh PLANETFALL "
                    f"acknowledgment: {choices}"
                )
            choices_by_side[side] = choices
            previous_instances[side] = previous_instance

        def acknowledge(side: str) -> dict:
            choices = choices_by_side[side]
            return request(
                sides[side],
                "semantic_command",
                command="acknowledge_popup",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )

        with ThreadPoolExecutor(max_workers=len(active_sides)) as executor:
            futures = {side: executor.submit(acknowledge, side) for side in active_sides}
            results = {side: future.result() for side, future in futures.items()}
        for side, acknowledged in results.items():
            if not acknowledged.get("ok") \
                    or not acknowledged.get("popup_label", "").startswith("PLANETFALL"):
                raise AssertionError(
                    f"{side} could not acknowledge PLANETFALL semantically: {acknowledged}"
                )
            counts[side] += 1
        for side in active_sides:
            previous_instance = previous_instances[side]
            snapshots[side] = wait_snapshot_predicate(
                sides[side],
                lambda state, previous=previous_instance: (
                    not state.get("interaction", {}).get("popup_label", "").startswith("PLANETFALL")
                    or state.get("interaction", {}).get("instance_id") != previous
                ),
            )


def resolve_opening_interactions_pair(
    priority: int = 1, maximum_rounds: int = 120,
) -> tuple[dict, dict, int, int]:
    sides = {"host": HOST_PORT, "join": JOIN_PORT}
    snapshots = {
        side: required_snapshot(port, f"opening-interaction scan ({side})")
        for side, port in sides.items()
    }
    planetfall_counts = {"host": 0, "join": 0}
    for _ in range(maximum_rounds):
        actions: dict[str, tuple[dict, dict]] = {}
        for side, snapshot in snapshots.items():
            interaction = snapshot.get("interaction", {})
            popup_label = interaction.get("popup_label")
            kind = interaction.get("kind")
            if kind == "research_priority":
                raise AssertionError(
                    f"{side} exposed a post-selection multiplayer research prompt: {snapshot}"
                )
            is_planetfall = str(popup_label or "").startswith("PLANETFALL")
            is_research_focus = popup_label == "TECHRANDOM"
            is_contact = popup_label in {"COMM", "COMMDIPLO"}
            is_information_notice = kind == "popup" \
                and not is_research_focus and not is_contact
            is_technology_presentation = kind == "technology_presentation"
            if not is_information_notice and not is_research_focus \
                    and not is_contact \
                    and not is_technology_presentation:
                continue
            choices = request(sides[side], "semantic_choices", kind="interaction")
            if is_contact:
                commands = [
                    (choice.get("command"), choice.get("response"))
                    for choice in choices.get("choices", [])
                    if choice.get("kind") != "information"
                    and choice.get("kind") != "capability_status"
                ]
                if commands != [("respond_to_contact", "decline")]:
                    raise AssertionError(
                        f"{side} invalid multiplayer contact choices: {choices}"
                    )
                arguments = {
                    "command": "respond_to_contact",
                    "response": "decline",
                }
            elif is_technology_presentation:
                commands = [
                    choice.get("command") for choice in choices.get("choices", [])
                    if choice.get("kind") != "information"
                ]
                if commands != ["advance_technology_presentation"]:
                    raise AssertionError(
                        f"{side} invalid technology presentation choices: {choices}"
                    )
                arguments = {"command": "advance_technology_presentation"}
            elif is_information_notice:
                commands = [
                    choice.get("command") for choice in choices.get("choices", [])
                    if choice.get("kind") != "information"
                ]
                label_matches = choices.get("popup_label") == popup_label
                if not label_matches or commands != ["acknowledge_popup"]:
                    raise AssertionError(
                        f"{side} invalid opening notice choices for {popup_label}: {choices}"
                    )
                arguments = {"command": "acknowledge_popup"}
            else:
                focus = [
                    choice for choice in choices.get("choices", [])
                    if choice.get("command") == "choose_research_priority"
                    and choice.get("priority") == priority
                ]
                all_focus_priorities = sorted(
                    choice.get("priority") for choice in choices.get("choices", [])
                    if choice.get("command") == "choose_research_priority"
                )
                if choices.get("popup_label") != "TECHRANDOM" \
                        or all_focus_priorities != [0, 1, 2, 3] or len(focus) != 1:
                    raise AssertionError(f"{side} invalid TECHRANDOM choices: {choices}")
                arguments = {
                    "command": "choose_research_priority",
                    "priority": priority,
                }
            actions[side] = (choices, arguments)
        if not actions:
            if all(
                snapshot.get("research", {}).get("priority") in {0, 1, 2, 3}
                and snapshot.get("interaction", {}).get("kind")
                not in {
                    "waiting_for_engine", "research_priority", "popup",
                    "technology_presentation",
                }
                for snapshot in snapshots.values()
            ):
                return (
                    snapshots["host"], snapshots["join"],
                    planetfall_counts["host"], planetfall_counts["join"],
                )
            time.sleep(0.25)
            snapshots = {
                side: required_snapshot(
                    port, f"opening-interaction idle refresh ({side})",
                )
                for side, port in sides.items()
            }
            continue

        def execute(side: str) -> dict:
            choices, arguments = actions[side]
            return request(
                sides[side],
                "semantic_command",
                **arguments,
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )

        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = {side: executor.submit(execute, side) for side in actions}
            results = {side: future.result() for side, future in futures.items()}
        transition_race = False
        for side, result in results.items():
            command = actions[side][1]["command"]
            if not result.get("ok") or result.get("command") != command:
                if result.get("error", {}).get("code") in {
                    "stale_state",
                    "popup_transition_pending",
                    "multiplayer_command_not_validated",
                    "technology_presentation_changed",
                }:
                    transition_race = True
                    continue
                raise AssertionError(
                    f"{side} opening action {actions[side]} failed: {result}"
                )
            if command == "acknowledge_popup" and str(
                actions[side][0].get("popup_label", "")
            ).startswith("PLANETFALL"):
                planetfall_counts[side] += 1
        if transition_race:
            snapshots = {
                side: required_snapshot(
                    port, f"opening-interaction transition refresh ({side})",
                )
                for side, port in sides.items()
            }
            continue
        for side, (choices, _) in actions.items():
            snapshots[side] = wait_snapshot_predicate(
                sides[side],
                lambda state, previous=choices["revision"]: state.get("revision") != previous,
            )
        for side in sides.keys() - actions.keys():
            snapshots[side] = required_snapshot(
                sides[side], f"opening-interaction peer refresh ({side})",
            )
    raise AssertionError(f"opening interactions exceeded {maximum_rounds} rounds: {snapshots}")


def wait_chat_text(port: int, expected_text: str, seconds: float = 20) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "semantic_chat", action="list")
        if any(
            message.get("text") == expected_text and not message.get("outbound")
            for message in latest.get("messages", [])
        ):
            return latest
        time.sleep(0.25)
    raise AssertionError(f"bridge {port} did not receive chat {expected_text!r}: {latest}")


def wait_action_complete(port: int, action_id: int, seconds: float = 25) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "action_status", action_id=action_id)
        action = latest.get("action", latest)
        if action.get("status") in {"completed", "rejected"}:
            return action
        time.sleep(0.25)
    raise AssertionError(f"bridge {port} action {action_id} did not complete: {latest}")


def wait_network_vehicle_match(seconds: float = 25) -> tuple[dict, dict]:
    def shared_vehicle_state(
        status: dict, human_factions: set[int],
    ) -> list[dict]:
        shared_fields = (
            "id", "faction_id", "prototype_id", "tile_id",
            "moves_spent", "damage_taken", "order",
        )
        return [
            {
                **{field: vehicle.get(field) for field in shared_fields},
                # VSTATE_WORKING (0x04000000) is maintained locally for the
                # currently presented/selected unit and legitimately differs
                # between the two human clients before either player acts.
                "state": int(vehicle.get("state", 0)) & ~0x04000000,
            }
            for vehicle in status.get("vehicles", [])
            if vehicle.get("faction_id") in human_factions
        ]

    def shared_base_state(
        status: dict, human_factions: set[int],
    ) -> list[dict]:
        return [
            base for base in status.get("bases", [])
            if base.get("faction_id") in human_factions
        ]

    def shared_faction_state(
        status: dict, human_factions: set[int],
    ) -> list[dict]:
        return [
            faction for faction in status.get("factions", [])
            if faction.get("id") in human_factions
        ]

    deadline = time.monotonic() + seconds
    host: dict = {}
    join: dict = {}
    while time.monotonic() < deadline:
        host = request(HOST_PORT, "test_network_sync_status")
        join = request(JOIN_PORT, "test_network_sync_status")
        if host.get("interaction") in {"popup", "technology_presentation"} \
                or join.get("interaction") in {"popup", "technology_presentation"}:
            # A passive upkeep/technology notice can arrive after the caller's
            # last semantic snapshot. Resolve it before judging shared state;
            # otherwise the authoritative peer may be paused mid-network turn.
            resolve_opening_interactions_pair()
            continue
        human_factions = {
            int(host.get("local_faction_id", -1)),
            int(join.get("local_faction_id", -1)),
        }
        if host.get("ok") and join.get("ok") \
                and host.get("turn") == join.get("turn") \
                and shared_vehicle_state(host, human_factions) \
                == shared_vehicle_state(join, human_factions) \
                and shared_base_state(host, human_factions) \
                == shared_base_state(join, human_factions) \
                and shared_faction_state(host, human_factions) \
                == shared_faction_state(join, human_factions):
            return host, join
        time.sleep(0.25)
    raise AssertionError(f"native vehicle state diverged: {host} / {join}")


def wait_unit_choices(
    port: int, unit_id: int, seconds: float = 15, peer_port: int | None = None,
) -> dict:
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = request(port, "semantic_choices", kind="unit_actions", unit_id=unit_id)
        if latest.get("ok"):
            return latest
        if latest.get("error", {}).get("code") != "wrong_choice_phase":
            return latest
        if peer_port is not None:
            # A stock upkeep/research notification can become modal between a
            # turn snapshot and the following choice request. Re-observe both
            # clients and resolve that fresh semantic interaction instead of
            # retrying a stale turn phase or injecting UI input.
            resolve_opening_interactions_pair()
        time.sleep(0.25)
    return latest


def wait_host_actionable_turn(label: str, seconds: float = 20) -> dict:
    """Reach a fresh host turn while resolving reviewed late interactions."""
    resolve_opening_interactions_pair()
    deadline = time.monotonic() + seconds
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = required_snapshot(HOST_PORT, f"{label} actionable-turn wait")
        kind = latest.get("interaction", {}).get("kind")
        if kind == "turn":
            return latest
        if kind in {"popup", "research_priority", "technology_presentation"}:
            resolve_opening_interactions_pair()
            continue
        time.sleep(0.1)
    raise AssertionError(f"{label} did not reach a fresh actionable turn: {latest}")


def open_paired_human_diplomacy(
    join_faction_id: int, label: str, maximum_attempts: int = 4,
) -> dict:
    """Open stock paired DiploWindows with bounded, observed collision retry.

    SMACX's native commlink path sends 0x1502 and immediately enters the local
    window; it has no receiver-ready acknowledgment. If the remote side is
    momentarily busy, both windows can close with DIPLOCLOSE. Retry only after
    observing that the exact deferred attempt completed and the host returned
    to a fresh actionable turn.
    """
    attempts: list[dict] = []
    for attempt in range(1, maximum_attempts + 1):
        wait_host_actionable_turn(f"human {label}")
        diplomacy_choices = request(HOST_PORT, "semantic_choices", kind="diplomacy")
        target = next(
            (
                choice for choice in diplomacy_choices.get("choices", [])
                if choice.get("command") == "open_diplomacy"
                and choice.get("faction_id") == join_faction_id
                and choice.get("human_controlled") is True
            ),
            None,
        )
        if target is None:
            raise AssertionError(
                f"human {label} channel was absent: {diplomacy_choices}"
            )
        opened = request(
            HOST_PORT,
            "semantic_command",
            command="open_diplomacy",
            faction_id=join_faction_id,
            match_id=diplomacy_choices["match_id"],
            session_id=diplomacy_choices["session_id"],
            expected_revision=diplomacy_choices["revision"],
        )
        if not opened.get("ok") or not opened.get("queued"):
            raise AssertionError(f"human {label} channel did not queue: {opened}")
        deadline = time.monotonic() + 8
        latest: dict = {}
        while time.monotonic() < deadline:
            latest = required_snapshot(
                HOST_PORT, f"human {label} open attempt {attempt}",
            )
            if latest.get("interaction", {}).get("kind") == "human_diplomacy":
                return opened
            action = latest.get("last_deferred_action") or {}
            if action.get("action_id") == opened.get("action_id") \
                    and action.get("status") in {"completed", "rejected"}:
                break
            time.sleep(0.1)
        attempts.append({
            "attempt": attempt,
            "action_id": opened.get("action_id"),
            "last_deferred_action": latest.get("last_deferred_action"),
            "interaction": latest.get("interaction", {}).get("kind"),
            "last_popup": latest.get("interaction", {}).get("engine_state", {}).get(
                "last_started_popup_label"
            ),
        })
        time.sleep(0.25)
    raise AssertionError(
        f"human {label} paired window did not survive bounded native retries: "
        f"{attempts}"
    )


def respond_human_diplomacy_fresh(
    port: int, response: str, label: str, seconds: float = 10,
) -> tuple[dict, dict]:
    """Submit an exact response, re-observing only on a stale revision."""
    deadline = time.monotonic() + seconds
    latest_result: dict = {}
    latest_choices: dict = {}
    while time.monotonic() < deadline:
        latest_choices = request(port, "semantic_choices", kind="interaction")
        exact_choice = next(
            (
                choice for choice in latest_choices.get("choices", [])
                if choice.get("command") == "respond_human_diplomacy"
                and choice.get("response") == response
            ),
            None,
        )
        if exact_choice is None:
            return ({
                "ok": False,
                "error": {
                    "code": "expected_response_absent",
                    "message": f"The fresh {response} choice was absent for {label}.",
                },
            }, latest_choices)
        latest_result = request(
            port,
            "semantic_command",
            command="respond_human_diplomacy",
            response=response,
            match_id=latest_choices["match_id"],
            session_id=latest_choices["session_id"],
            expected_revision=latest_choices["revision"],
        )
        if latest_result.get("ok"):
            return latest_result, latest_choices
        if latest_result.get("error", {}).get("code") != "stale_state":
            return latest_result, latest_choices
        time.sleep(0.05)
    return latest_result, latest_choices


def finish_human_diplomacy_fresh(
    port: int, label: str, seconds: float = 10,
) -> tuple[dict, dict]:
    """Finish a still-open transmission, re-observing stale revisions only."""
    deadline = time.monotonic() + seconds
    latest_result: dict = {}
    latest_choices: dict = {}
    while time.monotonic() < deadline:
        latest_choices = request(port, "semantic_choices", kind="interaction")
        exact_choice = next(
            (
                choice for choice in latest_choices.get("choices", [])
                if choice.get("command") == "finish_human_diplomacy"
            ),
            None,
        )
        if exact_choice is None:
            return ({
                "ok": False,
                "error": {
                    "code": "expected_finish_absent",
                    "message": f"The fresh finish choice was absent for {label}.",
                },
            }, latest_choices)
        latest_result = request(
            port,
            "semantic_command",
            command="finish_human_diplomacy",
            match_id=latest_choices["match_id"],
            session_id=latest_choices["session_id"],
            expected_revision=latest_choices["revision"],
        )
        if latest_result.get("ok"):
            return latest_result, latest_choices
        if latest_result.get("error", {}).get("code") != "stale_state":
            return latest_result, latest_choices
        time.sleep(0.05)
    return latest_result, latest_choices


def interaction_command_fresh(
    port: int, command: str, label: str, seconds: float = 10,
    initial_choices: dict | None = None, **parameters,
) -> tuple[dict, dict]:
    """Submit one interaction command, retrying only a stale-state rejection."""
    deadline = time.monotonic() + seconds
    latest_result: dict = {}
    latest_choices: dict = {}
    next_choices = initial_choices
    while time.monotonic() < deadline:
        latest_choices = next_choices or request(
            port, "semantic_choices", kind="interaction",
        )
        next_choices = None
        latest_result = request(
            port,
            "semantic_command",
            command=command,
            match_id=latest_choices["match_id"],
            session_id=latest_choices["session_id"],
            expected_revision=latest_choices["revision"],
            **parameters,
        )
        if latest_result.get("ok") \
                or latest_result.get("error", {}).get("code") != "stale_state":
            return latest_result, latest_choices
        time.sleep(0.05)
    raise AssertionError(
        f"{label} remained stale through its bounded retry: "
        f"{latest_result} / {latest_choices}"
    )


def negotiate_human_relationship(
    host_faction_id: int,
    join_faction_id: int,
    relationship: str,
    native_clause_type: int,
    relationship_bit: int,
) -> None:
    """Complete one exact native two-human relationship offer."""
    resolve_opening_interactions_pair()
    opened = open_paired_human_diplomacy(join_faction_id, relationship)
    host_choices = request(HOST_PORT, "semantic_choices", kind="interaction")
    if not any(
        choice.get("command") == "propose_human_relationship"
        and choice.get("relationship") == relationship
        for choice in host_choices.get("choices", [])
    ):
        raise AssertionError(
            f"human {relationship} proposal was absent: {host_choices}"
        )
    proposed, host_choices = interaction_command_fresh(
        HOST_PORT, "propose_human_relationship", f"human {relationship} offer",
        initial_choices=host_choices, relationship=relationship,
    )
    if not proposed.get("ok") \
            or proposed.get("native_clause_type") != native_clause_type \
            or proposed.get("proposer_committed") is not True:
        raise AssertionError(
            f"human {relationship} offer was not atomically committed: {proposed}"
        )
    wait_snapshot_predicate(
        JOIN_PORT,
        lambda state: state.get("interaction", {}).get("kind")
        == "human_diplomacy",
        seconds=20,
    )
    join_choices = request(JOIN_PORT, "semantic_choices", kind="interaction")
    join_context = next(
        (
            choice for choice in join_choices.get("choices", [])
            if choice.get("id") == "human_diplomacy:context"
        ),
        {},
    )
    if not any(
        clause.get("offering_faction_id") == host_faction_id
        and clause.get("clause") == relationship
        for clause in join_context.get("clauses", [])
    ):
        raise AssertionError(
            f"peer did not observe human {relationship}: {join_choices}"
        )
    join_accepted, join_choices = respond_human_diplomacy_fresh(
        JOIN_PORT, "accept", f"human {relationship} peer",
    )
    if not join_accepted.get("ok"):
        raise AssertionError(
            f"peer human {relationship} acceptance failed: {join_accepted}"
        )
    for port in (HOST_PORT, JOIN_PORT):
        snapshot = required_snapshot(port, f"{relationship} agreement close")
        if snapshot.get("interaction", {}).get("kind") != "human_diplomacy":
            continue
        finished, choices = finish_human_diplomacy_fresh(
            port, f"human {relationship} close",
        )
        if not finished.get("ok"):
            raise AssertionError(
                f"human {relationship} transmission did not close: {finished}"
            )
    completed = wait_action_complete(HOST_PORT, opened["action_id"], seconds=20)
    if completed.get("status") != "completed":
        raise AssertionError(
            f"human {relationship} opening did not complete: {completed}"
        )
    deadline = time.monotonic() + 20
    host_status: dict = {}
    join_status: dict = {}
    while time.monotonic() < deadline:
        host_status = request(HOST_PORT, "test_network_sync_status")
        join_status = request(JOIN_PORT, "test_network_sync_status")
        if host_status.get("interaction") in {"popup", "technology_presentation"} \
                or join_status.get("interaction") in {
                    "popup", "technology_presentation",
                }:
            resolve_opening_interactions_pair()
            continue
        converged = True
        for status in (host_status, join_status):
            for first, second in (
                (host_faction_id, join_faction_id),
                (join_faction_id, host_faction_id),
            ):
                pair = next(
                    entry for entry in status["diplo_pairs"]
                    if entry["from"] == first and entry["to"] == second
                )
                converged &= bool(pair["relationship_status"] & relationship_bit)
        if converged:
            break
        time.sleep(0.25)
    else:
        raise AssertionError(
            f"human {relationship} did not converge: {host_status} / {join_status}"
        )
    resolve_opening_interactions_pair()
    wait_snapshot_predicate(
        HOST_PORT,
        lambda state: state.get("interaction", {}).get("kind") == "turn",
        seconds=20,
    )
    wait_snapshot_predicate(
        JOIN_PORT,
        lambda state: state.get("interaction", {}).get("kind")
        == "waiting_for_turn",
        seconds=20,
    )


def exercise_multiplayer_ai_contact(host_faction_id: int) -> None:
    """Accept and cleanly finish one native AI conversation in a LAN match."""
    fixture_host = request(
        HOST_PORT, "test_lan_ai_contact_fixture", faction_id=host_faction_id,
    )
    fixture_join = request(
        JOIN_PORT, "test_lan_ai_contact_fixture", faction_id=host_faction_id,
    )
    if not fixture_host.get("ok") or fixture_host != fixture_join:
        raise AssertionError(
            f"contained LAN AI-contact fixtures diverged: "
            f"{fixture_host} / {fixture_join}"
        )
    counterpart = int(fixture_host["counterpart_faction_id"])
    wait_host_actionable_turn("multiplayer AI contact")
    diplomacy_choices = request(HOST_PORT, "semantic_choices", kind="diplomacy")
    channel = next(
        (
            choice for choice in diplomacy_choices.get("choices", [])
            if choice.get("command") == "open_diplomacy"
            and choice.get("faction_id") == counterpart
            and choice.get("human_controlled") is False
        ),
        None,
    )
    if channel is None:
        raise AssertionError(
            f"exact AI diplomatic channel was absent: {diplomacy_choices}"
        )
    opened = request(
        HOST_PORT,
        "semantic_command",
        command="open_diplomacy",
        faction_id=counterpart,
        match_id=diplomacy_choices["match_id"],
        session_id=diplomacy_choices["session_id"],
        expected_revision=diplomacy_choices["revision"],
    )
    if not opened.get("ok") or not opened.get("queued"):
        raise AssertionError(f"AI diplomatic channel did not queue: {opened}")

    saw_contact = False
    saw_greeting = False
    saw_technology_offer = False
    saw_main_menu = False
    finished = False
    trace: list[str] = []
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline and not finished:
        snapshot = request(HOST_PORT, "semantic_snapshot")["snapshot"]
        interaction = snapshot.get("interaction", {})
        kind = interaction.get("kind")
        label = str(interaction.get("popup_label") or "")
        if kind in {"waiting_for_engine", "waiting_for_turn"}:
            time.sleep(0.1)
            continue
        if kind == "turn" and saw_main_menu:
            finished = True
            break
        if kind != "popup":
            time.sleep(0.1)
            continue
        trace.append(label)
        choices = request(HOST_PORT, "semantic_choices", kind="interaction")
        if label in {"COMM", "COMMDIPLO"}:
            actionable = [
                (choice.get("command"), choice.get("response"))
                for choice in choices.get("choices", [])
                if choice.get("kind") not in {"information", "capability_status"}
            ]
            pending = [
                choice for choice in choices.get("choices", [])
                if choice.get("id") == "contact:accept_pending_validation"
            ]
            if actionable != [
                ("respond_to_contact", "accept"),
                ("respond_to_contact", "decline"),
            ] or pending:
                raise AssertionError(
                    f"multiplayer AI contact choices were not exact: {choices}"
                )
            result = request(
                HOST_PORT,
                "semantic_command",
                command="respond_to_contact",
                response="accept",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            saw_contact = True
        elif label.startswith("INTRO"):
            commands = [
                choice.get("command") for choice in choices.get("choices", [])
                if choice.get("kind") != "information"
            ]
            if commands != ["continue_diplomacy"]:
                raise AssertionError(f"AI greeting choices were not exact: {choices}")
            result = request(
                HOST_PORT,
                "semantic_command",
                command="continue_diplomacy",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            saw_greeting = True
        elif label == "DIPLO":
            finish_choice = next(
                (
                    choice for choice in choices.get("choices", [])
                    if choice.get("command") == "choose_diplomacy_option"
                    and choice.get("option") == "finish"
                    and choice.get("native_option_id") == 0
                ),
                None,
            )
            if finish_choice is None:
                raise AssertionError(f"AI finish option was absent: {choices}")
            result = request(
                HOST_PORT,
                "semantic_command",
                command="choose_diplomacy_option",
                option="finish",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            saw_main_menu = True
        elif label.startswith("TRADETECH") or label.startswith("DEMANDTECH"):
            terms = next(
                (
                    choice for choice in choices.get("choices", [])
                    if choice.get("kind") == "information"
                    and choice.get("offer_type") in {
                        "technology_or_map_exchange", "technology_demand",
                    }
                    and (choice.get("terms") or choice.get("player_gives"))
                ),
                None,
            )
            reject = next(
                (
                    choice for choice in choices.get("choices", [])
                    if choice.get("command") == "respond_to_diplomatic_offer"
                    and choice.get("response") == "reject"
                ),
                None,
            )
            if terms is None or reject is None:
                raise AssertionError(
                    f"AI technology offer or demand was not fully semantic: {choices}"
                )
            result = request(
                HOST_PORT,
                "semantic_command",
                command="respond_to_diplomatic_offer",
                response="reject",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            saw_technology_offer = True
        elif label == "SWEARAPACT":
            terms = next(
                (
                    choice for choice in choices.get("choices", [])
                    if choice.get("kind") == "information"
                    and choice.get("offer_type") == "pact"
                ),
                None,
            )
            reject = next(
                (
                    choice for choice in choices.get("choices", [])
                    if choice.get("command") == "respond_to_diplomatic_offer"
                    and choice.get("response") == "reject"
                ),
                None,
            )
            if terms is None or reject is None:
                raise AssertionError(f"AI Pact offer was not semantic: {choices}")
            result = request(
                HOST_PORT,
                "semantic_command",
                command="respond_to_diplomatic_offer",
                response="reject",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
        elif [
            choice.get("command") for choice in choices.get("choices", [])
            if choice.get("kind") != "information"
        ] == ["acknowledge_popup"]:
            result = request(
                HOST_PORT,
                "semantic_command",
                command="acknowledge_popup",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
        else:
            raise AssertionError(
                f"AI contact reached an unvalidated continuation {label}: {choices}"
            )
        if not result.get("ok"):
            raise AssertionError(
                f"AI contact action failed at {label}: {result} / {choices}"
            )
        peer = request(JOIN_PORT, "semantic_snapshot")["snapshot"]
        if peer.get("interaction", {}).get("kind") in {
            "popup", "human_diplomacy", "technology_presentation",
        }:
            raise AssertionError(
                f"local AI conversation incorrectly opened on LAN peer: {peer}"
            )
        time.sleep(0.1)

    if not finished or not saw_contact or not saw_greeting or not saw_main_menu:
        raise AssertionError(
            f"AI conversation did not complete its exact semantic chain: "
            f"contact={saw_contact} greeting={saw_greeting} menu={saw_main_menu} "
            f"finished={finished} trace={trace}"
        )
    completed = wait_action_complete(HOST_PORT, opened["action_id"], seconds=20)
    if completed.get("status") != "completed":
        raise AssertionError(f"AI diplomacy deferred action did not complete: {completed}")
    wait_snapshot_predicate(
        JOIN_PORT,
        lambda state: state.get("interaction", {}).get("kind")
        == "waiting_for_turn",
        seconds=20,
    )
    print(json.dumps({
        "event": "pass",
        "multiplayer_ai_contact_accepted_semantically": True,
        "ai_greeting_continued_semantically": True,
        "ai_technology_offer_or_demand_rejected_semantically": saw_technology_offer,
        "ai_conversation_finished_semantically": True,
        "counterpart_faction_id": counterpart,
        "native_popup_trace": trace,
        "peer_remained_nonmodal": True,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))


def negotiate_human_technology(
    host_faction_id: int, join_faction_id: int, technology_id: int,
) -> None:
    """Transfer one exact native technology through paired human diplomacy."""
    resolve_opening_interactions_pair()
    join_before = request(JOIN_PORT, "list_technologies")
    if any(item.get("id") == technology_id for item in join_before.get("items", [])):
        raise AssertionError(
            f"technology fixture did not begin unowned by recipient: {join_before}"
        )
    opened = open_paired_human_diplomacy(join_faction_id, "technology")
    host_choices = request(HOST_PORT, "semantic_choices", kind="interaction")
    technology_choice = next(
        (
            choice for choice in host_choices.get("choices", [])
            if choice.get("command") == "propose_human_technology"
            and choice.get("technology_id") == technology_id
        ),
        None,
    )
    if technology_choice is None:
        raise AssertionError(
            f"exact human technology offer was absent: {host_choices}"
        )
    proposed, host_choices = interaction_command_fresh(
        HOST_PORT, "propose_human_technology", "human technology offer",
        initial_choices=host_choices, tech_id=technology_id,
    )
    if not proposed.get("ok") or proposed.get("native_clause_type") != 0 \
            or proposed.get("proposer_committed") is not True:
        raise AssertionError(
            f"human technology offer was not atomically committed: {proposed}"
        )
    wait_snapshot_predicate(
        JOIN_PORT,
        lambda state: state.get("interaction", {}).get("kind")
        == "human_diplomacy",
        seconds=20,
    )
    join_choices = request(JOIN_PORT, "semantic_choices", kind="interaction")
    join_context = next(
        (
            choice for choice in join_choices.get("choices", [])
            if choice.get("id") == "human_diplomacy:context"
        ),
        {},
    )
    if not any(
        clause.get("offering_faction_id") == host_faction_id
        and clause.get("clause") == "technology"
        and clause.get("technology_id") == technology_id
        for clause in join_context.get("clauses", [])
    ):
        raise AssertionError(
            f"peer did not observe human technology clause: {join_choices}"
        )
    join_accepted, join_choices = respond_human_diplomacy_fresh(
        JOIN_PORT, "accept", "human technology peer",
    )
    if not join_accepted.get("ok"):
        raise AssertionError(
            f"peer human technology acceptance failed: {join_accepted}"
        )
    for port in (HOST_PORT, JOIN_PORT):
        snapshot = required_snapshot(port, "technology agreement close")
        if snapshot.get("interaction", {}).get("kind") != "human_diplomacy":
            continue
        finished, choices = finish_human_diplomacy_fresh(
            port, "human technology close",
        )
        if not finished.get("ok"):
            raise AssertionError(
                f"human technology transmission did not close: {finished}"
            )
    completed = wait_action_complete(HOST_PORT, opened["action_id"], seconds=20)
    if completed.get("status") != "completed":
        raise AssertionError(
            f"human technology opening did not complete: {completed}"
        )
    resolve_opening_interactions_pair()
    deadline = time.monotonic() + 20
    join_after: dict = {}
    while time.monotonic() < deadline:
        join_after = request(JOIN_PORT, "list_technologies")
        if any(item.get("id") == technology_id for item in join_after.get("items", [])):
            break
        time.sleep(0.25)
    else:
        raise AssertionError(
            f"native human technology transfer did not reach recipient: {join_after}"
        )
    host_after = request(HOST_PORT, "list_technologies")
    if not any(item.get("id") == technology_id for item in host_after.get("items", [])):
        raise AssertionError(
            f"technology donor lost its own research unexpectedly: {host_after}"
        )
    print(json.dumps({
        "event": "pass",
        "human_technology_clause_synchronized": True,
        "human_technology_accepted_both_windows": True,
        "human_technology_transfer_converged": True,
        "technology_id": technology_id,
        "technology_name": technology_choice.get("technology_name"),
        "native_clause_type": 0,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))


def negotiate_human_energy(
    host_faction_id: int, join_faction_id: int, amount: int,
) -> None:
    """Transfer one bounded energy amount through paired human diplomacy."""
    resolve_opening_interactions_pair()
    def energy(status: dict, faction_id: int) -> int:
        faction = next(
            (item for item in status.get("factions", [])
             if item.get("id") == faction_id),
            None,
        )
        if faction is None:
            raise AssertionError(
                f"network status omitted faction {faction_id}: {status}"
            )
        return int(faction["energy"])

    host_before = request(HOST_PORT, "test_network_sync_status")
    join_before = request(JOIN_PORT, "test_network_sync_status")
    donor_before = energy(host_before, host_faction_id)
    recipient_before = energy(host_before, join_faction_id)
    if (donor_before, recipient_before) != (
        energy(join_before, host_faction_id),
        energy(join_before, join_faction_id),
    ):
        raise AssertionError(
            f"energy fixture did not begin synchronized: {host_before} / {join_before}"
        )
    if amount < 1 or amount > donor_before:
        raise AssertionError(
            f"energy test amount {amount} exceeds donor treasury {donor_before}"
        )

    opened = open_paired_human_diplomacy(join_faction_id, "energy")
    host_choices = request(HOST_PORT, "semantic_choices", kind="interaction")
    energy_choice = next(
        (
            choice for choice in host_choices.get("choices", [])
            if choice.get("command") == "propose_human_energy"
        ),
        None,
    )
    if energy_choice is None \
            or energy_choice.get("amount_min") != 1 \
            or energy_choice.get("amount_max") != donor_before:
        raise AssertionError(f"bounded human energy choice was absent: {host_choices}")
    excessive, host_choices = interaction_command_fresh(
        HOST_PORT, "propose_human_energy", "excessive human energy offer",
        initial_choices=host_choices, amount=donor_before + 1,
    )
    if excessive.get("ok") \
            or excessive.get("error", {}).get("code") \
            != "multiplayer_command_not_validated":
        raise AssertionError(
            f"excessive human energy offer did not fail closed: {excessive}"
        )
    proposed, host_choices = interaction_command_fresh(
        HOST_PORT, "propose_human_energy", "bounded human energy offer",
        initial_choices=host_choices, amount=amount,
    )
    if not proposed.get("ok") or proposed.get("native_clause_type") != 1 \
            or proposed.get("proposer_committed") is not True:
        raise AssertionError(
            f"human energy offer was not atomically committed: {proposed}"
        )
    wait_snapshot_predicate(
        JOIN_PORT,
        lambda state: state.get("interaction", {}).get("kind")
        == "human_diplomacy",
        seconds=20,
    )
    join_choices = request(JOIN_PORT, "semantic_choices", kind="interaction")
    join_context = next(
        (choice for choice in join_choices.get("choices", [])
         if choice.get("id") == "human_diplomacy:context"),
        {},
    )
    if not any(
        clause.get("offering_faction_id") == host_faction_id
        and clause.get("clause") == "energy"
        and clause.get("energy_credits") == amount
        for clause in join_context.get("clauses", [])
    ):
        raise AssertionError(f"peer did not observe energy clause: {join_choices}")
    join_accepted, join_choices = respond_human_diplomacy_fresh(
        JOIN_PORT, "accept", "human energy recipient",
    )
    if not join_accepted.get("ok"):
        raise AssertionError(f"energy recipient acceptance failed: {join_accepted}")
    for port in (HOST_PORT, JOIN_PORT):
        snapshot = required_snapshot(port, "energy agreement close")
        if snapshot.get("interaction", {}).get("kind") != "human_diplomacy":
            continue
        finished, choices = finish_human_diplomacy_fresh(
            port, "human energy close",
        )
        if not finished.get("ok"):
            raise AssertionError(f"energy agreement did not close: {finished}")
    completed = wait_action_complete(HOST_PORT, opened["action_id"], seconds=20)
    if completed.get("status") != "completed":
        raise AssertionError(f"energy diplomacy opening did not complete: {completed}")
    resolve_opening_interactions_pair()

    expected = (donor_before - amount, recipient_before + amount)
    deadline = time.monotonic() + 20
    latest_host: dict = {}
    latest_join: dict = {}
    while time.monotonic() < deadline:
        latest_host = request(HOST_PORT, "test_network_sync_status")
        latest_join = request(JOIN_PORT, "test_network_sync_status")
        host_values = (
            energy(latest_host, host_faction_id),
            energy(latest_host, join_faction_id),
        )
        join_values = (
            energy(latest_join, host_faction_id),
            energy(latest_join, join_faction_id),
        )
        if host_values == expected and join_values == expected:
            break
        time.sleep(0.25)
    else:
        raise AssertionError(
            f"native energy transfer did not converge to {expected}: "
            f"{latest_host} / {latest_join}"
        )
    print(json.dumps({
        "event": "pass",
        "human_energy_clause_synchronized": True,
        "human_energy_accepted_both_windows": True,
        "human_energy_transfer_converged": True,
        "energy_credits": amount,
        "donor_before": donor_before,
        "donor_after": expected[0],
        "recipient_before": recipient_before,
        "recipient_after": expected[1],
        "native_clause_type": 1,
        "pixels_or_ui_input_used": False,
    }, separators=(",", ":")))


def main() -> int:
    join_display_process = launch_join_display()
    pair_process = launch_pair()
    try:
        host_operation = f"host-{uuid.uuid4().hex}"
        hosted = request(
            HOST_PORT,
            "semantic_lan",
            timeout=140,
            action="host",
            session_name="SMACX Two Client Semantic Test",
            player_name="Semantic Host",
            client_operation_id=host_operation,
        )
        if not hosted.get("ok") or not hosted.get("lobby_launch_queued"):
            raise AssertionError(f"host failed: {hosted}")
        host_lobby = wait_lobby(HOST_PORT)
        network_session_id = host_lobby.get("identity", {}).get("network_session_id")
        if not network_session_id:
            raise AssertionError(f"host omitted network session identity: {host_lobby}")

        discovered = request(
            JOIN_PORT,
            "semantic_lan",
            timeout=140,
            action="discover",
            host_address="127.0.0.1",
        )
        matching = [
            session for session in discovered.get("sessions", [])
            if session.get("network_session_id") == network_session_id
        ]
        if not discovered.get("ok") or len(matching) != 1:
            raise AssertionError(
                f"joiner did not discover exact host session {network_session_id}: {discovered}"
            )

        joined = request(
            JOIN_PORT,
            "semantic_lan",
            timeout=140,
            action="join",
            network_session_id=network_session_id,
            player_name="Semantic Joiner",
            host_address="127.0.0.1",
            client_operation_id=f"join-{uuid.uuid4().hex}",
        )
        if not joined.get("ok") or not joined.get("joined") \
                or not joined.get("lobby_launch_queued"):
            raise AssertionError(f"native join failed: {joined}")
        join_lobby = wait_lobby(JOIN_PORT)
        joined_network_session_id = join_lobby.get("identity", {}).get("network_session_id")
        if joined_network_session_id != network_session_id:
            raise AssertionError(
                f"host/join network identities diverged: {host_lobby} / {join_lobby}"
            )

        join_lobby = wait_lobby_predicate(
            JOIN_PORT,
            lambda lobby: lobby.get("participant_count") == 2
            and lobby.get("role") == "client",
        )
        host_lobby = wait_lobby_predicate(
            HOST_PORT,
            lambda lobby: lobby.get("participant_count") == 2
            and lobby.get("role") == "host",
        )
        profile_matrix = [
            ("tiny_citizen", {"id": 0, "name": "citizen"}, {"id": 0, "name": "tiny"}),
            ("standard_librarian", {"id": 3, "name": "librarian"}, {"id": 2, "name": "standard"}),
            ("large_thinker", {"id": 4, "name": "thinker"}, {"id": 3, "name": "large"}),
            ("huge_transcend", {"id": 5, "name": "transcend"}, {"id": 4, "name": "huge"}),
            ("small_easy", {"id": 0, "name": "citizen"}, {"id": 1, "name": "small"}),
        ]
        for profile_id, expected_difficulty, expected_size in profile_matrix:
            host_identity = host_lobby["identity"]
            configured = request(
                HOST_PORT,
                "semantic_lan",
                action="configure",
                profile=profile_id,
                match_id=host_identity["match_id"],
                session_id=host_identity["session_id"],
                expected_lobby_revision=host_lobby["lobby"]["revision"],
                client_operation_id=f"configure-{uuid.uuid4().hex}",
            )
            if not configured.get("ok") or configured.get("profile") != profile_id:
                raise AssertionError(f"semantic host configuration failed: {configured}")
            host_lobby = wait_lobby_predicate(
                HOST_PORT,
                lambda lobby, expected=profile_id: lobby.get("settings", {}).get("profile") == expected,
            )
            join_lobby = wait_lobby_predicate(
                JOIN_PORT,
                lambda lobby, expected=profile_id: lobby.get("settings", {}).get("profile") == expected,
            )
            for label, lobby_state in (("host", host_lobby), ("join", join_lobby)):
                settings = lobby_state["lobby"]["settings"]
                if settings.get("difficulty") != expected_difficulty \
                        or settings.get("map_size") != expected_size:
                    raise AssertionError(
                        f"{label} did not receive exact {profile_id} settings: {lobby_state}"
                    )
        if os.environ.get("SMACX_TEST_PROFILE_MATRIX_ONLY") == "1":
            print(json.dumps({
                "event": "pass",
                "native_directplay_profile_matrix": [item[0] for item in profile_matrix],
                "host_and_client_synchronized": True,
                "pixels_or_ui_input_used": False,
            }, separators=(",", ":")))
            return 0
        join_identity = join_lobby["identity"]
        ready_result = request(
            JOIN_PORT,
            "semantic_lan",
            action="set_ready",
            ready=True,
            match_id=join_identity["match_id"],
            session_id=join_identity["session_id"],
            expected_lobby_revision=join_lobby["lobby"]["revision"],
            client_operation_id=f"ready-{uuid.uuid4().hex}",
        )
        if not ready_result.get("ok"):
            raise AssertionError(f"semantic client ready failed: {ready_result}")

        host_lobby = wait_lobby_predicate(
            HOST_PORT,
            lambda lobby: lobby.get("participant_count") == 2
            and lobby.get("all_clients_ready") is True,
        )
        host_identity = host_lobby["identity"]
        start_result = request(
            HOST_PORT,
            "semantic_lan",
            action="start",
            match_id=host_identity["match_id"],
            session_id=host_identity["session_id"],
            expected_lobby_revision=host_lobby["lobby"]["revision"],
            client_operation_id=f"start-{uuid.uuid4().hex}",
        )
        if not start_result.get("ok"):
            raise AssertionError(f"semantic host start failed: {start_result}")
        host_game = wait_game(HOST_PORT)
        join_game = wait_game(JOIN_PORT)
        for label, game_state in (("host", host_game), ("join", join_game)):
            game_settings = game_state.get("game_settings", {})
            if game_settings.get("difficulty", {}).get("id") != 0 \
                    or game_settings.get("lobby_configuration", {}).get("profile") \
                    != "small_easy":
                raise AssertionError(
                    f"{label} did not enter the configured native game: {game_state}"
                )
        if (
            host_game["game_settings"].get("map_width"),
            host_game["game_settings"].get("map_height"),
        ) != (
            join_game["game_settings"].get("map_width"),
            join_game["game_settings"].get("map_height"),
        ):
            raise AssertionError(
                f"host/join generated different map dimensions: {host_game} / {join_game}"
            )

        host_snapshot = request(HOST_PORT, "semantic_snapshot")["snapshot"]
        join_snapshot = request(JOIN_PORT, "semantic_snapshot")["snapshot"]

        blocked_strategy = request(
            HOST_PORT, "semantic_choices", kind="unit_design",
        )
        if blocked_strategy.get("ok") \
                or blocked_strategy.get("error", {}).get("code") \
                != "multiplayer_choices_not_validated":
            raise AssertionError(
                f"unvalidated multiplayer strategy did not fail closed: {blocked_strategy}"
            )

        host_after_opening, join_after_opening, host_planetfall_count, \
            join_planetfall_count = drain_opening_planetfall_pair()
        host_after_research, join_after_research, extra_host_planetfall, \
            extra_join_planetfall = resolve_opening_interactions_pair()
        host_planetfall_count += extra_host_planetfall
        join_planetfall_count += extra_join_planetfall

        host_sync, join_sync = wait_network_vehicle_match()

        host_faction_id = host_sync["local_faction_id"]
        join_faction_id = join_sync["local_faction_id"]
        if os.environ.get("SMACX_TEST_MULTIPLAYER_AI_CONTACT") == "1":
            exercise_multiplayer_ai_contact(host_faction_id)
            return 0
        human_relationship_mode = os.environ.get(
            "SMACX_TEST_HUMAN_RELATIONSHIP", "treaty",
        )
        if human_relationship_mode not in {"treaty", "pact", "truce"}:
            raise AssertionError(
                f"unsupported human relationship test mode: "
                f"{human_relationship_mode}"
            )
        initial_relationship = {
            "treaty": "",
            "pact": "treaty",
            "truce": "vendetta",
        }[human_relationship_mode]
        human_trade_mode = os.environ.get("SMACX_TEST_HUMAN_TRADE", "")
        if human_trade_mode not in {"", "technology", "energy"}:
            raise AssertionError(
                f"unsupported human trade test mode: {human_trade_mode}"
            )
        diplomacy_fixture_host = request(
            HOST_PORT,
            "test_lan_diplomacy_fixture",
            faction_id=host_faction_id,
            counterpart_faction_id=join_faction_id,
            initial_relationship=initial_relationship,
            trade_fixture=human_trade_mode,
        )
        diplomacy_fixture_join = request(
            JOIN_PORT,
            "test_lan_diplomacy_fixture",
            faction_id=host_faction_id,
            counterpart_faction_id=join_faction_id,
            initial_relationship=initial_relationship,
            trade_fixture=human_trade_mode,
        )
        if not diplomacy_fixture_host.get("ok") \
                or diplomacy_fixture_host != diplomacy_fixture_join:
            raise AssertionError(
                f"contained LAN diplomacy fixtures diverged: "
                f"{diplomacy_fixture_host} / {diplomacy_fixture_join}"
            )
        if human_trade_mode == "technology":
            negotiate_human_technology(
                host_faction_id,
                join_faction_id,
                int(diplomacy_fixture_host["technology_id"]),
            )
            return 0
        if human_trade_mode == "energy":
            negotiate_human_energy(
                host_faction_id,
                join_faction_id,
                amount=75,
            )
            return 0
        if human_relationship_mode != "treaty":
            clause_type, relationship_bit = {
                "pact": (2, 0x1),
                "truce": (4, 0x4),
            }[human_relationship_mode]
            negotiate_human_relationship(
                host_faction_id,
                join_faction_id,
                relationship=human_relationship_mode,
                native_clause_type=clause_type,
                relationship_bit=relationship_bit,
            )
            print(json.dumps({
                "event": "pass",
                "relationship": human_relationship_mode,
                "native_clause_type": clause_type,
                "paired_human_diplomacy": True,
                "relationship_synchronized_both_directions": True,
                "post_transmission_settlement_phase_crossed": True,
                "pixels_or_ui_input_used": False,
            }, separators=(",", ":")))
            return 0
        diplomacy_open = open_paired_human_diplomacy(
            join_faction_id, "treaty",
        )
        host_diplomacy_snapshot = wait_snapshot_predicate(
            HOST_PORT,
            lambda state: state.get("interaction", {}).get("kind")
            == "human_diplomacy",
            seconds=20,
        )
        host_diplomacy_choices = request(
            HOST_PORT, "semantic_choices", kind="interaction",
        )
        diplomacy_context = next(
            (
                choice for choice in host_diplomacy_choices.get("choices", [])
                if choice.get("id") == "human_diplomacy:context"
            ),
            None,
        )
        if diplomacy_context is None \
                or diplomacy_context.get("initiator_faction_id") != host_faction_id \
                or diplomacy_context.get("counterpart_faction_id") != join_faction_id:
            raise AssertionError(
                f"human diplomacy ownership was not exact: "
                f"{host_diplomacy_snapshot} / {host_diplomacy_choices}"
            )
        treaty_choice = next(
            (
                choice for choice in host_diplomacy_choices.get("choices", [])
                if choice.get("command") == "propose_human_relationship"
                and choice.get("relationship") == "treaty"
            ),
            None,
        )
        if treaty_choice is None:
            raise AssertionError(
                f"exact native human Treaty proposal was absent: "
                f"{host_diplomacy_choices}"
            )
        treaty_proposal, host_diplomacy_choices = interaction_command_fresh(
            HOST_PORT, "propose_human_relationship", "human Treaty offer",
            initial_choices=host_diplomacy_choices, relationship="treaty",
        )
        if not treaty_proposal.get("ok") \
                or treaty_proposal.get("native_clause_type") != 3 \
                or treaty_proposal.get("proposer_committed") is not True:
            raise AssertionError(
                f"native human Treaty was not atomically committed: {treaty_proposal}"
            )
        host_diplomacy_choices = request(
            HOST_PORT, "semantic_choices", kind="interaction",
        )
        diplomacy_context = next(
            (
                choice for choice in host_diplomacy_choices.get("choices", [])
                if choice.get("id") == "human_diplomacy:context"
            ),
            None,
        )
        treaty_clauses = [] if diplomacy_context is None \
            else diplomacy_context.get("clauses", [])
        if treaty_proposal.get("proposer_committed") is not True and not any(
            clause.get("offering_faction_id") == host_faction_id
            and clause.get("clause") == "treaty"
            for clause in treaty_clauses
        ):
            raise AssertionError(
                f"composed human Treaty was not observable semantically: "
                f"{host_diplomacy_choices}"
            )
        # The offering side must commit while its modal is still locally
        # active.  Once the peer's paired window processes the proposal, the
        # host-side native loop may close to await that response.
        if treaty_proposal.get("proposer_committed") is not True and not any(
            choice.get("command") == "respond_human_diplomacy"
            and choice.get("response") == "accept"
            for choice in host_diplomacy_choices.get("choices", [])
        ):
            raise AssertionError(
                f"offering side could not commit its Treaty: "
                f"{host_diplomacy_choices}"
            )
        host_acceptance = {"ok": True, "atomic": True} \
            if treaty_proposal.get("proposer_committed") is True else request(
            HOST_PORT,
            "semantic_command",
            command="respond_human_diplomacy",
            response="accept",
            match_id=host_diplomacy_choices["match_id"],
            session_id=host_diplomacy_choices["session_id"],
            expected_revision=host_diplomacy_choices["revision"],
        )
        if not host_acceptance.get("ok"):
            raise AssertionError(
                f"offering side Treaty commitment failed: {host_acceptance}"
            )
        wait_snapshot_predicate(
            JOIN_PORT,
            lambda state: state.get("interaction", {}).get("kind")
            == "human_diplomacy",
            seconds=20,
        )
        join_diplomacy_choices = request(
            JOIN_PORT, "semantic_choices", kind="interaction",
        )
        join_context = next(
            (
                choice for choice in join_diplomacy_choices.get("choices", [])
                if choice.get("id") == "human_diplomacy:context"
            ),
            None,
        )
        join_clauses = [] if join_context is None \
            else join_context.get("clauses", [])
        if not any(
            clause.get("offering_faction_id") == host_faction_id
            and clause.get("clause") == "treaty"
            for clause in join_clauses
        ):
            raise AssertionError(
                f"live peer did not observe the native Treaty clause: "
                f"{join_diplomacy_choices}"
            )

        def accept_current_offer(port: int, choices: dict) -> dict:
            if not any(
                choice.get("command") == "respond_human_diplomacy"
                and choice.get("response") == "accept"
                for choice in choices.get("choices", [])
            ):
                raise AssertionError(
                    f"human offer acceptance was not enumerated: {choices}"
                )
            accepted = request(
                port,
                "semantic_command",
                command="respond_human_diplomacy",
                response="accept",
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            if not accepted.get("ok"):
                raise AssertionError(
                    f"native human offer acceptance failed: {accepted}"
                )
            return accepted

        # The peer's acceptance completes the native two-party handshake.
        wait_snapshot_predicate(
            JOIN_PORT,
            lambda state: state.get("interaction", {}).get("kind")
            == "human_diplomacy",
            seconds=20,
        )
        join_diplomacy_choices = request(
            JOIN_PORT, "semantic_choices", kind="interaction",
        )
        join_acceptance, join_diplomacy_choices = respond_human_diplomacy_fresh(
            JOIN_PORT, "accept", "broad human Treaty peer",
        )
        if not join_acceptance.get("ok"):
            raise AssertionError(
                f"native human Treaty peer acceptance failed: {join_acceptance}"
            )
        host_after_peer_accept = request(
            HOST_PORT, "semantic_snapshot",
        )["snapshot"]
        if treaty_proposal.get("proposer_committed") is not True \
                and host_after_peer_accept.get("interaction", {}).get("kind") \
                == "human_diplomacy":
            host_diplomacy_choices = request(
                HOST_PORT, "semantic_choices", kind="interaction",
            )
            accept_current_offer(HOST_PORT, host_diplomacy_choices)

        # Both native windows normally resolve the mutually accepted offer.
        # If either remains open solely for transmission cleanup, close it
        # through the same guarded semantic action—never UI input.
        for port in (HOST_PORT, JOIN_PORT):
            snapshot = request(port, "semantic_snapshot")["snapshot"]
            if snapshot.get("interaction", {}).get("kind") \
                    != "human_diplomacy":
                continue
            diplomacy_finish, finish_choices = finish_human_diplomacy_fresh(
                port, "broad human Treaty close",
            )
            if not diplomacy_finish.get("ok"):
                raise AssertionError(
                    f"accepted human diplomacy did not close: {diplomacy_finish}"
                )
        diplomacy_status = wait_action_complete(
            HOST_PORT, diplomacy_open["action_id"], seconds=20,
        )
        if diplomacy_status.get("status") != "completed":
            raise AssertionError(
                f"human diplomacy initiation did not complete: {diplomacy_status}"
            )
        diplomacy_deadline = time.monotonic() + 20
        diplomacy_host_sync: dict = {}
        diplomacy_join_sync: dict = {}
        while time.monotonic() < diplomacy_deadline:
            diplomacy_host_sync = request(
                HOST_PORT, "test_network_sync_status",
            )
            diplomacy_join_sync = request(
                JOIN_PORT, "test_network_sync_status",
            )
            if diplomacy_host_sync.get("interaction") in {
                "popup", "technology_presentation",
            } or diplomacy_join_sync.get("interaction") in {
                "popup", "technology_presentation",
            }:
                resolve_opening_interactions_pair()
                continue
            converged = True
            for status in (diplomacy_host_sync, diplomacy_join_sync):
                host_to_join = next(
                    pair for pair in status["diplo_pairs"]
                    if pair["from"] == host_faction_id
                    and pair["to"] == join_faction_id
                )
                join_to_host = next(
                    pair for pair in status["diplo_pairs"]
                    if pair["from"] == join_faction_id
                    and pair["to"] == host_faction_id
                )
                converged &= bool(host_to_join["relationship_status"] & 2)
                converged &= bool(join_to_host["relationship_status"] & 2)
            if converged:
                break
            time.sleep(0.25)
        else:
            raise AssertionError(
                f"mutually accepted native Treaty did not converge: "
                f"{diplomacy_host_sync} / {diplomacy_join_sync}"
            )

        # The paired native windows close before their final DirectPlay packet
        # tail is necessarily quiet.  Respect the bridge's explicit
        # post-negotiation phase boundary before beginning another mutation.
        resolve_opening_interactions_pair()
        wait_snapshot_predicate(
            HOST_PORT,
            lambda state: state.get("interaction", {}).get("kind") == "turn",
            seconds=20,
        )
        wait_snapshot_predicate(
            JOIN_PORT,
            lambda state: state.get("interaction", {}).get("kind")
            == "waiting_for_turn",
            seconds=20,
        )

        combat_fixture_host = request(
            HOST_PORT,
            "test_lan_combat_fixture",
            faction_id=host_faction_id,
        )
        combat_fixture_join = request(
            JOIN_PORT,
            "test_lan_combat_fixture",
            faction_id=host_faction_id,
        )
        combat_fixture_fields = (
            "attacker_faction_id", "attacker_unit_id", "defender_faction_id",
            "defender_unit_id", "origin_tile_id", "target_tile_id",
        )
        if not combat_fixture_host.get("ok") \
                or not combat_fixture_join.get("ok") \
                or any(
                    combat_fixture_host.get(field) != combat_fixture_join.get(field)
                    for field in combat_fixture_fields
                ):
            raise AssertionError(
                f"contained LAN combat fixtures diverged: "
                f"{combat_fixture_host} / {combat_fixture_join}"
            )
        attacker_id = combat_fixture_host["attacker_unit_id"]
        attacker_before = next(
            vehicle for vehicle in request(
                HOST_PORT, "test_network_sync_status",
            )["vehicles"]
            if vehicle["id"] == attacker_id
        )
        combat_choices = request(
            HOST_PORT,
            "semantic_choices",
            kind="unit_actions",
            unit_id=attacker_id,
        )
        combat_move = next(
            (
                choice for choice in combat_choices.get("choices", [])
                if choice.get("command") == "move_unit"
                and choice.get("target_tile_id")
                == combat_fixture_host["target_tile_id"]
                and choice.get("combat") is True
                and choice.get("already_at_war") is True
            ),
            None,
        )
        if combat_move is None:
            raise AssertionError(
                f"exact already-at-war LAN combat move was absent: {combat_choices}"
            )
        combat_queued = request(
            HOST_PORT,
            "semantic_command",
            command="move_unit",
            unit_id=attacker_id,
            target_tile_id=combat_move["target_tile_id"],
            match_id=combat_choices["match_id"],
            session_id=combat_choices["session_id"],
            expected_revision=combat_choices["revision"],
        )
        if not combat_queued.get("ok") or not combat_queued.get("queued"):
            raise AssertionError(
                f"validated native LAN combat was not queued: {combat_queued}"
            )
        combat_status = wait_action_complete(
            HOST_PORT, combat_queued["action_id"], seconds=35,
        )
        if combat_status.get("status") != "completed":
            raise AssertionError(
                f"validated native LAN combat failed: {combat_status}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        target_tile_id = combat_fixture_host["target_tile_id"]
        target_factions = {
            combat_fixture_host["attacker_faction_id"],
            combat_fixture_host["defender_faction_id"],
        }

        def combat_target_state(status: dict) -> list[tuple]:
            return sorted(
                (
                    vehicle.get("faction_id"), vehicle.get("prototype_id"),
                    vehicle.get("tile_id"), vehicle.get("moves_spent"),
                    vehicle.get("damage_taken"), vehicle.get("order"),
                )
                for vehicle in status.get("vehicles", [])
                if vehicle.get("tile_id") == target_tile_id
                and vehicle.get("faction_id") in target_factions
            )

        attacker_after = next(
            (
                vehicle for vehicle in host_sync["vehicles"]
                if vehicle.get("faction_id") == host_faction_id
                and vehicle.get("prototype_id") == attacker_before["prototype_id"]
                and vehicle.get("tile_id") in {
                    combat_fixture_host["origin_tile_id"], target_tile_id,
                }
            ),
            None,
        )
        if combat_target_state(host_sync) != combat_target_state(join_sync) \
                or attacker_after == attacker_before:
            raise AssertionError(
                f"native LAN combat did not converge or change attacker state: "
                f"{combat_status} / {host_sync} / {join_sync}"
            )
        host_after_research, join_after_research, _, _ = \
            resolve_opening_interactions_pair()
        safe_move_choices: dict | None = None
        safe_move: dict | None = None
        inspected_move_choices: list[dict] = []
        for unit in host_after_research.get("ready_unit_refs", []):
            choices = wait_unit_choices(
                HOST_PORT, unit["id"], peer_port=JOIN_PORT,
            )
            inspected_move_choices.append(choices)
            moves = [
                choice for choice in choices.get("choices", [])
                if choice.get("command") == "move_unit"
                and choice.get("safe_local_move") is True
            ]
            if moves:
                safe_move_choices = choices
                safe_move = moves[0]
                break
        if safe_move_choices is None or safe_move is None:
            origin_tile_id = host_after_research.get("ready_unit_refs", [{}])[0].get(
                "tile_id", -1
            )
            raise AssertionError(
                "host had no validated safe adjacent LAN move: "
                f"snapshot={host_after_research}; choices={inspected_move_choices}; "
                f"tiles={request(HOST_PORT, 'list_tiles', center_tile_id=origin_tile_id, radius=1)}; "
                f"native={request(HOST_PORT, 'test_network_sync_status')}"
            )
        moved = request(
            HOST_PORT,
            "semantic_command",
            command="move_unit",
            unit_id=safe_move["unit_id"],
            target_tile_id=safe_move["target_tile_id"],
            match_id=safe_move_choices["match_id"],
            session_id=safe_move_choices["session_id"],
            expected_revision=safe_move_choices["revision"],
        )
        if not moved.get("ok") or not moved.get("queued"):
            raise AssertionError(f"validated native LAN move was not queued: {moved}")
        move_status = wait_action_complete(HOST_PORT, moved["action_id"])
        if move_status.get("status") != "completed":
            raise AssertionError(f"validated native LAN move failed: {move_status}")
        host_sync, join_sync = wait_network_vehicle_match()
        synchronized_vehicle = next(
            (
                vehicle for vehicle in host_sync["vehicles"]
                if vehicle.get("id") == safe_move["unit_id"]
            ),
            None,
        )
        if synchronized_vehicle is None \
                or synchronized_vehicle.get("tile_id") != safe_move["target_tile_id"]:
            raise AssertionError(
                f"native LAN move did not reach the exact target on both peers: "
                f"{safe_move} / {host_sync} / {join_sync}"
            )

        # Validate a nonpersistent unit-finish action independently of
        # movement. The fresh choice tuple is mandatory because an upkeep
        # notice may have appeared after the preceding native action.
        host_ready, _, _, _ = resolve_opening_interactions_pair()
        skip_choices: dict | None = None
        skip_unit_id: int | None = None
        for unit in host_ready.get("ready_unit_refs", []):
            if unit["id"] == safe_move["unit_id"]:
                continue
            choices = wait_unit_choices(
                HOST_PORT, unit["id"], peer_port=JOIN_PORT,
            )
            if any(
                choice.get("command") == "skip_unit"
                for choice in choices.get("choices", [])
            ):
                skip_choices = choices
                skip_unit_id = unit["id"]
                break
        if skip_choices is None or skip_unit_id is None:
            raise AssertionError(f"no guarded LAN skip choice was available: {host_ready}")
        skipped = request(
            HOST_PORT,
            "semantic_command",
            command="skip_unit",
            unit_id=skip_unit_id,
            match_id=skip_choices["match_id"],
            session_id=skip_choices["session_id"],
            expected_revision=skip_choices["revision"],
        )
        if not skipped.get("ok") or skipped.get("command") != "skip_unit":
            raise AssertionError(f"guarded native LAN skip failed: {skipped}")
        host_sync, join_sync = wait_network_vehicle_match()
        skipped_vehicle = next(
            (vehicle for vehicle in host_sync["vehicles"] if vehicle["id"] == skip_unit_id),
            None,
        )
        if skipped_vehicle is None or skipped_vehicle.get("moves_spent", 0) <= 0:
            raise AssertionError(
                f"native LAN skip did not converge as spent: {skip_unit_id} / "
                f"{host_sync} / {join_sync}"
            )

        def execute_finish_action(
            port: int, peer_port: int, unit_id: int, command: str,
        ) -> tuple[dict, dict]:
            choices = wait_unit_choices(port, unit_id, peer_port=peer_port)
            if not any(
                choice.get("command") == command
                for choice in choices.get("choices", [])
            ):
                raise AssertionError(
                    f"{command} was not a fresh guarded choice for unit {unit_id}: {choices}"
                )
            result = request(
                port,
                "semantic_command",
                command=command,
                unit_id=unit_id,
                match_id=choices["match_id"],
                session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            if not result.get("ok") or result.get("command") != command:
                raise AssertionError(f"guarded LAN {command} failed: {result}")
            return wait_network_vehicle_match()

        # Resolve every remaining host unit individually; bulk skip remains
        # withheld in LAN so the model must inspect and guard each decision.
        while True:
            host_ready, _, _, _ = resolve_opening_interactions_pair()
            remaining = host_ready.get("ready_unit_refs", [])
            if not remaining:
                break
            execute_finish_action(
                HOST_PORT, JOIN_PORT, remaining[0]["id"], "skip_unit",
            )

        host_turn = wait_snapshot_predicate(
            HOST_PORT,
            lambda state: state.get("interaction", {}).get("kind") == "turn"
            and state.get("faction", {}).get("ready_units") == 0,
        )
        management = request(HOST_PORT, "semantic_choices", kind="game_management")
        management_commands = [
            choice.get("command") for choice in management.get("choices", [])
            if choice.get("command")
        ]
        if management_commands != ["end_turn"]:
            raise AssertionError(
                f"LAN game management exposed unvalidated commands: {management}"
            )
        ended = request(
            HOST_PORT,
            "semantic_command",
            command="end_turn",
            match_id=management["match_id"],
            session_id=management["session_id"],
            expected_revision=management["revision"],
        )
        if not ended.get("ok") or ended.get("command") != "end_turn":
            raise AssertionError(f"guarded native LAN end turn failed: {ended}")

        join_turn: dict = {}
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            _, join_turn, _, _ = resolve_opening_interactions_pair()
            if join_turn.get("interaction", {}).get("kind") == "turn":
                break
            request(HOST_PORT, "test_network_sync_status")
            request(JOIN_PORT, "test_network_sync_status")
            time.sleep(0.25)
        if join_turn.get("interaction", {}).get("kind") != "turn":
            raise AssertionError(f"native LAN turn did not transfer to joiner: {join_turn}")

        join_ready_ids = [
            unit["id"] for unit in join_turn.get("ready_unit_refs", [])
        ]
        if len(join_ready_ids) < 2:
            raise AssertionError(
                f"joiner lacked units needed for hold/sentry regression: {join_turn}"
            )
        host_sync, join_sync = execute_finish_action(
            JOIN_PORT, HOST_PORT, join_ready_ids[0], "hold_unit",
        )
        held = next(
            (vehicle for vehicle in join_sync["vehicles"] if vehicle["id"] == join_ready_ids[0]),
            None,
        )
        if held is None or held.get("order") != 2 or held.get("moves_spent", 0) <= 0:
            raise AssertionError(f"native LAN hold did not converge: {held}")

        host_sync, join_sync = execute_finish_action(
            JOIN_PORT, HOST_PORT, join_ready_ids[1], "sentry_unit",
        )
        sentried = next(
            (vehicle for vehicle in join_sync["vehicles"] if vehicle["id"] == join_ready_ids[1]),
            None,
        )
        if sentried is None or sentried.get("order") != 1 \
                or sentried.get("moves_spent", 0) <= 0:
            raise AssertionError(f"native LAN sentry did not converge: {sentried}")

        # Production is the first strategic (non-unit) mutation enabled in
        # multiplayer. Enumerate only native-buildable items, choose a
        # different one, and require the base record to converge on both peers.
        resolve_opening_interactions_pair()
        join_bases = request(JOIN_PORT, "list_bases")
        if not join_bases.get("items"):
            raise AssertionError(f"joiner has no fair-play owned base: {join_bases}")
        production_base = join_bases["items"][0]
        production_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="production",
            base_id=production_base["id"],
        )
        unvalidated_production_commands = {
            choice.get("command") for choice in production_choices.get("choices", [])
            if choice.get("command") and choice.get("command") != "set_production"
        }
        alternatives = [
            choice for choice in production_choices.get("choices", [])
            if choice.get("command") == "set_production"
            and choice.get("item_id") != production_base.get("production_id")
        ]
        if unvalidated_production_commands or not alternatives:
            raise AssertionError(
                f"LAN production choices were not exact/fail-closed: {production_choices}"
            )
        production_choice = alternatives[0]
        production_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_production",
            base_id=production_base["id"],
            item_id=production_choice["item_id"],
            match_id=production_choices["match_id"],
            session_id=production_choices["session_id"],
            expected_revision=production_choices["revision"],
        )
        if not production_result.get("ok") \
                or production_result.get("item_id") != production_choice["item_id"]:
            raise AssertionError(f"guarded LAN production change failed: {production_result}")
        host_sync, join_sync = wait_network_vehicle_match()
        synchronized_base = next(
            (
                base for base in join_sync["bases"]
                if base["id"] == production_base["id"]
            ),
            None,
        )
        if synchronized_base is None or synchronized_base.get("current_item_id") \
                != production_choice["item_id"]:
            raise AssertionError(
                f"native LAN production did not converge: {production_choice} / "
                f"{host_sync} / {join_sync}"
            )

        # Multiplayer base management is intentionally narrowed to the exact
        # native-synchronized queue subset. Append a real unit, verify both the
        # base queue and faction queue accounting, then remove that exact slot.
        queue_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_management",
            base_id=production_base["id"],
        )
        queue_commands = {
            choice.get("command") for choice in queue_choices.get("choices", [])
            if choice.get("command")
        }
        allowed_queue_commands = {
            "queue_production",
            "remove_queued_production",
            "clear_production_queue",
            "set_base_governor",
            "set_governor_permission",
        }
        queue_append = next(
            (
                choice for choice in queue_choices.get("choices", [])
                if choice.get("command") == "queue_production"
                and choice.get("kind") == "unit"
            ),
            None,
        )
        if not queue_commands.issubset(allowed_queue_commands) \
                or queue_append is None:
            raise AssertionError(
                f"LAN base management exposed a non-queue command or no unit: "
                f"{queue_choices}"
            )
        queue_append_result = request(
            JOIN_PORT,
            "semantic_command",
            command="queue_production",
            base_id=production_base["id"],
            item_id=queue_append["item_id"],
            match_id=queue_choices["match_id"],
            session_id=queue_choices["session_id"],
            expected_revision=queue_choices["revision"],
        )
        if not queue_append_result.get("ok") \
                or queue_append_result.get("queue_position") \
                != queue_append["queue_position"]:
            raise AssertionError(
                f"guarded LAN queue append failed: {queue_append_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        queued_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if queued_base.get("queue_size") != 1 \
                or queued_base.get("queue_items", [])[-1:] \
                != [queue_append["item_id"]]:
            raise AssertionError(
                f"native LAN queue append did not converge: "
                f"{queue_append} / {host_sync} / {join_sync}"
            )

        removal_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_management",
            base_id=production_base["id"],
        )
        queue_remove = next(
            (
                choice for choice in removal_choices.get("choices", [])
                if choice.get("command") == "remove_queued_production"
                and choice.get("queue_position") == queue_append["queue_position"]
                and choice.get("item_id") == queue_append["item_id"]
            ),
            None,
        )
        if queue_remove is None:
            raise AssertionError(
                f"fresh exact queue removal was absent: {removal_choices}"
            )
        queue_remove_result = request(
            JOIN_PORT,
            "semantic_command",
            command="remove_queued_production",
            base_id=production_base["id"],
            queue_position=queue_remove["queue_position"],
            match_id=removal_choices["match_id"],
            session_id=removal_choices["session_id"],
            expected_revision=removal_choices["revision"],
        )
        if not queue_remove_result.get("ok") \
                or queue_remove_result.get("removed_item_id") \
                != queue_append["item_id"]:
            raise AssertionError(
                f"guarded LAN queue removal failed: {queue_remove_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        restored_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if restored_base.get("queue_size") != 0 \
                or restored_base.get("queue_items") \
                != [restored_base.get("current_item_id")]:
            raise AssertionError(
                f"native LAN queue removal did not converge: "
                f"{host_sync} / {join_sync}"
            )

        governor_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_management",
            base_id=production_base["id"],
        )
        governor_choice = next(
            (
                choice for choice in governor_choices.get("choices", [])
                if choice.get("command") == "set_base_governor"
            ),
            None,
        )
        if governor_choice is None:
            raise AssertionError(
                f"exact LAN governor choice was absent: {governor_choices}"
            )
        governor_flags_before = restored_base.get("governor_flags")
        governor_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_base_governor",
            base_id=production_base["id"],
            active=governor_choice["active"],
            manage_citizens=governor_choice["manage_citizens"],
            manage_production=governor_choice["manage_production"],
            match_id=governor_choices["match_id"],
            session_id=governor_choices["session_id"],
            expected_revision=governor_choices["revision"],
        )
        if not governor_result.get("ok") or governor_result.get("changed") is not True:
            raise AssertionError(
                f"guarded LAN governor toggle failed: {governor_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        governed_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if governed_base.get("governor_flags") == governor_flags_before:
            raise AssertionError(
                f"native LAN governor state did not change: "
                f"{host_sync} / {join_sync}"
            )

        permission_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_management",
            base_id=production_base["id"],
        )
        permission_choice = next(
            (
                choice for choice in permission_choices.get("choices", [])
                if choice.get("command") == "set_governor_permission"
            ),
            None,
        )
        if permission_choice is None:
            raise AssertionError(
                f"exact LAN governor-permission choice was absent: {permission_choices}"
            )
        permission_flags_before = governed_base.get("governor_flags")
        permission_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_governor_permission",
            base_id=production_base["id"],
            governor_permission=permission_choice["governor_permission"],
            active=permission_choice["active"],
            match_id=permission_choices["match_id"],
            session_id=permission_choices["session_id"],
            expected_revision=permission_choices["revision"],
        )
        if not permission_result.get("ok"):
            raise AssertionError(
                f"guarded LAN governor permission failed: {permission_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        permission_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if permission_base.get("governor_flags") == permission_flags_before:
            raise AssertionError(
                f"native LAN governor permission did not change: "
                f"{host_sync} / {join_sync}"
            )

        # Manual citizen actions are invalid while the governor actively
        # manages citizens. Ensure the master switch is off through a fresh
        # exact choice, then convert one worked tile to a specialist and assign
        # that same citizen back to the original tile.
        manual_governor_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_management",
            base_id=production_base["id"],
        )
        manual_toggle = next(
            choice for choice in manual_governor_choices.get("choices", [])
            if choice.get("command") == "set_base_governor"
        )
        if manual_toggle.get("current_active") is True:
            disabled_governor = request(
                JOIN_PORT,
                "semantic_command",
                command="set_base_governor",
                base_id=production_base["id"],
                active=manual_toggle["active"],
                manage_citizens=manual_toggle["manage_citizens"],
                manage_production=manual_toggle["manage_production"],
                match_id=manual_governor_choices["match_id"],
                session_id=manual_governor_choices["session_id"],
                expected_revision=manual_governor_choices["revision"],
            )
            if not disabled_governor.get("ok"):
                raise AssertionError(
                    f"could not disable governor for manual citizens: "
                    f"{disabled_governor}"
                )
            host_sync, join_sync = wait_network_vehicle_match()

        citizen_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_citizens",
            base_id=production_base["id"],
        )
        worker_to_specialist = next(
            (
                choice for choice in citizen_choices.get("choices", [])
                if choice.get("command") == "convert_worker_to_specialist"
            ),
            None,
        )
        citizen_commands = {
            choice.get("command") for choice in citizen_choices.get("choices", [])
            if choice.get("command")
        }
        allowed_citizen_commands = {
            "convert_worker_to_specialist",
            "assign_specialist_to_tile",
            "set_specialist_type",
        }
        if citizen_choices.get("governor_manages_citizens") is not False \
                or not citizen_commands.issubset(allowed_citizen_commands) \
                or worker_to_specialist is None:
            raise AssertionError(
                f"LAN citizen choices were not exact/actionable: {citizen_choices}"
            )
        citizen_base_before = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        converted = request(
            JOIN_PORT,
            "semantic_command",
            command="convert_worker_to_specialist",
            base_id=production_base["id"],
            tile_index=worker_to_specialist["tile_index"],
            citizen_id=worker_to_specialist["citizen_id"],
            match_id=citizen_choices["match_id"],
            session_id=citizen_choices["session_id"],
            expected_revision=citizen_choices["revision"],
        )
        if not converted.get("ok"):
            raise AssertionError(
                f"guarded LAN worker-to-specialist failed: {converted}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        specialist_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if specialist_base.get("specialist_total") \
                != citizen_base_before.get("specialist_total", 0) + 1 \
                or specialist_base.get("specialist_types", [])[-1:] \
                != [worker_to_specialist["citizen_id"]]:
            raise AssertionError(
                f"native LAN specialist conversion did not converge: "
                f"{host_sync} / {join_sync}"
            )

        worker_choices = request(
            JOIN_PORT,
            "semantic_choices",
            kind="base_citizens",
            base_id=production_base["id"],
        )
        specialist_to_worker = next(
            (
                choice for choice in worker_choices.get("choices", [])
                if choice.get("command") == "assign_specialist_to_tile"
                and choice.get("tile_index") == worker_to_specialist["tile_index"]
            ),
            None,
        )
        if specialist_to_worker is None:
            raise AssertionError(
                f"fresh exact specialist-to-worker reversal absent: {worker_choices}"
            )
        reassigned = request(
            JOIN_PORT,
            "semantic_command",
            command="assign_specialist_to_tile",
            base_id=production_base["id"],
            specialist_index=specialist_to_worker["specialist_index"],
            tile_index=specialist_to_worker["tile_index"],
            match_id=worker_choices["match_id"],
            session_id=worker_choices["session_id"],
            expected_revision=worker_choices["revision"],
        )
        if not reassigned.get("ok"):
            raise AssertionError(
                f"guarded LAN specialist-to-worker failed: {reassigned}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        restored_citizen_base = next(
            base for base in join_sync["bases"]
            if base["id"] == production_base["id"]
        )
        if restored_citizen_base.get("specialist_total") \
                != citizen_base_before.get("specialist_total") \
                or restored_citizen_base.get("worked_tiles") \
                != citizen_base_before.get("worked_tiles"):
            raise AssertionError(
                f"native LAN specialist reversal did not converge: "
                f"{host_sync} / {join_sync}"
            )

        # Energy allocation is a faction-wide strategic mutation. Exercise a
        # returned semantic preset and require the native synch_alloc packet to
        # converge on the exact allocation for every faction on both peers.
        allocation_choices = request(
            JOIN_PORT, "semantic_choices", kind="energy_allocation",
        )
        current_allocation = allocation_choices.get("current", {})
        allocation_presets = allocation_choices.get("presets", [])
        allocation_choice = next(
            (
                preset for preset in allocation_presets
                if any(
                    preset.get(field) != current_allocation.get(field)
                    for field in ("economy", "psych", "labs")
                )
            ),
            None,
        )
        if allocation_choices.get("command") != "set_energy_allocation" \
                or allocation_choice is None:
            raise AssertionError(
                f"LAN energy allocation choices were not exact: {allocation_choices}"
            )
        allocation_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_energy_allocation",
            economy=allocation_choice["economy"],
            psych=allocation_choice["psych"],
            labs=allocation_choice["labs"],
            match_id=allocation_choices["match_id"],
            session_id=allocation_choices["session_id"],
            expected_revision=allocation_choices["revision"],
        )
        if not allocation_result.get("ok") \
                or allocation_result.get("allocation") != {
                    field: allocation_choice[field]
                    for field in ("economy", "psych", "labs")
                }:
            raise AssertionError(
                f"guarded LAN energy allocation failed: {allocation_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        join_faction_id = join_sync["local_faction_id"]
        synchronized_faction = next(
            (
                faction for faction in join_sync["factions"]
                if faction["id"] == join_faction_id
            ),
            None,
        )
        if synchronized_faction is None \
                or synchronized_faction.get("allocation") != {
                    field: allocation_choice[field]
                    for field in ("economy", "psych", "labs")
                }:
            raise AssertionError(
                f"native LAN energy allocation did not converge: "
                f"{allocation_choice} / {host_sync} / {join_sync}"
            )

        # Blind-research focus is independently mutable during a turn. Pick a
        # different returned focus and prove the stock synch_ai packet updates
        # the peer's faction record before any later turn transition.
        research_choices = request(
            JOIN_PORT, "semantic_choices", kind="research",
        )
        current_priority = synchronized_faction.get("research_priority")
        research_choice = next(
            (
                choice for choice in research_choices.get("choices", [])
                if choice.get("command") == "set_research_priority"
                and choice.get("priority") != current_priority
            ),
            None,
        )
        unvalidated_research_commands = {
            choice.get("command") for choice in research_choices.get("choices", [])
            if choice.get("command")
            and choice.get("command") != "set_research_priority"
        }
        if research_choices.get("blind") is not True \
                or unvalidated_research_commands or research_choice is None:
            raise AssertionError(
                f"LAN research-focus choices were not exact: {research_choices}"
            )
        research_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_research_priority",
            priority=research_choice["priority"],
            match_id=research_choices["match_id"],
            session_id=research_choices["session_id"],
            expected_revision=research_choices["revision"],
        )
        if not research_result.get("ok") \
                or research_result.get("priority") != research_choice["priority"]:
            raise AssertionError(
                f"guarded LAN research focus failed: {research_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        synchronized_faction = next(
            (
                faction for faction in join_sync["factions"]
                if faction["id"] == join_faction_id
            ),
            None,
        )
        if synchronized_faction is None \
                or synchronized_faction.get("research_priority") \
                != research_choice["priority"]:
            raise AssertionError(
                f"native LAN research focus did not converge: "
                f"{research_choice} / {host_sync} / {join_sync}"
            )

        # A normal turn-one game has no non-default social model. In the
        # contained test environment only, grant the same prerequisite to the
        # joiner's faction on both processes, then exercise a genuine paid
        # policy change through synch_soc and net_energy.
        social_fixture_host = request(
            HOST_PORT,
            "test_social_engineering_fixture",
            faction_id=join_faction_id,
        )
        social_fixture_join = request(
            JOIN_PORT,
            "test_social_engineering_fixture",
            faction_id=join_faction_id,
        )
        fixture_identity = {
            key: social_fixture_join.get(key)
            for key in (
                "category_id", "category", "model_id",
                "prerequisite_tech_id",
            )
        }
        if not social_fixture_host.get("ok") \
                or not social_fixture_join.get("ok") \
                or fixture_identity != {
                    key: social_fixture_host.get(key) for key in fixture_identity
                }:
            raise AssertionError(
                f"contained social fixtures diverged: "
                f"{social_fixture_host} / {social_fixture_join}"
            )
        social_choices = request(
            JOIN_PORT, "semantic_choices", kind="social_engineering",
        )
        social_command = social_choices.get("command", {})
        selected_social = social_choices.get("selected", {})
        desired_social = {
            key: selected_social.get(key, {}).get("model_id")
            for key in ("politics", "economics", "values", "future")
        }
        social_category = fixture_identity["category"]
        desired_social[social_category] = fixture_identity["model_id"]
        exact_model_available = any(
            category.get("key") == social_category
            and any(
                option.get("model_id") == fixture_identity["model_id"]
                for option in category.get("options", [])
            )
            for category in social_choices.get("categories", [])
        )
        if social_choices.get("enabled") is not True \
                or social_command.get("name") != "set_social_engineering" \
                or not exact_model_available \
                or any(value is None for value in desired_social.values()):
            raise AssertionError(
                f"LAN social-engineering choices were not exact: {social_choices}"
            )
        social_result = request(
            JOIN_PORT,
            "semantic_command",
            command="set_social_engineering",
            **desired_social,
            match_id=social_choices["match_id"],
            session_id=social_choices["session_id"],
            expected_revision=social_choices["revision"],
        )
        returned_social = {
            key: social_result.get("selected", {}).get(key, {}).get("model_id")
            for key in ("politics", "economics", "values", "future")
        }
        if not social_result.get("ok") or returned_social != desired_social:
            raise AssertionError(
                f"guarded LAN social-engineering change failed: {social_result}"
            )
        host_sync, join_sync = wait_network_vehicle_match()
        synchronized_faction = next(
            faction for faction in join_sync["factions"]
            if faction["id"] == join_faction_id
        )
        desired_social_list = [
            desired_social[key]
            for key in ("politics", "economics", "values", "future")
        ]
        if synchronized_faction.get("social_pending") != desired_social_list:
            raise AssertionError(
                f"native LAN social engineering did not converge: "
                f"{desired_social} / {host_sync} / {join_sync}"
            )

        host_chat = request(HOST_PORT, "semantic_chat", action="list")
        join_chat = request(JOIN_PORT, "semantic_chat", action="list")
        if not host_chat.get("can_send") or not join_chat.get("can_send"):
            raise AssertionError(f"native chat unavailable: {host_chat} / {join_chat}")
        host_text = f"host-to-join-{uuid.uuid4().hex[:12]}"
        sent_host = request(
            HOST_PORT,
            "semantic_chat",
            action="send",
            match_id=host_chat["identity"]["match_id"],
            session_id=host_chat["identity"]["session_id"],
            client_message_id=f"chat-{uuid.uuid4().hex}",
            text=host_text,
            recipient_faction_id=join_chat["local_faction_id"],
        )
        if not sent_host.get("ok") or not sent_host.get("sent"):
            raise AssertionError(f"host chat send failed: {sent_host}")
        wait_chat_text(JOIN_PORT, host_text)

        join_text = f"join-to-host-{uuid.uuid4().hex[:12]}"
        sent_join = request(
            JOIN_PORT,
            "semantic_chat",
            action="send",
            match_id=join_chat["identity"]["match_id"],
            session_id=join_chat["identity"]["session_id"],
            client_message_id=f"chat-{uuid.uuid4().hex}",
            text=join_text,
            recipient_faction_id=host_chat["local_faction_id"],
        )
        if not sent_join.get("ok") or not sent_join.get("sent"):
            raise AssertionError(f"join chat send failed: {sent_join}")
        wait_chat_text(HOST_PORT, join_text)

        print(json.dumps({
            "event": "pass",
            "host_bridge_port": HOST_PORT,
            "join_bridge_port": JOIN_PORT,
            "network_session_id": network_session_id,
            "discovered_exact_session": True,
            "joined_exact_session": True,
            "both_reached_stock_lobby": True,
            "small_easy_profile_synchronized": True,
            "guarded_profile_matrix_synchronized": [
                "tiny_citizen", "standard_librarian", "large_thinker",
                "huge_transcend", "small_easy",
            ],
            "client_readied_semantically": True,
            "host_started_semantically": True,
            "both_entered_native_match": True,
            "opening_planetfall_acknowledged_semantically": True,
            "opening_planetfall_instance_counts": {
                "host": host_planetfall_count,
                "join": join_planetfall_count,
            },
            "unvalidated_strategy_remained_fail_closed": True,
            "opening_research_prompts_resolved_semantically": True,
            "opening_research_priorities": {
                "host": host_after_research.get("research", {}).get("priority"),
                "join": join_after_research.get("research", {}).get("priority"),
            },
            "human_diplomacy_paired_semantically": True,
            "human_treaty_clause_synchronized": True,
            "human_treaty_accepted_and_synchronized": True,
            "safe_adjacent_unit_move_synchronized": True,
            "already_at_war_combat_synchronized": True,
            "skip_unit_synchronized": True,
            "hold_unit_synchronized": True,
            "sentry_unit_synchronized": True,
            "native_turn_transferred_to_joiner": True,
            "base_production_synchronized": True,
            "production_queue_append_remove_synchronized": True,
            "base_governor_and_permission_synchronized": True,
            "base_citizen_assignment_synchronized": True,
            "energy_allocation_synchronized": True,
            "research_focus_synchronized": True,
            "social_engineering_synchronized": True,
            "bidirectional_native_chat": True,
            "separate_process_session_ids": (
                host_lobby.get("identity", {}).get("session_id")
                != join_lobby.get("identity", {}).get("session_id")
            ),
            "host_lifecycle": host_game.get("lifecycle"),
            "join_lifecycle": join_game.get("lifecycle"),
            "map_dimensions": [
                host_game["game_settings"].get("map_width"),
                host_game["game_settings"].get("map_height"),
            ],
            "opening_decision_frames": {
                "host": {
                    "faction_id": host_snapshot.get("faction", {}).get("id"),
                    "interaction": host_snapshot.get("interaction", {}).get("kind"),
                    "popup_label": host_snapshot.get("interaction", {}).get("popup_label"),
                    "phase": host_snapshot.get("protocol", {}).get("phase"),
                    "can_command": host_snapshot.get("interaction", {}).get("can_command"),
                    "ready_units": host_snapshot.get("faction", {}).get("ready_units"),
                },
                "join": {
                    "faction_id": join_snapshot.get("faction", {}).get("id"),
                    "interaction": join_snapshot.get("interaction", {}).get("kind"),
                    "popup_label": join_snapshot.get("interaction", {}).get("popup_label"),
                    "phase": join_snapshot.get("protocol", {}).get("phase"),
                    "can_command": join_snapshot.get("interaction", {}).get("can_command"),
                    "ready_units": join_snapshot.get("faction", {}).get("ready_units"),
                },
            },
            "post_planetfall_decision_frames": {
                "host": {
                    "interaction": host_after_opening.get("interaction", {}).get("kind"),
                    "popup_label": host_after_opening.get("interaction", {}).get("popup_label"),
                    "phase": host_after_opening.get("protocol", {}).get("phase"),
                    "ready_units": host_after_opening.get("faction", {}).get("ready_units"),
                },
                "join": {
                    "interaction": join_after_opening.get("interaction", {}).get("kind"),
                    "popup_label": join_after_opening.get("interaction", {}).get("popup_label"),
                    "phase": join_after_opening.get("protocol", {}).get("phase"),
                    "ready_units": join_after_opening.get("faction", {}).get("ready_units"),
                },
            },
            "post_research_decision_frames": {
                "host": {
                    "interaction": host_after_research.get("interaction", {}).get("kind"),
                    "popup_label": host_after_research.get("interaction", {}).get("popup_label"),
                    "phase": host_after_research.get("protocol", {}).get("phase"),
                    "research_priority": host_after_research.get("research", {}).get("priority"),
                },
                "join": {
                    "interaction": join_after_research.get("interaction", {}).get("kind"),
                    "popup_label": join_after_research.get("interaction", {}).get("popup_label"),
                    "phase": join_after_research.get("protocol", {}).get("phase"),
                    "research_priority": join_after_research.get("research", {}).get("priority"),
                },
            },
            "pixels_or_ui_input_used": False,
        }, separators=(",", ":")))
        return 0
    finally:
        if pair_process.poll() is None:
            pair_process.terminate()
        terminate_test_instances()
        if join_display_process.poll() is None:
            join_display_process.terminate()
            try:
                join_display_process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                join_display_process.kill()


if __name__ == "__main__":
    sys.exit(main())
