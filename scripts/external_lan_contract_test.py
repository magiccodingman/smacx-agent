#!/usr/bin/env python3
"""Contained contract regression for mixed human/agent LAN finalization."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_control import ControlPlane
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager, WorkerManagerError


class FakeDocker:
    def __init__(self, driver: str = "macvlan", labels: dict | None = None) -> None:
        self.driver = driver
        self.labels = labels or {}

    def inspect_network(self, name: str) -> dict:
        return {
            "Name": name,
            "Driver": self.driver,
            "Internal": False,
            "Scope": "local",
            "Labels": self.labels,
        }


class ContractManager(WorkerManager):
    def __init__(self, control: ControlPlane, docker: FakeDocker) -> None:
        super().__init__(control, docker, network_name="smacx-player-lan")
        self.start_calls = 0
        self.host_lobby: dict = {}

    def _native_request(self, instance_id: str, operation: str, **arguments):
        if operation == "semantic_lan" and arguments.get("action") == "status":
            return self.host_lobby
        if operation == "semantic_lan" and arguments.get("action") == "start":
            self.start_calls += 1
            return {"ok": True}
        if operation == "semantic_snapshot":
            return {"ok": True, "snapshot": {
                "turn": 42, "year": 2142,
                "faction": {"id": 1, "name": "Gaians"},
                "outcome": {
                    "game_completed": True, "final_score_completed": True,
                    "victory_type": "economic_solo", "perspective_result": "win",
                },
            }}
        raise AssertionError(f"unexpected native request: {operation} {arguments}")

    def worker_status(self, instance_id: str):
        return {"running": True, "health": "healthy", "instance_id": instance_id}

    def _wait_native(self, instance_id: str, operation: str, predicate, **arguments):
        if operation == "semantic_lan":
            value = {"ok": True, "lifecycle": "game"}
        elif operation == "semantic_snapshot":
            value = {"ok": True, "snapshot": {"faction": {"id": 1, "name": "Gaians"}}}
        else:
            raise AssertionError(f"unexpected wait operation: {operation}")
        if not predicate(value):
            raise AssertionError(f"contract fixture did not satisfy {operation} predicate")
        return value


def participant(name: str, index: int, faction: int, *, ready: bool) -> dict:
    return {
        "player_index": index,
        "name": name,
        "local": index == 1,
        "host": index == 1,
        "ready": ready,
        "faction_id": faction,
        "faction_choice_id": faction,
        "required_faction_choice_id": faction,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-external-lan-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        agent = control.create_agent("Agent host")
        created = control.create_lan_match(
            "Mixed contract", [agent["agent_id"]], human_player_names=["Alice"],
        )
        seats = created["seats"]
        if [seat["controller_kind"] for seat in seats] != ["agent", "human"] \
                or seats[1]["agent_id"] is not None \
                or seats[1]["perspective_id"] is not None:
            raise AssertionError("external human seat received an agent identity or perspective")

        scope = MemoryScope(
            created["match"]["match_id"], agent["agent_id"], seats[0]["perspective_id"],
        )
        instance = control.store.register_instance(
            instance_id="instance-external-host", worker_kind="container-linux",
            scope=scope, runtime_root="worker-data",
        )
        game = control.register_game_source(
            "Legal source", "/legal/game", "a" * 64,
        )
        runtime = control.register_runtime(
            "Proton", "docker-volume", "runtime-volume", content_fingerprint="b" * 64,
        )
        bridge = control.vault.put(
            "worker.instance-external-host.bridge_token", "bridge-secret",
        )
        control.put_worker_spec(
            instance["instance_id"], game["game_source_id"], runtime["runtime_id"],
            "smacx-agent-worker:dev", "external-host-worker", "external-host-data",
            bridge["secret_id"], network={"secret_volume": "external-secret"},
        )
        control.assign_instance_to_seat(
            scope.match_id, scope.agent_id, scope.perspective_id, instance["instance_id"],
        )
        external = {
            "network": {"name": "smacx-player-lan", "driver": "macvlan"},
            "host_address": "192.0.2.44",
            "session_name": "Mixed Contract",
            "human_players": [{"seat_index": 1, "player_name": "Alice"}],
            "resume_slot": None,
        }
        control.update_match_lifecycle(
            scope.match_id, "lobby", host_instance_id=instance["instance_id"],
            metadata={"network_session_id": "network-contract", "external_lan": external},
        )
        manager = ContractManager(control, FakeDocker())
        manager.host_lobby = {
            "ok": True,
            "lifecycle": "lobby",
            "identity": {
                "match_id": scope.match_id,
                "session_id": "session-contract",
                "network_session_id": "network-contract",
            },
            "lobby": {
                "revision": "lobby-contract-1",
                "participants": [
                    participant("Semantic Host", 1, 1, ready=True),
                    participant("Alice", 2, 2, ready=False),
                ],
                "all_clients_ready": False,
            },
        }
        waiting = manager.finalize_external_lan_match(scope.match_id)
        if not waiting.get("awaiting_external_humans") \
                or manager.start_calls != 0 \
                or waiting["external_join"]["blockers"][0]["reason"] != "not_ready":
            raise AssertionError("unready external human did not block native start")

        manager.host_lobby["lobby"]["participants"].append(
            participant("Mallory", 3, 3, ready=True)
        )
        try:
            manager.finalize_external_lan_match(scope.match_id)
        except WorkerManagerError as exc:
            if "external_lan_participant_identity_mismatch" not in str(exc):
                raise
        else:
            raise AssertionError("unexpected external participant was accepted")
        manager.host_lobby["lobby"]["participants"].pop()

        control.update_lan_seat(scope.match_id, 1, faction_id=3)
        external["resume_slot"] = "checkpoint"
        control.update_match_lifecycle(
            scope.match_id, "lobby", metadata={"external_lan": external},
        )
        manager.host_lobby["lobby"]["participants"][1]["ready"] = True
        manager.host_lobby["lobby"]["all_clients_ready"] = True
        wrong_faction = manager.finalize_external_lan_match(scope.match_id)
        reasons = [item["reason"] for item in wrong_faction["external_join"]["blockers"]]
        if "saved_faction_not_restored" not in reasons or manager.start_calls != 0:
            raise AssertionError("wrong saved human faction did not block resume")

        external["resume_slot"] = None
        control.update_match_lifecycle(
            scope.match_id, "lobby", metadata={"external_lan": external},
        )
        started = manager.finalize_external_lan_match(scope.match_id)
        final_seats = control.list_seats(scope.match_id)
        if not started.get("external_humans_connected") \
                or manager.start_calls != 1 \
                or control.get_match(scope.match_id)["status"] != "running" \
                or final_seats[1]["faction_id"] != 2 \
                or final_seats[1]["metadata"].get("network_join_pending") is not False:
            raise AssertionError("validated mixed LAN did not enter durable running state")

        status = manager.lan_match_status(scope.match_id)
        outcome = status["seats"][0].get("outcome", {})
        stored_outcome = control.get_seat(scope.match_id, 0)["metadata"].get("outcome", {})
        if outcome.get("perspective_result") != "win" \
                or stored_outcome.get("victory_type") != "economic_solo" \
                or control.get_match(scope.match_id)["status"] != "completed" \
                or control.get_match(scope.match_id)["last_turn"] != 42:
            raise AssertionError("native outcome was not mirrored without inference")

        rejected = ContractManager(control, FakeDocker("bridge"))
        try:
            rejected._external_lan_network()
        except WorkerManagerError as exc:
            if str(exc) != "external_lan_requires_player_lan_transport":
                raise
        else:
            raise AssertionError("private bridge network was published as an external LAN")

        routed = ContractManager(control, FakeDocker("bridge", {
            "io.smacx.player-lan": "true",
            "io.smacx.transport": "tailscale-routed",
        }))
        if routed._external_lan_network()["transport"] != "tailscale-routed":
            raise AssertionError("labeled routed player LAN was rejected")

        print(json.dumps({
            "event": "pass",
            "payload": {
                "agent_hosted_external_client_path": True,
                "human_has_no_agent_perspective": True,
                "exact_player_names": True,
                "unexpected_player_rejected": True,
                "readiness_guarded": True,
                "saved_faction_guarded": True,
                "native_outcome_mirrored": True,
                "physical_or_firewalled_routed_player_lan_required": True,
                "pixels_or_ui_input_used": False,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
