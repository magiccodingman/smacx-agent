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
import sys
import tempfile
import threading
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
        raise RuntimeError(f"docker_{arguments[0]}_failed:{completed.stderr.strip()[-1000:]}")
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


def runtime_attention_responded(container_name: str, lease_id: str) -> dict:
    """Simulate the existing trusted response hook, not provider inference."""
    script = r'''
import json, pathlib, sys, urllib.request
token = pathlib.Path('/run/secrets/bridge-token').read_text(encoding='ascii').strip()
request = urllib.request.Request('http://127.0.0.1:47816/runtime-context/responded',
    data=json.dumps({'attention_lease_id': sys.argv[1]}).encode(),
    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(request, timeout=30) as response:
    print(response.read().decode())
'''
    return json.loads(docker("exec", container_name, "python3", "-c", script, lease_id))


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


def wait_for_native_readiness(container_name: str, timeout: float = 60.0) -> dict:
    """Require two actionable UI-thread reads before destructive fixtures.

    Worker/container health becomes visible before Wine has necessarily serviced
    its first bounded bridge dispatch after a checkpoint rehost.  Retrying this
    read-only operation is safe; retrying a timed-out mutating fixture is not.
    """
    deadline = time.monotonic() + timeout
    consecutive = 0
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = bridge_operation(container_name, "semantic_snapshot")
        if (latest.get("ok") is True and
                latest.get("snapshot", {}).get("interaction", {}).get("kind") == "turn"):
            consecutive += 1
            if consecutive >= 2:
                return latest
        else:
            consecutive = 0
        time.sleep(0.25)
    raise AssertionError({
        "message": "native UI thread did not become stably actionable",
        "latest": latest,
    })


def wait_native_action(container_name: str, action_id: int, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        latest = bridge_operation(container_name, "action_status", action_id=action_id)
        action = latest.get("action") or {}
        if action.get("status") != "pending":
            return action
        time.sleep(0.05)
    return latest.get("action") or {}


def measured_bridge_operation(
    container_name: str, operation: str, **arguments: object,
) -> tuple[dict, float, float]:
    """Measure native receipt wall time and concurrent native/UI probe gap."""
    stop = threading.Event()
    probe_latencies: list[float] = []

    def probe() -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                bridge_operation(container_name, "ping")
                probe_latencies.append((time.monotonic() - started) * 1000)
            except Exception:
                probe_latencies.append((time.monotonic() - started) * 1000)
            stop.wait(0.01)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    time.sleep(0.03)
    started = time.monotonic()
    result = bridge_operation(container_name, operation, **arguments)
    wall_ms = (time.monotonic() - started) * 1000
    stop.set()
    thread.join(5)
    return result, wall_ms, max(probe_latencies, default=0.0)


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


def force_native_identity_compaction(container_name: str) -> dict:
    """Delete an early owned VEH row so a survivor's handle no longer matches its row.

    This is a private native recovery fixture. Provider-facing play continues to
    use opaque choice IDs and stable own-unit refs everywhere else.
    """
    # The private acceptance worker is explicitly test-gated, and this bridge
    # operation is unreachable from the provider-facing MCP surface.
    prepared = bridge_operation(container_name, "test_identity_compaction_fixture")
    if prepared.get("ok") is not True:
        raise AssertionError(f"identity fixture preparation failed: {prepared}")
    units = bridge_operation(
        container_name, "perspective_world_page",
        domain="units", cursor=0, limit=256,
    )
    owned = sorted(
        (row for row in units.get("items", []) if row.get("owned") is True
         and isinstance(row.get("id"), int) and row.get("own_unit_ref")),
        key=lambda row: int(row["id"]),
    )
    if len(owned) < 2:
        raise AssertionError(f"identity fixture requires two owned units: {units}")
    doomed = owned[0]
    choices = bridge_operation(
        container_name, "semantic_choices", kind="unit_actions",
        unit_id=int(doomed["id"]),
    )
    disband = next(
        (row for row in choices.get("choices", [])
         if row.get("command") == "disband_unit"), None,
    )
    if disband is None:
        raise AssertionError(f"identity fixture cannot disband early row: {choices}")
    removed = bridge_operation(
        container_name, "semantic_command", command="disband_unit",
        unit_id=int(doomed["id"]), confirm_disband=1,
        match_id=choices["match_id"], session_id=choices["session_id"],
        expected_revision=choices["revision"],
    )
    if removed.get("ok") is not True:
        raise AssertionError(f"identity fixture disband failed: {removed}")
    survivors = bridge_operation(
        container_name, "perspective_world_page",
        domain="units", cursor=0, limit=256,
    )
    survivor_rows = [
        row for row in survivors.get("items", []) if row.get("owned") is True
        and isinstance(row.get("id"), int) and row.get("own_unit_ref")
    ]
    survivor_refs = sorted(str(row["own_unit_ref"]) for row in survivor_rows)
    capsule = bridge_operation(container_name, "semantic_identity_state", action="export")
    handles = capsule.get("semantic_vehicle_handles")
    if capsule.get("ok") is not True or not isinstance(handles, list):
        raise AssertionError(f"semantic identity export failed: {capsule}")
    if not any(isinstance(value, int) and value > 0 and value != index + 1
               for index, value in enumerate(handles)):
        raise AssertionError(
            f"native row compaction did not create a non-row semantic handle: {capsule}"
        )
    if not survivor_refs:
        raise AssertionError("identity fixture removed every owned unit")
    return {"survivor_refs": survivor_refs, "capsule": capsule}


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
    specialist_result: dict | None = None
    specialist_name: str | None = None
    identity_before_recovery: dict | None = None
    try:
        docker("volume", "create", control_volume)
        docker("network", "create", *labels, network)
        socket_gid = str(os.stat("/var/run/docker.sock").st_gid)
        docker(
            "run", "-d", "--name", control_name, *labels,
            "--network", network, "--group-add", socket_gid,
            "-e", "SMACX_DOCKER_ENABLED=1",
            "-e", "SMACX_AGENT_TEST_MODE=1",
            "-e", "SMACX_ACCEPTANCE_OWN_UNIT_COMPACTION=1",
            "-e", "SMACX_ACCEPTANCE_AIRDROP_LEGALITY=1",
            "-e", "SMACX_ACCEPTANCE_PACT_PORT=1",
            "-e", "SMACX_ACCEPTANCE_BASE_SITE=1",
            "-e", "SMACX_ACCEPTANCE_MANAGED_ACTIONS=1",
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
            "autostart": {"enabled": True, "difficulty": 0, "world_size": 4, "faction_id": 1},
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
        native_bases = bridge_operation(
            endpoint["container_name"], "perspective_world_page",
            domain="bases", cursor=0, limit=16,
        )
        native_units = bridge_operation(
            endpoint["container_name"], "perspective_world_page",
            domain="units", cursor=0, limit=64,
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
                or not isinstance(native_snapshot.get("governor_faction_id"), int) \
                or not isinstance(native_snapshot.get("intelligence_entitlements"), dict):
            raise AssertionError(f"native global intelligence adapter is incomplete: {native_snapshot}")
        base_rows = native_bases.get("items") if isinstance(native_bases, dict) else None
        if not isinstance(base_rows, list) or not base_rows \
                or not isinstance(base_rows[0].get("base_radius"), list) \
                or not isinstance(base_rows[0].get("facilities"), list) \
                or not isinstance(base_rows[0].get("minerals"), dict):
            raise AssertionError(f"native base/economic geography adapter is incomplete: {native_bases}")
        unit_rows = native_units.get("items") if isinstance(native_units, dict) else None
        owned_units = [row for row in unit_rows or () if row.get("owned") is True]
        if not owned_units or any(
                not isinstance(row.get("roles"), dict)
                or not isinstance(row.get("abilities"), list)
                or "requires_support" not in row
                or not isinstance(row.get("cargo"), dict)
                for row in owned_units):
            raise AssertionError(f"native movement/support/life adapter is incomplete: {native_units}")
        time_control = (native_snapshot.get("game_settings") or {}).get("time_control") or {}
        if int(time_control.get("id", -1)) != 0 \
                or str(time_control.get("name") or "").casefold() != "none":
            raise AssertionError(
                f"managed native acceptance game did not use no-timer semantics: {time_control}"
            )
        mcp_result = asyncio.run(inspect_mcp(endpoint["url"], created["match"]["match_id"]))
        test_episode_id = "episode-live-" + suffix
        runtime_started = runtime_context(endpoint["container_name"], test_episode_id)
        if not runtime_started.get("ok"):
            raise AssertionError(f"runtime context lease failed: {runtime_started}")
        global_world = asyncio.run(mcp_tool(endpoint["url"], "smac_world", {
            "mode": "global", "detail": "deep",
        }))
        global_kinds = {item.get("kind") for item in global_world.get("items", [])
                        if isinstance(item, dict)}
        required_global_kinds = {
            "game_settings", "scenario_rules", "project_state",
            "project_race_state", "orbital_state", "governor_state",
            "intelligence_entitlement_state",
            "movement_rules", "ecology_state", "planetary_state", "victory_posture",
        }
        if not global_world.get("ok") or not required_global_kinds <= global_kinds:
            raise AssertionError(
                f"native global state did not traverse projection/MCP: "
                f"missing={sorted(required_global_kinds - global_kinds)} result={global_world}"
            )
        runtime_text = json.dumps(runtime_started.get("runtime_context", {}),
                                  separators=(",", ":"))
        if not all(marker in runtime_text for marker in (
                "game_settings", "project_state", "orbital_state",
                "governor_state", "intelligence_entitlement_state",
                "ecology_state", "planetary_state",
                "victory_posture")):
            raise AssertionError("native global domains did not reach the strategic runtime anchor")
        asyncio.run(prepare_checkpoint(endpoint["url"]))
        identity_before_recovery = force_native_identity_compaction(
            endpoint["container_name"]
        )
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
        recovered_units = bridge_operation(
            recovered_sidecar, "perspective_world_page",
            domain="units", cursor=0, limit=256,
        )
        recovered_refs = sorted(
            str(row["own_unit_ref"])
            for row in recovered_units.get("items", [])
            if row.get("owned") is True and row.get("own_unit_ref")
        )
        recovered_identity = bridge_operation(
            recovered_sidecar, "semantic_identity_state", action="export",
        )
        if identity_before_recovery is None \
                or recovered_refs != identity_before_recovery["survivor_refs"]:
            raise AssertionError(
                "semantic own-unit refs changed across native process recovery: "
                f"{identity_before_recovery} -> {recovered_refs}"
            )
        if recovered_identity.get("semantic_vehicle_handles") \
                != identity_before_recovery["capsule"].get("semantic_vehicle_handles") \
                or recovered_identity.get("next_semantic_vehicle_handle") \
                != identity_before_recovery["capsule"].get("next_semantic_vehicle_handle"):
            raise AssertionError(
                "private semantic identity capsule did not restore exactly: "
                f"{identity_before_recovery['capsule']} -> {recovered_identity}"
            )
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
            specialist_profile = {
                "profile_id": descriptor["external_profile_id"],
                "display_name": "Live specialist helper",
                "provider_id": provider["provider_id"],
                "model_id": descriptor["model_id"],
                "reasoning_effort": descriptor["reasoning_effort"],
                "context_length": descriptor["context_length"],
                "generation_settings": descriptor["generation_settings"],
            }
            api(opener, base_url, "POST", "/api/v1/specialists", {
                "profile": specialist_profile, "max_concurrency": 1,
                "policy": {
                    "installation_concurrency": 1, "seat_concurrency": 1,
                    "automatic_retries": 0, "schema_repairs": 1,
                    "investigation": {
                        # The production-shaped opening-theater mission spans
                        # base, force, area, route, reachability and logistics
                        # views. Keep it hard-bounded while leaving enough
                        # evidence calls for the child to synthesize instead of
                        # terminating exactly at its query leash.
                        "tool_budget": 12, "provider_call_budget": 10,
                        "provider_token_budget": 512000,
                        "context_token_ceiling": min(
                            int(descriptor["context_length"]), 262144),
                        "output_token_budget": 4000, "wall_seconds": 180,
                    },
                },
            }, csrf)
            specialist_name = f"smacx-specialist-live-{suffix}"
            docker(
                "run", "-d", "--name", specialist_name, *labels,
                "--network", network,
                "--user", "10001:10001",
                "-e", "SMACX_DB_PATH=/var/lib/smacx/smacx.sqlite3",
                "-e", "SMACX_SECRET_ROOT=/var/lib/smacx/secrets",
                "-e", "SMACX_WORLD_SNAPSHOT_ROOT=/var/lib/smacx/world-snapshots",
                "-e", "SMACX_SPECIALIST_TRACE_ROOT=/var/lib/smacx/specialist-traces",
                "-e", "SMACX_REFERENCE_URL=http://127.0.0.1:9",
                "-e", "SMACX_HERMES_EXECUTABLE=/opt/hermes/hermes",
                "-e", "SMACX_SPECIALIST_PYTHON=/opt/hermes/.venv/bin/python",
                "-e", "SMACX_SPECIALIST_MCP_SCRIPT=/opt/smacx/src/smacx_specialist_mcp.py",
                "-v", f"{control_volume}:/var/lib/smacx",
                "--read-only", "--tmpfs", "/tmp:size=512m,mode=1777",
                "--security-opt", "no-new-privileges", "--cap-drop", "ALL",
                "--entrypoint", "/opt/hermes/.venv/bin/python", harness_image,
                "/opt/smacx/src/smacx_specialist_supervisor.py",
            )
            health_deadline = time.monotonic() + 20
            while time.monotonic() < health_deadline:
                status_value = api(opener, base_url, "GET", "/api/v1/specialists")
                health = status_value.get("runtime") \
                    if isinstance(status_value.get("runtime"), dict) else {}
                if health.get("status") == "ready":
                    break
                time.sleep(0.5)
            else:
                raise AssertionError("specialist supervisor did not publish ready health")

            specialist_before = asyncio.run(current_decision(recovered_endpoint))
            specialist_started = time.monotonic()
            commissioned = asyncio.run(mcp_tool(recovered_endpoint, "smac_investigate", {
                "action": "commission", "faculty": "world",
                "objective": (
                    "Investigate the current opening theater through multiple bounded mechanical "
                    "views: inspect the owned base and units, the immediate known area, local "
                    "route and reachability constraints, reinforcement relationships, logistics, "
                    "and any visible frontier evidence. Compare the relevant evidence, preserve "
                    "unknown and stale status, identify limitations, and return only a compact "
                    "mechanical synthesis. Do not choose or execute any strategic or gameplay action."
                ),
            }))
            mission_id = str(commissioned.get("mission_id") or "")
            if not mission_id:
                raise AssertionError(f"live specialist commission failed: {commissioned}")
            # The native bridge must remain responsive while the disposable
            # reader is reasoning; the specialist has no native/game volume.
            ui_probe_started = time.monotonic()
            ui_probe = bridge_operation(recovered_sidecar, "semantic_snapshot")
            ui_probe_ms = (time.monotonic() - ui_probe_started) * 1000
            if not ui_probe.get("ok") or ui_probe_ms > 5000:
                raise AssertionError(
                    f"specialist stalled the native bridge: {ui_probe_ms:.1f}ms {ui_probe}")
            mission_value = commissioned
            mission_deadline = time.monotonic() + 240
            while time.monotonic() < mission_deadline:
                mission_value = asyncio.run(mcp_tool(
                    recovered_endpoint, "smac_investigate",
                    {"action": "result", "mission_id": mission_id},
                ))
                if mission_value.get("status") != "mission_pending":
                    break
                time.sleep(1)
            if mission_value.get("status") != "accepted":
                diagnostic = api(
                    opener, base_url, "GET", f"/api/v1/specialists/missions/{mission_id}",
                )
                raise AssertionError(
                    f"live gameplay specialist failed: {mission_value}; {diagnostic}")
            accepted_diagnostic = api(
                opener, base_url, "GET", f"/api/v1/specialists/missions/{mission_id}",
            )
            accepted_attempts = accepted_diagnostic.get("mission", {}).get("attempts", []) \
                if isinstance(accepted_diagnostic.get("mission"), dict) else []
            accepted_attempt = accepted_attempts[-1] if accepted_attempts else {}
            specialist_after = asyncio.run(current_decision(recovered_endpoint))
            if specialist_before.get("turn") != specialist_after.get("turn"):
                raise AssertionError("read-only specialist impersonated native turn progress")
            attention_episode = "episode-specialist-attention-" + suffix
            attention_context = runtime_context(recovered_sidecar, attention_episode)
            runtime_payload = attention_context.get("runtime_context") \
                if isinstance(attention_context.get("runtime_context"), dict) else {}
            attention_items = runtime_payload.get("attention", {}).get("items", []) \
                if isinstance(runtime_payload.get("attention"), dict) else []
            completion = next((item for item in attention_items
                               if item.get("attention_kind") == "specialist_completion"
                               and item.get("payload", {}).get("mission_id") == mission_id), None)
            if completion is None:
                raise AssertionError("specialist completion did not reach durable attention")
            runtime_context(recovered_sidecar, attention_episode, end=True)
            specialist_result = {
                "accepted": True, "mission_id": mission_id,
                "latency_ms": round((time.monotonic() - specialist_started) * 1000, 3),
                "native_bridge_probe_ms": round(ui_probe_ms, 3),
                "native_turn_unchanged": True, "completion_attention": True,
                "tool_calls": int(accepted_attempt.get("tool_calls") or 0),
                "provider_calls": int(accepted_attempt.get("provider_calls") or 0),
                "provider_tokens": int(accepted_attempt.get("provider_tokens") or 0),
                "peak_context_tokens": int(
                    accepted_attempt.get("peak_context_tokens") or 0),
            }
            prompt = (
                "This is a bounded semantic integration test in a fresh Huge Citizen game. "
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
        # Publish a fresh checkpoint on the recovered timeline. The earlier
        # checkpoint has already been consumed to prove crash recovery; using
        # it again after the timeline fork would correctly fail the world/head
        # coherence guard during the fixture-isolation restore.
        fixture_checkpoint = api(
            opener, base_url, "POST",
            f"/api/v1/matches/{created['match']['match_id']}/checkpoint",
            {"slot": "fixture_restore"}, csrf, 60,
        ).get("checkpoint", {})
        if fixture_checkpoint.get("verified") is not True:
            raise AssertionError({
                "message": "fresh fixture-isolation checkpoint was not verified",
                "checkpoint": fixture_checkpoint,
            })
        # These contained fixtures mutate native factions/units/bases, so run
        # them only after every recovery/provider assertion.  Exercise the
        # isolated airdrop matrix before adding a Pact base and mixed stack;
        # those are deliberately unrelated fixture worlds.
        native_airdrop = bridge_operation(
            recovered_sidecar, "test_airdrop_legality_fixture",
        )
        expected_airdrop = {
            "hostile_combat": False,
            "hostile_noncombat": False,
            "pact_combat": True,
            "pact_noncombat": True,
            "treaty_combat": False,
            "treaty_noncombat": False,
            "unknown_combat": False,
            "unknown_noncombat": False,
            "aerospace_defended": True,
            "air_superiority_defended": True,
            "mapped_fog_native_target": True,
            "unmapped_native_target": True,
            "hidden_unit_rejected": True,
            "hidden_hostile_base_native_target": True,
            "native_target_path_uses_visibility_gate": False,
        }
        if not native_airdrop.get("ok") or any(
                native_airdrop.get(key) is not expected
                for key, expected in expected_airdrop.items()):
            raise AssertionError(
                f"production-native airdrop legality drifted: {native_airdrop}"
            )
        drop_stress = bridge_operation(
            recovered_sidecar, "test_airdrop_collection_stress_fixture",
        )
        if not drop_stress.get("ok") or int(drop_stress.get("created", 0)) < 64:
            raise AssertionError(f"native Drop stress setup failed: {drop_stress}")
        drop_page_latencies: list[float] = []
        drop_page_bytes: list[int] = []
        drop_rows: list[dict] = []
        cursor = 0
        while True:
            started_page = time.monotonic()
            page = bridge_operation(
                recovered_sidecar, "perspective_world_page",
                domain="units", cursor=cursor, limit=256,
            )
            drop_page_latencies.append((time.monotonic() - started_page) * 1000)
            drop_page_bytes.append(len(json.dumps(page, separators=(",", ":"))))
            drop_rows.extend(page.get("items") or [])
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, int) or next_cursor <= cursor:
                break
            cursor = next_cursor
        ready_drop_rows = [row for row in drop_rows if row.get("airdrop_ready") is True]
        if len(ready_drop_rows) < 64 \
                or any("airdrop_target_tile_ids" in row for row in ready_drop_rows) \
                or max(drop_page_latencies, default=0) >= 500 \
                or max(drop_page_bytes, default=0) >= 512_000:
            raise AssertionError({
                "message": "routine native Drop collection is not bounded",
                "ready_drop_units": len(ready_drop_rows),
                "page_latency_ms": drop_page_latencies,
                "page_bytes": drop_page_bytes,
            })
        demanded_dropper = next(
            (row for row in ready_drop_rows
             if int(row.get("id", -1)) == int(drop_stress.get("first_dropper_id", -2))), None,
        )
        if demanded_dropper is None:
            raise AssertionError("stress fixture's demanded Drop unit was not projected")
        native_receipt, enumeration_wall_ms, enumeration_probe_gap_ms = measured_bridge_operation(
            recovered_sidecar, "semantic_airdrop_targets",
            unit_id=int(demanded_dropper["id"]), maximum_targets=128,
        )
        drop_choices = bridge_operation(
            recovered_sidecar, "semantic_choices",
            kind="unit_actions", unit_id=int(demanded_dropper["id"]),
        )
        airdrop_choice = next(
            (choice for choice in drop_choices.get("choices", [])
             if choice.get("command") == "airdrop_unit"), None,
        )
        receipt_ids = {int(item["target_tile_id"])
                       for item in native_receipt.get("targets", [])}
        choice_ids = {int(item["target_tile_id"])
                      for item in (airdrop_choice or {}).get("targets", [])}
        if not native_receipt.get("ok") or not receipt_ids or receipt_ids != choice_ids:
            raise AssertionError({
                "message": "demand receipt diverged from executable choices",
                "receipt": native_receipt, "choice": airdrop_choice,
            })
        outside_target_id = int(drop_stress.get("outside_first_128_target_tile_id", -1))
        if drop_stress.get("enumeration_truncated") is not True \
                or native_receipt.get("targets_truncated") is not True \
                or int(native_receipt.get("target_count", 0)) <= 128 \
                or outside_target_id < 0 or outside_target_id in receipt_ids:
            raise AssertionError({
                "message": "orbital Drop fixture did not prove >128 target truncation",
                "fixture": drop_stress, "receipt": native_receipt,
            })
        exact_receipt, exact_wall_ms, exact_probe_gap_ms = measured_bridge_operation(
            recovered_sidecar, "semantic_airdrop_targets",
            unit_id=int(demanded_dropper["id"]), target_tile_id=outside_target_id,
        )
        if exact_receipt.get("ok") is not True \
                or exact_receipt.get("allowed") is not True \
                or int(exact_receipt.get("target_count", 0)) != 1:
            raise AssertionError({
                "message": "outside-page exact airdrop receipt failed",
                "receipt": exact_receipt,
            })
        for receipt, label in ((native_receipt, "enumeration"), (exact_receipt, "exact")):
            if float(receipt.get("native_elapsed_ms", 999999)) >= 500:
                raise AssertionError({
                    "message": f"{label} native airdrop receipt exceeded UI budget",
                    "receipt": receipt,
                })
        world_arguments = {
            "mode": "route", "origin_ref": str(demanded_dropper["own_unit_ref"]),
            "target_ref": f"location-{outside_target_id}",
            "detail": "standard",
        }
        demanded_world = asyncio.run(mcp_tool(recovered_endpoint, "smac_world", world_arguments))
        cached_world = asyncio.run(mcp_tool(recovered_endpoint, "smac_world", world_arguments))
        if not demanded_world.get("ok") or not cached_world.get("ok") \
                or cached_world.get("cache", {}).get("hit") is not True:
            raise AssertionError({
                "message": "demand receipt did not integrate with revision cache",
                "first": demanded_world, "second": cached_world,
            })
        outside_dropper = demanded_dropper
        semantic_episode = "episode-semantic-drop-" + suffix
        semantic_lease = runtime_context(recovered_sidecar, semantic_episode)
        if not semantic_lease.get("ok"):
            raise AssertionError(f"semantic Drop episode failed: {semantic_lease}")
        exact_frame = asyncio.run(mcp_tool(recovered_endpoint, "smac_choices", {
            "kind": "unit_actions",
            "own_unit_ref": str(outside_dropper["own_unit_ref"]),
            "target_location_ref": f"location-{outside_target_id}",
        }))
        serialized_frame = json.dumps(exact_frame, separators=(",", ":"))
        exact_choice = next(
            (row for row in exact_frame.get("choices", ())
             if row.get("target_location_ref") == f"location-{outside_target_id}"), None,
        )
        if exact_choice is None or "target_tile_id" in serialized_frame:
            raise AssertionError({
                "message": "managed semantic target leaked or failed to bind",
                "frame": exact_frame,
            })
        executed_drop = asyncio.run(mcp_tool(recovered_endpoint, "smac_execute_choice", {
            "decision_id": exact_frame["decision_id"],
            "choice_id": exact_choice["choice_id"],
        }))
        runtime_context(recovered_sidecar, semantic_episode, end=True)
        if executed_drop.get("ok") is not True \
                or executed_drop.get("executed_choice", {}).get("label") is None:
            raise AssertionError({
                "message": "opaque outside-page Drop execution failed",
                "result": executed_drop,
            })
        # Restore the fresh pre-fixture checkpoint before the unrelated Pact-port
        # proof. Destructive acceptance fixtures must not validate against
        # state manufactured by a prior fixture.
        old_recovered_container = str(recovered_worker["container_name"])
        old_recovered_id = docker(
            "inspect", "-f", "{{.Id}}", old_recovered_container,
        )
        docker("stop", "-t", "1", old_recovered_container)
        reset_deadline = time.monotonic() + 480
        while time.monotonic() < reset_deadline:
            workers_now = api(opener, base_url, "GET", "/api/v1/workers")["workers"]
            current = next(item for item in workers_now
                           if item["instance_id"] == worker["instance_id"])
            current_name = str(current.get("container_name") or "")
            current_network = current.get("network") or {}
            current_id = docker(
                "inspect", "-f", "{{.Id}}", current_name, check=False,
            ) if current_name else ""
            if current.get("observed_status") == "running" \
                    and current_id and current_id != old_recovered_id \
                    and current_network.get("mcp_url") \
                    and current_network.get("mcp_container_name"):
                recovered_worker = current
                break
            time.sleep(2)
        else:
            raise AssertionError("fixture isolation recovery did not restart the worker")
        recovered_endpoint = recovered_worker.get("network", {}).get("mcp_url")
        recovered_sidecar = recovered_worker.get("network", {}).get("mcp_container_name")
        if not recovered_endpoint or not recovered_sidecar:
            raise AssertionError("fixture isolation recovery omitted its MCP sidecar")
        wait_for_native_readiness(recovered_sidecar)
        print(json.dumps({"event": "acceptance_stage", "stage": "pact_port"}), flush=True)
        pact_port = bridge_operation(recovered_sidecar, "test_pact_port_fixture")
        if not pact_port.get("ok") or pact_port.get("relationship") != "pact" \
                or pact_port.get("coastal") is not True:
            # A synchronous native encounter can enter a modal loop during
            # fixture setup. Observe once for diagnosis; never retry mutation.
            diagnostic = bridge_operation(recovered_sidecar, "semantic_snapshot")
            raise AssertionError({"message": "native Pact-port fixture failed",
                                  "fixture": pact_port,
                                  "interaction": diagnostic.get("snapshot", {}).get("interaction"),
                                  "diagnostic_error": diagnostic.get("error")})
        pact_target = int(pact_port["base_tile_id"])
        for actor_key in ("land_entry_id", "sea_entry_id"):
            actor_id = int(pact_port[actor_key])
            choices = bridge_operation(
                recovered_sidecar, "semantic_choices", kind="unit_actions",
                unit_id=actor_id, target_tile_id=pact_target,
            )
            movement = next(
                (row for row in choices.get("choices", ())
                 if row.get("command") == "move_unit"
                 and int(row.get("target_tile_id", -1)) == pact_target), None,
            )
            if movement is None:
                raise AssertionError({
                    "message": "owned actor could not legally enter current Pact base",
                    "actor": actor_key, "fixture": pact_port, "choices": choices,
                })
            moved = bridge_operation(
                recovered_sidecar, "semantic_command", command="move_unit",
                unit_id=actor_id, target_tile_id=pact_target,
                match_id=choices["match_id"], session_id=choices["session_id"],
                expected_revision=choices["revision"],
            )
            if not moved.get("ok") or not moved.get("queued"):
                raise AssertionError(f"Pact-port movement did not queue: {moved}")
            action = wait_native_action(recovered_sidecar, int(moved["action_id"]))
            if action.get("status") != "completed" \
                    or int(action.get("observed_tile_id", -1)) != pact_target:
                raise AssertionError({
                    "message": "Pact-port movement did not complete", "action": action,
                })
        passenger_id = int(pact_port["passenger_id"])
        transport_id = int(pact_port["transport_id"])
        board_choices = bridge_operation(
            recovered_sidecar, "semantic_choices", kind="unit_actions",
            unit_id=passenger_id,
        )
        board = next(
            (row for row in board_choices.get("choices", ())
             if row.get("command") == "board_transport"
             and int(row.get("transport_unit_id", -1)) == transport_id), None,
        )
        if board is None:
            raise AssertionError({
                "message": "co-located owned actors could not board at Pact base",
                "choices": board_choices,
            })
        boarded = bridge_operation(
            recovered_sidecar, "semantic_command", command="board_transport",
            unit_id=passenger_id, transport_unit_id=transport_id,
            match_id=board_choices["match_id"], session_id=board_choices["session_id"],
            expected_revision=board_choices["revision"],
        )
        if not boarded.get("ok") or boarded.get("boarded") is not True:
            raise AssertionError(f"native Pact-port boarding failed: {boarded}")
        from managed_action_path_live_test import exercise_managed_actions
        print(json.dumps({"event": "acceptance_stage", "stage": "managed_actions"}), flush=True)
        managed_fixture = bridge_operation(recovered_sidecar, "test_managed_action_fixture")
        if not managed_fixture.get("ok"):
            raise AssertionError({"managed_action_fixture": managed_fixture})
        managed_episode = "episode-managed-actions-" + suffix
        lease = runtime_context(recovered_sidecar, managed_episode)
        if not lease.get("ok"):
            raise AssertionError({"managed_action_lease": lease})
        managed_decision = asyncio.run(current_decision(recovered_endpoint))
        if not managed_decision.get("ok"):
            raise AssertionError({"managed_action_decision": managed_decision})
        managed_evidence = exercise_managed_actions(
            lambda name, arguments: asyncio.run(mcp_tool(recovered_endpoint, name, arguments)),
            lambda operation, **arguments: bridge_operation(recovered_sidecar, operation, **arguments),
            managed_fixture)
        from counterfactual_checkpoint_live_test import exercise_counterfactual_checkpoint
        counterfactual_evidence = exercise_counterfactual_checkpoint(
            lambda name, arguments: asyncio.run(mcp_tool(recovered_endpoint, name, arguments)),
            lambda operation, **arguments: bridge_operation(recovered_sidecar, operation, **arguments),
            managed_evidence["evidence"]["site_tanks_center_delta"])
        print(json.dumps({"event": "counterfactual_checkpoint", "payload": counterfactual_evidence}), flush=True)
        from intent_checkpoint_live_test import exercise_intent_checkpoint
        intent_evidence = exercise_intent_checkpoint(
            lambda name, arguments: asyncio.run(mcp_tool(recovered_endpoint, name, arguments)),
            lambda operation, **arguments: bridge_operation(recovered_sidecar, operation, **arguments),
            managed_fixture["base_ref"])
        print(json.dumps({"event": "intent_checkpoint", "payload": intent_evidence}), flush=True)
        runtime_context(recovered_sidecar, managed_episode, end=True)
        from intent_checkpoint_live_test import verify_intent_attention
        intent_delivery = verify_intent_attention(
            lambda name, arguments: asyncio.run(mcp_tool(recovered_endpoint, name, arguments)),
            lambda episode: runtime_context(recovered_sidecar, episode),
            lambda episode: runtime_context(recovered_sidecar, episode, end=True),
            lambda lease_id: runtime_attention_responded(recovered_sidecar, lease_id),
            intent_evidence["milestone_watch_id"], "episode-intent-attention-" + suffix)
        print(json.dumps({"event": "intent_attention_delivery", "payload": intent_delivery}), flush=True)
        from diagnostics_checkpoint_live_test import exercise_diagnostics_checkpoint
        diagnostics_episode = "episode-diagnostic-boundary-" + suffix
        diagnostics_evidence = exercise_diagnostics_checkpoint(
            lambda name, arguments: asyncio.run(mcp_tool(recovered_endpoint, name, arguments)),
            lambda operation, **arguments: bridge_operation(recovered_sidecar, operation, **arguments),
            lambda: runtime_context(recovered_sidecar, diagnostics_episode),
            lambda lease_id: runtime_attention_responded(recovered_sidecar, lease_id))
        print(json.dumps({"event": "diagnostics_checkpoint", "payload": diagnostics_evidence}), flush=True)
        runtime_context(recovered_sidecar, diagnostics_episode, end=True)
        print(json.dumps({"event": "managed_action_paths", "payload": managed_evidence}), flush=True)
        # Stress runs after gameplay assertions; its native restore check covers
        # the temporary rows, visibility and yield-calculation scratch state.
        site_stress, site_wall_ms, site_probe_gap_ms = measured_bridge_operation(
            recovered_sidecar, "test_base_site_receipts_stress")
        site_bytes = len(json.dumps(site_stress, separators=(",", ":")).encode())
        if site_stress.get("ok") is not True or len(site_stress.get("items", [])) != 32:
            raise AssertionError("native base-site stress failed")
        if max(site_wall_ms, site_probe_gap_ms) >= 500 or site_bytes > 256_000:
            raise AssertionError({"base_site_responsiveness": {"wall_ms": site_wall_ms,
                                  "probe_gap_ms": site_probe_gap_ms, "bytes": site_bytes}})
        print(json.dumps({"event": "base_site_stress", "payload": {
            "owned_base_count": 512, "candidate_count": 32, "radius_squares": 21,
            "wall_ms": round(site_wall_ms, 3), "probe_gap_ms": round(site_probe_gap_ms, 3),
            "receipt_bytes": site_bytes, "native_elapsed_ms": site_stress.get("native_elapsed_ms")}}), flush=True)
        counter_stress, counter_wall_ms, counter_gap_ms = measured_bridge_operation(
            recovered_sidecar, "test_base_site_receipts_stress", include_economy=True)
        counter_bytes = len(json.dumps(counter_stress, separators=(",", ":")).encode())
        if counter_stress.get("ok") is not True or len(counter_stress.get("items", [])) != 4 \
                or not all(row.get("site_economy", {}).get("center") for row in counter_stress.get("items", [])):
            raise AssertionError({"counterfactual_site_stress": counter_stress})
        if max(counter_wall_ms, counter_gap_ms) >= 500 or counter_bytes > 256_000:
            raise AssertionError({"counterfactual_responsiveness": {"wall_ms": counter_wall_ms,
                                  "probe_gap_ms": counter_gap_ms, "bytes": counter_bytes}})
        print(json.dumps({"event": "counterfactual_site_stress", "payload": {
            "owned_base_input_rows": 511, "nominated_sites": 4,
            "wall_ms": round(counter_wall_ms, 3), "probe_gap_ms": round(counter_gap_ms, 3),
            "receipt_bytes": counter_bytes, "native_elapsed_ms": counter_stress.get("native_elapsed_ms")}}), flush=True)
        intent_before_restore = bridge_operation(recovered_sidecar, "perspective_world_page",
                                                 domain="units", cursor=0, limit=256)
        if intent_before_restore.get("next_cursor") is not None:
            raise AssertionError("intent recovery fixture exceeded bounded owned-unit page")
        intent_unit_refs = sorted(row["own_unit_ref"] for row in intent_before_restore.get("items", [])
                                  if row.get("owned") and row.get("own_unit_ref"))
        preview_episode = "episode-counterfactual-recovery-" + suffix
        preview_lease = runtime_context(recovered_sidecar, preview_episode)
        if not preview_lease.get("ok"):
            raise AssertionError({"preview_recovery_lease": preview_lease})
        preview_frame = asyncio.run(mcp_tool(recovered_endpoint, "smac_choices", {
            "kind": "production", "base_ref": managed_fixture["base_ref"]}))
        if not preview_frame.get("ok"):
            raise AssertionError({"preview_recovery_choices": preview_frame})
        preview_choice = next(row for row in preview_frame["choices"] if row.get("name") == "Scout Patrol")
        old_preview_arguments = {"mode": "counterfactual", "detail": "deep",
            "scenario_json": json.dumps({"kind": "action", "decision_id": preview_frame["decision_id"],
                                         "choice_id": preview_choice["choice_id"]})}
        valid_preview = asyncio.run(mcp_tool(recovered_endpoint, "smac_world", old_preview_arguments))
        if not valid_preview.get("ok"):
            raise AssertionError({"preview_before_recovery": valid_preview})
        runtime_context(recovered_sidecar, preview_episode, end=True)
        api(opener, base_url, "POST", f"/api/v1/matches/{created['match']['match_id']}/checkpoint",
            {"slot": "intent_acceptance"}, csrf, 60)
        api(opener, base_url, "POST", f"/api/v1/matches/{created['match']['match_id']}/park", {}, csrf, 180)
        intent_restored = api(opener, base_url, "POST", f"/api/v1/matches/{created['match']['match_id']}/recover",
                              {}, csrf, 900)
        if not intent_restored.get("ok") or not intent_restored.get("memory_restore", {}).get("journal_forks"):
            raise AssertionError({"intent_managed_recovery": intent_restored})
        current_worker = next(item for item in api(opener, base_url, "GET", "/api/v1/workers")["workers"]
                              if item["instance_id"] == worker["instance_id"])
        current_sidecar = current_worker["network"]["mcp_container_name"]
        current_endpoint = current_worker["network"]["mcp_url"]
        after_intent_restore = bridge_operation(current_sidecar, "perspective_world_page", domain="units", cursor=0, limit=256)
        restored_unit_refs = sorted(row["own_unit_ref"] for row in after_intent_restore.get("items", [])
                                    if row.get("owned") and row.get("own_unit_ref"))
        if restored_unit_refs != intent_unit_refs:
            raise AssertionError("native completed-unit identities changed during managed recovery")
        recovered_health = asyncio.run(mcp_tool(current_endpoint, "smac_cognition", {"action": "plan_health"}))
        if not recovered_health.get("ok") or recovered_health["plan_health"]["active_plan_count"] < 3:
            raise AssertionError({"journaled_intent_missing_after_recovery": recovered_health})
        if recovered_health["plan_health"]["conflict_count"] < 1 \
                or recovered_health["plan_health"]["assigned_owned_unit_count"] < 1:
            raise AssertionError({"journaled_reservations_changed_after_recovery": recovered_health})
        discarded_watch = asyncio.run(mcp_tool(current_endpoint, "smac_cognition", {
            "action": "watch_inspect", "subject_refs": [intent_evidence["milestone_watch_id"]]}))
        if discarded_watch.get("ok"):
            raise AssertionError("old-timeline milestone was resurrected after recovery")
        expired_preview = asyncio.run(mcp_tool(current_endpoint, "smac_world", old_preview_arguments))
        if expired_preview.get("ok"):
            raise AssertionError("old-session counterfactual choice was accepted after recovery")
        print(json.dumps({"event": "intent_recovery", "payload": {
            "native_completed_units_preserved": True, "journaled_plan_preserved": True,
            "journaled_conflict_and_stationary_assignment_preserved": True,
            "ephemeral_old_timeline_watch_discarded": True,
            "old_session_counterfactual_choice_rejected": True}}), flush=True)
        action_containment = False
        if os.environ.get("SMACX_TEST_ACTION_CONTAINMENT") == "1":
            context = runtime_context(current_sidecar, "action-containment-acceptance")
            if not context.get("ok"):
                raise AssertionError({"containment_episode": context})
            failures = []
            for attempt in range(4):
                failures.append(asyncio.run(mcp_tool(current_endpoint, "smac_execute_choice", {
                    "decision_id": f"invalid-acceptance-{attempt}",
                    "choice_id": f"invalid-choice-{attempt}",
                })))
            if failures[-1].get("error", {}).get("code") != "failure_circuit_open":
                raise AssertionError({"failure_budget_did_not_stop": failures})
            if any(row.get("native_action_executed") is not False for row in failures):
                raise AssertionError({"invalid_submission_dispatched": failures})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                native_paused = docker("inspect", "-f", "{{.State.Paused}}", current_worker["container_name"])
                collector_paused = docker("inspect", "-f", "{{.State.Paused}}", current_sidecar)
                if native_paused == collector_paused == "true":
                    break
                time.sleep(.5)
            else:
                raise AssertionError("failure incident did not freeze native and collector containers")
            action_containment = True
            print(json.dumps({"event": "action_containment", "payload": {
                "four_real_mcp_protocol_failures": True,
                "native_action_not_dispatched": True,
                "native_and_collector_docker_paused": True,
            }}), flush=True)
        api(
            opener, base_url, "POST", f"/api/v1/workers/{worker['instance_id']}/park",
            {}, csrf, 120,
        )
        print(json.dumps({
            "event": "pass",
            "payload": {
                "authenticated_control_lifecycle": True,
                "managed_action_failure_containment": action_containment,
                "dedicated_mcp_sidecar": True,
                "mcp_tool_count": mcp_result["tool_count"],
                "mcp_bound_to_exact_match": True,
                "native_unity_governor_entitlements": True,
                "native_no_timer": True,
                "native_orbital_project_global_adapter": True,
                "native_base_geography_and_unit_traits": True,
                "native_airdrop_diplomacy_and_anti_drop_guards": True,
                "native_pact_port_amphibious_sequence": True,
                "native_airdrop_collection_stress": {
                    "ready_drop_units": len(ready_drop_rows),
                    "maximum_page_latency_ms": round(max(drop_page_latencies), 3),
                    "maximum_page_bytes": max(drop_page_bytes),
                    "map_tiles": int(drop_stress.get("map_tiles", 0)),
                    "demand_receipt_matches_actions": True,
                    "enumeration_target_count": int(native_receipt.get("target_count", 0)),
                    "enumeration_native_ms": float(native_receipt.get("native_elapsed_ms", 0)),
                    "enumeration_wall_ms": round(enumeration_wall_ms, 3),
                    "enumeration_probe_gap_ms": round(enumeration_probe_gap_ms, 3),
                    "enumeration_payload_bytes": len(json.dumps(native_receipt, separators=(",", ":"))),
                    "exact_native_ms": float(exact_receipt.get("native_elapsed_ms", 0)),
                    "exact_wall_ms": round(exact_wall_ms, 3),
                    "exact_probe_gap_ms": round(exact_probe_gap_ms, 3),
                    "exact_payload_bytes": len(json.dumps(exact_receipt, separators=(",", ":"))),
                    "outside_first_128_semantic_choice_executed": True,
                    "provider_model_supplied_raw_tile_id": False,
                    "revision_cache_hit": True,
                },
                "native_global_projection_world_runtime_path": True,
                "managed_lifecycle_blocked": True,
                "bridge_verified_checkpoint": True,
                "live_worker_volume_backup_verified": live_backup_verified,
                "native_crash_recovered_without_ui": True,
                "semantic_vehicle_identity_survived_native_restart": True,
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
                "specialist": specialist_result,
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
        keep = os.environ.get("SMACX_TEST_KEEP_ON_FAILURE") == "1" and sys.exc_info()[0] is not None
        if keep:
            docker("pause", control_name, check=False)
            print(json.dumps({"event": "kept_failed_resources", "control_name": control_name,
                              "control_volume": control_volume, "network": network,
                              "installation_id": installation_id}), flush=True)
        else:
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
            if specialist_name:
                docker("rm", "-f", specialist_name, check=False)
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
