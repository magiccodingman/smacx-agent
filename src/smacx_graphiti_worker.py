"""Failure-isolated scheduler for optional per-perspective Graphiti projection."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import signal
import time
from typing import Any

from smacx_graphiti import GraphitiCoreSink, GraphitiProjector
from smacx_store import MemoryScope, SmacxStore


def _enabled(store: SmacxStore) -> bool:
    with store.transaction() as connection:
        row = connection.execute(
            "SELECT value_json FROM control_settings WHERE setting_key='graphiti.enabled'"
        ).fetchone()
    return bool(row and json.loads(row["value_json"]) is True)


def _scopes(store: SmacxStore) -> list[MemoryScope]:
    with store.transaction() as connection:
        rows = connection.execute(
            "SELECT p.match_id, p.agent_id, p.perspective_id FROM perspectives p "
            "JOIN matches m ON m.match_id=p.match_id JOIN agents a ON a.agent_id=p.agent_id "
            "WHERE p.status='active' AND a.status='active' "
            "AND m.status IN ('provisioned','running','paused','error') "
            "ORDER BY p.match_id, p.agent_id, p.perspective_id"
        ).fetchall()
    return [MemoryScope(str(row[0]), str(row[1]), str(row[2])) for row in rows]


def _state(store: SmacxStore, status: str, *, active_scopes: int = 0,
           projected: int = 0, failed: int = 0, error: str | None = None,
           projection: bool = False) -> None:
    now = time.time()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO graphiti_runtime_state(singleton, status, projected_events, failed_events, "
            "active_scopes, last_heartbeat_unix, last_projection_unix, last_error, metadata_json, "
            "updated_unix) VALUES (1, ?, ?, ?, ?, ?, ?, ?, '{}', ?) "
            "ON CONFLICT(singleton) DO UPDATE SET status=excluded.status, "
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

    def stop(*_unused: Any) -> None:
        stopping.set()

    for name in (signal.SIGTERM, signal.SIGINT):
        signal.signal(name, stop)
    sink = None
    _state(store, "starting")
    try:
        while not stopping.is_set():
            if not _enabled(store):
                if sink is not None:
                    await sink.close()
                    sink = None
                _state(store, "disabled")
            else:
                scopes = _scopes(store)
                try:
                    if sink is None:
                        sink = await GraphitiCoreSink.from_environment()
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
                        try:
                            await sink.close()
                        except Exception:
                            pass
                        sink = None
                    _state(store, "degraded", active_scopes=len(scopes), failed=1,
                           error=f"{type(exc).__name__}:{exc}")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
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
