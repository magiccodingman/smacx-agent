"""Review-only campaign exports with bounded, explicit capture watermarks."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
import uuid
import zipfile
import sqlite3
import os
from smacx_diagnostics import redact

TABLES = ("matches", "harness_runs", "supervision_incidents", "attention_items",
          "attention_leases", "world_watches", "world_observation_projection",
          "specialist_missions", "specialist_attempts", "world_telemetry",
          "goals", "plans", "commitments", "specialist_dependencies", "specialist_trace_manifests")


def snapshot_hermes(source: Path, target: Path, match_id: str, profile_id: str) -> None:
    """Called in an isolated helper with a read-only Hermes volume."""
    from smacx_diagnostics import _SAFE
    if not _SAFE.fullmatch(profile_id) or not _SAFE.fullmatch(match_id):
        raise ValueError("invalid_diagnostic_scope")
    target.mkdir(parents=True, exist_ok=True)
    database = source / "profiles" / profile_id / "state.db"
    total=0
    if database.exists():
        connection=sqlite3.connect(f"file:{database}?mode=ro",uri=True)
        connection.row_factory=sqlite3.Row
        try:
            connection.execute("BEGIN")
            with (target/"hermes-history.jsonl").open("w") as out:
                for row in connection.execute("SELECT m.id,m.session_id,m.role,m.content,m.tool_call_id,m.tool_calls,m.tool_name,m.timestamp FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.title=? ORDER BY m.id",(match_id,)):
                    record=json.dumps({"schema":"smacx.hermes-history.v1","actor":"sovereign",
                        "kind":"retained_message","payload":redact(dict(row))})+"\n"
                    size=len(record.encode())
                    if total+size>128*1024*1024:
                        out.write(json.dumps({"kind":"capture_gap","payload":{"reason":"history_byte_limit"}})+"\n")
                        break
                    out.write(record);total+=size
        finally:connection.close()
    logs=source/"diagnostics"/match_id
    if logs.exists():
        for path in sorted(logs.glob("*.jsonl")):
            if path.is_symlink():continue
            size=path.stat().st_size
            if total+size>256*1024*1024:
                (target/"capture-gap.jsonl").write_text(json.dumps({"kind":"capture_gap","payload":{"reason":"helper_byte_limit"}})+"\n")
                break
            with path.open("rb") as stream:data=stream.read(size)
            (target/path.name).write_bytes(data);total+=len(data)
    if os.geteuid() == 0:
        for path in [target, *target.rglob("*")]:
            os.chown(path,10001,10001)
            os.chmod(path,0o750 if path.is_dir() else 0o640)


def build_bundle(store, match_id: str, output: Path, roots: list[Path], *,
                 max_bytes: int = 256 * 1024 * 1024) -> dict:
    from smacx_diagnostics import _SAFE
    if not _SAFE.fullmatch(match_id): raise ValueError("invalid_diagnostic_match")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"campaign-{match_id}-{uuid.uuid4().hex}.zip"
    manifest = {"schema": "smacx.campaign-diagnostics.v1", "match_id": match_id,
                "created_unix": time.time(), "authority": "diagnostic_only",
                "consistency": "database_transaction_plus_per_file_byte_watermarks",
                "files": [], "gaps": [], "complete": False}
    total = 0
    human = []
    def write(archive, name, data):
        nonlocal total
        if total + len(data) > max_bytes:
            manifest["gaps"].append({"file": name, "reason": "export_byte_limit"});return
        total += len(data)
        archive.writestr(name, data)
        manifest["files"].append({"path": name, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()})
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        with store.transaction() as connection:
            available = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table not in available: continue
                columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
                if "match_id" in columns:
                    query=f"SELECT * FROM {table} WHERE match_id=? LIMIT 10001"
                elif "mission_id" in columns:
                    query=f"SELECT * FROM {table} WHERE mission_id IN (SELECT mission_id FROM specialist_missions WHERE match_id=?) LIMIT 10001"
                else: continue
                rows = []
                for row in connection.execute(query, (match_id,)):
                    value = dict(row)
                    # Provider configuration is not a gameplay diagnostic.
                    value.pop("model_profile_json", None)
                    for key in list(value):
                        if key.endswith("_json") and isinstance(value[key], str):
                            try: value[key] = json.loads(value[key])
                            except ValueError: pass
                    rows.append(redact(value))
                if len(rows)>10000:
                    manifest["gaps"].append({"table": table, "reason": "row_limit"});rows=rows[:10000]
                write(archive, f"state/{table}.json", json.dumps(rows, ensure_ascii=False).encode())
        stream_count = 0
        for index, root in enumerate(roots):
            if not root.exists():
                manifest["gaps"].append({"source": index, "reason": "source_missing"});continue
            for path in sorted(list(root.rglob("*.jsonl")) + list(root.rglob("*.jsonl.zst"))):
                if path.is_symlink() or not path.is_file(): continue
                if not path.resolve().is_relative_to(root.resolve()): continue
                stream_count += 1
                size = path.stat().st_size
                if size > max_bytes-total:
                    manifest["gaps"].append({"file": path.name, "reason": "export_byte_limit"});continue
                with path.open("rb") as source: data = source.read(size)
                if path.name.endswith(".jsonl.zst"):
                    write(archive, f"streams/{index}/{path.relative_to(root).as_posix()}", data)
                    manifest["files"][-1]["source_byte_watermark"] = len(data)
                    continue
                end = data.rfind(b"\n")+1
                if end != len(data): manifest["gaps"].append({"file": path.name,"reason":"partial_final_record"})
                data = data[:end]
                write(archive, f"streams/{index}/{path.relative_to(root).as_posix()}", data)
                manifest["files"][-1]["source_byte_watermark"] = end
                for line in data.splitlines():
                    try: event=json.loads(line)
                    except ValueError:
                        manifest["gaps"].append({"file":path.name,"reason":"invalid_json_record"});continue
                    payload=event.get("payload",{})
                    if event.get("kind")=="capture_gap" or payload.get("capture_status")=="omitted":
                        manifest["gaps"].append({"event_id":event.get("event_id"),"reason":"capture_gap"})
                    if event.get("kind") in {"tool_requested","managed_tool_returned","sovereign_response","tool_returned","retained_message"}:
                        tool=payload.get("managed_name",payload.get("tool",""))
                        summary=payload.get("result",payload.get("content",payload.get("message",{})))
                        human.append(f"{event.get('recorded_unix','')} [{event.get('actor','')}] {event.get('kind')} {tool} {json.dumps(summary,ensure_ascii=False)[:1800]}")
        if not stream_count:manifest["gaps"].append({"reason":"no_diagnostic_streams"})
        # The current adapters do not yet certify all requested categories.
        manifest["gaps"].append({"reason":"acceptance_coverage_in_progress",
            "details":"See gameplay diagnostics coverage matrix; absence of errors is not completeness."})
        write(archive,"gameplay.txt",("\n".join(human)+"\n").encode())
        archive.writestr("manifest.json",json.dumps(manifest,indent=2))
    return {"file_name":target.name,"relative_path":"diagnostics/"+target.name,
            "size_bytes":target.stat().st_size,"complete":manifest["complete"],
            "gap_count":len(manifest["gaps"])}
