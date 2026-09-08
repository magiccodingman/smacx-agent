"""Review-only campaign exports with bounded, explicit capture watermarks."""
from __future__ import annotations
import hashlib
import gzip
import zlib
import io
import json
from pathlib import Path
import time
import uuid
import zipfile
import sqlite3
import os
import subprocess
import tempfile
import shutil
from contextlib import contextmanager
from smacx_diagnostics import redact


def zstd_lines(data: bytes, *, max_bytes: int = 64 * 1024 * 1024):
    """Read a captured compressed prefix with a bounded decoded byte budget."""
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        process = subprocess.Popen(['zstd', '-q', '-d', '-c', '--memory=64MB'],
            stdin=source, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            remaining = max_bytes
            while True:
                line = process.stdout.readline(remaining + 1)
                if not line:
                    break
                if len(line) > remaining:
                    raise ValueError('decoded_trace_byte_limit')
                remaining -= len(line)
                yield line
            if process.wait(timeout=10) != 0:
                raise ValueError('invalid_zstd_trace')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()

TABLES = ("matches", "harness_runs", "supervision_incidents", "attention_items",
          "attention_leases", "world_watches", "world_observation_projection",
          "specialist_missions", "specialist_attempts", "world_telemetry",
          "goals", "plans", "commitments", "events", "cognitive_operations", "campaign_checkpoint_generations", "specialist_dependencies", "specialist_trace_manifests")


@contextmanager
def _hermes_history_connection(database: Path, target: Path):
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.Error:
        connection.close()
    else:
        try:
            yield connection
        finally:
            connection.close()
        return
    # A closed WAL database without its SHM cannot be opened on a read-only
    # mount. Rebuild transient metadata in private staging, never in source.
    # A live writer can race this fallback: refuse changed files explicitly.
    files = [database, Path(str(database) + "-wal"), Path(str(database) + "-journal")]
    def signatures():
        return [(p.name, p.stat().st_size, p.stat().st_mtime_ns)
                if p.exists() else (p.name, None, None) for p in files]
    before = signatures()
    if sum(row[1] or 0 for row in before) > 512 * 1024 * 1024:
        raise RuntimeError("history_staging_byte_limit")
    with tempfile.TemporaryDirectory(dir=target, prefix=".history-") as temporary:
        for source in files:
            if source.exists():
                shutil.copyfile(source, Path(temporary) / source.name)
        if signatures() != before:
            raise RuntimeError("history_source_changed_during_capture")
        connection = sqlite3.connect(Path(temporary) / database.name)
        try:
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("BEGIN")
            yield connection
        finally:
            connection.close()


def snapshot_hermes(source: Path, target: Path, match_id: str, profile_id: str) -> None:
    """Called in an isolated helper with a read-only Hermes volume."""
    from smacx_diagnostics import _SAFE
    if not _SAFE.fullmatch(profile_id) or not _SAFE.fullmatch(match_id):
        raise ValueError("invalid_diagnostic_scope")
    target.mkdir(parents=True, exist_ok=True)
    database = source / "profiles" / profile_id / "state.db"
    total=0
    if database.exists():
        try:
            with _hermes_history_connection(database, target) as connection, \
                    (target/"hermes-history.jsonl").open("w") as out:
                connection.row_factory=sqlite3.Row
                for row in connection.execute("SELECT m.id,m.session_id,m.role,m.content,m.tool_call_id,m.tool_calls,m.tool_name,m.timestamp FROM messages m JOIN sessions s ON s.id=m.session_id WHERE s.title=? ORDER BY m.id",(match_id,)):
                    record=json.dumps({"schema":"smacx.hermes-history.v1","actor":"sovereign",
                        "kind":"retained_message","recorded_unix":row["timestamp"],"payload":redact(dict(row))})+"\n"
                    size=len(record.encode())
                    if total+size>128*1024*1024:
                        out.write(json.dumps({"kind":"capture_gap","payload":{"reason":"history_byte_limit"}})+"\n")
                        break
                    out.write(record);total+=size
        except (sqlite3.Error, OSError, RuntimeError) as exc:
            # History is one diagnostic source, not authority for restoration.
            # Preserve all other streams even when this source is unavailable.
            (target/"history-gap.jsonl").write_text(json.dumps({
                "kind":"capture_gap", "payload":{"reason":"retained_history_unavailable",
                "exception_type":type(exc).__name__}})+"\n")
    else:
        (target/"history-missing.jsonl").write_text(json.dumps({"kind":"capture_gap","payload":{"reason":"retained_history_missing"}})+"\n")
    logs=source/"diagnostics"/match_id
    if (logs/".capacity-exhausted").exists():
        (target/"capacity-gap.jsonl").write_text(json.dumps({"kind":"capture_gap","payload":{"reason":"match_byte_limit"}})+"\n")
    if logs.exists():
        for path in sorted(logs.glob("*.jsonl*")):
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
                "files": [], "gaps": [], "state_windows": {}, "complete": False}
    total = 0
    human = []
    human_bytes = 0
    human_truncated = False
    from smacx_diagnostic_summary import Metrics, summary
    metrics = Metrics()
    def write(archive, name, data):
        nonlocal total
        if total + len(data) > max_bytes:
            manifest["gaps"].append({"file": name, "reason": "export_byte_limit"});return
        total += len(data)
        archive.writestr(name, data)
        manifest["files"].append({"path": name, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()})
    target.touch(mode=0o600)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        with store.transaction() as connection:
            available = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table not in available: continue
                columns = {r[1] for r in connection.execute(f"PRAGMA table_info({table})")}
                # An unordered LIMIT can select whole index categories before
                # any recent runtime telemetry. Export an explicit recent
                # window, with rowid breaking equal-timestamp ties.
                time_column = next((name for name in (
                    "updated_unix", "recorded_unix", "observed_unix", "created_unix",
                ) if name in columns), None)
                order = f"{time_column} DESC, rowid DESC" if time_column else "rowid DESC"
                if "match_id" in columns:
                    query=f"SELECT * FROM {table} WHERE match_id=? ORDER BY {order} LIMIT 10001"
                elif "mission_id" in columns:
                    query=f"SELECT * FROM {table} WHERE mission_id IN (SELECT mission_id FROM specialist_missions WHERE match_id=?) ORDER BY {order} LIMIT 10001"
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
                manifest["state_windows"][table] = {
                    "selection": "most_recent", "order": order,
                    "row_limit": 10000, "retained_rows": len(rows),
                    "newest_timestamp": rows[0].get(time_column) if rows and time_column else None,
                    "oldest_timestamp": rows[-1].get(time_column) if rows and time_column else None,
                }
                write(archive, f"state/{table}.json", json.dumps(rows, ensure_ascii=False).encode())
        stream_count = 0
        for index, root in enumerate(roots):
            if not root.exists():
                manifest["gaps"].append({"source": index, "reason": "source_missing"});continue
            if (root / ".capacity-exhausted").exists():
                manifest["gaps"].append({"source":index,"reason":"match_byte_limit"})
            for path in sorted(list(root.rglob("*.jsonl")) + list(root.rglob("*.jsonl.zst")) + list(root.rglob("*.jsonl.gz"))):
                if path.is_symlink() or not path.is_file(): continue
                if not path.resolve().is_relative_to(root.resolve()): continue
                stream_count += 1
                size = path.stat().st_size
                if size > max_bytes-total:
                    manifest["gaps"].append({"file": path.name, "reason": "export_byte_limit"});continue
                with path.open("rb") as source: data = source.read(size)
                zstandard = path.name.endswith(".jsonl.zst")
                compressed = path.name.endswith(".gz")
                end = len(data) if compressed or zstandard else data.rfind(b"\n")+1
                if end != len(data): manifest["gaps"].append({"file": path.name,"reason":"partial_final_record"})
                data = data[:end]
                write(archive, f"streams/{index}/{path.relative_to(root).as_posix()}", data)
                manifest["files"][-1]["source_byte_watermark"] = end
                def lines():
                    try:
                        if zstandard:
                            yield from zstd_lines(data)
                        elif compressed:
                            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                                for line in stream: yield line
                        else:
                            yield from data.splitlines()
                    except FileNotFoundError:
                        manifest["gaps"].append({"file":path.name,"reason":"trace_decoder_unavailable"})
                    except (EOFError, OSError, zlib.error):
                        manifest["gaps"].append({"file":path.name,"reason":"partial_compressed_tail"})
                    except (ValueError, subprocess.TimeoutExpired) as exc:
                        manifest["gaps"].append({"file":path.name,"reason":str(exc) if isinstance(exc,ValueError) else "trace_decoder_timeout"})
                for line in lines():
                    try: event=json.loads(line)
                    except ValueError:
                        manifest["gaps"].append({"file":path.name,"reason":"invalid_json_record"});continue
                    payload=event.get("payload",{})
                    if event.get("kind")=="capture_gap" or payload.get("capture_status")=="omitted":
                        manifest["gaps"].append({"event_id":event.get("event_id"),"reason":"capture_gap"})
                    metrics.add(event)
                    rendered = summary(event)
                    if rendered:
                        timestamp=event.get("recorded_unix") or event.get("timestamp_unix") or 0
                        try: timestamp=float(timestamp)
                        except (TypeError,ValueError): timestamp=0
                        if len(rendered)>6000:rendered=rendered[:6000]+" [truncated; see structured stream]"
                        line=f"{timestamp} [{event.get('actor','unknown')}] {rendered}"
                        size=len(line.encode())
                        if human_bytes+size <= 8*1024*1024:
                            human.append((timestamp,line));human_bytes+=size
                        elif not human_truncated:
                            human_truncated=True
                            manifest["gaps"].append({"reason":"human_summary_byte_limit","structured_streams_retained":True})
        if not stream_count:manifest["gaps"].append({"reason":"no_diagnostic_streams"})
        # The current adapters do not yet certify all requested categories.
        manifest["gaps"].append({"reason":"acceptance_coverage_in_progress",
            "details":"See gameplay diagnostics coverage matrix; absence of errors is not completeness."})
        write(archive,"gameplay.txt",("\n".join(line for _,line in sorted(human,key=lambda row:row[0]))+"\n").encode())
        write(archive,"metrics.json",json.dumps(metrics.as_dict(),indent=2).encode())
        archive.writestr("manifest.json",json.dumps(manifest,indent=2))
    older=sorted(output.glob(f"campaign-{match_id}-*.zip"),key=lambda path:path.stat().st_mtime,reverse=True)
    for stale in older[3:]:
        if stale != target: stale.unlink(missing_ok=True)
    return {"file_name":target.name,"relative_path":"diagnostics/"+target.name,
            "size_bytes":target.stat().st_size,"complete":manifest["complete"],
            "gap_count":len(manifest["gaps"])}


def snapshot_journals(root: Path, target: Path, match_id: str, *, max_bytes: int = 64*1024*1024) -> None:
    """Freeze committed prefixes under the same locks used by journal writers."""
    from smacx_journal import CampaignJournal
    journal = CampaignJournal(root)
    target.mkdir(parents=True, exist_ok=True)
    used = 0
    with (target / 'campaign-journal.jsonl').open('w') as output:
        for timeline in sorted((root/match_id).glob('perspectives/*/*/timelines/*')):
            if not timeline.is_dir() or timeline.is_symlink(): continue
            with journal._locked(root, shared=True), journal._locked(timeline):
                manifest_path = timeline/'manifest.json'
                if not manifest_path.exists(): continue
                manifest = json.loads(manifest_path.read_text())
                head = int(manifest.get('sequence', 0))
                watermark = {'kind':'journal_snapshot_watermark','actor':'campaign-journal',
                    'payload':{'timeline_path':str(timeline.relative_to(root/match_id)),
                               'sequence':head,'head_hash':manifest.get('head_hash'),
                               'diagnostic_copy_not_restore_authority':True}}
                output.write(json.dumps(watermark)+'\n')
                for path in sorted((timeline/'events').glob('*.json')):
                    if path.is_symlink(): continue
                    event = json.loads(path.read_text())
                    if int(event.get('sequence', head+1)) > head: continue
                    if event.get('match_id') != match_id: raise ValueError('journal_export_scope_mismatch')
                    line = json.dumps({'kind':'journal_event','actor':'campaign-journal',
                        'recorded_unix':event.get('recorded_unix'), 'payload':redact(event)})+'\n'
                    size = len(line.encode())
                    if used + size > max_bytes:
                        output.write(json.dumps({'kind':'capture_gap','payload':{'reason':'journal_snapshot_byte_limit'}})+'\n')
                        return
                    output.write(line);used += size
