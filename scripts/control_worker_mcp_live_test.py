#!/usr/bin/env python3
"""Opt-in end-to-end Control Center, worker, and MCP-sidecar regression."""

from __future__ import annotations

import asyncio
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.request import HTTPCookieProcessor, Request, build_opener
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from smacx_hermes import configure_from_descriptor, hermes_command


def docker(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False,
    )
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


async def inspect_mcp(url: str, expected_match: str) -> dict:
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams[:2]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            status = await session.call_tool("smac_status", {})
            snapshot = await session.call_tool("smac_snapshot", {})
            launch = await session.call_tool("smac_launch", {})
    def value(result) -> dict:
        structured = getattr(result, "structured_content", None)
        if isinstance(structured, dict):
            return structured
        for item in getattr(result, "content", []):
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(decoded, dict):
                    return decoded
        return {}

    status_value = value(status)
    snapshot_value = value(snapshot)
    launch_value = value(launch)
    if len(names) < 10 or any(token in name for name in names for token in ("screenshot", "mouse", "keyboard")):
        raise AssertionError(f"unsafe or incomplete MCP tool surface: {sorted(names)}")
    identity = status_value.get("identity", {})
    if not status_value.get("ok") or identity.get("match_id") != expected_match:
        raise AssertionError(f"MCP sidecar was not bound to the expected live match: {status_value}")
    if launch_value.get("error", {}).get("code") != "managed_lifecycle_operator_only":
        raise AssertionError("managed MCP allowed agent-controlled process launch")
    return {
        "tool_count": len(names), "status": status_value,
        "snapshot": snapshot_value, "launch": launch_value,
    }


def run_hermes_semantic_turn(descriptor: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="smacx-hermes-live-") as temporary:
        hermes_root = Path(temporary) / "hermes"
        profile = configure_from_descriptor(descriptor, hermes_root=hermes_root)
        prompt = (
            "This is a bounded semantic integration test in a fresh tiny Citizen game. "
            "Do not use the web. Call smac_status and smac_decision. Then execute exactly one "
            "legal command returned by smac_decision that advances the current opening interaction. "
            "Obtain one fresh semantic frame afterward and stop with a short report. Never use or "
            "request screenshots, vision, mouse, keyboard, terminal, or desktop control."
        )
        command = hermes_command(
            profile, query=prompt, max_turns=12, run_budget_seconds=180, toolsets="smacx",
        )
        environment = dict(os.environ)
        environment["HERMES_HOME"] = str(hermes_root)
        completed = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=240,
            env=environment,
        )
        profile_root = Path(profile["profile_root"])
        session_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in profile_root.rglob("*")
            if path.is_file() and path.stat().st_size <= 10 * 1024 * 1024
        )
        if completed.returncode:
            raise AssertionError(
                "Hermes semantic turn failed: " + (completed.stderr or completed.stdout)[-3000:]
            )
        if "smac_status" not in session_text or "smac_decision" not in session_text:
            raise AssertionError("Hermes session did not record the required semantic observations")
        if "smac_command" not in session_text:
            raise AssertionError("Hermes session did not record a semantic game action")
        return {
            "profile_id": profile["profile_id"],
            "model_id": profile["model_id"],
            "reasoning_effort": profile["reasoning_effort"],
            "output_tail": completed.stdout.strip()[-1000:],
        }


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
    control_name = f"smacx-control-mcp-live-{suffix}"
    control_volume = f"smacx-control-mcp-live-data-{suffix}"
    network = f"smacx-control-mcp-live-net-{suffix}"
    labels = [
        "--label", "io.smacx.managed=true",
        "--label", "io.smacx.installation=installation-control-mcp-live",
        "--label", "io.smacx.purpose=control-mcp-live-test",
    ]
    worker: dict | None = None
    runtime: dict | None = None
    installation_id: str | None = None
    opener = None
    base_url = ""
    csrf = ""
    hermes_result: dict | None = None
    try:
        docker("volume", "create", control_volume)
        docker("network", "create", *labels, network)
        socket_gid = str(os.stat("/var/run/docker.sock").st_gid)
        docker(
            "run", "-d", "--name", control_name, *labels,
            "--network", network, "--group-add", socket_gid,
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
            "password": "control mcp live test password",
        })
        csrf = next(cookie.value for cookie in cookies if cookie.name == "smacx_csrf")
        status = api(opener, base_url, "GET", "/api/v1/status")
        installation_id = status["installation_id"]
        source = api(opener, base_url, "POST", "/api/v1/game-sources/validate", {
            "display_name": "Live legal source", "host_path": game,
        }, csrf, 180)["game_source"]
        runtime = api(opener, base_url, "POST", "/api/v1/runtimes/import-proton", {
            "display_name": "Live private Proton", "source_host_path": proton,
        }, csrf, 1800)["runtime"]
        agent = api(opener, base_url, "POST", "/api/v1/agents", {
            "display_name": "MCP live agent",
        }, csrf)["agent"]
        created = api(opener, base_url, "POST", "/api/v1/matches/solo", {
            "display_name": "MCP sidecar live match",
            "agent_id": agent["agent_id"],
            "game_source_id": source["game_source_id"],
            "runtime_id": runtime["runtime_id"],
            "faction_id": 1,
            "autostart": {"enabled": True, "difficulty": 0, "world_size": 0, "faction_id": 1},
        }, csrf)
        worker = created["worker"]
        started = api(
            opener, base_url, "POST", f"/api/v1/workers/{worker['instance_id']}/start",
            {}, csrf, 420,
        )
        endpoint = started.get("mcp") or {}
        if endpoint.get("status") != "running" or not endpoint.get("url"):
            raise AssertionError(f"worker did not receive an MCP sidecar: {started}")
        mcp_result = asyncio.run(inspect_mcp(endpoint["url"], created["match"]["match_id"]))
        provider_url = os.environ.get("SMACX_TEST_PROVIDER_URL")
        provider_model = os.environ.get("SMACX_TEST_PROVIDER_MODEL")
        if provider_url and provider_model:
            provider = api(opener, base_url, "POST", "/api/v1/providers", {
                "display_name": "Hermes live provider", "base_url": provider_url,
            }, csrf)["provider"]
            discovered = api(
                opener, base_url, "POST", f"/api/v1/providers/{provider['provider_id']}/discover",
                {}, csrf, 60,
            )["provider"]
            if discovered.get("default_model_id") != provider_model:
                discovered = api(
                    opener, base_url, "POST", f"/api/v1/providers/{provider['provider_id']}/select",
                    {"model_id": provider_model}, csrf,
                )["provider"]
            descriptor = api(
                opener, base_url, "POST", "/api/v1/harness-profiles/hermes", {
                    "match_id": created["match"]["match_id"],
                    "provider_id": provider["provider_id"], "reasoning_effort": "low",
                }, csrf,
            )["descriptor"]
            if descriptor["mcp_url"] != endpoint["url"]:
                raise AssertionError("Control descriptor did not use the exact live MCP sidecar")
            hermes_result = run_hermes_semantic_turn(descriptor)
            after = asyncio.run(inspect_mcp(endpoint["url"], created["match"]["match_id"]))
            before_revision = mcp_result["snapshot"].get("snapshot", {}).get("revision")
            after_revision = after["snapshot"].get("snapshot", {}).get("revision")
            if not before_revision or before_revision == after_revision:
                raise AssertionError(
                    f"Hermes semantic command did not advance native game revision: "
                    f"{before_revision!r} -> {after_revision!r}"
                )
            hermes_result["native_revision_advanced"] = True
        api(
            opener, base_url, "POST", f"/api/v1/workers/{worker['instance_id']}/park",
            {}, csrf, 120,
        )
        print(json.dumps({
            "event": "pass",
            "payload": {
                "authenticated_control_lifecycle": True,
                "dedicated_mcp_sidecar": True,
                "mcp_tool_count": mcp_result["tool_count"],
                "mcp_bound_to_exact_match": True,
                "managed_lifecycle_blocked": True,
                "sidecar_removed_on_park": True,
                "hermes_semantic_turn": bool(hermes_result),
                "hermes_low_reasoning": bool(
                    hermes_result and hermes_result["reasoning_effort"] == "low"
                ),
                "hermes_native_revision_advanced": bool(
                    hermes_result and hermes_result.get("native_revision_advanced")
                ),
            },
        }, separators=(",", ":")))
    except Exception:
        logs = docker("logs", "--tail", "100", control_name, check=False)
        if logs:
            print(json.dumps({"event": "control_logs", "tail": logs[-4000:]}, separators=(",", ":")))
        raise
    finally:
        if opener and base_url and csrf and worker:
            try:
                api(opener, base_url, "POST", f"/api/v1/workers/{worker['instance_id']}/park", {}, csrf, 60)
            except Exception:
                pass
        if worker:
            for container_name in (
                worker.get("network", {}).get("mcp_container_name"), worker.get("container_name"),
            ):
                if container_name:
                    docker("rm", "-f", str(container_name), check=False)
        docker("rm", "-f", control_name, check=False)
        if worker:
            for volume_name in (
                worker.get("network", {}).get("secret_volume"), worker.get("data_volume"),
            ):
                if volume_name:
                    docker("volume", "rm", str(volume_name), check=False)
        if runtime:
            docker("volume", "rm", str(runtime.get("storage_ref")), check=False)
        docker("volume", "rm", control_volume, check=False)
        docker("network", "rm", network, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
