#!/usr/bin/env python3
"""Opt-in real two-worker managed LAN orchestration regression."""

from __future__ import annotations

import asyncio
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import HTTPCookieProcessor, Request, build_opener
from urllib.error import HTTPError
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from control_worker_mcp_live_test import bridge_operation, runtime_context, current_decision


def docker(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=False)
    if check and completed.returncode:
        raise RuntimeError(f"docker_{arguments[0]}_failed:{completed.stderr.strip()[:1000]}")
    return completed.stdout.strip()


def api(opener, base_url: str, method: str, path: str, body: dict | None = None,
        csrf: str | None = None, timeout: float = 60.0) -> dict:
    data = json.dumps(body or {}, separators=(",", ":")).encode() if method != "GET" else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    try:
        with opener.open(Request(base_url + path, data=data, headers=headers, method=method), timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        try:
            detail = json.load(exc)
        except (json.JSONDecodeError, ValueError):
            detail = {"body": exc.read().decode("utf-8", errors="replace")[:2000]}
        raise RuntimeError(f"api_{method}_{path}_{exc.code}:{detail}") from exc


def mcp_result(result) -> dict:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if isinstance(text, str):
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return {}


async def mcp_tool(url: str, name: str, arguments: dict | None = None) -> dict:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            return mcp_result(await session.call_tool(name, arguments or {}))



def managed_effect_state(worker: dict) -> dict:
    """Read owned agreement effects through the restored provider projection."""
    sidecar = worker["network"]["mcp_container_name"]
    endpoint = worker["network"]["mcp_url"]
    episode = "acceptance-recovery-observation-" + uuid.uuid4().hex
    runtime_context(sidecar, episode)
    try:
        decision = asyncio.run(current_decision(endpoint))
        assert decision.get("ok"), decision
        result = asyncio.run(mcp_tool(endpoint, "smac_world", {
            "mode": "global", "subject_refs": ["global-economy", "global-owned-technologies", "global-social-engineering"],
            "detail": "deep",
        }))
        assert result.get("ok"), result
        rows = {row["object_ref"]: row for row in result["items"]}
        economy = rows["global-economy"]["fields"]["state"]
        technologies = rows["global-owned-technologies"]["fields"]["technologies"]
        assert economy["epistemic_status"] == technologies["epistemic_status"] == "current"
        factions = bridge_operation(sidecar, "list_factions")["items"]
        return {"energy_credits": economy["value"]["energy_credits"],
                "social_selected": rows["global-social-engineering"]["fields"]["state"]["value"]["selected"],
                "technologies": technologies["value"],
                "pact_counterparts": sorted(row["id"] for row in factions
                                            if row.get("relations", {}).get("pact"))}
    finally:
        runtime_context(sidecar, episode, end=True)


def restored_identity_publications(worker: dict) -> dict:
    """Audit every new-timeline publication, not only the eventual projection."""
    script = """
import json
from smacx_controller import world_service, _store
scope, world, _ = world_service(MATCH, agent_id=AGENT)
identity, projection = world._projection()
own_refs = sorted(row['object_ref'] for row in projection['objects']
                  if row.get('kind') == 'own_unit' and row.get('status') == 'active')
with _store()._connect() as connection:
    rows = connection.execute(
        'SELECT payload_json FROM world_observation_projection WHERE match_id=? '
        'AND agent_id=? AND perspective_id=? AND timeline_id=? '
        "AND observation_kind IN ('world_object','world_batch')",
        (scope.match_id, scope.agent_id, scope.perspective_id, identity.timeline_id)).fetchall()
transitions = []
for row in rows:
    payload = json.loads(row[0])
    for delta in payload.get('deltas', [payload]):
        if str(delta.get('object_ref', '')).startswith('own-unit-') \\
                and delta.get('change') in ('appeared', 'removed'):
            transitions.append({'object_ref': delta['object_ref'], 'change': delta['change']})
print(json.dumps({'owned_refs': own_refs, 'identity_transitions': transitions}))
"""
    bindings = f"MATCH={worker['match_id']!r}\nAGENT={worker['agent_id']!r}\n"
    return json.loads(docker("exec", worker["network"]["mcp_container_name"],
                             "/opt/smacx/mcp-venv/bin/python", "-c", bindings + script))


def main() -> int:
    game = os.environ.get("SMACX_TEST_GAME_SOURCE")
    if not game:
        print(json.dumps({"event": "skip", "reason": "missing_live_game_source"}))
        return 0
    if not Path(game).is_absolute():
        raise SystemExit("live game source path must be absolute")
    control_image = os.environ.get("SMACX_TEST_CONTROL_IMAGE", "smacx-agent-control:dev")
    worker_image = os.environ.get("SMACX_TEST_WORKER_IMAGE", "smacx-agent-worker:dev")
    mcp_image = os.environ.get("SMACX_TEST_MCP_IMAGE", control_image)

    suffix = uuid.uuid4().hex[:12]
    control_name = f"smacx-control-lan-live-{suffix}"
    control_volume = f"smacx-control-lan-live-data-{suffix}"
    network = f"smacx-control-lan-live-net-{suffix}"
    labels = [
        "--label", "io.smacx.managed=true",
        "--label", "io.smacx.installation=installation-control-lan-live",
        "--label", "io.smacx.purpose=control-lan-live-test",
    ]
    installation_id = ""
    match_id = ""
    opener = None
    base_url = ""
    csrf = ""
    failed = False
    try:
        docker("volume", "create", control_volume)
        docker("network", "create", *labels, network)
        socket_gid = str(os.stat("/var/run/docker.sock").st_gid)
        docker(
            "run", "-d", "--name", control_name, *labels, "--network", network,
            "--group-add", socket_gid,
            "-e", "SMACX_DOCKER_ENABLED=1",
            "-e", "SMACX_DOCKER_SOCKET=/var/run/docker.sock",
            "-e", f"SMACX_DOCKER_NETWORK={network}",
            "-e", f"SMACX_WORKER_IMAGE={worker_image}",
            "-e", "SMACX_AGENT_TEST_MODE=1",
            "-e", "SMACX_AGENT_TEST_LAN_HOST=1",
            "-e", "SMACX_ACCEPTANCE_MANAGED_ACTIONS=1",
            "-e", f"SMACX_GAME_SOURCE={game}",
            "-e", f"SMACX_MCP_IMAGE={mcp_image}",
            "-e", f"SMACX_CONTROL_DATA_VOLUME={control_volume}",
            "-v", f"{game}:{game}:ro",
            "-v", f"{control_volume}:/var/lib/smacx",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-p", "127.0.0.1::8081", "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777", "--tmpfs", "/run:size=16m,mode=0755",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            control_image,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if docker("inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{end}}", control_name) == "healthy":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Control Center did not become healthy")
        port = docker("port", control_name, "8081/tcp").rsplit(":", 1)[-1]
        base_url = f"http://127.0.0.1:{port}"
        token = docker("exec", control_name, "smacx-control", "bootstrap-token")
        cookies = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookies))
        api(opener, base_url, "POST", "/api/v1/setup/bootstrap", {
            "username": "admin", "bootstrap_token": token,
            "password": "control lan live test password",
        })
        csrf = next(cookie.value for cookie in cookies if cookie.name == "smacx_csrf")
        installation_id = api(opener, base_url, "GET", "/api/v1/status")["installation_id"]
        sources = api(opener, base_url, "GET", "/api/v1/game-sources")["game_sources"]
        source = next(item for item in sources if item.get("host_path") == game)
        runtimes = api(opener, base_url, "GET", "/api/v1/runtimes")["runtimes"]
        runtime = next(item for item in runtimes if item.get("status") == "ready")
        agents = [
            api(opener, base_url, "POST", "/api/v1/agents", {
                "display_name": f"Managed LAN agent {index + 1}",
            }, csrf)["agent"]
            for index in range(2)
        ]
        created = api(opener, base_url, "POST", "/api/v1/matches/lan", {
            "display_name": "Managed native LAN live match",
            "session_name": "Managed LAN Live Test",
            "agent_ids": [agent["agent_id"] for agent in agents],
            "agent_seats": [{"agent_id": agent["agent_id"], "player_name": f"Managed Seat {index + 1}",
                             "faction_choice_id": index, "faction_key": ("gaians", "hive")[index],
                             "faction_name": ("Gaia's Stepdaughters", "Human Hive")[index],
                             "personality_id": "none"}
                            for index, agent in enumerate(agents)],
            "faction_roster_choice_ids": list(range(7)),
            "game_source_id": source["game_source_id"],
            "runtime_id": runtime["runtime_id"],
            "profile": "small_easy", "start_now": True,
        }, csrf, 900)
        match_id = created["match"]["match_id"]
        started = created.get("started", {})
        if not started.get("ok") or len(started.get("seats", [])) != 2 \
                or started.get("pixels_or_ui_input_used") is not False:
            raise AssertionError(f"managed LAN did not start semantically: {started}")
        if len({seat["faction_id"] for seat in started["seats"]}) != 2:
            raise AssertionError(f"managed LAN seats did not receive distinct factions: {started}")
        status = api(
            opener, base_url, "POST", f"/api/v1/matches/{match_id}/status", {}, csrf, 60,
        )
        if len(status["seats"]) != 2 \
                or any(seat.get("native", {}).get("lifecycle") != "game" for seat in status["seats"]):
            raise AssertionError(f"managed LAN seat health diverged: {status}")
        host_seat = next(
            (seat for seat in status["seats"]
             if seat.get("native", {}).get("network", {}).get("role") == "host"),
            None,
        )
        if not host_seat:
            raise AssertionError(f"native LAN host role was not observable: {status}")
        workers = api(opener, base_url, "GET", "/api/v1/workers")["workers"]
        lan_workers = [worker for worker in workers if worker["match_id"] == match_id]
        if len(lan_workers) != 2 or any(not worker["network"].get("mcp_url") for worker in lan_workers):
            raise AssertionError("managed LAN did not receive one MCP sidecar per seat")
        snapshots = [
            bridge_operation(worker["network"]["mcp_container_name"], "semantic_snapshot") for worker in lan_workers
        ]
        identities = [snapshot.get("snapshot", {}) for snapshot in snapshots]
        if any(identity.get("match_id") != match_id for identity in identities) \
                or len({identity.get("session_id") for identity in identities}) != 2 \
                or len({identity.get("faction", {}).get("id") for identity in identities}) != 2:
            raise AssertionError(f"LAN MCP scopes were not shared-match/distinct-seat: {identities}")
        host_worker = next(
            worker for worker in lan_workers
            if worker["instance_id"] == host_seat["instance_id"]
        )
        save_slot = "managed_lan_checkpoint"
        from managed_human_action_live_test import exercise_human_actions
        human_evidence = exercise_human_actions(lan_workers, host_worker)
        before_recovery = {worker["instance_id"]: managed_effect_state(worker) for worker in lan_workers}
        owned_before = {worker["instance_id"]: restored_identity_publications(worker)["owned_refs"]
                        for worker in lan_workers}
        saved = api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/checkpoint",
                    {"slot": save_slot}, csrf, 120)["checkpoint"]
        save_path = f"/var/lib/smacx/game/saves/agent/{match_id}/{save_slot}.sav"
        save_bytes = int(docker(
            "exec", host_worker["container_name"], "stat", "-c", "%s", save_path,
        ))
        if save_bytes < 1024:
            raise AssertionError(f"native multiplayer save was unexpectedly small: {save_bytes}")
        api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/park", {}, csrf, 180)
        original_factions = {
            seat["seat_index"]: seat["faction_id"] for seat in started["seats"]
        }
        # Recover the native save together with journal/world/identity state.
        # A plain lobby load does not establish the managed recovery contract.
        resumed = api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/recover",
                      {}, csrf, 900)
        assert len(resumed.get("native_semantic_identity_restore", ())) == 2, resumed
        assert resumed.get("memory_restore", {}).get("journal_forks"), resumed
        if not resumed.get("ok") or resumed.get("resume_slot") != save_slot \
                or resumed.get("loaded_checkpoint", {}).get("turn") != saved.get("turn"):
            raise AssertionError(f"managed LAN checkpoint did not resume: {resumed}")
        resumed_factions = {
            seat["seat_index"]: seat["faction_id"] for seat in resumed.get("seats", [])
        }
        if resumed_factions != original_factions:
            raise AssertionError(
                f"resumed factions changed seats: {original_factions} -> {resumed_factions}"
            )
        resumed_status = api(
            opener, base_url, "POST", f"/api/v1/matches/{match_id}/status", {}, csrf, 60,
        )
        if any(seat.get("native", {}).get("lifecycle") != "game"
               for seat in resumed_status.get("seats", [])):
            raise AssertionError(f"resumed LAN seats did not enter gameplay: {resumed_status}")
        restored_workers = [worker for worker in api(opener, base_url, "GET", "/api/v1/workers")["workers"]
                            if worker["match_id"] == match_id]
        after_recovery = {worker["instance_id"]: managed_effect_state(worker) for worker in restored_workers}
        assert after_recovery == before_recovery, "managed agreement effects changed across checkpoint recovery"
        for worker in restored_workers:
            publications = restored_identity_publications(worker)
            assert publications["owned_refs"] == owned_before[worker["instance_id"]], publications
            assert not publications["identity_transitions"], publications
        api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/park", {}, csrf, 180)
        print(json.dumps({
            "event": "pass",
            "payload": {
                "two_isolated_workers": True,
                "native_host_discover_join": True,
                "guarded_lobby_config_ready_start": True,
                "shared_match_distinct_sessions": True,
                "distinct_faction_perspectives": True,
                "one_mcp_sidecar_per_seat": True,
                "native_host_role_observable": True,
                "native_host_campaign_save": True,
                "human_actions": human_evidence,
                "multiplayer_save_bytes": save_bytes,
                "stock_multiplayer_lobby_reload": True,
                "faction_seats_restored": True,
                "journal_and_native_identity_restored": True,
                "agreement_effects_preserved_in_current_world_after_recovery": True,
                "both_seats_no_transient_owned_identity_publications": True,
                "pixels_or_ui_input_used": False,
                "match_wide_park": True,
            },
        }, separators=(",", ":")))
    except Exception:
        failed = True
        logs = docker("logs", "--tail", "120", control_name, check=False)
        if logs:
            print(json.dumps({"event": "control_logs", "tail": logs[-6000:]}, separators=(",", ":")))
        if installation_id:
            containers = docker(
                "ps", "-aq", "--filter", f"label=io.smacx.installation={installation_id}",
                check=False,
            ).splitlines()
            for identifier in containers:
                if not identifier:
                    continue
                worker_logs = docker("logs", "--tail", "100", identifier, check=False)
                if worker_logs:
                    print(json.dumps({
                        "event": "managed_container_logs",
                        "container_id": identifier[:12], "tail": worker_logs[-6000:],
                    }, separators=(",", ":")))
        raise
    finally:
        keep_failed = failed and os.environ.get("SMACX_TEST_KEEP_ON_FAILURE") == "1"
        if keep_failed:
            docker("pause", control_name, check=False)
            print(json.dumps({
                "event": "kept_failed_resources",
                "control_name": control_name,
                "control_volume": control_volume,
                "network": network,
                "installation_id": installation_id,
                "match_id": match_id,
            }, separators=(",", ":")))
        if not keep_failed and opener and base_url and csrf and match_id:
            try:
                api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/park", {}, csrf, 180)
            except Exception:
                pass
        if not keep_failed and installation_id:
            containers = docker(
                "ps", "-aq", "--filter", f"label=io.smacx.installation={installation_id}", check=False,
            ).splitlines()
            for identifier in containers:
                if identifier:
                    docker("rm", "-f", identifier, check=False)
            volumes = docker(
                "volume", "ls", "-q", "--filter", f"label=io.smacx.installation={installation_id}",
                check=False,
            ).splitlines()
            for volume in volumes:
                if volume:
                    docker("volume", "rm", volume, check=False)
        if not keep_failed:
            docker("rm", "-f", control_name, check=False)
            docker("volume", "rm", control_volume, check=False)
            docker("network", "rm", network, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
