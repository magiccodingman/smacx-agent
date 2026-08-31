"""Failure-isolated scheduler for optional per-perspective Graphiti projection."""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import time
from typing import Any

from smacx_graphiti import GraphitiCoreSink, GraphitiProjector, load_runtime_config
from smacx_store import MemoryScope, SmacxStore


def _enabled(store: SmacxStore) -> bool:
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT value_json FROM control_settings WHERE setting_key='graphiti.enabled'"
        ).fetchone()
    return bool(row and json.loads(row["value_json"]) is True)


class RecallBroker:
    def __init__(self, store: SmacxStore, loop: asyncio.AbstractEventLoop) -> None:
        self.store = store
        self.loop = loop
        self.sink: GraphitiCoreSink | None = None

    async def recall(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.sink is None:
            return {"ok": False, "error": "graphiti_not_ready", "facts": []}
        scope = MemoryScope(
            str(body.get("match_id", "")), str(body.get("agent_id", "")),
            str(body.get("perspective_id", "")),
        )
        self.store.require_scope(scope)
        query = str(body.get("query") or "").strip()
        if not 2 <= len(query) <= 4000:
            return {"ok": False, "error": "invalid_graphiti_query", "facts": []}
        facts = await self.sink.search(
            self.store.graph_namespace(scope), query,
            min(max(int(body.get("limit", 6)), 1), 20),
        )
        return {
            "ok": True, "facts": facts, "namespace": self.store.graph_namespace(scope),
            "fair_play_scope": {
                "match_id": scope.match_id, "agent_id": scope.agent_id,
                "perspective_id": scope.perspective_id,
            },
        }


class RecallHandler(BaseHTTPRequestHandler):
    broker: RecallBroker

    def log_message(self, *_args: Any) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404); return
        payload = json.dumps({"ok": self.broker.sink is not None}).encode()
        self.send_response(200 if self.broker.sink is not None else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers(); self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/recall":
            self.send_error(404); return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16_384:
                raise ValueError("invalid_body")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("invalid_body")
            future = asyncio.run_coroutine_threadsafe(self.broker.recall(body), self.broker.loop)
            result = future.result(timeout=10)
            status = 200 if result.get("ok") else 503
        except FutureTimeoutError:
            result = {"ok": False, "error": "graphiti_recall_timeout", "facts": []}; status = 504
        except Exception as exc:
            result = {"ok": False, "error": f"graphiti_recall_failed:{type(exc).__name__}", "facts": []}; status = 400
        payload = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)


def _start_recall_server(broker: RecallBroker) -> ThreadingHTTPServer:
    RecallHandler.broker = broker
    server = ThreadingHTTPServer((
        os.environ.get("SMACX_GRAPHITI_RECALL_HOST", "0.0.0.0"),
        int(os.environ.get("SMACX_GRAPHITI_RECALL_PORT", "8091")),
    ), RecallHandler)
    threading.Thread(target=server.serve_forever, name="graphiti-recall", daemon=True).start()
    return server


def _scopes(store: SmacxStore) -> list[MemoryScope]:
    with store.transaction() as connection:
        rows = connection.execute(
            "SELECT p.match_id, p.agent_id, p.perspective_id FROM perspectives p "
            "JOIN matches m ON m.match_id=p.match_id JOIN agents a ON a.agent_id=p.agent_id "
            "WHERE p.status='active' AND a.status='active' "
            "AND m.status IN ('provisioned','running','paused','error') "
            "AND coalesce(json_extract(m.metadata_json, '$.graphiti_enabled'), 1) = 1 "
            "ORDER BY p.match_id, p.agent_id, p.perspective_id"
        ).fetchall()
    return [MemoryScope(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _state(store: SmacxStore, status: str, *, active_scopes: int = 0,
           projected: int = 0, failed: int = 0, error: str | None = None,
           projection: bool = False) -> None:
    now = time.time()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO graphiti_runtime_state(singleton, status, backend, projected_events, failed_events, "
            "active_scopes, last_heartbeat_unix, last_projection_unix, last_error, metadata_json, "
            "updated_unix) VALUES (1, ?, 'falkordb', ?, ?, ?, ?, ?, ?, '{}', ?) "
            "ON CONFLICT(singleton) DO UPDATE SET status=excluded.status, "
            "backend=excluded.backend, "
            "projected_events=graphiti_runtime_state.projected_events+excluded.projected_events, "
            "failed_events=graphiti_runtime_state.failed_events+excluded.failed_events, "
            "active_scopes=excluded.active_scopes, last_heartbeat_unix=excluded.last_heartbeat_unix, "
            "last_projection_unix=CASE WHEN ? THEN excluded.last_projection_unix "
            "ELSE graphiti_runtime_state.last_projection_unix END, last_error=excluded.last_error, "
            "updated_unix=excluded.updated_unix",
            (status, projected, failed, active_scopes, now, now if projection else None,
             error[:2000] if error else None, now, 1 if projection else 0),
        )


def _claim_rebuild(store: SmacxStore) -> dict[str, Any] | None:
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM graphiti_rebuild_requests WHERE status='queued' "
            "ORDER BY created_unix LIMIT 1"
        ).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE graphiti_rebuild_requests SET status='running', started_unix=? "
            "WHERE rebuild_id=? AND status='queued'", (time.time(), row["rebuild_id"]),
        )
    return dict(row)


def _finish_rebuild(store: SmacxStore, rebuild_id: str, result: dict[str, Any]) -> None:
    okay = result.get("ok") is True
    with store.transaction() as connection:
        connection.execute(
            "UPDATE graphiti_rebuild_requests SET status=?, result_json=?, last_error=?, "
            "completed_unix=? WHERE rebuild_id=? AND status='running'",
            ("completed" if okay else "failed",
             json.dumps(result, sort_keys=True, separators=(",", ":")),
             None if okay else str(result.get("error", "graphiti_rebuild_failed"))[:2000],
             time.time(), rebuild_id),
        )


async def run(database: Path, *, interval: float, limit: int) -> int:
    store = SmacxStore(database)
    stopping = asyncio.Event()
    broker = RecallBroker(store, asyncio.get_running_loop())
    recall_server = _start_recall_server(broker)

    def stop(*_unused: Any) -> None:
        stopping.set()

    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, stop)
    sink = None
    sink_fingerprint = None
    _state(store, "starting")
    try:
        while not stopping.is_set():
            if not _enabled(store):
                if sink is not None:
                    broker.sink = None
                    await sink.close()
                    sink = None
                    sink_fingerprint = None
                _state(store, "disabled")
            else:
                scopes = _scopes(store)
                try:
                    config = load_runtime_config(store)
                    if sink is None or sink_fingerprint != config.fingerprint:
                        broker.sink = None
                        if sink is not None:
                            await sink.close()
                        sink = await GraphitiCoreSink.from_config(config)
                        sink_fingerprint = config.fingerprint
                        broker.sink = sink
                    projector = GraphitiProjector(store, sink)
                    projected = failed = 0
                    rebuild = _claim_rebuild(store)
                    if rebuild:
                        scope = MemoryScope(
                            str(rebuild["match_id"]), str(rebuild["agent_id"]),
                            str(rebuild["perspective_id"]),
                        )
                        result = await projector.rebuild(scope, limit=limit)
                        _finish_rebuild(store, str(rebuild["rebuild_id"]), result)
                        projected += int(result.get("projected", 0))
                        failed += int(not result.get("ok"))
                    for scope in scopes:
                        result = await projector.run_once(scope, limit=limit)
                        projected += int(result.get("projected", 0))
                        failed += int(not result.get("ok"))
                    _state(store, "ready" if not failed else "degraded",
                           active_scopes=len(scopes), projected=projected, failed=failed,
                           error=("one_or_more_scope_projections_failed" if failed else None),
                           projection=projected > 0)
                except Exception as exc:
                    if sink is not None:
                        broker.sink = None
                        try:
                            await sink.close()
                        except Exception:
                            pass
                        sink = None
                        sink_fingerprint = None
                    _state(store, "degraded", active_scopes=len(scopes), failed=1,
                           error=f"{type(exc).__name__}:{exc}")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        broker.sink = None
        recall_server.shutdown()
        recall_server.server_close()
        if sink is not None:
            await sink.close()
        _state(store, "stopped")
    return 0


def health(database: Path, maximum_age: float) -> int:
    store = SmacxStore(database)
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT status, last_heartbeat_unix FROM graphiti_runtime_state WHERE singleton=1"
        ).fetchone()
    okay = bool(row and row["status"] != "stopped" and row["last_heartbeat_unix"]
                and time.time() - float(row["last_heartbeat_unix"]) <= maximum_age)
    return 0 if okay else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--maximum-heartbeat-age", type=float, default=120)
    arguments = parser.parse_args()
    database = Path(arguments.database)
    if arguments.health:
        return health(database, arguments.maximum_heartbeat_age)
    return asyncio.run(run(
        database, interval=min(max(arguments.interval, 5), 3600),
        limit=min(max(arguments.limit, 1), 500),
    ))


if __name__ == "__main__":
    raise SystemExit(main())
