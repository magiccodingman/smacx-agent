#!/usr/bin/env python3
"""Verify pre-resolved faction-leader identities across two real SMACX processes.

DirectPlay accepts SetPlayerName after a participant joins but Wine's SMACX
session does not publish the replacement.  Managed identities must therefore
be resolved before host/join.  This test proves that the native new-game
faction selector can be driven semantically while those names remain stable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import uuid

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))

from lan_two_client_join_test import (  # noqa: E402
    HOST_PORT,
    JOIN_PORT,
    launch_join_display,
    launch_pair,
    request,
    terminate_test_instances,
    wait_game,
    wait_lobby,
    wait_lobby_predicate,
)


def select_faction(port: int, lobby: dict, choice_id: int) -> dict:
    identity = lobby["identity"]
    return request(
        port,
        "semantic_lan",
        action="select_faction",
        faction_choice_id=choice_id,
        match_id=identity["match_id"],
        session_id=identity["session_id"],
        expected_lobby_revision=lobby["lobby"]["revision"],
        client_operation_id=f"faction-{uuid.uuid4().hex}",
    )


def participant_names(lobby: dict) -> list[str]:
    return [item["name"] for item in lobby["lobby"]["participants"]]


def drop_player(port: int, lobby: dict, player_index: int, name: str) -> dict:
    identity = lobby["identity"]
    return request(
        port, "semantic_lan", action="drop_player",
        player_index=player_index, expected_player_name=name,
        match_id=identity["match_id"], session_id=identity["session_id"],
        expected_lobby_revision=lobby["lobby"]["revision"],
        client_operation_id=f"drop-{uuid.uuid4().hex}",
    )


def main() -> int:
    join_display = launch_join_display()
    pair = launch_pair()
    try:
        hosted = request(
            HOST_PORT,
            "semantic_lan",
            timeout=140,
            action="host",
            session_name="SMACX Identity Probe",
            player_name="Deirdre",
            client_operation_id=f"host-{uuid.uuid4().hex}",
        )
        if not hosted.get("ok"):
            raise AssertionError(f"host failed: {hosted}")
        host_lobby = wait_lobby(HOST_PORT)
        network_session_id = host_lobby["identity"]["network_session_id"]
        discovered = request(
            JOIN_PORT,
            "semantic_lan",
            timeout=140,
            action="discover",
            host_address="127.0.0.1",
        )
        if not any(
            item.get("network_session_id") == network_session_id
            for item in discovered.get("sessions", [])
        ):
            raise AssertionError(f"session discovery failed: {discovered}")
        joined = request(
            JOIN_PORT,
            "semantic_lan",
            timeout=140,
            action="join",
            network_session_id=network_session_id,
            player_name="Chairman Yang",
            host_address="127.0.0.1",
            client_operation_id=f"join-{uuid.uuid4().hex}",
        )
        if not joined.get("ok") or not joined.get("joined"):
            raise AssertionError(f"join failed: {joined}")
        host_lobby = wait_lobby_predicate(
            HOST_PORT, lambda value: value.get("participant_count") == 2,
        )
        join_lobby = wait_lobby_predicate(
            JOIN_PORT, lambda value: value.get("participant_count") == 2,
        )
        expected_names = {"Deirdre", "Chairman Yang"}
        for label, lobby in (("host", host_lobby), ("join", join_lobby)):
            if set(participant_names(lobby)) != expected_names:
                raise AssertionError(f"{label} pre-resolved names drifted: {lobby}")
        if os.environ.get("SMACX_TEST_DROP_ONLY") == "1":
            removed = drop_player(HOST_PORT, host_lobby, 2, "Chairman Yang")
            if not removed.get("ok"):
                raise AssertionError(f"host could not remove guarded participant: {removed}")
            wait_lobby_predicate(
                HOST_PORT, lambda value: value.get("participant_count") == 1,
            )
            print(json.dumps({
                "event": "pass",
                "guarded_native_participant_removal": True,
                "blocking_confirmation_used": False,
                "pixels_or_ui_input_used": False,
            }, separators=(",", ":")))
            return 0
        host_selected = select_faction(HOST_PORT, host_lobby, 0)
        if not host_selected.get("ok"):
            raise AssertionError(f"host faction selection failed: {host_selected}")
        host_lobby = wait_lobby_predicate(
            HOST_PORT,
            lambda value: value["participants"][0].get("faction_choice_id") == 0,
        )
        join_lobby = wait_lobby_predicate(
            JOIN_PORT,
            lambda value: value["participants"][0].get("faction_choice_id") == 0,
        )

        duplicate = select_faction(JOIN_PORT, join_lobby, 0)
        if duplicate.get("error", {}).get("code") not in {
            "invalid_lan_faction_choice", "lan_faction_already_selected",
        }:
            raise AssertionError(f"duplicate faction was not rejected: {duplicate}")

        join_lobby = wait_lobby(JOIN_PORT)
        join_selected = select_faction(JOIN_PORT, join_lobby, 1)
        if not join_selected.get("ok"):
            raise AssertionError(f"join faction selection failed: {join_selected}")
        join_lobby = wait_lobby_predicate(
            JOIN_PORT,
            lambda value: value["participants"][1].get("faction_choice_id") == 1,
        )

        join_identity = join_lobby["identity"]
        ready = request(
            JOIN_PORT,
            "semantic_lan",
            action="set_ready",
            ready=True,
            match_id=join_identity["match_id"],
            session_id=join_identity["session_id"],
            expected_lobby_revision=join_lobby["lobby"]["revision"],
            client_operation_id=f"ready-{uuid.uuid4().hex}",
        )
        if not ready.get("ok"):
            raise AssertionError(f"ready failed: {ready}")
        host_lobby = wait_lobby_predicate(
            HOST_PORT, lambda value: value.get("all_clients_ready") is True,
        )
        host_identity = host_lobby["identity"]
        started = request(
            HOST_PORT,
            "semantic_lan",
            action="start",
            match_id=host_identity["match_id"],
            session_id=host_identity["session_id"],
            expected_lobby_revision=host_lobby["lobby"]["revision"],
            client_operation_id=f"start-{uuid.uuid4().hex}",
        )
        if not started.get("ok"):
            raise AssertionError(f"start failed: {started}")
        wait_game(HOST_PORT)
        wait_game(JOIN_PORT)
        host_snapshot = request(HOST_PORT, "semantic_snapshot")
        join_snapshot = request(JOIN_PORT, "semantic_snapshot")
        faction_names = [
            host_snapshot.get("snapshot", {}).get("faction", {}).get("name"),
            join_snapshot.get("snapshot", {}).get("faction", {}).get("name"),
        ]
        if faction_names != ["Gaia's Stepdaughters", "Human Hive"]:
            raise AssertionError(
                f"selected faction identities did not survive game start: {faction_names}"
            )
        host_chat = request(HOST_PORT, "semantic_chat", action="list")
        join_chat = request(JOIN_PORT, "semantic_chat", action="list")
        for label, chat in (("host", host_chat), ("join", join_chat)):
            names = {item["player_name"] for item in chat.get("participants", [])}
            if names != {"Deirdre", "Chairman Yang"}:
                raise AssertionError(f"{label} game identity drifted: {chat}")

        print(json.dumps({
            "event": "pass",
            "pre_resolved_names_propagated_to_both_lobbies": True,
            "pre_resolved_names_survived_game_start": True,
            "semantic_new_game_faction_selection": True,
            "duplicate_faction_rejected": True,
            "final_faction_names": faction_names,
            "final_player_names": ["Deirdre", "Chairman Yang"],
            "pixels_or_ui_input_used": False,
        }, separators=(",", ":")))
        return 0
    finally:
        terminate_test_instances()
        pair.terminate()
        try:
            pair.wait(timeout=10)
        except Exception:
            pair.kill()
        join_display.terminate()
        try:
            join_display.wait(timeout=5)
        except Exception:
            join_display.kill()


if __name__ == "__main__":
    raise SystemExit(main())
