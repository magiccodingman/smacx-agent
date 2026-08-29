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
    with opener.open(Request(base_url + path, data=data, headers=headers, method=method), timeout=timeout) as response:
        return json.load(response)


async def mcp_identity(url: str) -> dict:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.call_tool("smac_snapshot", {})
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
                "pixels_or_ui_input_used": False,
                "match_wide_park": True,
            },
        }, separators=(",", ":")))
    except Exception:
        logs = docker("logs", "--tail", "120", control_name, check=False)
        if logs:
            print(json.dumps({"event": "control_logs", "tail": logs[-6000:]}, separators=(",", ":")))
        raise
    finally:
        if opener and base_url and csrf and match_id:
            try:
                api(opener, base_url, "POST", f"/api/v1/matches/{match_id}/park", {}, csrf, 180)
            except Exception:
                pass
        if installation_id:
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
        docker("rm", "-f", control_name, check=False)
        docker("volume", "rm", control_volume, check=False)
        docker("network", "rm", network, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
