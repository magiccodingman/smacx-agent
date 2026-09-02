#!/usr/bin/env python3
"""Create a sanitized, reproducible agent-game benchmark from durable state.

The report deliberately excludes prompts, responses, reasoning text, chat,
provider URLs, secrets, native saves, and game assets. It combines causal
campaign-journal actions with aggregate portal turn telemetry.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any, Iterable


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def summarize_values(values: Iterable[float]) -> dict[str, float | int | None]:
    rows = [float(value) for value in values if value is not None]
    return {
        "samples": len(rows),
        "minimum": round(min(rows), 3) if rows else None,
        "median": round(statistics.median(rows), 3) if rows else None,
        "p90": percentile(rows, 0.90),
        "maximum": round(max(rows), 3) if rows else None,
        "mean": round(statistics.fmean(rows), 3) if rows else None,
    }


def journal_events(campaign_root: Path, match_id: str) -> list[dict[str, Any]]:
    base = campaign_root / match_id / "perspectives"
    events: list[dict[str, Any]] = []
    if not base.is_dir():
        raise FileNotFoundError(
            f"campaign journal is unavailable or unreadable: {base}"
        )
    for path in sorted(base.glob("*/*/timelines/timeline-main/events/*.json")):
        value = load_json(path)
        if value is not None and value.get("match_id") == match_id:
            events.append(value)
    if not events:
        raise RuntimeError(f"campaign journal contains no readable events: {base}")
    return sorted(events, key=lambda item: (
        float(item.get("recorded_unix") or 0),
        str(item.get("agent_id") or ""), int(item.get("sequence") or 0),
    ))


def verify_chains(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_perspective: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in events:
        key = (str(event.get("agent_id")), str(event.get("perspective_id")),
               str(event.get("timeline_id")))
        by_perspective.setdefault(key, []).append(event)
    failures = 0
    for rows in by_perspective.values():
        previous = "0" * 64
        for event in sorted(rows, key=lambda item: int(item.get("sequence") or 0)):
            candidate = dict(event)
            claimed = str(candidate.pop("event_hash", ""))
            encoded = json.dumps(
                candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode()
            actual = hashlib.sha256(previous.encode("ascii") + encoded).hexdigest()
            if candidate.get("previous_hash") != previous or actual != claimed:
                failures += 1
                break
            previous = claimed
    return {
        "perspectives": len(by_perspective),
        "events": len(events),
        "hash_chains_valid": failures == 0,
        "failed_chains": failures,
    }


def portal_metrics(database_path: Path | None, match_id: str) -> dict[str, Any]:
    if database_path is None:
        return {"available": False}
    # Do not use immutable=1 here. The portal is normally live while a report
    # is generated and its newest status/telemetry may still be in the WAL;
    # immutable mode deliberately ignores that WAL and would publish stale
    # benchmark rows even though this connection itself is read-only.
    database = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in database.execute(
            "SELECT Turn,DurationSeconds,PromptTokens,CompletionTokens,"
            "CacheReadTokens,CacheWriteTokens,ReasoningTokens,ApiCalls,Errored "
            "FROM PortalTurnMetrics WHERE MatchId=? ORDER BY Turn,StartedAt",
            (match_id,),
        )]
        match = database.execute(
            "SELECT Status,Mode,SettingsJson,NativeSettingsJson,"
            "CurrentTurn,CurrentYear FROM PortalMatches WHERE MatchId=?",
            (match_id,),
        ).fetchone()
    finally:
        database.close()
    completed = [row for row in rows if not bool(row["Errored"])]
    totals = {
        key: sum(int(row[key] or 0) for row in rows)
        for key in ("PromptTokens", "CompletionTokens", "CacheReadTokens",
                    "CacheWriteTokens", "ReasoningTokens", "ApiCalls")
    }
    settings: dict[str, Any] = {}
    native_settings: dict[str, Any] = {}
    if match:
        for source, target in ((match["SettingsJson"], settings),
                               (match["NativeSettingsJson"], native_settings)):
            try:
                decoded = json.loads(source or "{}")
                if isinstance(decoded, dict):
                    target.update(decoded)
            except json.JSONDecodeError:
                pass
    time_control = native_settings.get("time_control")
    if time_control is None:
        time_control = settings.get("TimeControl", settings.get("time_control"))
    return {
        "available": True,
        "match": ({
            "status": match["Status"], "mode": match["Mode"],
            "time_control": time_control,
            "current_turn": match["CurrentTurn"], "current_year": match["CurrentYear"],
        } if match else None),
        "turn_rows": len(rows),
        "successful_turn_rows": len(completed),
        "errored_turn_rows": len(rows) - len(completed),
        "duration_seconds": summarize_values(
            row["DurationSeconds"] for row in completed
            if row["DurationSeconds"] is not None
        ),
        "totals": {
            "input_tokens": totals["PromptTokens"],
            "output_tokens": totals["CompletionTokens"],
            "cache_read_tokens": totals["CacheReadTokens"],
            "cache_write_tokens": totals["CacheWriteTokens"],
            "reasoning_tokens": totals["ReasoningTokens"],
            "api_calls": totals["ApiCalls"],
        },
    }


def build_report(campaign_root: Path, match_id: str,
                 portal_database: Path | None) -> dict[str, Any]:
    events = journal_events(campaign_root, match_id)
    actions = [event for event in events if event.get("event_type") == "game.action"]
    outcomes: Counter[str] = Counter()
    commands: Counter[str] = Counter()
    turn_advance_times: list[float] = []
    turn_advance_events: list[tuple[int, float]] = []
    action_turns: set[int] = set()
    previous_action_turn: int | None = None
    for event in actions:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        commands[str(payload.get("selected_action") or "unknown")] += 1
        outcomes[str(payload.get("outcome") or (
            "native_accepted" if payload.get("native_result") is not None else "recorded"
        ))] += 1
        before = payload.get("before") if isinstance(payload.get("before"), dict) else {}
        after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
        before_turn = before.get("turn") if isinstance(before.get("turn"), int) else None
        after_turn = after.get("turn") if isinstance(after.get("turn"), int) else before_turn
        action_turns.update(
            value for value in (before_turn, after_turn)
            if isinstance(value, int) and value >= 1
        )
        if before_turn is not None and previous_action_turn is not None \
                and before_turn > previous_action_turn:
            # Native automation can end a turn between the post-action snapshot
            # and the next decision. The adjacent, hash-linked action records
            # still prove the transition without relying on UI timing.
            turn_advance_events.append((before_turn, float(event["recorded_unix"])))
        if before_turn is not None and after_turn is not None and after_turn > before_turn:
            turn_advance_events.append((after_turn, float(event["recorded_unix"])))
        if after_turn is not None:
            previous_action_turn = after_turn
    first_by_turn: dict[int, float] = {}
    for turn, timestamp in sorted(turn_advance_events):
        first_by_turn.setdefault(turn, timestamp)
    ordered = sorted(first_by_turn.items())
    for (_, previous), (_, current) in zip(ordered, ordered[1:]):
        if current >= previous:
            turn_advance_times.append(current - previous)
    rebases = sum(
        1 for event in actions
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("guard_revalidated") is True
    )
    circuits = sum(
        1 for event in actions
        if isinstance(event.get("payload"), dict)
        and event["payload"].get("outcome") == "repetition_circuit_open"
    )
    incidents = [event for event in events if event.get("event_type") == "incident.supervision"]
    checkpoints = [event for event in events if event.get("event_type") == "checkpoint.native"]
    return {
        "schema": "smacx.agent-simulation-report.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "match_id": match_id,
        "privacy": {
            "raw_prompts_included": False, "raw_responses_included": False,
            "reasoning_text_included": False, "chat_included": False,
            "provider_endpoint_included": False, "game_assets_included": False,
        },
        "journal": verify_chains(events),
        "causal_progress": {
            "journaled_game_actions": len(actions),
            "distinct_action_turns": len(action_turns),
            "journaled_turn_advances": len(first_by_turn),
            "first_advanced_turn": min(first_by_turn) if first_by_turn else None,
            "last_advanced_turn": max(first_by_turn) if first_by_turn else None,
            "seconds_between_turn_advances": summarize_values(turn_advance_times),
            "native_command_counts": dict(sorted(commands.items())),
            "outcome_counts": dict(sorted(outcomes.items())),
            "automatic_stale_rebases": rebases,
            "repetition_circuits": circuits,
            "supervision_incidents": len(incidents),
            "verified_checkpoint_events": len(checkpoints),
        },
        "portal_telemetry": portal_metrics(portal_database, match_id),
        "validation": {
            "native_timer_must_be_none": True,
            "causal_success_requires_journaled_agent_turn_advance": True,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-root", type=Path, required=True)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--portal-db", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    report = build_report(
        arguments.campaign_root.expanduser().resolve(), arguments.match_id,
        arguments.portal_db.expanduser().resolve() if arguments.portal_db else None,
    )
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
