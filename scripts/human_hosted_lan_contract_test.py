#!/usr/bin/env python3
"""Contained contract regression for a human-owned native LAN lobby."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from smacx_control import ControlPlane
from smacx_store import MemoryScope, SmacxStore
from smacx_worker_manager import WorkerManager, WorkerManagerError


class FakeDocker:
    def inspect_network(self, name: str) -> dict:
        return {"Name": name, "Driver": "macvlan", "Internal": False, "Scope": "local"}


class HumanHostManager(WorkerManager):
    def __init__(self, control: ControlPlane) -> None:
        super().__init__(control, FakeDocker(), network_name="smacx-player-lan")
        self.lifecycle = "menu"
        self.started_workers: list[str] = []
        self.joined: dict[str, dict] = {}
        self.native_start_calls = 0
        self.session_id = "11111111-2222-3333-4444-555555555555"
        self.host_address = "192.0.2.55"
        self.humans = [
            self._participant("Alice", 1, 1, host=True, ready=True),
            self._participant("Bob", 4, 4, host=False, ready=True),
        ]

    @staticmethod
    def _participant(name: str, index: int, faction: int, *,
                     host: bool, ready: bool, local: bool = False) -> dict:
        return {
            "player_index": index, "name": name, "host": host, "local": local,
            "ready": ready, "faction_id": faction,
            "faction_choice_id": faction,
            "required_faction_choice_id": faction,
        }

    def start_worker(self, instance_id: str, **_arguments) -> dict:
        self.started_workers.append(instance_id)
        return {"ok": True, "status": "running", "instance_id": instance_id}

    def _participants(self, instance_id: str) -> list[dict]:
        participants = [dict(item) for item in self.humans]
        participants.extend(dict(item) for item in self.joined.values())
        for item in participants:
            item["local"] = self.joined.get(instance_id, {}).get("name") == item["name"]
        return sorted(participants, key=lambda item: int(item["player_index"]))

    def _lobby(self, instance_id: str) -> dict:
        return {
            "ok": True, "lifecycle": self.lifecycle,
            "identity": {
                "match_id": self.control.get_worker_spec(instance_id)["match_id"],
                "session_id": f"session-{instance_id[-8:]}",
                "network_session_id": self.session_id,
            },
            "lobby": {
                "revision": f"revision-{len(self.joined)}-{instance_id[-4:]}",
                "participant_count": len(self._participants(instance_id)),
                "participants": self._participants(instance_id),
                "session_name": "Alice's Planet",
                "game_type": "new",
            },
        }

    def _native_request(self, instance_id: str, operation: str, **arguments):
        if operation == "semantic_lan" and arguments.get("action") == "discover":
            if arguments.get("host_address") != self.host_address:
                return {"ok": True, "sessions": []}
            return {"ok": True, "sessions": [{
                "network_session_id": self.session_id,
                "session_name": "Alice's Planet", "joinable": True,
            }]}
        if operation == "semantic_lan" and arguments.get("action") == "join":
            seat = next(
                item for item in self.control.list_seats(
                    self.control.get_worker_spec(instance_id)["match_id"]
                ) if item.get("instance_id") == instance_id
            )
            index = int(seat["seat_index"])
            self.joined[instance_id] = self._participant(
                str(arguments["player_name"]), index + 1, index + 1,
                host=False, ready=False, local=True,
            )
            self.lifecycle = "lobby"
            return {"ok": True, "joined": True}
        if operation == "semantic_lan" and arguments.get("action") == "status":
            return self._lobby(instance_id)
        if operation == "semantic_lan" and arguments.get("action") == "set_ready":
            self.joined[instance_id]["ready"] = True
            return {"ok": True, "ready": True}
        if operation == "semantic_lan" and arguments.get("action") == "start":
            self.native_start_calls += 1
            raise AssertionError("managed client attempted to start a human-owned lobby")
        if operation == "semantic_chat" and arguments.get("action") == "list":
            return {
                "ok": True,
                "participants": [{
                    "player_id": item["player_index"] + 100,
                    "player_name": item["name"],
                    "faction_id": item["faction_id"],
                    "faction_name": f"Faction {item['faction_id']}",
                    "local": item["local"],
                } for item in self._participants(instance_id)],
                "messages": [],
            }
        raise AssertionError(f"unexpected native request: {operation} {arguments}")

    def _wait_native(self, instance_id: str, operation: str, predicate, **_arguments):
        if operation == "semantic_lan":
            value = self._lobby(instance_id)
        elif operation == "semantic_snapshot":
            faction = self.joined[instance_id]["faction_id"]
            value = {"ok": True, "snapshot": {
                "faction": {"id": faction, "name": f"Faction {faction}"},
            }}
        else:
            raise AssertionError(f"unexpected native wait: {operation}")
        if not predicate(value):
            raise WorkerManagerError(
                "native_semantic_lan_human_host_start_transition_timeout:{}"
            )
        return value


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-human-host-contract-") as temporary:
        root = Path(temporary)
        control = ControlPlane(SmacxStore(root / "state.sqlite3"), root / "secrets")
        agents = [control.create_agent(f"Agent {index + 1}") for index in range(2)]
        created = control.create_lan_match(
            "Human hosted contract", [item["agent_id"] for item in agents],
            host_controller_kind="human", human_host_name="Alice",
            human_player_names=["Bob"],
        )
        seats = created["seats"]
        if [seat["controller_kind"] for seat in seats] != [
            "human", "agent", "agent", "human",
        ] or seats[0]["metadata"].get("role") != "host" \
                or seats[0].get("instance_id") is not None:
            raise AssertionError("human host did not own seat zero without a worker")

        for seat in seats:
            if seat["controller_kind"] != "agent":
                continue
            scope = MemoryScope(
                created["match"]["match_id"], str(seat["agent_id"]),
                str(seat["perspective_id"]),
            )
            instance = control.store.register_instance(
                instance_id=f"instance-human-host-{seat['seat_index']}",
                worker_kind="container-linux", scope=scope,
                runtime_root=f"worker-data-{seat['seat_index']}",
            )
            bridge = control.vault.put(
                f"worker.{instance['instance_id']}.bridge_token", "bridge-secret",
            )
            game = control.register_game_source(
                f"Legal source {seat['seat_index']}", f"/legal/game-{seat['seat_index']}",
                str(seat["seat_index"]) * 64,
            )
            runtime = control.register_runtime(
                f"Runtime {seat['seat_index']}", "docker-volume",
                f"runtime-{seat['seat_index']}", content_fingerprint="f" * 64,
            )
            control.put_worker_spec(
                instance["instance_id"], game["game_source_id"], runtime["runtime_id"],
                "smacx-agent-worker:dev", f"worker-{seat['seat_index']}",
                f"data-{seat['seat_index']}", bridge["secret_id"],
                network={"secret_volume": f"secret-{seat['seat_index']}"},
            )
            control.assign_instance_to_seat(
                scope.match_id, scope.agent_id, scope.perspective_id,
                instance["instance_id"],
            )

        manager = HumanHostManager(control)
        prepared = manager.start_lan_match(created["match"]["match_id"])
        if not prepared.get("awaiting_external_host") \
                or prepared["external_host"]["player_name"] != "Alice" \
                or len(manager.started_workers) != 2:
            raise AssertionError("human-hosted match did not prepare every managed client")
        discovered = manager.discover_human_hosted_lan_match(
            created["match"]["match_id"], host_address=manager.host_address,
        )
        if [item["network_session_id"] for item in discovered["sessions"]] \
                != [manager.session_id]:
            raise AssertionError("exact external session was not discovered")
        joined = manager.join_human_hosted_lan_match(
            created["match"]["match_id"], host_address=manager.host_address,
            network_session_id=manager.session_id,
        )
        if not joined.get("awaiting_human_start") \
                or not all(item["ready"] for item in manager.joined.values()) \
                or manager.native_start_calls:
            raise AssertionError("managed agents did not join and ready without stealing host")
        waiting = manager.finalize_human_hosted_lan_match(created["match"]["match_id"])
        if not waiting.get("awaiting_human_start") or waiting.get("blockers"):
            raise AssertionError("ready human-owned lobby did not wait for native Start")

        manager.lifecycle = "game"
        finished = manager.finalize_human_hosted_lan_match(created["match"]["match_id"])
        durable = control.list_seats(created["match"]["match_id"])
        if not finished.get("human_hosted") \
                or control.get_match(created["match"]["match_id"])["status"] != "running" \
                or [seat["faction_id"] for seat in durable] != [1, 2, 3, 4] \
                or manager.native_start_calls:
            raise AssertionError("human-owned Start was not observed and bound durably")

        try:
            control.create_lan_match(
                "Duplicate names", [agents[0]["agent_id"]],
                host_controller_kind="human", human_host_name="Alice",
                human_player_names=["Alice"],
            )
        except Exception as exc:
            if "duplicate_lan_human_player_name" not in str(exc):
                raise
        else:
            raise AssertionError("duplicate human host/client identity was accepted")

        print(json.dumps({
            "event": "pass",
            "payload": {
                "human_host_owns_seat_zero_without_worker": True,
                "exact_session_discovery": True,
                "managed_agents_join_and_ready": True,
                "managed_agents_never_start_human_lobby": True,
                "human_start_observed": True,
                "all_factions_bound_durably": True,
                "pixels_or_ui_input_used": False,
            },
        }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
