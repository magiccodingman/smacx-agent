"""Linux-side controller for the SMACX in-process agent bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import threading
import time
from typing import Any
import re
import uuid


PROJECT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT / "runtime"
GAME = RUNTIME / "game"
TOKEN_FILE = RUNTIME / "agent-token"
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 47813
STEAM_ROOT = Path.home() / ".local/share/Steam"
PRESSURE_VESSEL = STEAM_ROOT / "steamapps/common/SteamLinuxRuntime_4/run"
PROTON = STEAM_ROOT / "steamapps/common/Proton - Experimental/proton"
COMPAT_DATA = RUNTIME / "compatdata"
LOG_FILE = RUNTIME / "game-launch.log"
KNOWLEDGE_ROOT = Path.home() / "Documents/ai/SidMeiers/games"
IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
SLOT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KNOWLEDGE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
KNOWLEDGE_CATEGORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")
_knowledge_lock = threading.Lock()


class BridgeUnavailable(ConnectionError):
    pass


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="ascii").strip()


def bridge_request(operation: str, timeout: float = 8.0, **arguments: Any) -> dict[str, Any]:
    request = {"op": operation, "token": _token(), **arguments}
    try:
        with socket.create_connection((BRIDGE_HOST, BRIDGE_PORT), timeout=timeout) as connection:
            connection.settimeout(timeout)
            connection.sendall(json.dumps(request, separators=(",", ":")).encode() + b"\n")
            chunks: list[bytes] = []
            size = 0
            while True:
                chunk = connection.recv(65536)
                if not chunk:
                    raise BridgeUnavailable("The game bridge closed the connection without a response.")
                newline = chunk.find(b"\n")
                if newline >= 0:
                    chunks.append(chunk[:newline])
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 4_000_000:
                    raise BridgeUnavailable("The game bridge response exceeded the safety limit.")
        return json.loads(b"".join(chunks))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeUnavailable(f"SMACX bridge is unavailable at {BRIDGE_HOST}:{BRIDGE_PORT}: {exc}") from exc


def bridge_available() -> bool:
    try:
        return bool(bridge_request("ping", timeout=1.0).get("ok"))
    except BridgeUnavailable:
        return False


def _isolated_process_pids(game_only: bool = False) -> list[int]:
    """Return only processes belonging to this project's isolated game launch."""
    matches: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / "cwd").resolve() != GAME.resolve():
                continue
            raw_command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            executable = raw_command.split(" ", 1)[0]
            is_game = Path(executable).name.lower() == "terranx.exe"
            is_wrapper = str(GAME / "thinker.exe") in raw_command
            if is_game or (is_wrapper and not game_only):
                matches.append(int(entry.name))
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return matches


def _game_pids() -> list[int]:
    return _isolated_process_pids(game_only=True)


def _terminate_isolated_game(wait_seconds: int = 8) -> dict[str, Any]:
    pids = _isolated_process_pids()
    if not pids:
        return {"ok": True, "stopped": False, "reason": "not_running"}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + min(max(wait_seconds, 1), 15)
    while time.monotonic() < deadline:
        remaining = _isolated_process_pids()
        if not remaining:
            return {"ok": True, "stopped": True, "method": "isolated_process_sigterm", "pids": pids}
        time.sleep(0.25)
    return {"ok": False, "error": "process_close_timeout", "pids": _isolated_process_pids()}


def _read_match_manifest(match_id: str) -> dict[str, Any]:
    manifest_path = KNOWLEDGE_ROOT / match_id / "match.json"
    if not manifest_path.exists():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return manifest if isinstance(manifest, dict) and manifest.get("match_id") == match_id else {}


def _write_match_manifest(match_id: str, manifest: dict[str, Any]) -> Path:
    match_dir = KNOWLEDGE_ROOT / match_id
    match_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = match_dir / "match.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return match_dir


def _knowledge_path(match_id: str) -> Path | None:
    if not IDENTITY_PATTERN.fullmatch(match_id):
        return None
    manifest = _read_match_manifest(match_id)
    if manifest.get("match_id") != match_id:
        return None
    return KNOWLEDGE_ROOT / match_id / "knowledge.json"


def _read_knowledge_file(match_id: str) -> dict[str, Any]:
    path = _knowledge_path(match_id)
    if path is None or not path.exists():
        return {"version": 1, "match_id": match_id, "entries": {}, "history": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != 1 \
    or data.get("match_id") != match_id \
    or not isinstance(data.get("entries"), dict) \
    or not isinstance(data.get("history"), list):
        return {}
    return data


def _write_knowledge_file(match_id: str, data: dict[str, Any]) -> Path:
    path = KNOWLEDGE_ROOT / match_id / "knowledge.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def _active_match_identity() -> dict[str, Any]:
    if not bridge_available():
        return {}
    status = bridge_request("status", timeout=2)
    identity = status.get("identity")
    return identity if isinstance(identity, dict) else {}


def read_match_knowledge(
    match_id: str,
    *,
    key: str = "",
    include_history: bool = False,
) -> dict[str, Any]:
    path = _knowledge_path(match_id)
    if path is None:
        return {"ok": False, "error": "unknown_or_invalid_match_id"}
    active = _active_match_identity()
    if active and active.get("match_id") != match_id:
        return {
            "ok": False,
            "error": "wrong_active_match",
            "message": "Stop the running game or use its current match_id before reading match knowledge.",
            "current_match_id": active.get("match_id"),
        }
    if key and not KNOWLEDGE_KEY_PATTERN.fullmatch(key):
        return {"ok": False, "error": "invalid_knowledge_key"}
    with _knowledge_lock:
        data = _read_knowledge_file(match_id)
    if not data:
        return {"ok": False, "error": "invalid_knowledge_ledger"}
    entries = data["entries"]
    if key:
        entry = entries.get(key)
        if not isinstance(entry, dict):
            return {"ok": False, "error": "knowledge_key_not_found", "key": key}
        result: dict[str, Any] = {
            "ok": True,
            "match_id": match_id,
            "key": key,
            "entry": entry,
        }
        if include_history:
            result["history"] = [
                item for item in data["history"]
                if isinstance(item, dict) and item.get("key") == key
            ]
        return result
    items = [
        {"key": stored_key, **entry}
        for stored_key, entry in sorted(entries.items())
        if isinstance(entry, dict)
    ]
    return {
        "ok": True,
        "match_id": match_id,
        "entries": items,
        "entry_count": len(items),
        "history_count": len(data["history"]),
    }


def put_match_knowledge(
    match_id: str,
    session_id: str,
    observed_revision: str,
    key: str,
    value: str,
    *,
    category: str = "general",
    subject: str = "",
) -> dict[str, Any]:
    if _knowledge_path(match_id) is None:
        return {"ok": False, "error": "unknown_or_invalid_match_id"}
    if not IDENTITY_PATTERN.fullmatch(session_id) or not observed_revision:
        return {"ok": False, "error": "missing_knowledge_observation_guard"}
    if not KNOWLEDGE_KEY_PATTERN.fullmatch(key):
        return {"ok": False, "error": "invalid_knowledge_key"}
    if not KNOWLEDGE_CATEGORY_PATTERN.fullmatch(category):
        return {"ok": False, "error": "invalid_knowledge_category"}
    if not value.strip() or len(value) > 4000 or len(subject) > 160:
        return {"ok": False, "error": "invalid_knowledge_content"}
    try:
        envelope = bridge_request("semantic_snapshot", timeout=5)
    except BridgeUnavailable:
        return {
            "ok": False,
            "error": "game_not_connected",
            "message": "Knowledge writes require the running match and the exact observation being recorded.",
        }
    snapshot = envelope.get("snapshot")
    if not isinstance(snapshot, dict):
        return {"ok": False, "error": "game_not_in_semantic_match"}
    if snapshot.get("match_id") != match_id or snapshot.get("session_id") != session_id:
        return {
            "ok": False,
            "error": "wrong_game_identity",
            "current_match_id": snapshot.get("match_id"),
            "current_session_id": snapshot.get("session_id"),
        }
    if str(snapshot.get("revision", "")) != observed_revision:
        return {
            "ok": False,
            "error": "stale_knowledge_observation",
            "message": "Take a fresh snapshot before recording a newly learned fact.",
            "current_revision": snapshot.get("revision"),
        }
    now = time.time()
    record = {
        "value": value.strip(),
        "category": category,
        "subject": subject.strip(),
        "observed_turn": snapshot.get("turn"),
        "observed_year": snapshot.get("year"),
        "session_id": session_id,
        "observed_revision": observed_revision,
        "recorded_unix": now,
    }
    with _knowledge_lock:
        data = _read_knowledge_file(match_id)
        if not data:
            return {"ok": False, "error": "invalid_knowledge_ledger"}
        entries = data["entries"]
        history = data["history"]
        if key not in entries and len(entries) >= 1000:
            return {"ok": False, "error": "knowledge_entry_limit"}
        if len(history) >= 10000:
            return {"ok": False, "error": "knowledge_history_limit"}
        revision_number = 1 + sum(
            1 for item in history
            if isinstance(item, dict) and item.get("key") == key
        )
        event = {"key": key, "knowledge_revision": revision_number, **record}
        history.append(event)
        entries[key] = {"knowledge_revision": revision_number, **record}
        data["updated_unix"] = now
        path = _write_knowledge_file(match_id, data)
    return {
        "ok": True,
        "match_id": match_id,
        "key": key,
        "entry": entries[key],
        "updated_existing": revision_number > 1,
        "ledger_path": str(path),
    }


def launch_game(
    wait_seconds: int = 30,
    *,
    autostart: bool = False,
    difficulty: int = 0,
    world_size: int = 0,
    faction_id: int = 1,
    blind_research: bool = True,
    initial_research_priority: int = 1,
    initial_tech_id: int = -1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str | None = None,
    session_id: str | None = None,
    startup_save: str | None = None,
) -> dict[str, Any]:
    if bridge_available():
        result: dict[str, Any] = {"ok": True, "launched": False, "reason": "already_running"}
    else:
        match_id = match_id or f"match-{uuid.uuid4().hex}"
        session_id = session_id or f"session-{uuid.uuid4().hex}"
        if not IDENTITY_PATTERN.fullmatch(match_id) or not IDENTITY_PATTERN.fullmatch(session_id):
            return {"ok": False, "error": "invalid_game_identity"}
        missing = [str(path) for path in (PRESSURE_VESSEL, PROTON, GAME / "thinker.exe", GAME / "thinker.dll") if not path.exists()]
        if missing:
            return {"ok": False, "error": "missing_runtime", "paths": missing}
        environment = os.environ.copy()
        environment.update(
            {
                "DISPLAY": environment.get("DISPLAY", ":0"),
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(STEAM_ROOT),
                "STEAM_COMPAT_DATA_PATH": str(COMPAT_DATA),
                "SMACX_AGENT_ENABLE": "1",
                "SMACX_AGENT_TOKEN": _token(),
                "SMACX_AGENT_PORT": str(BRIDGE_PORT),
                "SMACX_AGENT_MATCH_ID": match_id,
                "SMACX_AGENT_SESSION_ID": session_id,
            }
        )
        if autostart and startup_save:
            return {"ok": False, "error": "conflicting_startup_modes"}
        if autostart:
            environment.update(
                {
                    "SMACX_AGENT_AUTOSTART": "1",
                    "SMACX_AGENT_DIFFICULTY": str(min(max(difficulty, 0), 5)),
                    "SMACX_AGENT_WORLD_SIZE": str(min(max(world_size, 0), 4)),
                    "SMACX_AGENT_FACTION_ID": str(min(max(faction_id, 1), 7)),
                    "SMACX_AGENT_BLIND_RESEARCH": "1" if blind_research else "0",
                    "SMACX_AGENT_INITIAL_RESEARCH_PRIORITY": str(min(max(initial_research_priority, 0), 3)),
                    "SMACX_AGENT_NARRATIVE_UI": "1" if narrative_ui else "0",
                    "SMACX_AGENT_TUTORIAL_UI": "1" if tutorial_ui else "0",
                }
            )
            if not blind_research and initial_tech_id >= 0:
                environment["SMACX_AGENT_INITIAL_TECH_ID"] = str(initial_tech_id)
        command = [str(PRESSURE_VESSEL), "--", str(PROTON), "run", str(GAME / "thinker.exe"), "-windowed"]
        if startup_save:
            command.append(startup_save)
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("ab", buffering=0) as log:
            subprocess.Popen(
                command,
                cwd=GAME,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + min(max(wait_seconds, 1), 60)
        while time.monotonic() < deadline and not bridge_available():
            time.sleep(0.25)
        if not bridge_available():
            return {"ok": False, "error": "launch_timeout", "log": str(LOG_FILE)}
        now = time.time()
        manifest = _read_match_manifest(match_id)
        sessions = manifest.get("sessions")
        if not isinstance(sessions, list):
            sessions = []
        session_record: dict[str, Any] = {
            "session_id": session_id,
            "started_unix": now,
            "status": "running",
        }
        if startup_save:
            session_record["loaded_save"] = startup_save
        sessions.append(session_record)
        manifest.update({
            "match_id": match_id,
            "current_session_id": session_id,
            "created_unix": manifest.get("created_unix", now),
            "last_started_unix": now,
            "status": "running",
            "sessions": sessions,
            "knowledge_scope": "Only record information legitimately observed by the player faction in this match.",
        })
        if startup_save:
            manifest["last_loaded_save"] = startup_save
        match_dir = _write_match_manifest(match_id, manifest)
        result = {
            "ok": True,
            "launched": True,
            "bridge_port": BRIDGE_PORT,
            "identity": {"match_id": match_id, "session_id": session_id},
            "knowledge_directory": str(match_dir),
        }
    result["state"] = bridge_request("status")
    return result


def new_game(
    wait_seconds: int = 90,
    difficulty: int = 0,
    world_size: int = 0,
    faction_id: int = 1,
    blind_research: bool = True,
    initial_research_priority: int = 1,
    initial_tech_id: int = -1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str | None = None,
) -> dict[str, Any]:
    if bridge_available():
        state = bridge_request("status")
        return {
            "ok": False,
            "error": "game_already_running",
            "message": "Stop the existing spectator game before starting a new one.",
            "state": state,
        }
    match_id = match_id or f"match-{uuid.uuid4().hex}"
    session_id = f"session-{uuid.uuid4().hex}"
    launched = launch_game(
        wait_seconds=min(max(wait_seconds, 5), 120),
        autostart=True,
        difficulty=difficulty,
        world_size=world_size,
        faction_id=faction_id,
        blind_research=blind_research,
        initial_research_priority=initial_research_priority,
        initial_tech_id=initial_tech_id,
        narrative_ui=narrative_ui,
        tutorial_ui=tutorial_ui,
        match_id=match_id,
        session_id=session_id,
    )
    if not launched.get("ok"):
        return launched
    deadline = time.monotonic() + min(max(wait_seconds, 5), 120)
    last: dict[str, Any] = launched.get("state", {})
    while time.monotonic() < deadline:
        try:
            last = bridge_request("semantic_snapshot")
        except BridgeUnavailable:
            time.sleep(0.5)
            continue
        snapshot = last.get("snapshot", {})
        if snapshot:
            return {
                "ok": True,
                "launched": True,
                "setup": {
                    "difficulty": difficulty,
                    "world_size": world_size,
                    "faction_id": faction_id,
                    "blind_research": blind_research,
                    "initial_research_priority": initial_research_priority if blind_research else None,
                    "initial_tech_id": initial_tech_id if not blind_research else None,
                    "narrative_ui": narrative_ui,
                    "tutorial_ui": tutorial_ui,
                    "input_method": "native_noninteractive_setup",
                },
                "identity": {"match_id": match_id, "session_id": session_id},
                "knowledge_directory": str(KNOWLEDGE_ROOT / match_id),
                "snapshot": last,
            }
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_setup_timeout", "last_state": last, "log": str(LOG_FILE)}


def stop_game(wait_seconds: int = 10) -> dict[str, Any]:
    # Do not synthesize Ctrl+Q/Enter or send desktop-window commands.  A modal
    # game could consume those on the wrong screen.  This project owns an
    # isolated Proton launch, so terminate only processes whose /proc cwd is
    # the copied runtime/game directory.
    identity: dict[str, Any] = {}
    try:
        state = bridge_request("status", timeout=2)
        if isinstance(state.get("identity"), dict):
            identity = state["identity"]
    except BridgeUnavailable:
        pass
    result = _terminate_isolated_game(wait_seconds)
    match_id = identity.get("match_id")
    session_id = identity.get("session_id")
    if result.get("stopped") and isinstance(match_id, str) \
    and isinstance(session_id, str) and IDENTITY_PATTERN.fullmatch(match_id):
        manifest = _read_match_manifest(match_id)
        if manifest:
            now = time.time()
            manifest["status"] = "stopped"
            manifest["last_stopped_unix"] = now
            sessions = manifest.get("sessions", [])
            if isinstance(sessions, list):
                for session in reversed(sessions):
                    if isinstance(session, dict) and session.get("session_id") == session_id:
                        session["status"] = "stopped"
                        session["stopped_unix"] = now
                        break
            _write_match_manifest(match_id, manifest)
        result["identity"] = identity
    return result


def _save_path(match_id: str, slot: str) -> Path | None:
    if not IDENTITY_PATTERN.fullmatch(match_id) or not SLOT_PATTERN.fullmatch(slot):
        return None
    return GAME / "saves" / "agent" / match_id / f"{slot}.sav"


def list_saved_games(match_id: str) -> dict[str, Any]:
    if not IDENTITY_PATTERN.fullmatch(match_id):
        return {"ok": False, "error": "invalid_match_id"}
    directory = GAME / "saves" / "agent" / match_id
    saves: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.sav")):
            if not SLOT_PATTERN.fullmatch(path.stem):
                continue
            stat = path.stat()
            saves.append({
                "slot": path.stem,
                "bytes": stat.st_size,
                "modified_unix": stat.st_mtime,
            })
    return {"ok": True, "match_id": match_id, "saves": saves}


def load_saved_game(match_id: str, slot: str, wait_seconds: int = 90) -> dict[str, Any]:
    path = _save_path(match_id, slot)
    if path is None:
        return {"ok": False, "error": "invalid_save_identity_or_slot"}
    manifest_path = KNOWLEDGE_ROOT / match_id / "match.json"
    if not manifest_path.exists():
        return {"ok": False, "error": "unknown_match_id"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "invalid_match_manifest"}
    if manifest.get("match_id") != match_id:
        return {"ok": False, "error": "match_manifest_mismatch"}
    if not path.is_file():
        return {"ok": False, "error": "save_not_found", "slot": slot, "match_id": match_id}
    if bridge_available():
        return {
            "ok": False,
            "error": "game_already_running",
            "message": "Stop the current spectator game before loading; this prevents accidental loss of unsaved state.",
        }
    session_id = f"session-{uuid.uuid4().hex}"
    relative_windows_path = f"saves\\agent\\{match_id}\\{slot}.sav"
    launched = launch_game(
        wait_seconds=min(max(wait_seconds, 5), 120),
        match_id=match_id,
        session_id=session_id,
        startup_save=relative_windows_path,
    )
    if not launched.get("ok"):
        return launched
    deadline = time.monotonic() + min(max(wait_seconds, 5), 120)
    last: dict[str, Any] = launched.get("state", {})
    while time.monotonic() < deadline:
        try:
            last = bridge_request("semantic_snapshot")
        except BridgeUnavailable:
            time.sleep(0.5)
            continue
        snapshot = last.get("snapshot", {})
        if snapshot:
            return {
                "ok": True,
                "loaded": True,
                "slot": slot,
                "identity": {"match_id": match_id, "session_id": session_id},
                "knowledge_directory": str(KNOWLEDGE_ROOT / match_id),
                "snapshot": last,
            }
        time.sleep(0.5)
    return {"ok": False, "error": "semantic_load_timeout", "last_state": last, "log": str(LOG_FILE)}

