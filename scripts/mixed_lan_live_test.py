#!/usr/bin/env python3
"""Opt-in native mixed-LAN regression with two agents and an external client.

The external client owns a separate durable fixture scope and has no seat,
perspective, MCP, or worker binding in the match under test. It uses the same
semantic bridge only to automate what a physical human will do manually during
exercise: discover, join by exact native session ID, choose the saved
faction, ready, exchange chat, disconnect, and rejoin.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from smacx_control import ControlPlane
from smacx_docker import DockerClient
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager


def docker(*arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"docker_{arguments[0]}_failed:{result.stderr.strip()[:1200]}"
        )
    return result.stdout.strip()


def wait_for(manager: WorkerManager, instance_id: str, operation: str,
             predicate, *, timeout: float, **arguments) -> dict:
    return manager._wait_native(  # noqa: SLF001 - native test seam
        instance_id, operation, predicate, timeout=timeout,
        poll_seconds=0.5, **arguments,
    )


def execute_choice(manager: WorkerManager, instance_id: str,
                   choices: dict, choice: dict, **extra) -> dict:
    permitted = {
        "response", "option", "phase", "priority", "name", "tech_id",
        "faction_choice_id",
    }
    arguments = {
        "command": choice["command"],
        "match_id": choices["match_id"],
        "session_id": choices["session_id"],
        "expected_revision": choices["revision"],
    }
    arguments.update({key: choice[key] for key in permitted if key in choice})
    arguments.update(extra)
    return manager._native_request(  # noqa: SLF001
        instance_id, "semantic_command", **arguments,
    )


def resolve_opening_interactions(manager: WorkerManager,
                                 instance_ids: list[str],
                                 *, maximum_rounds: int = 180) -> None:
    """Clear only the exact, known opening modals on every native peer.

    DirectPlay chat is not serviced while a peer remains inside the stock
    PLANETFALL/research modal loop. A physical player dismisses these dialogs
    before chatting; the fixture must do the semantic equivalent on all three
    processes or it is testing an impossible user sequence.
    """
    transient_errors = {
        "stale_state", "popup_transition_pending",
        "technology_presentation_changed",
    }
    active_kinds = {
        "waiting_for_engine", "research_priority", "popup",
        "technology_presentation",
    }
    settled_rounds = 0
    for _ in range(maximum_rounds):
        snapshots = {
            instance_id: manager._native_request(  # noqa: SLF001
                instance_id, "semantic_snapshot",
            ).get("snapshot", {})
            for instance_id in instance_ids
        }
        unresolved = False
        acted = False
        for instance_id, snapshot in snapshots.items():
            interaction = snapshot.get("interaction", {})
            kind = interaction.get("kind")
            if kind not in active_kinds:
                continue
            unresolved = True
            if kind == "waiting_for_engine":
                continue
            choices = manager._native_request(  # noqa: SLF001
                instance_id, "semantic_choices", kind="interaction",
            )
            legal = [
                item for item in choices.get("choices", [])
                if item.get("kind") not in {"information", "capability_status"}
            ]
            selected = None
            if kind == "research_priority" or (
                kind == "popup" and interaction.get("popup_label") == "TECHRANDOM"
            ):
                selected = next(
                    (item for item in legal
                     if item.get("command") == "choose_research_priority"
                     and item.get("priority") == 1),
                    None,
                )
            elif interaction.get("popup_label") in {"COMM", "COMMDIPLO"}:
                selected = next(
                    (item for item in legal
                     if item.get("command") == "respond_to_contact"
                     and item.get("response") == "decline"),
                    None,
                )
            elif str(interaction.get("popup_label") or "").startswith("INTRO"):
                if len(legal) == 1 \
                        and legal[0].get("command") in {
                            "continue_diplomacy", "acknowledge_popup",
                        }:
                    selected = legal[0]
            elif interaction.get("popup_label") == "DIPLO":
                selected = next(
                    (item for item in legal
                     if item.get("command") == "choose_diplomacy_option"
                     and item.get("option") == "finish"),
                    None,
                )
            elif len(legal) == 1 \
                    and legal[0].get("command") == "respond_to_diplomatic_offer" \
                    and legal[0].get("response") == "reject":
                selected = legal[0]
            elif len(legal) == 1 and legal[0].get("command") in {
                "acknowledge_popup", "advance_technology_presentation",
            }:
                selected = legal[0]
            if selected is None:
                raise AssertionError(
                    f"unexpected mixed-LAN opening interaction: {choices}"
                )
            result = execute_choice(manager, instance_id, choices, selected)
            if not result.get("ok"):
                if result.get("error", {}).get("code") in transient_errors:
                    continue
                raise AssertionError(f"opening interaction failed: {result}")
            acted = True
        if not unresolved:
            # Multiplayer startup advances each faction's initialization on
            # asynchronously delivered packets. A momentarily clear frame can
            # be followed by the next peer's TECHRANDOM modal, so require a
            # sustained nonmodal window before exercising chat or saving.
            settled_rounds += 1
            if settled_rounds >= 12:
                return
            time.sleep(0.5)
            continue
        settled_rounds = 0
        time.sleep(0.35 if acted else 0.6)
    raise AssertionError(f"mixed-LAN opening interactions did not settle: {snapshots}")


def save_host(manager: WorkerManager, instance_id: str, slot: str) -> dict:
    allowed_opening = {
        "acknowledge_popup", "choose_research_priority",
        "advance_technology_presentation",
    }
    for _ in range(30):
        interaction = manager._native_request(  # noqa: SLF001
            instance_id, "semantic_choices", kind="interaction",
        )
        if interaction.get("ok"):
            choice = next(
                (item for item in interaction.get("choices", [])
                 if item.get("command") in allowed_opening),
                None,
            )
            if choice:
                result = execute_choice(manager, instance_id, interaction, choice)
                if not result.get("ok"):
                    raise AssertionError(f"opening interaction failed: {result}")
                time.sleep(0.5)
                continue
        management = manager._native_request(  # noqa: SLF001
            instance_id, "semantic_choices", kind="game_management",
        )
        choice = next(
            (item for item in management.get("choices", [])
             if item.get("command") == "save_game"),
            None,
        )
        if management.get("ok") and choice:
            saved = execute_choice(
                manager, instance_id, management, choice, slot=slot,
            )
            if saved.get("ok") and saved.get("native_host") is True:
                return saved
            raise AssertionError(f"mixed native host save failed: {saved}")
        time.sleep(1)
    raise AssertionError("mixed native host never exposed save_game")


def join_external(manager: WorkerManager, instance_id: str, staged: dict,
                  *, player_name: str,
                  expected_faction_id: int | None = None) -> dict:
    manager.start_worker(instance_id, timeout=300)
    host_address = staged["external_join"]["host_address"]
    network_session_id = staged["network_session_id"]
    discovered = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="discover",
        host_address=host_address, timeout=140,
    )
    exact = [
        item for item in discovered.get("sessions", [])
        if item.get("network_session_id") == network_session_id
    ]
    if len(exact) != 1 or exact[0].get("joinable") is not True:
        raise AssertionError(f"external fixture did not discover exact lobby: {discovered}")
    joined = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="join",
        network_session_id=network_session_id,
        player_name=player_name, host_address=host_address,
        client_operation_id=f"external-join-{uuid.uuid4().hex[:20]}",
        timeout=140,
    )
    if not joined.get("ok") or not joined.get("joined"):
        raise AssertionError(f"external fixture did not join: {joined}")
    lobby = wait_for(
        manager, instance_id, "semantic_lan",
        lambda value: value.get("lifecycle") == "lobby"
        and value.get("identity", {}).get("network_session_id") == network_session_id
        and value.get("lobby", {}).get("participant_count") == 3,
        timeout=90, action="status",
    )
    local = next(
        (item for item in lobby["lobby"].get("participants", [])
         if item.get("local") is True),
        {},
    )
    if expected_faction_id is not None:
        required_choice = local.get("required_faction_choice_id")
        if local.get("faction_id") != expected_faction_id \
                or not isinstance(required_choice, int):
            raise AssertionError(
                f"loaded external faction was not recoverable: {local}"
            )
        if local.get("faction_choice_id") != required_choice:
            selected = manager._native_request(  # noqa: SLF001
                instance_id, "semantic_lan", action="select_faction",
                faction_choice_id=required_choice,
                match_id=lobby["identity"]["match_id"],
                session_id=lobby["identity"]["session_id"],
                expected_lobby_revision=lobby["lobby"]["revision"],
                client_operation_id=f"external-faction-{uuid.uuid4().hex[:20]}",
            )
            if not selected.get("ok"):
                raise AssertionError(f"external saved faction selection failed: {selected}")
            lobby = wait_for(
                manager, instance_id, "semantic_lan",
                lambda value: next(
                    (item for item in value.get("lobby", {}).get("participants", [])
                     if item.get("local") is True),
                    {},
                ).get("faction_choice_id") == required_choice,
                timeout=60, action="status",
            )
    ready = manager._native_request(  # noqa: SLF001
        instance_id, "semantic_lan", action="set_ready", ready=True,
        match_id=lobby["identity"]["match_id"],
        session_id=lobby["identity"]["session_id"],
        expected_lobby_revision=lobby["lobby"]["revision"],
        client_operation_id=f"external-ready-{uuid.uuid4().hex[:20]}",
    )
    if not ready.get("ok"):
        raise AssertionError(f"external fixture could not ready: {ready}")
    return lobby


def exchange_chat(manager: WorkerManager, host_instance: str,
                  external_instance: str, marker: str) -> None:
    external_chat = manager._native_request(  # noqa: SLF001
        external_instance, "semantic_chat", action="list",
    )
    sent = manager._native_request(  # noqa: SLF001
        external_instance, "semantic_chat", action="send",
        match_id=external_chat["identity"]["match_id"],
        session_id=external_chat["identity"]["session_id"],
        client_message_id=f"external-{marker}",
        text=f"Alice external test {marker}",
        recipient_faction_id=0,
    )
    if not sent.get("ok") or sent.get("sent") is not True:
        raise AssertionError(f"external chat send failed: {sent}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        host_chat = manager._native_request(  # noqa: SLF001
            host_instance, "semantic_chat", action="list",
        )
        participant = next(
            (item for item in host_chat.get("participants", [])
             if item.get("player_name") == "Alice"),
            None,
        )
        message = next(
            (item for item in host_chat.get("messages", [])
             if item.get("text") == f"Alice external test {marker}"),
            None,
        )
        if participant and isinstance(participant.get("faction_id"), int) and message \
                and message.get("sender_faction_id") == participant["faction_id"]:
            return
        time.sleep(0.5)
    raise AssertionError("host did not map external chat name to its connected faction")


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

    suffix = uuid.uuid4().hex[:10]
    configured_network = os.environ.get("SMACX_TEST_NETWORK")
    network_name = configured_network or f"smacx-mixed-live-{suffix}"
    owns_network = configured_network is None
    network_subnet = f"198.18.{int(suffix[:2], 16)}.0/24"
    docker_client = DockerClient()
    manager: WorkerManager | None = None
    runtime: dict | None = None
    workers: list[dict] = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="smacx-mixed-lan-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        try:
            if owns_network:
                docker(
                    "network", "create", "-d", "macvlan",
                    f"--subnet={network_subnet}",
                    "--label", "io.smacx.managed=true",
                    "--label", "io.smacx.purpose=mixed-lan-live-test",
                    network_name,
                )
            manager = WorkerManager(
                control, docker_client, network_name=network_name,
                directx_redist_host_path=str(assets["directx"]),
            )
            source = manager.validate_game_source(
                str(assets["game"]), display_name="Mixed LAN legal source",
            )
            runtime = manager.import_proton(
                str(assets["proton"]), display_name="Mixed LAN Proton",
            )
            agents = [
                control.create_agent(f"Mixed LAN agent {index + 1}")
                for index in range(2)
            ]
            created = control.create_lan_match(
                "Mixed native LAN live", [item["agent_id"] for item in agents],
                human_player_names=["Alice"],
                metadata={
                    "lan_profile": "small_easy",
                    "lan_session_name": "Mixed LAN Live",
                },
            )
            for seat in created["seats"]:
                if seat["controller_kind"] != "agent":
                    continue
                worker = manager.provision_worker(
                    MemoryScope(
                        created["match"]["match_id"], str(seat["agent_id"]),
                        str(seat["perspective_id"]),
                    ),
                    source["game_source_id"], runtime["runtime_id"],
                    autostart={"enabled": False},
                )
                workers.append(worker)

            fixture_agent = control.create_agent("External human fixture")
            fixture_match = control.create_solo_match(
                "External fixture lifecycle", fixture_agent["agent_id"], faction_id=1,
            )
            fixture_scope = MemoryScope(
                fixture_match["match"]["match_id"], fixture_agent["agent_id"],
                fixture_match["perspective"]["perspective_id"],
            )
            fixture_worker = manager.provision_worker(
                fixture_scope, source["game_source_id"], runtime["runtime_id"],
                autostart={"enabled": False},
            )
            workers.append(fixture_worker)

            match_id = created["match"]["match_id"]
            staged = manager.start_lan_match(
                match_id, session_name="Mixed LAN Live", timeout=600,
            )
            if not staged.get("awaiting_external_humans") \
                    or staged.get("pixels_or_ui_input_used") is not False:
                raise AssertionError(f"mixed lobby was not staged: {staged}")
            join_external(
                manager, fixture_worker["instance_id"], staged, player_name="Alice",
            )
            started = manager.finalize_external_lan_match(match_id, timeout=300)
            if not started.get("external_humans_connected") \
                    or len(started.get("seats", [])) != 3:
                raise AssertionError(f"mixed match did not start: {started}")
            wait_for(
                manager, fixture_worker["instance_id"], "semantic_lan",
                lambda value: value.get("lifecycle") == "game",
                timeout=120, action="status",
            )
            # The creation response predates worker provisioning; resolve the
            # durable assignment after all managed seats have workers.
            host_instance = str(control.get_seat(match_id, 0)["instance_id"])
            native_peers = [
                str(control.get_seat(match_id, index)["instance_id"])
                for index in range(2)
            ] + [fixture_worker["instance_id"]]
            resolve_opening_interactions(manager, native_peers)
            exchange_chat(
                manager, host_instance, fixture_worker["instance_id"], "fresh",
            )
            human_seat = control.get_seat(match_id, 2)
            human_faction = human_seat.get("faction_id")
            if not isinstance(human_faction, int):
                raise AssertionError("external human faction was not persisted")

            save_slot = "mixed_lan_checkpoint"
            saved = save_host(manager, host_instance, save_slot)
            manager.park_match(match_id)
            manager.park_worker(fixture_worker["instance_id"])

            resumed_stage = manager.start_lan_match(
                match_id, session_name="Mixed LAN Resume",
                resume_slot=save_slot, timeout=600,
            )
            join_external(
                manager, fixture_worker["instance_id"], resumed_stage,
                player_name="Alice", expected_faction_id=human_faction,
            )
            resumed = manager.finalize_external_lan_match(match_id, timeout=300)
            if resumed.get("resume_slot") != save_slot \
                    or control.get_seat(match_id, 2).get("faction_id") != human_faction:
                raise AssertionError(f"mixed checkpoint did not restore human seat: {resumed}")
            wait_for(
                manager, fixture_worker["instance_id"], "semantic_lan",
                lambda value: value.get("lifecycle") == "game",
                timeout=120, action="status",
            )
            resolve_opening_interactions(manager, native_peers)
            exchange_chat(
                manager, host_instance, fixture_worker["instance_id"], "resumed",
            )
            manager.park_match(match_id)
            manager.park_worker(fixture_worker["instance_id"])

            print(json.dumps({
                "event": "pass",
                "payload": {
                    "two_managed_agents": True,
                    "external_client_has_no_match_seat_or_mcp": True,
                    "native_macvlan_discover_join": True,
                    "exact_human_name_and_faction_mapping": True,
                    "bidirectional_identity_ready_gate": True,
                    "human_chat_mapped_to_connected_faction": True,
                    "native_host_checkpoint": True,
                    "external_disconnect_rejoin": True,
                    "saved_human_faction_restored": True,
                    "post_resume_chat": True,
                    "pixels_or_ui_input_used": False,
                    "saved_turn": saved.get("turn"),
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
            if not keep and owns_network:
                docker("network", "rm", network_name, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
