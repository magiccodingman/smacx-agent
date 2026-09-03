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
from urllib.error import HTTPError
import uuid

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

def docker(*arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=False,
    )
    if check and completed.returncode:
        raise RuntimeError(f"docker_{arguments[0]}_failed:{completed.stderr.strip()[:1000]}")
    return completed.stdout.strip()


def runtime_context(container_name: str, episode_id: str, *, end: bool = False) -> dict:
    script = r'''
import json, pathlib, sys, urllib.error, urllib.parse, urllib.request
token = pathlib.Path('/run/secrets/bridge-token').read_text(encoding='ascii').strip()
base = 'http://127.0.0.1:47816/runtime-context'
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
if sys.argv[2] == 'end':
    request = urllib.request.Request(
        base + '/episode-ended',
        data=json.dumps({'episode_id': sys.argv[1], 'committed': True}).encode(),
        headers=headers, method='POST')
else:
    query = urllib.parse.urlencode({
        'episode_id': sys.argv[1], 'episode_mode': 'gameplay', 'context_length': 65536})
    request = urllib.request.Request(base + '?' + query, headers=headers)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.read().decode())
except urllib.error.HTTPError as exc:
    print(exc.read().decode())
'''
    return json.loads(docker(
        "exec", container_name, "python3", "-c", script,
        episode_id, "end" if end else "start",
    ))


def bridge_operation(container_name: str, operation: str, **arguments: object) -> dict:
    """Exercise the real sidecar-to-native adapter without provider content."""
    script = r'''
import json, sys
from smacx_controller import bridge_request
print(json.dumps(bridge_request(sys.argv[1], timeout=30, **json.loads(sys.argv[2])),
                 separators=(',', ':')))
'''
    return json.loads(docker(
        "exec", container_name, "python3", "-c", script,
        operation, json.dumps(arguments, separators=(",", ":")),
    ))


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
        except Exception:
            detail = {"status": exc.code}
        raise RuntimeError(f"api_{method}_{path}:{detail}") from exc


async def inspect_mcp(url: str, expected_match: str) -> dict:
    async with streamable_http_client(url) as streams:
        read_stream, write_stream = streams[:2]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            briefing = await session.call_tool("smac_match_briefing", {"action": "read"})
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

    decision_value = value(briefing)
    if len(names) < 14 or any(token in name for name in names for token in ("screenshot", "mouse", "keyboard")):
        raise AssertionError(f"unsafe or incomplete MCP tool surface: {sorted(names)}")
    forbidden = {"smac_status", "smac_snapshot", "smac_launch", "smac_command", "smac_list"}
    if names & forbidden:
        raise AssertionError(f"managed MCP leaked operator/legacy tools: {sorted(names & forbidden)}")
    body = decision_value.get("briefing") if isinstance(decision_value.get("briefing"), dict) else {}
    identity = body.get("identity", {})
    if not decision_value.get("ok") or identity.get("match_id") != expected_match:
        raise AssertionError(
            "MCP sidecar was not bound to the expected live match: "
            f"decoded={decision_value!r}, content={getattr(briefing, 'content', None)!r}, "
            f"structured={getattr(briefing, 'structured_content', None)!r}"
        )
    return {
        "tool_count": len(names), "decision": decision_value,
        "snapshot": decision_value, "managed_lifecycle_hidden": True,
    }


async def prepare_checkpoint(url: str) -> dict:
    latest: dict = {}
    for _ in range(30):
        latest = await mcp_tool(url, "smac_decision", {"finish_ready_units": True})
        if not latest.get("ok"):
            raise AssertionError(f"decision failed before checkpoint: {latest}")
        if latest.get("kind") == "match_briefing_required":
            briefing = await mcp_tool(url, "smac_match_briefing", {"action": "read"})
            if not briefing.get("ok"):
                raise AssertionError(f"briefing read failed before checkpoint: {briefing}")
            acknowledged = await mcp_tool(url, "smac_match_briefing", {
                "action": "acknowledge", "briefing_hash": briefing["briefing_hash"],
            })
            if not acknowledged.get("ok"):
                raise AssertionError(f"briefing acknowledgement failed: {acknowledged}")
            continue
        if latest.get("phase") == "turn":
            return latest
        if latest.get("phase") == "wait":
            await mcp_tool(url, "smac_wait", {"seconds": 1})
            continue
        choice = next((item for item in latest.get("choices", []) if item.get("choice_id")), None)
        if not choice:
            raise AssertionError(f"unexpected interaction before checkpoint: {latest}")
        result = await mcp_tool(url, "smac_execute_choice", {
            "decision_id": latest["decision_id"], "choice_id": choice["choice_id"],
        })
        if not result.get("ok"):
            raise AssertionError(f"opening interaction failed before checkpoint: {result}")
    raise AssertionError(f"game never became checkpointable: {latest}")


async def current_decision(url: str) -> dict:
    """Read one fresh, non-mutating semantic frame after any briefing gate."""
    for _ in range(3):
        decision = await mcp_tool(url, "smac_decision", {"finish_ready_units": False})
        if decision.get("kind") != "match_briefing_required":
            return decision
        briefing = await mcp_tool(url, "smac_match_briefing", {"action": "read"})
        if not briefing.get("ok"):
            return briefing
        acknowledged = await mcp_tool(url, "smac_match_briefing", {
            "action": "acknowledge", "briefing_hash": briefing["briefing_hash"],
        })
        if not acknowledged.get("ok"):
            return acknowledged
    return {"ok": False, "error": {"code": "briefing_gate_did_not_clear"}}


async def mcp_tool(url: str, name: str, arguments: dict | None = None) -> dict:
    async with streamable_http_client(url) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments or {})
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []):
        value = getattr(item, "text", None)
        if isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(decoded, dict):
                return decoded
    return {
        "ok": False,
        "_unparsed_mcp_result": repr(getattr(result, "content", None)),
        "_structured_mcp_result": repr(getattr(result, "structured_content", None)),
    }


def main() -> int:
    game = os.environ.get("SMACX_TEST_GAME_SOURCE")
    if not game:
        print(json.dumps({"event": "skip", "reason": "missing_live_game_source"}))
        return 0
    if not Path(game).is_absolute():
        raise SystemExit("live game source path must be absolute")

    suffix = uuid.uuid4().hex[:12]
    control_image = os.environ.get("SMACX_TEST_CONTROL_IMAGE", "smacx-agent-control:dev")
    worker_image = os.environ.get("SMACX_TEST_WORKER_IMAGE", "smacx-agent-worker:dev")
    mcp_image = os.environ.get("SMACX_TEST_MCP_IMAGE", control_image)
    harness_image = os.environ.get("SMACX_TEST_HERMES_IMAGE", "smacx-agent-harness:dev")
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
            "-e", f"SMACX_GAME_SOURCE={game}",
            "-e", f"SMACX_WORKER_IMAGE={worker_image}",
            "-e", f"SMACX_MCP_IMAGE={mcp_image}",
            "-e", f"SMACX_HERMES_IMAGE={harness_image}",
            "-e", f"SMACX_CONTROL_DATA_VOLUME={control_volume}",
            "-v", f"{control_volume}:/var/lib/smacx",
            "-v", "/var/run/docker.sock:/var/run/docker.sock",
            "-v", f"{game}:{game}:ro",
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
        # The one-shot CLI briefly contends with the server's SQLite bootstrap
        # connection on very fast hosts.  Let that read-only process fully
        # release its descriptor before the first state-changing HTTP request.
        time.sleep(1)
        cookies = CookieJar()
        opener = build_opener(HTTPCookieProcessor(cookies))
        api(opener, base_url, "POST", "/api/v1/setup/bootstrap", {
            "username": "admin", "bootstrap_token": token,
            "password": "control mcp live test password",
        })
        csrf = next(cookie.value for cookie in cookies if cookie.name == "smacx_csrf")
        status = api(opener, base_url, "GET", "/api/v1/status")
        installation_id = status["installation_id"]
        sources = api(opener, base_url, "GET", "/api/v1/game-sources")["game_sources"]
        source = next(item for item in sources if item.get("host_path") == game)
        runtimes = api(opener, base_url, "GET", "/api/v1/runtimes")["runtimes"]
        runtime = next(item for item in runtimes if item.get("status") == "ready")
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
        }, csrf, 1800)
        worker = created["worker"]
        started = api(
            opener, base_url, "POST", f"/api/v1/workers/{worker['instance_id']}/start",
            {}, csrf, 420,
        )
        endpoint = started.get("mcp") or {}
        if endpoint.get("status") != "running" or not endpoint.get("url"):
            raise AssertionError(f"worker did not receive an MCP sidecar: {started}")
        worker.setdefault("network", {})["mcp_container_name"] = endpoint.get("container_name")
        native_summary = bridge_operation(
            endpoint["container_name"], "perspective_world_page",
            domain="summary", cursor=0, limit=1,
        )
        native_snapshot = bridge_operation(
            endpoint["container_name"], "semantic_snapshot",
        ).get("snapshot", {})
        if not native_summary.get("ok") \
                or not isinstance(native_summary.get("unity_survey"), bool) \
                or not isinstance(native_summary.get("is_governor"), bool):
            raise AssertionError(f"native entitlement summary is incomplete: {native_summary}")
        if not isinstance(native_snapshot.get("own_orbitals"), dict) \
                or not isinstance(native_snapshot.get("public_projects"), list) \
                or not isinstance(native_snapshot.get("governor_faction_id"), int):
            raise AssertionError(f"native global intelligence adapter is incomplete: {native_snapshot}")
        mcp_result = asyncio.run(inspect_mcp(endpoint["url"], created["match"]["match_id"]))
        test_episode_id = "episode-live-" + suffix
        runtime_started = runtime_context(endpoint["container_name"], test_episode_id)
        if not runtime_started.get("ok"):
            raise AssertionError(f"runtime context lease failed: {runtime_started}")
        asyncio.run(prepare_checkpoint(endpoint["url"]))
        runtime_context(endpoint["container_name"], test_episode_id, end=True)
        checkpoint = api(
            opener, base_url, "POST",
            f"/api/v1/matches/{created['match']['match_id']}/checkpoint",
            {"slot": "control_recovery"}, csrf, 60,
        )
        checkpoint_turn = checkpoint["checkpoint"].get("turn")
        provider_url = os.environ.get("SMACX_TEST_PROVIDER_URL")
        provider_model = os.environ.get("SMACX_TEST_PROVIDER_MODEL")
        live_backup_verified = False
        if not (provider_url and provider_model):
            backup = api(
                opener, base_url, "POST", "/api/v1/backups",
                {"include_secrets": True, "include_workers": True}, csrf, 1800,
            )["backup"]
            verified = api(
                opener, base_url, "POST", f"/api/v1/backups/{backup['backup_id']}/verify",
                {}, csrf, 120,
            )
            if verified.get("worker_count") != 1:
                raise AssertionError(f"live worker backup was incomplete: {verified}")
            live_backup_verified = True
        docker("stop", "-t", "1", worker["container_name"])
        recovery_deadline = time.monotonic() + 480
        recovered_worker = None
        while time.monotonic() < recovery_deadline:
            workers_now = api(opener, base_url, "GET", "/api/v1/workers")["workers"]
            current = next(item for item in workers_now if item["instance_id"] == worker["instance_id"])
            matches_now = api(opener, base_url, "GET", "/api/v1/matches")["matches"]
            current_match = next(
                item for item in matches_now if item["match_id"] == created["match"]["match_id"]
            )
            if current["observed_status"] == "running" and current_match["status"] == "running" \
                    and current_match.get("metadata", {}).get("last_recovered_slot") == "control_recovery":
                recovered_worker = current
                break
            time.sleep(2)
        if recovered_worker is None:
            raise AssertionError("supervisor did not recover the crashed native worker")
        recovered_endpoint = recovered_worker.get("network", {}).get("mcp_url")
        if not recovered_endpoint:
            raise AssertionError("recovered worker did not receive a fresh MCP sidecar")
        recovered_mcp = asyncio.run(inspect_mcp(
            recovered_endpoint, created["match"]["match_id"],
        ))
        recovered_sidecar = recovered_worker.get("network", {}).get("mcp_container_name")
        if not recovered_sidecar:
            raise AssertionError("recovered worker did not publish its MCP container identity")
        recovery_probe_episode = "episode-recovery-probe-" + suffix
        recovery_lease = runtime_context(recovered_sidecar, recovery_probe_episode)
        if not recovery_lease.get("ok"):
            raise AssertionError(f"recovered runtime context lease failed: {recovery_lease}")
        recovered_decision = asyncio.run(current_decision(recovered_endpoint))
        runtime_context(recovered_sidecar, recovery_probe_episode, end=True)
        if not recovered_decision.get("ok"):
            raise AssertionError(f"recovered semantic decision failed: {recovered_decision}")
        recovered_turn = recovered_decision.get("turn")
        if checkpoint_turn is not None and recovered_turn != checkpoint_turn:
            raise AssertionError(
                f"supervisor recovered the wrong checkpoint turn: {checkpoint_turn} -> {recovered_turn}"
            )
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
            if descriptor["mcp_url"] != recovered_endpoint:
                raise AssertionError("Control descriptor did not use the exact live MCP sidecar")
            prompt = (
                "This is a bounded semantic integration test in a fresh tiny Citizen game. "
                "Do not use the web. Read and acknowledge the match briefing if required, then call "
                "smac_decision. Execute exactly one opaque legal choice returned by smac_decision "
                "that advances the current opening interaction. "
                "Obtain one fresh semantic frame afterward and stop with a short report. Never use or "
                "request screenshots, vision, mouse, keyboard, terminal, or desktop control."
            )
            managed = api(opener, base_url, "POST", "/api/v1/harness-runs", {
                "match_id": created["match"]["match_id"],
                "agent_id": agent["agent_id"], "provider_id": provider["provider_id"],
                "reasoning_effort": "low", "initial_prompt": prompt,
                "run_budget_seconds": 180, "max_turns": 12, "restart_limit": 0,
            }, csrf, 120)["run"]
            managed_name = managed.get("container_name")
            if not managed_name:
                raise AssertionError("managed harness did not publish its container identity")
            inspected = json.loads(docker("inspect", managed_name))[0]
            inspect_text = json.dumps({
                "Config": inspected.get("Config"), "HostConfig": inspected.get("HostConfig"),
            })
            if "/run/secrets" not in inspect_text \
                    or "SMACX_PROVIDER_API_KEY=" in inspect_text:
                raise AssertionError("managed secret mount/config contract regressed")
            run_deadline = time.monotonic() + 300
            final_run = managed
            provider_progress: dict = {}
            starting_revision = recovered_decision.get("identity", {}).get("revision")
            while time.monotonic() < run_deadline:
                runs = api(opener, base_url, "GET", "/api/v1/harness-runs")["harness_runs"]
                final_run = next(item for item in runs if item["run_id"] == managed["run_id"])
                if final_run["status"] == "error":
                    break
                metadata = final_run.get("metadata") \
                    if isinstance(final_run.get("metadata"), dict) else {}
                progress = metadata.get("semantic_progress") \
                    if isinstance(metadata.get("semantic_progress"), dict) else {}
                if progress.get("available") and progress.get("revision") \
                        and progress.get("revision") != starting_revision:
                    provider_progress = progress
                    break
                time.sleep(2)
            if not provider_progress:
                logs = docker("logs", "--tail", "200", managed_name, check=False)
                raise AssertionError(
                    f"managed Hermes run did not advance semantic state: {final_run}; "
                    f"{logs[-4000:]}"
                )
            telemetry_result = api(
                opener, base_url, "POST",
                f"/api/v1/harness-runs/{managed['run_id']}/telemetry", {}, csrf, 60,
            )["result"]
            provider_usage = telemetry_result.get("telemetry") \
                if isinstance(telemetry_result.get("telemetry"), dict) else {}
            stopped_run = api(
                opener, base_url, "POST",
                f"/api/v1/harness-runs/{managed['run_id']}/stop", {}, csrf, 120,
            )["result"]
            if stopped_run.get("status") != "stopped":
                raise AssertionError(f"managed Hermes run did not stop cleanly: {stopped_run}")
            session_backup = api(
                opener, base_url, "POST", "/api/v1/backups",
                {"include_secrets": True, "include_workers": True}, csrf, 1800,
            )["backup"]
            session_verified = api(
                opener, base_url, "POST",
                f"/api/v1/backups/{session_backup['backup_id']}/verify", {}, csrf, 120,
            )
            if session_verified.get("harness_count") != 1:
                raise AssertionError(f"Hermes conversation backup was incomplete: {session_verified}")
            if session_verified.get("worker_count") != 1:
                raise AssertionError(f"live worker backup was incomplete: {session_verified}")
            live_backup_verified = True
            hermes_result = {
                "profile_id": descriptor["external_profile_id"],
                "model_id": descriptor["model_id"],
                "reasoning_effort": descriptor["reasoning_effort"],
                "managed_container": True,
                "session_backup_verified": True,
                "semantic_progress": provider_progress,
                "usage": {
                    key: int(provider_usage.get(key) or 0)
                    for key in (
                        "api_calls", "input_tokens", "output_tokens",
                        "reasoning_tokens", "cache_read_tokens", "cache_write_tokens",
                    )
                },
            }
            after_probe_episode = "episode-after-hermes-" + suffix
            after_lease = runtime_context(recovered_sidecar, after_probe_episode)
            if not after_lease.get("ok"):
                raise AssertionError(f"post-Hermes runtime context lease failed: {after_lease}")
            after = asyncio.run(current_decision(recovered_endpoint))
            runtime_context(recovered_sidecar, after_probe_episode, end=True)
            if not after.get("ok"):
                raise AssertionError(f"post-Hermes semantic decision failed: {after}")
            before_revision = recovered_decision.get("identity", {}).get("revision")
            after_revision = after.get("identity", {}).get("revision")
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
                "native_unity_governor_entitlements": True,
                "native_orbital_project_global_adapter": True,
                "managed_lifecycle_blocked": True,
                "bridge_verified_checkpoint": True,
                "live_worker_volume_backup_verified": live_backup_verified,
                "native_crash_recovered_without_ui": True,
                "recovered_checkpoint_turn": recovered_turn,
                "sidecar_removed_on_park": True,
                "hermes_semantic_turn": bool(hermes_result),
                "hermes_low_reasoning": bool(
                    hermes_result and hermes_result["reasoning_effort"] == "low"
                ),
                "hermes_native_revision_advanced": bool(
                    hermes_result and hermes_result.get("native_revision_advanced")
                ),
                "hermes_managed_container": bool(
                    hermes_result and hermes_result.get("managed_container")
                ),
                "hermes_session_backup_verified": bool(
                    hermes_result and hermes_result.get("session_backup_verified")
                ),
                "hermes_usage": hermes_result.get("usage") if hermes_result else None,
            },
        }, separators=(",", ":")))
    except Exception:
        if worker:
            sidecar_name = worker.get("network", {}).get("mcp_container_name")
            if sidecar_name:
                sidecar_logs = docker("logs", "--tail", "200", str(sidecar_name), check=False)
                if sidecar_logs:
                    print(json.dumps({"event": "mcp_logs", "tail": sidecar_logs[-12000:]}, separators=(",", ":")))
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
        if installation_id:
            managed_containers = docker(
                "ps", "-aq", "--filter", f"label=io.smacx.installation={installation_id}",
                "--filter", "label=io.smacx.managed=true", check=False,
            ).splitlines()
            for container in managed_containers:
                if container:
                    docker("rm", "-f", container, check=False)
            for purpose in ("harness-data", "harness-secret"):
                volumes = docker(
                    "volume", "ls", "-q",
                    "--filter", f"label=io.smacx.installation={installation_id}",
                    "--filter", f"label=io.smacx.purpose={purpose}", check=False,
                ).splitlines()
                for volume in volumes:
                    if volume:
                        docker("volume", "rm", volume, check=False)
            prepared_images = docker(
                "images", "-q", "--filter", f"label=io.smacx.installation={installation_id}",
                "--filter", "label=io.smacx.purpose=prepared-worker-image", check=False,
            ).splitlines()
            for image_id in dict.fromkeys(prepared_images):
                if image_id:
                    docker("image", "rm", image_id, check=False)
        docker("volume", "rm", control_volume, check=False)
        docker("network", "rm", network, check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
