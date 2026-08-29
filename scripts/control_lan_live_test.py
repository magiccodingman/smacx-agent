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


async def mcp_identity(url: str) -> dict:
    return await mcp_tool(url, "smac_snapshot")


async def prepare_game_management(url: str) -> dict:
    """Resolve only audited opening notices, then request the guarded management frame."""
    allowed_parameters = {
        "response", "option", "phase", "priority", "name", "tech_id",
    }
    latest: dict = {}
    for _ in range(20):
        latest = await mcp_tool(url, "smac_decision", {"finish_ready_units": True})
        if not latest.get("ok"):
            raise AssertionError(f"MCP decision failed while preparing save: {latest}")
        phase = latest.get("phase")
        if phase == "turn":
            return latest
        if phase == "wait":
            await mcp_tool(url, "smac_wait", {"seconds": 1})
            continue
        if phase != "interaction":
            raise AssertionError(f"unexpected pre-save decision phase: {latest}")
        choices = latest.get("choices", [])
        choice = next((item for item in choices if item.get("command") in {
            "acknowledge_popup", "choose_research_priority",
            "advance_technology_presentation",
        }), None)
        if not choice:
            raise AssertionError(f"non-audited interaction blocked save regression: {latest}")
        guard = latest["required_next"]["guard"]
        arguments = {"command": choice["command"], **guard}
        arguments.update({key: choice[key] for key in allowed_parameters if key in choice})
        result = await mcp_tool(url, "smac_command", arguments)
        if not result.get("ok"):
            raise AssertionError(f"opening interaction command failed: {result}")
    raise AssertionError(f"game did not become actionable for save: {latest}")


async def save_multiplayer_host(url: str, slot: str) -> dict:
    frame = await prepare_game_management(url)
    choice = next(
        (item for item in frame.get("choices", []) if item.get("command") == "save_game"),
        None,
    )
    if not choice or choice.get("native_host_only") is not True:
        raise AssertionError(f"native host save choice was not exposed: {frame}")
    result = await mcp_tool(url, "smac_command", {
        "command": "save_game", "slot": slot, **frame["required_next"]["guard"],
    })
    if not result.get("ok") or result.get("multiplayer") is not True \
            or result.get("native_host") is not True:
        raise AssertionError(f"native multiplayer save failed: {result}")
    return result


def main() -> int:
    game = os.environ.get("SMACX_TEST_GAME_SOURCE")
    proton = os.environ.get("SMACX_TEST_PROTON_SOURCE")
    directx = os.environ.get("SMACX_TEST_DIRECTX_REDIST")
    if not all((game, proton, directx)):
        print(json.dumps({"event": "skip", "reason": "missing_live_assets"}))
        return 0
    for path in (game, proton, directx):
        if not Path(path).is_absolute():
            raise SystemExit("live asset paths must be absolute")

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
            "-e", "SMACX_WORKER_IMAGE=smacx-agent-worker:dev",
            "-e", "SMACX_MCP_IMAGE=smacx-agent-control:dev",
            "-e", f"SMACX_CONTROL_DATA_VOLUME={control_volume}",
            "-e", f"SMACX_DIRECTX_REDIST_HOST={directx}",
            "-v", f"{control_volume}:/var/lib/smacx",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-p", "127.0.0.1::8080", "--read-only",
            "--tmpfs", "/tmp:size=64m,mode=1777", "--tmpfs", "/run:size=16m,mode=0755",
            "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
            "smacx-agent-control:dev",
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if docker("inspect", "-f", "{{if .State.Health}}{{.State.Health.Status}}{{end}}", control_name) == "healthy":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Control Center did not become healthy")
        port = docker("port", control_name, "8080/tcp").rsplit(":", 1)[-1]
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
        source = api(opener, base_url, "POST", "/api/v1/game-sources/validate", {
            "display_name": "LAN legal source", "host_path": game,
        }, csrf, 180)["game_source"]
        runtime = api(opener, base_url, "POST", "/api/v1/runtimes/import-proton", {
            "display_name": "LAN private Proton", "source_host_path": proton,
        }, csrf, 1800)["runtime"]
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
            asyncio.run(mcp_identity(worker["network"]["mcp_url"])) for worker in lan_workers
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
        saved = asyncio.run(save_multiplayer_host(host_worker["network"]["mcp_url"], save_slot))
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
        resumed = api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/start", {
            "session_name": "Managed LAN Resume Test",
            "profile": "small_easy",
            "resume_slot": save_slot,
        }, csrf, 900)
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
                "multiplayer_save_bytes": save_bytes,
                "stock_multiplayer_lobby_reload": True,
                "faction_seats_restored": True,
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
