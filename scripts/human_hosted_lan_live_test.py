#!/usr/bin/env python3
"""Opt-in native regression for a human-hosted LAN with two managed agents."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import uuid

from mixed_lan_live_test import execute_choice, resolve_opening_interactions, save_host, wait_for
from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


def host_fixture(manager: WorkerManager, instance_id: str, *, session_name: str,
                 resume_slot: str | None = None,
                 expected_faction_id: int | None = None) -> dict:
    manager.start_worker(instance_id, timeout=300)
    hosted = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="host",
        session_name=session_name, player_name="Alice",
        client_operation_id=f"human-host-{uuid.uuid4().hex[:20]}",
        timeout=140,
    )
    if not hosted.get("ok") or not hosted.get("lobby_launch_queued"):
        raise AssertionError(f"external human fixture could not host: {hosted}")
    lobby = wait_for(
        manager, instance_id, "semantic_lan",
        lambda value: value.get("lifecycle") == "lobby",
        timeout=60, action="status",
    )
    if resume_slot is None:
        configured = manager._native_request(  # noqa: SLF001
            instance_id, "semantic_lan", action="configure", profile="small_easy",
            match_id=lobby["identity"]["match_id"],
            session_id=lobby["identity"]["session_id"],
            expected_lobby_revision=lobby["lobby"]["revision"],
            client_operation_id=f"human-configure-{uuid.uuid4().hex[:20]}",
        )
        if not configured.get("ok"):
            raise AssertionError(f"external human fixture could not configure: {configured}")
        return wait_for(
            manager, instance_id, "semantic_lan",
            lambda value: value.get("lifecycle") == "lobby"
            and value.get("lobby", {}).get("settings", {}).get("profile") == "small_easy",
            timeout=60, action="status",
        )

    loaded = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="load_save", slot=resume_slot,
        match_id=lobby["identity"]["match_id"],
        session_id=lobby["identity"]["session_id"],
        expected_lobby_revision=lobby["lobby"]["revision"],
        client_operation_id=f"human-load-{uuid.uuid4().hex[:20]}",
    )
    if not loaded.get("ok"):
        raise AssertionError(f"external human fixture could not load: {loaded}")
    lobby = wait_for(
        manager, instance_id, "semantic_lan",
        lambda value: value.get("lifecycle") == "lobby"
        and value.get("lobby", {}).get("game_type") == "load",
        timeout=75, action="status",
    )
    local = next(
        (item for item in lobby["lobby"].get("participants", [])
         if item.get("local") is True), {},
    )
    required = local.get("required_faction_choice_id")
    if local.get("faction_id") != expected_faction_id or not isinstance(required, int):
        raise AssertionError(f"external host saved faction was not recoverable: {local}")
    if local.get("faction_choice_id") != required:
        selected = manager._native_request(  # noqa: SLF001
            instance_id, "semantic_lan", action="select_faction",
            faction_choice_id=required,
            match_id=lobby["identity"]["match_id"],
            session_id=lobby["identity"]["session_id"],
            expected_lobby_revision=lobby["lobby"]["revision"],
            client_operation_id=f"human-faction-{uuid.uuid4().hex[:20]}",
        )
        if not selected.get("ok"):
            raise AssertionError(f"external host faction selection failed: {selected}")
        lobby = wait_for(
            manager, instance_id, "semantic_lan",
            lambda value: next(
                (item for item in value.get("lobby", {}).get("participants", [])
                 if item.get("local") is True), {},
            ).get("faction_choice_id") == required,
            timeout=60, action="status",
        )
    return lobby


def start_from_human_host(manager: WorkerManager, instance_id: str,
                          expected_count: int) -> None:
    lobby = wait_for(
        manager, instance_id, "semantic_lan",
        lambda value: value.get("lifecycle") == "lobby"
        and value.get("lobby", {}).get("participant_count") == expected_count
        and value.get("lobby", {}).get("all_clients_ready") is True,
        timeout=90, action="status",
    )
    started = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="start",
        match_id=lobby["identity"]["match_id"],
        session_id=lobby["identity"]["session_id"],
        expected_lobby_revision=lobby["lobby"]["revision"],
        client_operation_id=f"human-native-start-{uuid.uuid4().hex[:20]}",
    )
    if not started.get("ok"):
        raise AssertionError(f"external human fixture could not start: {started}")


def exchange_bidirectional_chat(manager: WorkerManager, agent_instance: str,
                                human_instance: str, marker: str) -> None:
    human_chat = manager._native_request(  # noqa: SLF001
        human_instance, "semantic_chat", action="list",
    )
    human_faction = human_chat["local_faction_id"]
    agent_participant = next(
        item for item in human_chat.get("participants", []) if item.get("local") is False
    )
    agent_faction = agent_participant["faction_id"]
    human_text = f"Alice human-host test {marker}"
    sent_human = manager._native_request(  # noqa: SLF001
        human_instance, "semantic_chat", action="send",
        match_id=human_chat["identity"]["match_id"],
        session_id=human_chat["identity"]["session_id"],
        client_message_id=f"human-host-{marker}", text=human_text,
        recipient_faction_id=agent_faction,
    )
    if not sent_human.get("ok") or sent_human.get("sent") is not True:
        raise AssertionError(f"human-host chat send failed: {sent_human}")

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        agent_chat = manager._native_request(  # noqa: SLF001
            agent_instance, "semantic_chat", action="list",
        )
        participant = next(
            (item for item in agent_chat.get("participants", [])
             if item.get("player_name") == "Alice"), None,
        )
        received = next(
            (item for item in agent_chat.get("messages", [])
             if item.get("text") == human_text), None,
        )
        if participant and participant.get("faction_id") == human_faction \
                and received and received.get("sender_faction_id") == human_faction:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("managed agent did not receive attributed human-host chat")

    agent_chat = manager._native_request(  # noqa: SLF001
        agent_instance, "semantic_chat", action="list",
    )
    agent_text = f"Managed agent reply {marker}"
    sent_agent = manager._native_request(  # noqa: SLF001
        agent_instance, "semantic_chat", action="send",
        match_id=agent_chat["identity"]["match_id"],
        session_id=agent_chat["identity"]["session_id"],
        client_message_id=f"managed-reply-{marker}", text=agent_text,
        recipient_faction_id=human_faction,
    )
    if not sent_agent.get("ok") or sent_agent.get("sent") is not True:
        raise AssertionError(f"managed agent chat reply failed: {sent_agent}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        observed = manager._native_request(  # noqa: SLF001
            human_instance, "semantic_chat", action="list",
        )
        if any(item.get("text") == agent_text
               and item.get("sender_faction_id") == agent_faction
               for item in observed.get("messages", [])):
            return
        time.sleep(0.5)
    raise AssertionError("human host did not receive managed agent reply")


def main() -> int:
    assets = {
        "game": os.environ.get("SMACX_TEST_GAME_SOURCE"),
        "proton": os.environ.get("SMACX_TEST_PROTON_SOURCE"),
        "directx": os.environ.get("SMACX_TEST_DIRECTX_REDIST"),
    }
    if not all(assets.values()):
        print(json.dumps({"event": "skip", "reason": "missing_live_assets"}))
        return 0
    if any(not Path(str(value)).is_absolute() for value in assets.values()):
        raise SystemExit("live asset paths must be absolute")
    network_name = os.environ.get("SMACX_TEST_NETWORK")
    if not network_name:
        raise SystemExit("human-hosted live test requires its isolated wrapper network")

    docker_client = DockerClient()
    manager: WorkerManager | None = None
    runtime: dict | None = None
    workers: list[dict] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="smacx-human-host-live-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        try:
            manager = WorkerManager(
                control, docker_client, network_name=network_name,
                directx_redist_host_path=str(assets["directx"]),
            )
            source = manager.validate_game_source(
                str(assets["game"]), display_name="Human-host legal source",
            )
            runtime = manager.import_proton(
                str(assets["proton"]), display_name="Human-host Proton",
            )
            agents = [control.create_agent(f"Managed client {index + 1}") for index in range(2)]
            created = control.create_lan_match(
                "Human-hosted native LAN", [item["agent_id"] for item in agents],
                host_controller_kind="human", human_host_name="Alice",
                metadata={"lan_profile": "small_easy"},
            )
            for seat in created["seats"]:
                if seat["controller_kind"] != "agent":
                    continue
                workers.append(manager.provision_worker(
                    MemoryScope(
                        created["match"]["match_id"], str(seat["agent_id"]),
                        str(seat["perspective_id"]),
                    ),
                    source["game_source_id"], runtime["runtime_id"],
                    autostart={"enabled": False},
                ))

            fixture_agent = control.create_agent("Independent human host fixture")
            fixture = control.create_solo_match(
                "External host fixture lifecycle", fixture_agent["agent_id"], faction_id=1,
            )
            fixture_worker = manager.provision_worker(
                MemoryScope(
                    fixture["match"]["match_id"], fixture_agent["agent_id"],
                    fixture["perspective"]["perspective_id"],
                ),
                source["game_source_id"], runtime["runtime_id"],
                autostart={"enabled": False},
            )
            workers.append(fixture_worker)

            match_id = created["match"]["match_id"]
            prepared = manager.start_lan_match(match_id, timeout=600)
            if not prepared.get("awaiting_external_host"):
                raise AssertionError(f"human-hosted match was not prepared: {prepared}")
            human_lobby = host_fixture(
                manager, fixture_worker["instance_id"], session_name="Human Host Live",
            )
            host_address = manager._container_address(fixture_worker["instance_id"])  # noqa: SLF001
            discovered = manager.discover_human_hosted_lan_match(
                match_id, host_address=host_address,
            )
            network_session_id = human_lobby["identity"]["network_session_id"]
            if [item["network_session_id"] for item in discovered["sessions"]] \
                    != [network_session_id]:
                raise AssertionError(f"managed clients did not discover exact host: {discovered}")
            joined = manager.join_human_hosted_lan_match(
                match_id, host_address=host_address,
                network_session_id=network_session_id, timeout=300,
            )
            if not joined.get("awaiting_human_start"):
                raise AssertionError(f"managed agents did not join and ready: {joined}")
            start_from_human_host(manager, fixture_worker["instance_id"], 3)
            running = manager.finalize_human_hosted_lan_match(match_id, timeout=180)
            if not running.get("human_hosted") or len(running.get("seats", [])) != 3:
                raise AssertionError(f"human-owned Start was not finalized: {running}")

            agent_instances = [
                str(control.get_seat(match_id, index)["instance_id"])
                for index in (1, 2)
            ]
            all_peers = [fixture_worker["instance_id"], *agent_instances]
            resolve_opening_interactions(manager, all_peers)
            exchange_bidirectional_chat(
                manager, agent_instances[0], fixture_worker["instance_id"], "fresh",
            )
            original_factions = {
                int(seat["seat_index"]): int(seat["faction_id"])
                for seat in control.list_seats(match_id)
            }
            save_slot = "human_host_checkpoint"
            saved = save_host(manager, fixture_worker["instance_id"], save_slot)
            manager.park_match(match_id)
            manager.park_worker(fixture_worker["instance_id"])

            resumed_prepare = manager.start_lan_match(
                match_id, resume_slot=save_slot, timeout=600,
            )
            if not resumed_prepare.get("awaiting_external_host"):
                raise AssertionError(f"human-hosted resume was not prepared: {resumed_prepare}")
            resumed_lobby = host_fixture(
                manager, fixture_worker["instance_id"], session_name="Human Host Resume",
                resume_slot=save_slot, expected_faction_id=original_factions[0],
            )
            resumed_address = manager._container_address(  # noqa: SLF001
                fixture_worker["instance_id"]
            )
            resumed_session = resumed_lobby["identity"]["network_session_id"]
            manager.discover_human_hosted_lan_match(
                match_id, host_address=resumed_address,
            )
            manager.join_human_hosted_lan_match(
                match_id, host_address=resumed_address,
                network_session_id=resumed_session, timeout=300,
            )
            start_from_human_host(manager, fixture_worker["instance_id"], 3)
            resumed = manager.finalize_human_hosted_lan_match(match_id, timeout=180)
            restored_factions = {
                int(seat["seat_index"]): int(seat["faction_id"])
                for seat in control.list_seats(match_id)
            }
            if restored_factions != original_factions:
                raise AssertionError(
                    f"saved human-hosted seats changed: {original_factions} -> {restored_factions}"
                )
            resolve_opening_interactions(manager, all_peers)
            exchange_bidirectional_chat(
                manager, agent_instances[0], fixture_worker["instance_id"], "resumed",
            )
            manager.park_match(match_id)
            manager.park_worker(fixture_worker["instance_id"])

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "human_host_has_no_match_worker_or_mcp": True,
                    "two_managed_agents_discovered_exact_session": True,
                    "managed_agents_joined_and_readied": True,
                    "native_start_owned_by_external_human": True,
                    "bidirectional_chat_with_faction_identity": True,
                    "external_host_native_checkpoint": True,
                    "managed_disconnect_rejoin": True,
                    "all_saved_factions_restored": True,
                    "post_resume_bidirectional_chat": True,
                    "pixels_or_ui_input_used": False,
                    "saved_turn": saved.get("turn"),
                    "resumed_session_id": resumed.get("network_session_id"),
                },
            }, separators=(",", ":")))
        except Exception:
            failed = True
            raise
        finally:
            keep = failed and os.environ.get("SMACX_TEST_KEEP_ON_FAILURE") == "1"
            if keep:
                print(json.dumps({
                    "event": "kept_failed_resources", "network": network_name,
                    "installation_id": manager.installation_id if manager else None,
                }, separators=(",", ":")))
            if not keep and manager:
                for worker in reversed(workers):
                    try:
                        manager.park_worker(worker["instance_id"])
                    except Exception:
                        pass
                for worker in workers:
                    for volume_name, purpose in (
                        (worker["network"]["secret_volume"], "worker-secret"),
                        (worker["data_volume"], "worker-data"),
                    ):
                        try:
                            volume = docker_client.inspect_volume(volume_name)
                            docker_client.require_owned(
                                volume, manager.installation_id, purpose=purpose,
                            )
                            docker_client.remove_volume(volume_name)
                        except Exception:
                            pass
                if runtime:
                    try:
                        volume = docker_client.inspect_volume(runtime["storage_ref"])
                        docker_client.require_owned(
                            volume, manager.installation_id, purpose="proton-runtime",
                        )
                        docker_client.remove_volume(runtime["storage_ref"])
                    except Exception:
                        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
