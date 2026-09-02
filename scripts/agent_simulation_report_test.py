#!/usr/bin/env python3
"""Regression for privacy-safe simulation report telemetry snapshots."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

from agent_simulation_report import portal_metrics


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="smacx-report-") as temporary:
        database_path = Path(temporary) / "portal.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE PortalTurnMetrics (
          MatchId TEXT, Turn INTEGER, StartedAt TEXT, DurationSeconds REAL,
          PromptTokens INTEGER, CompletionTokens INTEGER,
          CacheReadTokens INTEGER, CacheWriteTokens INTEGER,
          ReasoningTokens INTEGER, ApiCalls INTEGER, Errored INTEGER
        );
        CREATE TABLE PortalMatches (
          MatchId TEXT PRIMARY KEY, Status TEXT, Mode TEXT, SettingsJson TEXT,
          NativeSettingsJson TEXT, CurrentTurn INTEGER, CurrentYear INTEGER
        );
        """)
        connection.execute(
            "INSERT INTO PortalMatches VALUES (?,?,?,?,?,?,?)",
            ("match-test", "running", "singleplayer", "{}",
             '{"time_control":0}', 1, 2101),
        )
        connection.commit()
        # Keep the writer open and place the newest values in WAL. A report
        # must still observe them without mutating/checkpointing the database.
        connection.execute(
            "UPDATE PortalMatches SET Status='parked',CurrentTurn=7,CurrentYear=2107 "
            "WHERE MatchId='match-test'"
        )
        connection.execute(
            "INSERT INTO PortalTurnMetrics VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("match-test", 7, "now", 12.5, 100, 20, 0, 0, 5, 2, 0),
        )
        connection.commit()
        result = portal_metrics(database_path, "match-test")
        connection.close()
        if result.get("match") != {
            "status": "parked", "mode": "singleplayer", "time_control": 0,
            "current_turn": 7, "current_year": 2107,
        } or result.get("totals", {}).get("input_tokens") != 100:
            raise AssertionError(result)
    print(json.dumps({"event": "pass", "payload": {
        "live_wal_observed": True, "read_only_report": True,
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
