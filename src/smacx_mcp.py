#!/usr/bin/env python3
"""Persistent Streamable-HTTP MCP server for Sid Meier's Alpha Centauri."""

from __future__ import annotations

import inspect
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Literal, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlsplit
import uuid

from mcp.server import MCPServer

from smacx_capabilities import capability_manifest
from smacx_choice_preparation import ChoicePreparations, PreparationError, bind_numeric_choices, bind_ballot_choices
from smacx_controller import (
    acknowledge_match_briefing as controller_acknowledge_match_briefing,
    BridgeUnavailable,
    bridge_request,
    campaign_notebook as controller_campaign_notebook,
    chat_attention as controller_chat_attention,
    launch_game,
    list_scenarios,
    list_saved_games,
    load_saved_game,
    match_briefing_context as controller_match_briefing_context,
    match_briefing_acknowledgement_status as controller_match_briefing_acknowledgement_status,
    match_briefing_is_acknowledged as controller_match_briefing_is_acknowledged,
    new_game,
    scenario_game,
    record_campaign_action as controller_record_campaign_action,
    read_game_reference,
    read_platform_memory,
    semantic_chat as controller_semantic_chat,
    semantic_group_chat as controller_semantic_group_chat,
    stop_game,
    write_platform_memory,
    collect_perspective_world as controller_collect_perspective_world,
    world_service as controller_world_service,
)
from smacx_attention import AttentionError, WATCH_KINDS
from smacx_world import WORLD_MODES, WorldQueryError
from smacx_counterfactual import action_relationships, parse_scenario
from smacx_runtime_context import RuntimeContextAssembler
from smacx_world_types import content_hash
from smacx_specialists import (
    SpecialistError, SpecialistService,
)


mcp = MCPServer(
    "smacx",
    title="SMACX Agent",
    description="Nonvisual fair-play state and semantic control for Sid Meier's Alpha Centauri: Alien Crossfire.",
    instructions=(
        "Use only structured observations and opaque enumerated choices. "
        "Read and acknowledge smac_match_briefing before the first gameplay mutation and only "
        "when smac_decision reports a changed configuration thereafter. "
        "There are deliberately no screenshot, click, keyboard, or raw text-entry tools. "
        "If a needed capability is absent, call smac_report_capability_gap once and stop. "
        "A rule_advisory explains why an action is currently illegal; follow its recovery "
        "instruction instead of misreporting that native rule as a missing capability. "
        "Execute only with smac_execute_choice; its bounded rebase owns revision churn. "
        "Observations are restricted to the current human faction's legitimate perspective."
    ),
    version="0.48.0",
)

GAP_LOG = Path(os.environ.get(
    "SMACX_CAPABILITY_GAP_LOG",
    Path(__file__).resolve().parents[1] / "runtime" / "capability-gaps.jsonl",
))
MANAGED_ATTACHED = os.environ.get("SMACX_MANAGED_ATTACHED", "0") == "1"
CAPABILITY_GAPS: dict[tuple[str, str], dict] = {}
CAPABILITY_GAP_LOCK = threading.Lock()
MATCH_BRIEFING_CACHE: dict[tuple[str, str], str] = {}
MATCH_CONFIGURATION_CACHE: dict[tuple[str, str, str], dict] = {}
MATCH_BRIEFING_RESUME_NOTICES: set[tuple[str, str]] = set()
MATCH_BRIEFING_LOCK = threading.Lock()
DECISION_CACHE: dict[str, dict] = {}
DECISION_LOCK = threading.Lock()
DECISION_TTL_SECONDS = 180.0
CHOICE_PREPARATIONS = ChoicePreparations(ttl=DECISION_TTL_SECONDS)
AIRDROP_RECEIPT_CACHE: dict[tuple[str, ...], dict] = {}
AIRDROP_RECEIPT_LOCK = threading.Lock()
BASE_SITE_RECEIPT_CACHE: dict[tuple[str, ...], dict] = {}
BASE_SITE_RECEIPT_LOCK = threading.Lock()
SEMANTIC_LOCATION_CAPABILITIES: dict[tuple[str, ...], tuple[int, float]] = {}
SEMANTIC_LOCATION_CAPABILITY_LOCK = threading.Lock()
DECISION_CACHE_LIMIT = 128
ACTION_PROGRESS: dict[tuple[str, str], dict] = {}
ACTION_PROGRESS_LOCK = threading.RLock()
ACTION_REPEAT_LIMIT = 3
FAILED_CHOICE_LIMIT = 4
FAILED_CHOICE_ATTEMPTS: dict[tuple[str, str], list[str]] = {}
RUNTIME_CIRCUITS: dict[tuple[str, str], dict] = {}
TURN_HANDOFF_LOCK = threading.RLock()
TURN_HANDOFF_STATE: dict[tuple[str, str], dict[str, int]] = {}
TURN_HANDOFF_WORD_LIMIT = 120
RUNTIME_EPISODE_LOCK = threading.RLock()
RUNTIME_EPISODE_TOKENS: dict[str, str] = {}


def _short_text(value: object, maximum: int) -> str:
    """Bound provider-visible labels without accepting structured payloads."""
    text = str(value or "").replace("\x00", " ")
    return " ".join(text.split())[:maximum]


def _runtime_context_token() -> str:
    """Read the worker-scoped secret shared only with its Hermes container."""
    return Path(os.environ.get(
        "SMACX_AGENT_TOKEN_FILE", str(Path(__file__).resolve().parents[1] / "runtime" / "agent-token"),
    )).read_text(encoding="ascii").strip()


def _managed_scope_identity() -> tuple[str, str, str, str]:
    """Return the immutable managed seat identity supplied by orchestration."""
    return (
        os.environ.get("SMACX_AGENT_MATCH_ID", ""),
        os.environ.get("SMACX_AGENT_SESSION_ID", ""),
        os.environ.get("SMACX_AGENT_ID", ""),
        os.environ.get("SMACX_PERSPECTIVE_ID", ""),
    )


def _bound_scope_identity(
    match_id: str = "", session_id: str = "", agent_id: str = "",
    perspective_id: str = "",
) -> tuple[str, str, str, str]:
    """Bind managed calls to their immutable sidecar scope.

    Standalone compatibility calls retain their explicit arguments. A managed
    sovereign may omit redundant identifiers, but it may never nominate a
    different match/session/agent/perspective even if it has seen an opaque ID
    in old conversation text.
    """
    if not MANAGED_ATTACHED:
        return match_id, session_id, agent_id, perspective_id
    expected = _managed_scope_identity()
    supplied = (match_id, session_id, agent_id, perspective_id)
    labels = ("match", "session", "agent", "perspective")
    for label, value, bound in zip(labels, supplied, expected, strict=True):
        if value and value != bound:
            raise ValueError(f"managed_{label}_scope_mismatch")
    if not all(expected):
        raise ValueError("managed_scope_identity_unavailable")
    return expected


def _refresh_managed_world(*, background: bool = False) -> dict:
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    if not match_id or not session_id:
        return {"ok": False, "error": "managed_world_identity_unavailable"}
    try:
        return controller_collect_perspective_world(
            match_id, session_id, agent_id=agent_id, perspective_id=perspective_id,
            background=background,
        )
    except (BridgeUnavailable, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


def _runtime_services() -> tuple[RuntimeContextAssembler, object]:
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    scope, world, attention = controller_world_service(
        match_id, session_id=session_id, agent_id=agent_id,
        perspective_id=perspective_id,
    )

    def snapshot() -> dict:
        result = _call("semantic_snapshot")
        value = result.get("snapshot")
        if not result.get("ok") or not isinstance(value, dict):
            raise RuntimeError("runtime_snapshot_unavailable")
        return value

    def working_state() -> dict:
        result = read_platform_memory(
            "working_set", match_id, session_id=session_id,
            agent_id=agent_id, perspective_id=perspective_id,
        )
        if not isinstance(result, dict) or not result.get("ok"):
            return {}
        value = result.get("working_state") or result.get("memory") or result
        return value if isinstance(value, dict) else {}

    return RuntimeContextAssembler(
        scope=scope, world=world, attention=attention,
        snapshot=snapshot, working_state=working_state,
        interpretive_recall=lambda query: _graphiti_recall({
            "match_id": match_id, "session_id": session_id,
        }, query, limit=8),
    ), attention


def _sovereign_gameplay_gate(operation: str) -> dict | None:
    if not MANAGED_ATTACHED:
        return None
    try:
        _, attention = _runtime_services()
        active = attention.sovereign_state()
    except Exception as exc:
        return {"ok": False, "error": {"code": "sovereign_authority_unavailable",
                "message": str(exc)}, "gameplay_mutations_blocked": True}
    if not active:
        return {"ok": False, "error": {"code": "sovereign_episode_not_active",
                "message": f"{operation} requires the active serialized gameplay episode."},
                "gameplay_mutations_blocked": True}
    if active.get("episode_mode") != "gameplay":
        return {"ok": False, "error": {"code": "communication_episode_read_only",
                "message": f"{operation} is unavailable during a communication episode."},
                "gameplay_mutations_blocked": True}
    return None


class _RuntimeContextHandler(BaseHTTPRequestHandler):
    """Private, token-authenticated request-time bridge for the Hermes hook."""

    server_version = "SMACX-RuntimeContext"
    sys_version = ""

    def log_message(self, format_string: str, *args: object) -> None:
        return

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        return supplied.startswith("Bearer ") and hmac.compare_digest(
            supplied[7:].strip(), _runtime_context_token(),
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        parts = urlsplit(self.path)
        if parts.path != "/runtime-context":
            self._json(404, {"ok": False, "error": "not_found"})
            return
        query = parse_qs(parts.query)
        episode_id = query.get("episode_id", [""])[0]
        episode_mode = query.get("episode_mode", ["gameplay"])[0]
        try:
            context_length = int(query.get("context_length", ["65536"])[0])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}", episode_id):
                raise ValueError("invalid_episode_id")
            if not 65536 <= context_length <= 16_777_216:
                raise ValueError("invalid_context_length")
            refresh = _refresh_managed_world()
            if not refresh.get("ok"):
                raise RuntimeError(str(refresh.get("error")))
            match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
            controller_chat_attention(
                match_id, session_id, agent_id=agent_id, perspective_id=perspective_id,
            )
            assembler, attention = _runtime_services()
            with RUNTIME_EPISODE_LOCK:
                if episode_id not in RUNTIME_EPISODE_TOKENS:
                    RUNTIME_EPISODE_TOKENS[episode_id] = attention.acquire_sovereign(
                        episode_id, episode_mode,
                    )
            payload = assembler.build(
                episode_id=episode_id, episode_mode=episode_mode,
                context_length=context_length,
            )
            telemetry_dimensions = {"episode_mode": episode_mode}
            for query_name, metric_name in (
                ("request_tokens_before_gc", "request_tokens_before_gc"),
                ("request_tokens_after_gc", "request_tokens_after_gc"),
                ("semantic_gc_removed_rows", "semantic_gc_removed_rows"),
            ):
                try:
                    value = max(0, int(query.get(query_name, ["0"])[0]))
                except ValueError:
                    value = 0
                assembler.world.store.telemetry(
                    "runtime_context", metric_name, value, scope=assembler.scope,
                    timeline_id=str(payload.get("identity", {}).get("timeline_id") or ""),
                    dimensions=telemetry_dimensions,
                )
            lease = payload.get("attention", {})
            if lease.get("status", "leased") == "leased":
                attention.placed(str(lease["attention_lease_id"]))
                lease["status"] = "placed"
            self._json(200, {"ok": True, "runtime_context": payload})
        except Exception as exc:
            self._json(409, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        parts = urlsplit(self.path)
        if parts.path not in {"/runtime-context/responded", "/runtime-context/episode-ended"}:
            self._json(404, {"ok": False, "error": "not_found"})
            return
        try:
            length = min(max(int(self.headers.get("Content-Length", "0")), 0), 4096)
            body = json.loads(self.rfile.read(length) or b"{}")
            if parts.path == "/runtime-context/episode-ended":
                episode_id = str(body.get("episode_id") or "")
                with RUNTIME_EPISODE_LOCK:
                    token = RUNTIME_EPISODE_TOKENS.pop(episode_id, "")
                if not token:
                    self._json(200, {"ok": True, "already_released": True})
                    return
                _, attention = _runtime_services()
                attention.release_sovereign(token, committed=bool(body.get("committed", True)))
                self._json(200, {"ok": True})
                return
            lease_id = str(body.get("attention_lease_id") or "")
            _, attention = _runtime_services()
            attention.responded(lease_id)
            self._json(200, {"ok": True})
        except AttentionError as exc:
            # Retrying a durably recorded assistant response is idempotent.
            if str(exc) == "invalid_attention_lease_transition":
                self._json(200, {"ok": True, "already_recorded": True})
            else:
                self._json(409, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._json(409, {"ok": False, "error": str(exc)})


def _start_runtime_context_server() -> ThreadingHTTPServer:
    port = int(os.environ.get("SMACX_RUNTIME_CONTEXT_PORT", "47816"))
    server = ThreadingHTTPServer(("0.0.0.0", port), _RuntimeContextHandler)
    threading.Thread(target=server.serve_forever, name="smacx-runtime-context", daemon=True).start()
    return server


def _start_managed_runtime() -> None:
    """Seed an active world's projection before advertising MCP readiness.

    A first Huge-map collection can outlast a provider HTTP request. Lobby
    sessions may have no active world yet; their observer continues retrying,
    and every later runtime request still requires successful reconciliation.
    """
    _refresh_managed_world()
    _refresh_managed_world(background=True)
    _start_runtime_context_server()


def _turn_number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _turn_handoff_payload(completed_from: int, completed_through: int,
                          next_turn: int | None) -> dict:
    completed = (str(completed_from) if completed_from == completed_through else
                 f"{completed_from}-{completed_through}")
    return {
        "required": True,
        "completed_turns": completed,
        "next_turn": next_turn,
        "instruction": (
            "Native control passed. Make no more tool calls in this episode. "
            "Return the concise TURN HANDOFF required by your system contract now."
        ),
        "sections": [
            "Outcome", "Rationale", "Changed conclusions", "Next intent", "Uncertainty",
        ],
        "target_words": 85,
        "maximum_words": TURN_HANDOFF_WORD_LIMIT,
    }


def _semantic_turn_handoff_gc(identity: dict, next_turn: int) -> None:
    try:
        match_id = str(identity.get("match_id") or "")
        session_id = str(identity.get("session_id") or "")
        _, _, attention = controller_world_service(
            match_id, session_id=session_id,
            agent_id=os.environ.get("SMACX_AGENT_ID", ""),
            perspective_id=os.environ.get("SMACX_PERSPECTIVE_ID", ""),
        )
        attention.turn_handoff(next_turn)
        # Regeneration is deterministic on the changed turn; no old anchor is
        # copied into durable Hermes history.
        scope, world, _ = controller_world_service(
            match_id, session_id=session_id,
            agent_id=os.environ.get("SMACX_AGENT_ID", ""),
            perspective_id=os.environ.get("SMACX_PERSPECTIVE_ID", ""),
        )
        SpecialistService(world.store.store, world.store, scope).cancel_for_turn_handoff()
        world.anchor(context_length=int(os.environ.get("SMACX_CONTEXT_LENGTH", "65536")))
    except Exception:
        # Native handoff remains authoritative. The next request retries world
        # reconstruction and surfaces a fail-closed incident if it cannot fit.
        return


def _track_observed_turn(identity: dict, current_turn: object) -> None:
    """Remember the newest native turn without declaring a boundary."""
    current = _turn_number(current_turn)
    key = (str(identity.get("match_id") or ""), str(identity.get("session_id") or ""))
    if current is None or not all(key):
        return
    with TURN_HANDOFF_LOCK:
        state = TURN_HANDOFF_STATE.get(key)
        if state is None or current < state["observed_turn"]:
            TURN_HANDOFF_STATE[key] = {
                "observed_turn": current, "handed_off_through": current - 1,
            }
        else:
            state["observed_turn"] = max(state["observed_turn"], current)


def _implicit_turn_handoff(snapshot: dict, identity: dict) -> dict | None:
    """Detect control cycling away and back even when no end-turn action returned it.

    Some native automation ends a turn after the final unit order and the next
    observation arrives directly in the following turn, without exposing a
    stable wait phase.  The semantic frame must still yield a durable episode
    boundary before offering the following turn's legal choices.
    """
    current = _turn_number(snapshot.get("turn"))
    key = (str(identity.get("match_id") or ""), str(identity.get("session_id") or ""))
    if current is None or not all(key):
        return None
    with TURN_HANDOFF_LOCK:
        state = TURN_HANDOFF_STATE.get(key)
        if state is None or current < state["observed_turn"]:
            TURN_HANDOFF_STATE[key] = {
                "observed_turn": current, "handed_off_through": current - 1,
            }
            return None
        previous = state["observed_turn"]
        if current <= previous:
            return None
        state["observed_turn"] = current
        completed_from = max(previous, state["handed_off_through"] + 1)
        completed_through = current - 1
        # Turn zero is native match setup, not a playable turn to memorialize.
        if completed_through < max(completed_from, 1):
            return None
        completed_from = max(completed_from, 1)
        state["handed_off_through"] = completed_through
    handoff = _turn_handoff_payload(completed_from, completed_through, current)
    _semantic_turn_handoff_gc(identity, current)
    return {
        "ok": True,
        "kind": "turn_handoff_required",
        "identity": identity,
        "turn": current,
        "year": snapshot.get("year"),
        "phase": "handoff",
        "state": _compact_decision_state(snapshot),
        "focus": {
            "kind": "native_turn_boundary",
            "completed_turns": handoff["completed_turns"],
            "next_turn": current,
        },
        "choices": [],
        "required_next": {"stop_after": True, "ordinary_message": "TURN HANDOFF"},
        "turn_handoff_required": handoff,
    }


def _attach_turn_handoff(response: dict, choice: dict, decision: dict,
                         snapshot: dict | None) -> None:
    """Tell the player when one native-control episode has truly ended."""
    if not isinstance(snapshot, dict):
        return
    protocol = snapshot.get("protocol")
    after_phase = protocol.get("phase") if isinstance(protocol, dict) else None
    after_turn = snapshot.get("turn")
    end_action = choice.get("command") in {
        "end_turn", "respond_to_end_turn_confirmation",
    }
    before_turn = _turn_number(decision.get("turn"))
    current_turn = _turn_number(after_turn)
    identity = decision.get("identity") if isinstance(decision.get("identity"), dict) else {}
    _track_observed_turn(identity, current_turn)
    turn_advanced = before_turn is not None and current_turn is not None \
        and current_turn > before_turn
    if before_turn is None or before_turn < 1 or not (
            turn_advanced or (end_action and after_phase == "wait")):
        return
    through = (current_turn - 1) if turn_advanced and current_turn is not None else before_turn
    key = (str(identity.get("match_id") or ""), str(identity.get("session_id") or ""))
    if all(key):
        with TURN_HANDOFF_LOCK:
            state = TURN_HANDOFF_STATE.setdefault(key, {
                "observed_turn": current_turn if current_turn is not None else before_turn,
                "handed_off_through": before_turn - 1,
            })
            state["handed_off_through"] = max(state["handed_off_through"], through)
    response["turn_handoff_required"] = _turn_handoff_payload(
        before_turn, through, current_turn if turn_advanced else None,
    )
    _semantic_turn_handoff_gc(identity, current_turn or before_turn + 1)

STALE_REBASE_UNIT_COMMANDS = {
    "auto_explore_unit", "hold_unit", "sentry_unit", "skip_unit",
}


def _revision_conflict_report(*values: str) -> bool:
    text = " ".join(values).lower()
    return any(marker in text for marker in (
        "stale_state", "stale state", "expected_revision", "current_revision",
        "revision guard", "revision counter", "revision mismatch",
    ))


def _fresh_unit_choice_for_stale_command(
    command: str, unit_id: int, target_tile_id: int, target_unit_id: int,
) -> dict | None:
    if command not in STALE_REBASE_UNIT_COMMANDS or unit_id < 0:
        return None
    refreshed = _call(
        "semantic_choices", kind="unit_actions", unit_id=unit_id,
        target_tile_id=target_tile_id, target_unit_id=target_unit_id,
    )
    if not refreshed.get("ok"):
        return None
    for choice in refreshed.get("choices", []):
        if not isinstance(choice, dict) or choice.get("command") != command:
            continue
        if int(choice.get("unit_id", unit_id)) != unit_id:
            continue
        return refreshed
    return None
SESSION_LOCAL_KNOWLEDGE_REFERENCE = re.compile(
    r"(?:\b(?:unit|vehicle|base|prototype)[ _-]?ids?\b"
    r"|\(\s*id\s*[:=#-]?\s*\d+\s*\)"
    r"|\bbase\s+#?\d+\b)",
    re.IGNORECASE,
)


def _capability_gap_summary(report: dict) -> dict:
    return {
        key: report[key]
        for key in (
            "gap_id", "reported_at_unix", "match_id", "session_id", "revision",
            "turn", "screen_or_state", "intended_decision", "required_observation",
            "required_action", "why_blocked",
        )
        if key in report
    }


def _pending_capability_gap() -> dict | None:
    with CAPABILITY_GAP_LOCK:
        if not CAPABILITY_GAPS:
            return None
        report = max(
            CAPABILITY_GAPS.values(), key=lambda item: float(item.get("reported_at_unix", 0)),
        )
        return _capability_gap_summary(report)


def _capability_gap_blocked(operation: str) -> dict | None:
    gap = _pending_capability_gap()
    if not gap:
        return None
    return {
        "ok": False,
        "error": {
            "code": "capability_gap_latched",
            "message": f"{operation} is blocked because a semantic capability gap is awaiting bridge development.",
        },
        "gap": gap,
        "gameplay_mutations_blocked": True,
        "instruction": "STOP. The orchestrator must extend and test the bridge, restart the MCP service, and only then start a fresh native session.",
    }


def _call(operation: str, **arguments: object) -> dict:
    try:
        return bridge_request(operation, **arguments)
    except BridgeUnavailable as exc:
        next_step = (
            "Ask the operator to start or recover this match's managed game worker."
            if MANAGED_ATTACHED else "Call smac_launch."
        )
        return {"ok": False, "error": "game_not_connected", "message": str(exc), "next": next_step}


def _resolve_native_unit_id(own_unit_ref: str) -> int | None:
    """Resolve a provider-safe owned-unit ref behind the native boundary."""
    if not re.fullmatch(r"own-unit-[1-9][0-9]{0,9}", own_unit_ref):
        return None
    cursor = 0
    for _ in range(16):
        page = _call("perspective_world_page", domain="units", cursor=cursor, limit=256)
        if page.get("ok") is not True:
            return None
        for item in page.get("items", []):
            if isinstance(item, dict) and item.get("owned") is True \
                    and item.get("own_unit_ref") == own_unit_ref:
                native_id = item.get("id")
                return int(native_id) if isinstance(native_id, int) else None
        next_cursor = page.get("next_cursor")
        if not isinstance(next_cursor, int) or next_cursor <= cursor:
            return None
        cursor = next_cursor
    return None


class SemanticSelectorError(ValueError):
    """A provider semantic reference is not valid in the current native frame."""


def _native_pages(domain: str) -> list[dict[str, Any]]:
    """Read one bounded native domain while rejecting revision changes mid-read."""
    rows: list[dict[str, Any]] = []
    cursor = 0
    revision = ""
    for _ in range(32):
        page = _call("perspective_world_page", domain=domain, cursor=cursor, limit=256)
        if page.get("ok") is not True:
            raise SemanticSelectorError("semantic_reference_native_projection_unavailable")
        page_revision = str(page.get("action_revision") or "")
        if revision and page_revision != revision:
            raise SemanticSelectorError("semantic_reference_native_projection_changed")
        revision = page_revision
        rows.extend(item for item in page.get("items", ()) if isinstance(item, dict))
        next_cursor = page.get("next_cursor")
        if not isinstance(next_cursor, int) or next_cursor <= cursor:
            break
        cursor = next_cursor
    return rows


def _field_current(item: Mapping[str, Any], name: str) -> bool:
    fields = item.get("fields") if isinstance(item.get("fields"), Mapping) else {}
    value = fields.get(name)
    return isinstance(value, Mapping) and value.get("epistemic_status") == "current"


def _semantic_selector_context(expected_revision: str) -> dict[str, Any]:
    """Bind provider refs to the exact current perspective and private native rows.

    The spelling of a semantic reference is never decoded. Resolution is an
    equality join between the active world projection and a same-revision
    native perspective page. This rejects old timelines/epochs, compacted VEH
    rows, lost contacts, and refs copied from another seat.
    """
    if not MANAGED_ATTACHED:
        summary = _call("perspective_world_page", domain="summary", cursor=0, limit=1)
        if summary.get("ok") is not True \
                or str(summary.get("action_revision") or "") != expected_revision:
            raise SemanticSelectorError("semantic_reference_native_revision_changed")
        units = _native_pages("units")
        bases = _native_pages("bases")
        by_ref: dict[str, int] = {}
        objects: dict[str, dict[str, Any]] = {}
        reverse_units: dict[int, str] = {}
        reverse_bases: dict[int, str] = {}
        reverse_locations: dict[int, str] = {}
        for row in units:
            native_id = row.get("id")
            ref = row.get("own_unit_ref")
            if row.get("owned") is True and isinstance(native_id, int) and isinstance(ref, str):
                by_ref[ref] = native_id
                reverse_units[native_id] = ref
                objects[ref] = {"kind": "own_unit", "status": "active"}
        for row in bases:
            native_id = row.get("id")
            ref = row.get("base_ref")
            if row.get("owned") is True and isinstance(native_id, int) and isinstance(ref, str):
                by_ref[ref] = native_id
                reverse_bases[native_id] = ref
                objects[ref] = {"kind": "base", "status": "active", "fields": {
                    "owner_ref": {"source": "owned_state", "epistemic_status": "current"},
                }}
            tile_id = row.get("tile_id")
            if isinstance(tile_id, int):
                location = f"location-{tile_id}"
                by_ref[location] = tile_id
                reverse_locations[tile_id] = location
                objects[location] = {"kind": "location", "status": "active"}
        for row in units:
            tile_id = row.get("tile_id")
            if isinstance(tile_id, int):
                location = f"location-{tile_id}"
                by_ref.setdefault(location, tile_id)
                reverse_locations.setdefault(tile_id, location)
                objects.setdefault(location, {"kind": "location", "status": "active"})
        return {
            "identity": {}, "action_revision": expected_revision, "by_ref": by_ref,
            "objects": objects, "reverse_units": reverse_units,
            "reverse_bases": reverse_bases, "reverse_locations": reverse_locations,
        }
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    try:
        _, world, _ = controller_world_service(
            match_id, session_id=session_id, agent_id=agent_id,
            perspective_id=perspective_id,
        )
        identity, projection = world._projection()
    except Exception as exc:
        raise SemanticSelectorError("semantic_reference_world_unavailable") from exc
    if identity.match_id != match_id or identity.perspective_id != perspective_id:
        raise SemanticSelectorError("semantic_reference_scope_mismatch")
    action_revision = str(projection.get("action_revision") or "")
    if not action_revision or action_revision != expected_revision:
        raise SemanticSelectorError("semantic_reference_stale_revision")
    objects = world._objects(projection)
    native_units = _native_pages("units")
    native_bases = _native_pages("bases")
    # Pages are each coherent; confirm the native head remains unchanged after
    # both joins. No result from an earlier compacted row may be reused.
    check = _call("perspective_world_page", domain="summary", cursor=0, limit=1)
    if check.get("ok") is not True \
            or str(check.get("action_revision") or "") != expected_revision:
        raise SemanticSelectorError("semantic_reference_native_revision_changed")

    by_ref: dict[str, int] = {}
    reverse_units: dict[int, str] = {}
    reverse_bases: dict[int, str] = {}
    reverse_locations: dict[int, str] = {}

    unit_by_owned_ref = {
        str(row.get("own_unit_ref")): row for row in native_units
        if row.get("owned") is True and row.get("own_unit_ref")
    }
    unit_by_observation_key = {
        str(row.get("native_observation_key")): row for row in native_units
        if row.get("owned") is not True and row.get("native_observation_key")
    }
    base_by_ref = {str(row.get("base_ref")): row for row in native_bases if row.get("base_ref")}
    # SQLite reconstruction stores dimensions in the map-state object. Guarded
    # selectors require that explicit evidence; the read-only topology fallback
    # that estimates dimensions from known squares must never bind an action.
    map_state = next((item for item in objects.values() if item.get("kind") == "map_state"), {})
    if not isinstance(projection.get("map_shape"), Mapping) and not all(
        _field_current(map_state, name) for name in ("width", "height", "horizontal_wrap")
    ):
        raise SemanticSelectorError("semantic_reference_map_shape_unavailable")
    shape = world._topology(projection).shape
    for ref, item in objects.items():
        kind = str(item.get("kind") or "")
        if item.get("status", "active") != "active" and not (
            kind == "location" and item.get("status") == "stale"
        ):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
        if kind == "location":
            x = metadata.get("native_x")
            y = metadata.get("native_y")
            if isinstance(x, int) and isinstance(y, int) and shape.normalize((x, y)) == (x, y):
                tile_id = (x + shape.width * y) // 2
                by_ref[ref] = tile_id
                reverse_locations[tile_id] = ref
        elif kind == "own_unit":
            row = unit_by_owned_ref.get(ref)
            native_id = row.get("id") if row else None
            if isinstance(native_id, int):
                by_ref[ref] = native_id
                reverse_units[native_id] = ref
        elif kind == "foreign_contact":
            key = metadata.get("native_observation_key")
            row = unit_by_observation_key.get(str(key)) if key else None
            native_id = row.get("id") if row else None
            if isinstance(native_id, int) and _field_current(item, "last_seen_turn"):
                by_ref[ref] = native_id
                reverse_units[native_id] = ref
        elif kind == "base":
            row = base_by_ref.get(ref)
            native_id = row.get("id") if row else None
            if isinstance(native_id, int) and _field_current(item, "owner_ref"):
                by_ref[ref] = native_id
                reverse_bases[native_id] = ref
    capability_prefix = (
        match_id, session_id, agent_id, perspective_id,
        identity.timeline_id, identity.world_epoch, expected_revision,
    )
    now = time.monotonic()
    with SEMANTIC_LOCATION_CAPABILITY_LOCK:
        expired = [key for key, (_, expires) in SEMANTIC_LOCATION_CAPABILITIES.items()
                   if expires <= now]
        for key in expired:
            SEMANTIC_LOCATION_CAPABILITIES.pop(key, None)
        for key, (tile_id, _) in SEMANTIC_LOCATION_CAPABILITIES.items():
            if key[:7] != capability_prefix:
                continue
            ref = key[7]
            by_ref[ref] = tile_id
            reverse_locations[tile_id] = ref
    return {
        "identity": identity.as_dict(), "action_revision": action_revision,
        "by_ref": by_ref, "objects": objects,
        "reverse_units": reverse_units, "reverse_bases": reverse_bases,
        "reverse_locations": reverse_locations,
    }


def _resolve_managed_selectors(
    expected_revision: str, *, own_unit_ref: str = "", base_ref: str = "",
    target_location_ref: str = "", target_unit_ref: str = "",
) -> tuple[dict[str, int], dict[str, Any]]:
    context = _semantic_selector_context(expected_revision)
    objects = context["objects"]
    by_ref = context["by_ref"]
    resolved: dict[str, int] = {}
    contracts = (
        ("own_unit_ref", own_unit_ref, "unit_id", {"own_unit"}),
        ("base_ref", base_ref, "base_id", {"base"}),
        ("target_location_ref", target_location_ref, "target_tile_id", {"location"}),
        ("target_unit_ref", target_unit_ref, "target_unit_id", {"own_unit", "foreign_contact"}),
    )
    for public_name, ref, native_name, kinds in contracts:
        if not ref:
            continue
        item = objects.get(ref)
        synthetic_location = public_name == "target_location_ref" \
            and ref in by_ref and item is None
        remembered_location = public_name == "target_location_ref" and isinstance(item, Mapping) \
            and item.get("kind") == "location" and item.get("status") == "stale"
        if not synthetic_location and (
                not isinstance(item, Mapping) or item.get("status", "active") != "active" and not remembered_location
                or item.get("kind") not in kinds or ref not in by_ref):
            raise SemanticSelectorError(f"invalid_current_{public_name}")
        if public_name == "base_ref":
            owner = item.get("fields", {}).get("owner_ref", {})
            if not isinstance(owner, Mapping) or owner.get("source") != "owned_state":
                raise SemanticSelectorError("base_ref_must_be_current_owned_base")
        resolved[native_name] = int(by_ref[ref])
    return resolved, context


_CHOICE_REF_KEYS = {
    "unit_id": ("own_unit_ref", "reverse_units"),
    "attacker_unit_id": ("attacker_unit_ref", "reverse_units"),
    "carrier_unit_id": ("carrier_unit_ref", "reverse_units"),
    "defender_unit_id": ("defender_unit_ref", "reverse_units"),
    "deleted_unit_id": ("deleted_unit_ref", "reverse_units"),
    "former_unit_id": ("former_unit_ref", "reverse_units"),
    "selected_unit_id": ("selected_unit_ref", "reverse_units"),
    "source_unit_id": ("source_unit_ref", "reverse_units"),
    "transport_unit_id": ("transport_unit_ref", "reverse_units"),
    "target_unit_id": ("target_unit_ref", "reverse_units"),
    "base_id": ("base_ref", "reverse_bases"),
    "headquarters_base_id": ("headquarters_base_ref", "reverse_bases"),
    "home_base_id": ("home_base_ref", "reverse_bases"),
    "old_home_base_id": ("old_home_base_ref", "reverse_bases"),
    "opened_base_id": ("opened_base_ref", "reverse_bases"),
    "source_base_id": ("source_base_ref", "reverse_bases"),
    "destination_base_id": ("destination_base_ref", "reverse_bases"),
    "target_base_id": ("target_base_ref", "reverse_bases"),
    "tile_id": ("location_ref", "reverse_locations"),
    "base_tile_id": ("base_location_ref", "reverse_locations"),
    "from_tile_id": ("from_location_ref", "reverse_locations"),
    "observed_tile_id": ("observed_location_ref", "reverse_locations"),
    "origin_tile_id": ("origin_location_ref", "reverse_locations"),
    "to_tile_id": ("to_location_ref", "reverse_locations"),
    "source_tile_id": ("source_location_ref", "reverse_locations"),
    "destination_tile_id": ("destination_location_ref", "reverse_locations"),
    "target_tile_id": ("target_location_ref", "reverse_locations"),
}

_CHOICE_REF_LIST_KEYS = {
    "ready_unit_ids": ("ready_unit_refs", "reverse_units"),
    "skipped_unit_ids": ("skipped_unit_refs", "reverse_units"),
}


def _semanticize_choice(value: Any, context: Mapping[str, Any] | None) -> Any:
    """Translate private native entity selectors to provider-safe world refs."""
    if isinstance(value, list):
        return [_semanticize_choice(item, context) for item in value]
    if not isinstance(value, Mapping):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"chassis_id", "weapon_id", "armor_id", "reactor_id", "ability_id_1", "ability_id_2", "prototype_id", "source_prototype_id", "target_prototype_id"}:
            continue
        list_contract = _CHOICE_REF_LIST_KEYS.get(str(key))
        if list_contract:
            public_key, reverse_name = list_contract
            reverse = context.get(reverse_name, {}) if isinstance(context, Mapping) else {}
            refs = [reverse.get(native_id) for native_id in item] \
                if isinstance(item, list) and isinstance(reverse, Mapping) else []
            result[public_key] = [ref for ref in refs if isinstance(ref, str)]
            continue
        contract = _CHOICE_REF_KEYS.get(str(key))
        if contract:
            public_key, reverse_name = contract
            reverse = context.get(reverse_name, {}) if isinstance(context, Mapping) else {}
            ref = reverse.get(item) if isinstance(reverse, Mapping) else None
            if isinstance(ref, str):
                result[public_key] = ref
            continue
        result[str(key)] = _semanticize_choice(item, context)
    return result


def _choice_contains_private_selector(value: Any) -> bool:
    if isinstance(value, list):
        return any(_choice_contains_private_selector(item) for item in value)
    if not isinstance(value, Mapping):
        return False
    return any(
        str(key) in _CHOICE_REF_KEYS or str(key) in _CHOICE_REF_LIST_KEYS
        or _choice_contains_private_selector(item)
        for key, item in value.items()
    )


def _managed_lifecycle_block(operation: str) -> dict | None:
    if not MANAGED_ATTACHED:
        return None
    return {
        "ok": False,
        "error": {
            "code": "managed_lifecycle_operator_only",
            "message": f"{operation} is controlled by the authenticated SMACX Control Center.",
        },
        "next": "Continue only with semantic in-game actions, or ask the operator to manage this worker.",
    }


def _requested_native_differences(requested: object, native: object) -> list[dict]:
    if not isinstance(requested, dict) or not isinstance(native, dict):
        return []
    game_map = native.get("map") if isinstance(native.get("map"), dict) else {}
    rules = native.get("rules") if isinstance(native.get("rules"), dict) else {}
    difficulty = native.get("difficulty") \
        if isinstance(native.get("difficulty"), dict) else {}
    time_control = native.get("time_control") \
        if isinstance(native.get("time_control"), dict) else {}
    observed: dict[str, object] = {
        **{key: rules[key] for key in rules},
        "world_size": game_map.get("size_id"),
        "custom_width": game_map.get("width"),
        "custom_height": game_map.get("height"),
        "difficulty": difficulty.get("id"),
        "time_control": time_control.get("id"),
    }
    for key in ("ocean_coverage", "erosive_forces", "native_life", "cloud_cover"):
        observed[key] = game_map.get(key)
    differences = []
    for key, expected in sorted(requested.items()):
        # Random/custom generation is a launch method, not a native persisted
        # rule. Everything else is compared only when the bridge can observe it.
        if key == "map_generation" or key not in observed or observed[key] is None:
            continue
        if observed[key] != expected:
            differences.append({
                "field": key, "requested": expected, "native": observed[key],
            })
    return differences


def _configuration_changes(previous: object, current: object,
                           path: str = "") -> list[dict]:
    if isinstance(previous, dict) and isinstance(current, dict):
        changes: list[dict] = []
        for key in sorted(set(previous) | set(current)):
            child = f"{path}.{key}" if path else str(key)
            if key not in previous:
                changes.append({"field": child, "before": None, "after": current[key]})
            elif key not in current:
                changes.append({"field": child, "before": previous[key], "after": None})
            else:
                changes.extend(_configuration_changes(previous[key], current[key], child))
        return changes
    if isinstance(previous, list) and isinstance(current, list) and previous == current:
        return []
    return [] if previous == current else [{
        "field": path or "$", "before": previous, "after": current,
    }]


def _canonical_match_configuration(snapshot: dict, context: dict) -> dict:
    active = snapshot.get("faction") if isinstance(snapshot.get("faction"), dict) else {}
    seat = context.get("seat") if isinstance(context.get("seat"), dict) else {}
    match = context.get("match") if isinstance(context.get("match"), dict) else {}
    source = context.get("game_source") \
        if isinstance(context.get("game_source"), dict) else {}
    configuration = {
        "match": {
            "mode": match.get("mode"),
            "ruleset_id": match.get("ruleset_id"),
        },
        "seat": {
            "seat_index": seat.get("seat_index"),
            "controller_kind": seat.get("controller_kind"),
            "faction_id": active.get("id", seat.get("assigned_faction_id")),
            "faction_name": active.get("name", seat.get("assigned_faction_name")),
        },
        # These bridge objects are defined as configuration-only contracts.
        # Their complete shape is intentionally canonicalized so newly exposed
        # native rules are safe-by-default and automatically participate in the hash.
        "native_game_settings": snapshot.get("game_settings"),
        "scenario": snapshot.get("scenario"),
        "control_policy": context.get("policy") or {},
        "game_artifact": {
            "executable_sha256": source.get("executable_sha256"),
        },
    }
    # Detach the immutable contract from the bridge response. Test doubles and
    # future bridge clients may reuse mutable dictionaries between snapshots.
    return json.loads(json.dumps(configuration, ensure_ascii=False))


def _compose_match_briefing(snapshot: dict) -> dict:
    match_id = str(snapshot.get("match_id") or "")
    session_id = str(snapshot.get("session_id") or "")
    context = controller_match_briefing_context(match_id, session_id)
    if not context.get("ok"):
        return context
    configuration = _canonical_match_configuration(snapshot, context)
    contract = {
        "schema": "smacx.match-briefing.v2",
        "configuration": configuration,
    }
    encoded = json.dumps(
        contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    import hashlib
    briefing_hash = hashlib.sha256(encoded).hexdigest()
    status = controller_match_briefing_acknowledgement_status(
        match_id, session_id, briefing_hash,
    )
    if not status.get("ok"):
        return status
    acknowledged = bool(status.get("acknowledged"))
    inherited_from_session = None
    if acknowledged and not status.get("current_session"):
        inherited_from_session = status.get("previous_session_id")
        inherited = controller_acknowledge_match_briefing(
            match_id, session_id, briefing_hash,
        )
        if not inherited.get("ok"):
            return inherited
    scope_key = (
        match_id, str(context["scope"]["agent_id"]),
        str(context["scope"]["perspective_id"]),
    )
    with MATCH_BRIEFING_LOCK:
        previous = MATCH_CONFIGURATION_CACHE.get(scope_key)
        MATCH_BRIEFING_CACHE[(match_id, session_id)] = briefing_hash
        if acknowledged:
            MATCH_CONFIGURATION_CACHE[scope_key] = {
                "briefing_hash": briefing_hash, "configuration": configuration,
            }
        resume_notice = None
        notice_key = (match_id, session_id)
        if acknowledged and not status.get("current_session") \
                and notice_key not in MATCH_BRIEFING_RESUME_NOTICES:
            resume_notice = {
                "kind": "session_resumed",
                "session_id": session_id,
                "configuration_hash": briefing_hash,
                "configuration_unchanged": True,
                "inherited_from_session_id": inherited_from_session,
                "required_next": "Use this session's fresh smac_decision guard.",
            }
    previous_hash = (
        str(previous.get("briefing_hash")) if isinstance(previous, dict)
        else str(status.get("previous_briefing_hash") or "")
    )
    if acknowledged:
        change = {"kind": "unchanged", "changes": []}
    elif previous_hash and previous_hash != briefing_hash:
        changes = _configuration_changes(
            previous.get("configuration"), configuration,
        ) if isinstance(previous, dict) else []
        change = {
            "kind": "configuration_changed",
            "previous_hash": previous_hash,
            "changes": changes[:64],
            "complete_delta_available": bool(previous),
        }
    else:
        change = {"kind": "opening", "changes": []}
    topics = context.get("reference_topics")
    topic_count = len(topics) if isinstance(topics, list) else 0
    briefing = {
        "schema": "smacx.match-briefing.v2",
        "identity": {
            "match_id": match_id, "session_id": session_id,
            "agent_id": context["scope"]["agent_id"],
            "perspective_id": context["scope"]["perspective_id"],
        },
        "configuration": configuration,
        "information": {
            "display_name": context.get("match", {}).get("display_name"),
            "game_source_name": context.get("game_source", {}).get("display_name")
                if isinstance(context.get("game_source"), dict) else None,
            "reference_available": context.get("reference_status") == "ready",
            "reference_topic_count": topic_count,
            "requested_vs_native_changes": _requested_native_differences(
                context.get("requested_settings"), snapshot.get("game_settings"),
            ),
        },
    }
    result = {
        "ok": True, "briefing": briefing, "briefing_hash": briefing_hash,
        "configuration_hash": briefing_hash, "change": change,
        "acknowledged": acknowledged,
        "gameplay_mutations_blocked": not acknowledged,
    }
    if resume_notice is not None:
        result["resume_notice"] = resume_notice
    return result


def _match_briefing_gate(match_id: str, session_id: str) -> dict | None:
    with MATCH_BRIEFING_LOCK:
        briefing_hash = MATCH_BRIEFING_CACHE.get((match_id, session_id))
    if briefing_hash and controller_match_briefing_is_acknowledged(
            match_id, session_id, briefing_hash):
        return None
    return {
        "ok": False,
        "error": {
            "code": "match_briefing_required",
            "message": "Read and acknowledge the authoritative match briefing before gameplay mutations.",
        },
        "required_next": {"tool": "smac_match_briefing", "action": "read"},
        "gameplay_mutations_blocked": True,
    }


def _attach_briefing_status(frame: dict, briefing: dict) -> dict:
    frame["configuration_hash"] = briefing.get("configuration_hash")
    if isinstance(briefing.get("resume_notice"), dict):
        frame["resume_notice"] = briefing["resume_notice"]
        identity = frame.get("identity") if isinstance(frame.get("identity"), dict) else {}
        with MATCH_BRIEFING_LOCK:
            MATCH_BRIEFING_RESUME_NOTICES.add((
                str(identity.get("match_id") or ""),
                str(identity.get("session_id") or ""),
            ))
    return frame


@mcp.tool(
    description=(
        "Read or acknowledge the authoritative match configuration. It combines stable native "
        "game settings and scenario restrictions with managed seat, source, clock, and policy "
        "context. Ordinary turn state never changes its hash; unchanged recovery emits a compact "
        "resume notice. Gameplay mutations remain locked until a changed configuration_hash is "
        "acknowledged."
    )
)
def smac_match_briefing(
    action: Literal["read", "acknowledge"],
    briefing_hash: str = "",
) -> dict:
    snapshot_result = _call("semantic_snapshot")
    snapshot = snapshot_result.get("snapshot")
    if not snapshot_result.get("ok") or not isinstance(snapshot, dict):
        return snapshot_result
    result = _compose_match_briefing(snapshot)
    if not result.get("ok") or action == "read":
        notice = result.get("resume_notice")
        if isinstance(notice, dict):
            with MATCH_BRIEFING_LOCK:
                MATCH_BRIEFING_RESUME_NOTICES.add((
                    str(snapshot.get("match_id") or ""),
                    str(snapshot.get("session_id") or ""),
                ))
        return result
    current_hash = str(result.get("briefing_hash") or "")
    if briefing_hash != current_hash:
        return {
            "ok": False,
            "error": {
                "code": "stale_match_briefing",
                "message": "The acknowledgement must copy the exact current briefing_hash.",
            },
            "current_briefing_hash": current_hash,
            "gameplay_mutations_blocked": True,
        }
    acknowledged = controller_acknowledge_match_briefing(
        str(snapshot["match_id"]), str(snapshot["session_id"]), current_hash,
    )
    if not acknowledged.get("ok"):
        return acknowledged
    context = result.get("briefing", {}).get("identity", {})
    configuration = result.get("briefing", {}).get("configuration")
    scope_key = (
        str(snapshot["match_id"]), str(context.get("agent_id") or ""),
        str(context.get("perspective_id") or ""),
    )
    with MATCH_BRIEFING_LOCK:
        MATCH_CONFIGURATION_CACHE[scope_key] = {
            "briefing_hash": current_hash, "configuration": configuration,
        }
    return {
        "ok": True,
        "schema": "smacx.match-briefing-acknowledgement.v2",
        "briefing_hash": current_hash,
        "configuration_hash": current_hash,
        "acknowledged": True,
        "gameplay_mutations_blocked": False,
        "acknowledged_unix": acknowledged.get("acknowledgement", {}).get(
            "acknowledged_unix"
        ),
        "next": "Call smac_decision and act only on a fresh exact choice.",
    }


def _await_deferred_action(result: dict, timeout: float = 8.0) -> dict:
    """Turn a queued native action into a definitive MCP result when possible."""
    action_id = result.get("action_id")
    if not result.get("ok") or not result.get("queued") or not isinstance(action_id, int):
        return result
    deadline = time.monotonic() + timeout
    action: dict | None = None
    while time.monotonic() < deadline:
        status = _call("action_status", action_id=action_id)
        if not status.get("ok"):
            return status
        action = status.get("action")
        if isinstance(action, dict) and action.get("status") != "pending":
            break
        time.sleep(0.05)
    if not isinstance(action, dict) or action.get("status") == "pending":
        return {
            **result,
            "execution": action or {"action_id": action_id, "status": "pending"},
            "next": "Wait, observe last_deferred_action, and do not queue another command meanwhile.",
        }
    if action.get("status") == "rejected":
        return {
            "ok": False,
            "error": {
                "code": "native_action_rejected",
                "message": "The queued action did not complete. Read its execution receipt; a native rejection does not establish why movement failed. Obtain a fresh decision before another attempt.",
            },
            "command": result.get("command"),
            "execution": action,
        }
    return {**result, "queued": False, "completed": True, "execution": action}


@mcp.tool(description="Report whether SMACX and its fair-play in-game bridge are connected, plus current menu/game state.")
def smac_status() -> dict:
    result = _call("status")
    pending_gap = _pending_capability_gap()
    if pending_gap:
        return {
            **result,
            "capability_gap_latched": pending_gap,
            "gameplay_mutations_blocked": True,
            "next": "Stop the current game if needed. The orchestrator must extend/test the bridge and restart MCP before any launch, load, new game, or command can proceed.",
        }
    return result


@mcp.tool(
    description=(
        "Read the platform availability and safety boundary. This is static product coverage, "
        "not the current turn's legal actions. Query a section to keep context compact."
    )
)
def smac_capabilities(
    section: Literal[
        "all", "policy", "launch_modes", "lan_profiles", "semantic_surface",
        "known_fail_closed_gaps", "deployment",
    ] = "all",
) -> dict:
    return capability_manifest(section)


@mcp.tool(description="Launch the isolated Alien Crossfire spectator window and connect its semantic bridge. This does not click a menu.")
def smac_launch(
    wait_seconds: int = 30,
    agent_id: str = "",
    perspective_id: str = "",
    instance_id: str = "",
) -> dict:
    managed = _managed_lifecycle_block("Game launch")
    if managed:
        return managed
    blocked = _capability_gap_blocked("Game launch")
    if blocked:
        return blocked
    return launch_game(
        wait_seconds=wait_seconds,
        agent_id=agent_id or None,
        perspective_id=perspective_id or None,
        instance_id=instance_id or None,
    )


@mcp.tool(description="Start a fully typed random or customized single-player game through the native noninteractive setup path. No menu input is used. Difficulty 0 is Citizen; world_size 0 is Tiny. World levels are 0 low, 1 average, 2 high. Custom dimensions require map_generation=custom and both dimensions. Nullable rule fields preserve the installation default when omitted; blind_research remains explicit for research setup.")
def smac_new_game(
    difficulty: int = 0,
    world_size: int = 0,
    map_generation: Literal["random", "custom"] = "random",
    custom_width: int = 0,
    custom_height: int = 0,
    ocean_coverage: int = -1,
    erosive_forces: int = -1,
    native_life: int = -1,
    cloud_cover: int = -1,
    faction_id: int = 1,
    blind_research: bool = True,
    victory_transcendence: bool | None = None,
    victory_conquest: bool | None = None,
    victory_diplomatic: bool | None = None,
    victory_economic: bool | None = None,
    victory_cooperative: bool | None = None,
    do_or_die: bool | None = None,
    look_first: bool | None = None,
    tech_stagnation: bool | None = None,
    spoils_of_war: bool | None = None,
    intense_rivalry: bool | None = None,
    unity_survey: bool | None = None,
    unity_scattering: bool | None = None,
    random_events: bool | None = None,
    time_warp: bool | None = None,
    ironman: bool | None = None,
    random_leader_personalities: bool | None = None,
    random_leader_agendas: bool | None = None,
    initial_research_priority: int = 1,
    initial_tech_id: int = -1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    instance_id: str = "",
    wait_seconds: int = 90,
) -> dict:
    managed = _managed_lifecycle_block("New-game setup")
    if managed:
        return managed
    blocked = _capability_gap_blocked("New game")
    if blocked:
        return blocked
    game_settings = {
        "map_generation": map_generation,
        "world_size": world_size,
        "blind_research": blind_research,
    }
    if custom_width or custom_height:
        game_settings.update({"custom_width": custom_width, "custom_height": custom_height})
    for key, value in {
        "ocean_coverage": ocean_coverage,
        "erosive_forces": erosive_forces,
        "native_life": native_life,
        "cloud_cover": cloud_cover,
    }.items():
        if value >= 0:
            game_settings[key] = value
    for key, value in {
        "victory_transcendence": victory_transcendence,
        "victory_conquest": victory_conquest,
        "victory_diplomatic": victory_diplomatic,
        "victory_economic": victory_economic,
        "victory_cooperative": victory_cooperative,
        "do_or_die": do_or_die,
        "look_first": look_first,
        "tech_stagnation": tech_stagnation,
        "spoils_of_war": spoils_of_war,
        "intense_rivalry": intense_rivalry,
        "unity_survey": unity_survey,
        "unity_scattering": unity_scattering,
        "random_events": random_events,
        "time_warp": time_warp,
        "ironman": ironman,
        "random_leader_personalities": random_leader_personalities,
        "random_leader_agendas": random_leader_agendas,
    }.items():
        if value is not None:
            game_settings[key] = value
    return new_game(
        wait_seconds=wait_seconds,
        difficulty=difficulty,
        world_size=world_size,
        faction_id=faction_id,
        blind_research=blind_research,
        initial_research_priority=initial_research_priority,
        initial_tech_id=initial_tech_id,
        narrative_ui=narrative_ui,
        tutorial_ui=tutorial_ui,
        match_id=match_id or None,
        agent_id=agent_id or None,
        perspective_id=perspective_id or None,
        instance_id=instance_id or None,
        game_settings=game_settings,
    )


@mcp.tool(description="List safe scenario identifiers present in this worker's operator-supplied legal game copy. Scenario assets are never returned or distributed.")
def smac_scenarios() -> dict:
    return list_scenarios()


@mcp.tool(description="Start one exact catalogued single-player scenario without screenshots, clicks, or menu input. Scenario-defined rules are preserved; difficulty and faction apply only when the scenario does not force them.")
def smac_new_scenario(
    scenario_id: str,
    difficulty: int = 0,
    faction_id: int = 1,
    narrative_ui: bool = False,
    tutorial_ui: bool = False,
    match_id: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    instance_id: str = "",
    wait_seconds: int = 90,
) -> dict:
    managed = _managed_lifecycle_block("Scenario setup")
    if managed:
        return managed
    blocked = _capability_gap_blocked("New scenario")
    if blocked:
        return blocked
    return scenario_game(
        scenario_id, wait_seconds=wait_seconds, difficulty=difficulty,
        faction_id=faction_id, narrative_ui=narrative_ui, tutorial_ui=tutorial_ui,
        match_id=match_id or None, agent_id=agent_id or None,
        perspective_id=perspective_id or None, instance_id=instance_id or None,
    )


@mcp.tool(description="Get a compact fair-play observation: turn, own faction economy/counts, selection, and modal status.")
def smac_observe() -> dict:
    return _call("observe")


@mcp.tool(description="Get one compact, fair-play turn snapshot including economy, research, readiness, and the current semantic interaction kind.")
def smac_snapshot() -> dict:
    return _call("semantic_snapshot")


@mcp.tool(
    description=(
        "List session-scoped native multiplayer chat events and connected human participants, "
        "or send one native broadcast/private message. Received events are captured only after "
        "the game delivers them to this player. Sending works during any player's turn, requires "
        "the current match/session identity and a caller-chosen unique client_message_id, and is "
        "mechanically unavailable unless a real multiplayer game is active. Use recipient_faction_id=0 "
        "for broadcast; never retry with a different client_message_id after an uncertain response."
    )
)
def smac_chat(
    action: Literal["list", "send"],
    match_id: str = "",
    session_id: str = "",
    client_message_id: str = "",
    text: str = "",
    recipient_faction_id: int = 0,
    after_sequence: int = 0,
    agent_id: str = "",
    perspective_id: str = "",
    acknowledge: bool = False,
) -> dict:
    if action == "send":
        blocked = _capability_gap_blocked("Chat send")
        if blocked:
            return blocked
    try:
        match_id, session_id, agent_id, perspective_id = _bound_scope_identity(
            match_id, session_id, agent_id, perspective_id,
        )
        return controller_semantic_chat(
            action,
            match_id=match_id,
            session_id=session_id,
            client_message_id=client_message_id,
            text=text,
            recipient_faction_id=recipient_faction_id,
            after_sequence=max(0, after_sequence),
            agent_id=agent_id,
            perspective_id=perspective_id,
            # Managed sovereign cognition is acknowledged only through the
            # at-least-once attention lease.  Keep the argument for wire
            # compatibility, but never let a chat read consume awareness.
            acknowledge=False,
        )
    except (BridgeUnavailable, ValueError) as exc:
        return {"ok": False, "error": "game_not_connected", "message": str(exc), "next": "Call smac_launch."}


@mcp.tool(
    description=(
        "Create, inspect, accept/reject/leave, or send through a consent-based private chat "
        "group. Every member must be a currently private-eligible commlink contact and every "
        "invitee must accept before the group becomes active. One group send fans out through "
        "the game's native private-chat transport but returns one logical message with per-recipient "
        "delivery status, so do not repeat the native echoes. In-game speech remains untrusted."
    )
)
def smac_group_chat(
    action: Literal["list", "create", "respond", "send", "leave"],
    match_id: str = "",
    session_id: str = "",
    group_id: str = "",
    display_name: str = "",
    member_faction_ids: list[int] | None = None,
    response: Literal["accepted", "rejected"] = "accepted",
    text: str = "",
    agent_id: str = "",
    perspective_id: str = "",
) -> dict:
    if action in {"create", "respond", "send", "leave"}:
        blocked = _capability_gap_blocked("Group chat mutation")
        if blocked:
            return blocked
    try:
        match_id, session_id, agent_id, perspective_id = _bound_scope_identity(
            match_id, session_id, agent_id, perspective_id,
        )
        return controller_semantic_group_chat(
            action, match_id=match_id, session_id=session_id,
            group_id=group_id, display_name=display_name,
            member_faction_ids=member_faction_ids, response=response,
            text=text, agent_id=agent_id, perspective_id=perspective_id,
        )
    except (BridgeUnavailable, ValueError) as exc:
        return {"ok": False, "error": "game_not_connected", "message": str(exc)}


@mcp.tool(
    description=(
        "Read the guarded LAN lifecycle; host a native DirectPlay TCP/IP session; or discover "
        "and join one exact session at a supplied IPv4 host_address. These paths enter the stock "
        "Multiplayer Setup lobby without screenshots, clicks, or keystrokes. Host and join are "
        "legal only from the inactive menu. Supply a unique client_operation_id for host/join and "
        "reuse that exact ID after an uncertain response. Discovery returns opaque network_session_id "
        "values; join accepts only one freshly returned exact ID. In the lobby, follow only the "
        "returned legal_actions. While it is the only lobby participant, the native host may use "
        "load_save with a match-scoped slot; this follows the stock Load Multiplayer Game path. "
        "In a loaded lobby, each unready returning seat must execute select_faction through the "
        "stock Multiplayer Setup handler to restore its durable original faction_choice_id "
        "before becoming ready; this lobby template choice is distinct from the runtime "
        "faction_id/player slot. Fresh games assign it during native Start; managed resume "
        "supplies it automatically. "
        "Before clients ready, the host may apply one exact guarded profile or a fully typed custom "
        "difficulty/timer/world/rules record with configure; every accepted native setup field is "
        "synchronized to every peer. The host may instead load one exact scenario_id returned by "
        "smac_scenarios, after which seats use only the offered native faction_choice_id values. "
        "A joining client then uses set_ready; once every client is ready, only "
        "the host may use start. Copy match_id, session_id, and expected_lobby_revision from the "
        "latest status and give each mutation a unique client_operation_id. These call the game's "
        "native Ready/Start protocol and do not synthesize UI input."
    )
)
def smac_lan(
    action: Literal["status", "host", "discover", "join", "load_save", "load_scenario", "select_faction", "configure", "set_ready", "start"],
    session_name: str = "SMACX Agent Game",
    player_name: str = "Semantic Host",
    client_operation_id: str = "",
    host_address: str = "",
    network_session_id: str = "",
    match_id: str = "",
    session_id: str = "",
    expected_lobby_revision: str = "",
    profile: str = "small_easy",
    slot: str = "",
    scenario_id: str = "",
    difficulty: int = -1,
    time_control: int = -1,
    world_size: int = -1,
    ocean_coverage: int = -1,
    erosive_forces: int = -1,
    native_life: int = -1,
    cloud_cover: int = -1,
    victory_transcendence: bool | None = None,
    victory_conquest: bool | None = None,
    victory_diplomatic: bool | None = None,
    victory_economic: bool | None = None,
    victory_cooperative: bool | None = None,
    do_or_die: bool | None = None,
    look_first: bool | None = None,
    tech_stagnation: bool | None = None,
    spoils_of_war: bool | None = None,
    blind_research: bool | None = None,
    intense_rivalry: bool | None = None,
    unity_survey: bool | None = None,
    unity_scattering: bool | None = None,
    random_events: bool | None = None,
    time_warp: bool | None = None,
    ironman: bool | None = None,
    faction_choice_id: int = -1,
    ready: bool = False,
    agent_id: str = "",
    perspective_id: str = "",
    instance_id: str = "",
) -> dict:
    if action in {"host", "join", "load_save", "load_scenario", "select_faction", "configure", "set_ready", "start"}:
        blocked = _capability_gap_blocked(f"LAN {action}")
        if blocked:
            return blocked
    if action in {"host", "discover", "join"}:
        status = _call("status")
        if status.get("error") == "game_not_connected":
            managed = _managed_lifecycle_block("LAN game launch")
            if managed:
                return managed
            launched = launch_game(
                wait_seconds=30,
                agent_id=agent_id or None,
                perspective_id=perspective_id or None,
                instance_id=instance_id or None,
            )
            if not launched.get("ok"):
                return launched
    payload = {
        "action": action,
        "session_name": session_name, "player_name": player_name,
        "client_operation_id": client_operation_id,
        "host_address": host_address, "network_session_id": network_session_id,
        "match_id": match_id, "session_id": session_id,
        "expected_lobby_revision": expected_lobby_revision,
        "profile": profile, "slot": slot, "scenario_id": scenario_id,
        "difficulty": difficulty, "time_control": time_control,
        "world_size": world_size, "ocean_coverage": ocean_coverage,
        "erosive_forces": erosive_forces, "native_life": native_life,
        "cloud_cover": cloud_cover, "faction_choice_id": faction_choice_id,
        "ready": ready,
    }
    optional_rules = {
        "victory_transcendence": victory_transcendence,
        "victory_conquest": victory_conquest,
        "victory_diplomatic": victory_diplomatic,
        "victory_economic": victory_economic,
        "victory_cooperative": victory_cooperative,
        "do_or_die": do_or_die, "look_first": look_first,
        "tech_stagnation": tech_stagnation, "spoils_of_war": spoils_of_war,
        "blind_research": blind_research, "intense_rivalry": intense_rivalry,
        "unity_survey": unity_survey, "unity_scattering": unity_scattering,
        "random_events": random_events, "time_warp": time_warp,
        "ironman": ironman,
    }
    payload.update({key: value for key, value in optional_rules.items() if value is not None})
    return _call("semantic_lan", timeout=60, **payload)


def _compact_decision_state(snapshot: dict) -> dict:
    """Keep decision ordering visible without replaying the full turn document."""
    faction = snapshot.get("faction", {})
    economy = snapshot.get("economy", {})
    research = snapshot.get("research", {})
    protocol = snapshot.get("protocol", {})
    return {
        "faction": {
            key: faction.get(key)
            for key in ("id", "name", "energy_credits", "bases", "units", "ready_units")
            if key in faction
        },
        "economy_allocation": economy.get("allocation", {}),
        "research": {
            key: research.get(key)
            for key in ("enabled", "blind", "tech_name", "priority", "progress_percent")
            if key in research
        },
        "protocol": {
            key: protocol.get(key)
            for key in ("required_action", "end_turn_blocked", "ready_unit_count")
            if key in protocol
        },
    }


def _compact_decision_choices(choices: object) -> list[dict]:
    """Remove only redundant safe-move defaults; preserve every action and risk."""
    if not isinstance(choices, list):
        return []
    compact: list[dict] = []
    redundant_safe_move = {
        "direction_id", "known", "visible_now", "is_ocean",
        "may_initiate_combat_or_contact", "boards_transport",
    }
    for raw_choice in choices:
        if not isinstance(raw_choice, dict):
            continue
        choice = dict(raw_choice)
        if choice.get("command") == "move_unit":
            for key in redundant_safe_move:
                value = choice.get(key)
                if key == "direction_id" \
                        or (key in {"known", "visible_now"} and value is True) \
                        or (key in {
                            "is_ocean", "may_initiate_combat_or_contact", "boards_transport",
                        } and value is False):
                    choice.pop(key, None)
            if not choice.get("features"):
                choice.pop("features", None)
        compact.append(choice)
    return compact


def _decision_advisories(choices: object, *, semantic_context: Mapping[str, Any] | None = None) -> list[dict]:
    """Preserve native rule explanations that are deliberately not executable."""
    if not isinstance(choices, list):
        return []
    allowed = {
        "kind", "available", "unit_id", "base_id", "reason",
        "minimum_base_range", "nearest_known_base_range", "meaning",
    }
    advisories: list[dict] = []
    for raw in choices:
        if not isinstance(raw, dict) or isinstance(raw.get("command"), str):
            continue
        if raw.get("kind") != "rule_status":
            continue
        item = _semanticize_choice(
            {key: raw[key] for key in allowed if key in raw}, semantic_context,
        )
        if item:
            advisories.append(item)
        if len(advisories) >= 8:
            break
    return advisories


def _decision_information(choices: object, context: Mapping | None) -> list[dict]:
    """Keep the native terms that make a closed response understandable."""
    return [_semanticize_choice({key: value for key, value in row.items() if key != "id"}, context)
            for row in choices if isinstance(row, dict) and not row.get("command")
            and row.get("kind") in {"information", "capability_status"}]


def _latest_rule_advisories(match_id: str, session_id: str) -> list[dict]:
    now = time.monotonic()
    with DECISION_LOCK:
        candidates = [
            value for value in DECISION_CACHE.values()
            if value.get("identity", {}).get("match_id") == match_id
            and value.get("identity", {}).get("session_id") == session_id
            and now - float(value.get("created_monotonic", 0)) <= DECISION_TTL_SECONDS
        ]
    if not candidates:
        return []
    latest = max(candidates, key=lambda value: float(
        value.get("created_monotonic", 0)))
    advisories = latest.get("advisories")
    return advisories if isinstance(advisories, list) else []


def _settlement_rule_explains_request(required_action: str,
                                      advisories: list[dict]) -> dict | None:
    requested = required_action.casefold()
    wants_settlement = bool(re.search(
        r"(?:found|build|construct)[ _-]{0,3}(?:a[ _-]{0,3})?base|"
        r"base[ _-]{0,3}(?:found|build|construct)", requested,
    ))
    if not wants_settlement:
        return None
    return next((item for item in advisories if
                 isinstance(item, dict) and item.get("kind") == "rule_status"
                 and item.get("available") is False
                 and isinstance(item.get("reason"), str)
                 and "base" in str(item.get("meaning", "")).casefold()), None)


def _cache_decision_choices(identity: dict, choices: object, *,
                            choice_kind: str, choice_arguments: dict,
                            semantic_context: Mapping[str, Any] | None = None,
                            catalog_information: list[dict] | None = None,
                            focus: dict | None = None,
                            turn: int | None = None,
                            year: int | None = None,
                            phase: str = "") -> tuple[str, list[dict]]:
    """Bind model-visible opaque choices to exact native command payloads."""
    now = time.monotonic()
    decision_id = "decision-" + uuid.uuid4().hex
    public: list[dict] = []
    private: dict[str, dict] = {}
    labels: dict[str, str] = {}
    raw_items = bind_ballot_choices(choices) if isinstance(choices, list) else []
    compact: list[dict] = []
    advisories = _decision_advisories(raw_items, semantic_context=semantic_context)
    for raw in raw_items:
        if not isinstance(raw, dict) or not isinstance(raw.get("command"), str):
            continue
        shown_items = _compact_decision_choices([raw])
        if not shown_items:
            continue
        shown = _semanticize_choice(shown_items[0], semantic_context)
        action = str(raw["command"])
        bound = dict(raw)
        requires = bound.pop("requires", None)
        if isinstance(requires, dict):
            # Selecting this exact destructive choice is itself the deliberate
            # confirmation. Keep the transport flag private and server-owned.
            for key, value in requires.items():
                if isinstance(key, str) and key.startswith("confirm_") \
                        and value in (1, True):
                    bound[key] = 1
        # Collection selectors can be response-level metadata in the native
        # catalog. Bind them before determining whether a choice is complete.
        for key in ("base_id", "unit_id", "target_tile_id", "target_unit_id"):
            value = choice_arguments.get(key)
            if isinstance(value, int) and value >= 0 and key not in bound:
                bound[key] = value

        parameters = raw.get("parameters")
        parameter_names: set[str] = set()
        if isinstance(parameters, dict):
            parameter_names = {
                key for key in parameters if isinstance(key, str)
            }
        elif isinstance(parameters, list):
            parameter_names = {
                key for key in parameters if isinstance(key, str)
            }
        name_contract = parameters.get("name") \
            if isinstance(parameters, dict) else None
        text_name_supported = action == "set_first_base_name" \
            or isinstance(name_contract, dict)
        if action in {"give_energy_gift", "propose_human_energy"}:
            parameter_names.add("amount")
        unresolved = {
            key for key in parameter_names
            if key not in bound and not (key == "name" and text_name_supported)
        }
        if unresolved:
            # A choice ID promises exact executability. Older or unfinished
            # catalogs can expose schema-shaped pseudo choices; withhold them
            # instead of inviting the model to invent native arguments.
            continue
        choice_id = "choice-" + uuid.uuid4().hex
        item = dict(shown)
        item.pop("command", None)
        item.pop("id", None)
        item.pop("requires", None)
        item.pop("parameters", None)
        for key in tuple(item):
            if key.startswith("confirm_"):
                item.pop(key, None)
        item["choice_id"] = choice_id
        if action == "set_first_base_name" or isinstance(name_contract, dict):
            design_name = action == "create_unit_design"
            item["text_input"] = {
                "purpose": "unit_design_name" if design_name else "base_name",
                "required": action not in {"set_first_base_name", "create_unit_design"},
                "min_length": 0 if design_name else 1,
                "max_length": 31 if design_name else 24,
            }
            if action == "set_first_base_name" and raw.get("suggested_name"):
                item["text_input"]["default"] = raw["suggested_name"]
        label = _short_text(item.get("label") or action.replace("_", " ").capitalize(), 160)
        item["label"] = label
        public.append(item)
        compact.append(dict(shown))
        labels[choice_id] = label
        private[choice_id] = bound
    with DECISION_LOCK:
        expired = [key for key, value in DECISION_CACHE.items()
                   if now - float(value.get("created_monotonic", 0)) > DECISION_TTL_SECONDS]
        for key in expired:
            DECISION_CACHE.pop(key, None)
        while len(DECISION_CACHE) >= DECISION_CACHE_LIMIT:
            oldest = min(DECISION_CACHE, key=lambda key: float(
                DECISION_CACHE[key].get("created_monotonic", 0)))
            DECISION_CACHE.pop(oldest, None)
        DECISION_CACHE[decision_id] = {
            "managed_scope": _managed_scope_identity(),
            "created_monotonic": now,
            "identity": dict(identity),
            "choice_kind": choice_kind,
            "choice_arguments": dict(choice_arguments),
            "focus": dict(focus or {}),
            "turn": turn,
            "year": year,
            "phase": phase,
            "state_fingerprint": hashlib.sha256(json.dumps({
                "turn": turn, "year": year, "phase": phase,
                "focus": focus or {}, "choices": compact,
                "information": (_decision_information(raw_items, semantic_context)
                                if catalog_information is None else catalog_information),
            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "choices": private,
            "choice_labels": labels,
            "advisories": advisories,
            "information": (_decision_information(raw_items, semantic_context)
                            if catalog_information is None else catalog_information),
            "consumed": False,
        }
    return decision_id, public


def _counterfactual_choice(scenario: Mapping[str, Any], action_revision: str) -> dict:
    """Resolve a current, scoped opaque choice without consuming or executing it."""
    with DECISION_LOCK:
        decision = DECISION_CACHE.get(str(scenario.get("decision_id")))
        if not decision or decision.get("consumed") or time.monotonic() - float(
                decision.get("created_monotonic", 0)) > DECISION_TTL_SECONDS:
            raise WorldQueryError("counterfactual_requires_live_unconsumed_decision")
        scope = _managed_scope_identity()
        identity = decision.get("identity", {})
        if tuple(decision.get("managed_scope", ())) != scope \
                or identity.get("match_id") != scope[0] or identity.get("session_id") != scope[1] \
                or not action_revision or identity.get("revision") != action_revision:
            raise WorldQueryError("counterfactual_choice_scope_or_revision_changed")
        choice = decision.get("choices", {}).get(str(scenario.get("choice_id")))
        if not isinstance(choice, dict):
            raise WorldQueryError("unknown_counterfactual_choice")
        commands = {"social": {"set_social_engineering"}, "terraform": {"terraform"},
                    "deployment": {"set_production", "hurry_production", "upgrade_unit"},
                    "action": {"set_production", "hurry_production", "upgrade_unit", "move_unit",
                               "rehome_unit", "disband_unit"}}
        if choice.get("command") not in commands.get(scenario["kind"], set()):
            raise WorldQueryError("counterfactual_requires_matching_final_choice")
        return dict(choice)


def _attach_chat_attention(frame: dict, identity: dict) -> dict:
    """Capture chat for the next request-only attention lease; do not duplicate it here."""
    match_id = str(identity.get("match_id") or "")
    session_id = str(identity.get("session_id") or "")
    if not match_id or not session_id:
        return frame
    attention = controller_chat_attention(match_id, session_id)
    del attention
    return frame


def _attach_working_state(frame: dict, identity: dict) -> dict:
    """Compatibility shim: cognition now appears once in trusted runtime context."""
    del identity
    return frame


def _graphiti_recall(identity: dict, query: str, *, limit: int = 6) -> dict:
    endpoint = os.environ.get("SMACX_GRAPHITI_RECALL_URL", "").rstrip("/")
    if not endpoint:
        return {"ok": False, "error": "graphiti_recall_not_configured", "facts": []}
    body = json.dumps({
        "match_id": identity.get("match_id"),
        "agent_id": os.environ.get("SMACX_AGENT_ID", ""),
        "perspective_id": os.environ.get("SMACX_PERSPECTIVE_ID", ""),
        "query": query[:4000], "limit": min(max(limit, 1), 20),
    }, separators=(",", ":")).encode()
    request = Request(endpoint + "/recall", data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=2.0) as response:
            raw = response.read(65_537)
        if len(raw) > 65_536:
            return {"ok": False, "error": "graphiti_recall_too_large", "facts": []}
        result = json.loads(raw)
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_graphiti_recall", "facts": []}
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
        # Graphiti is deliberately failure-isolated from the action loop.
        return {"ok": False, "error": "graphiti_recall_unavailable", "facts": []}


@mcp.tool(
    description=(
        "Inspect the fair-play world using returned opaque references. Modes cover geography, "
        "mechanics, routes, forces, bases, intelligence and changes. Detail levels have fixed ceilings. "
        "Unknown terrain is never routed through. Counterfactual mode takes scenario_json: "
        "site_economy with populations:[1,2,3] and up to four subject locations; "
        "social|terraform|action with decision_id and choice_id from a current final choice; "
        "deployment with capability (combat|colony|former|transport|probe|supply), target_ref, "
        "and optional choice_refs:[{decision_id,choice_id}] for up to four build, hurry or upgrade options. "
        "Put kind in scenario_json; target_ref is a tool argument. Previews are conditional and never execute."
    )
)
def smac_world(
    mode: Literal[
        "overview", "area", "relation", "route", "reachability", "compare",
        "base", "forces", "logistics", "intel", "changes", "global", "render", "counterfactual",
    ],
    subject_refs: list[str] | None = None,
    origin_ref: str = "",
    target_ref: str = "",
    movement_profile_ref: str = "mobility-land-default",
    radius: int = 3,
    since_cursor: int = 0,
    detail: Literal["compact", "standard", "deep"] = "standard",
    continuation: str = "",
    scenario_json: str = "",
) -> dict:
    """Provider-facing facade; internal calculators remain independently bounded."""
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    if not match_id or not session_id:
        return {"ok": False, "error": "managed_world_identity_unavailable"}
    refresh = _refresh_managed_world()
    if not refresh.get("ok"):
        return refresh
    try:
        scenario = parse_scenario(scenario_json) if mode == "counterfactual" else None
        if scenario and scenario["kind"] == "site_economy" and not 1 <= len(subject_refs or []) <= 4:
            raise WorldQueryError("site_economy_requires_one_to_four_nominated_sites")
        _, world, attention = controller_world_service(
            match_id, session_id=session_id, agent_id=agent_id,
            perspective_id=perspective_id,
        )
        runtime_airdrop_receipt = None
        runtime_base_site_receipts = None
        runtime_counterfactual_receipt = None
        if scenario and scenario["kind"] in {"social", "terraform"}:
            _, projection = world._projection()
            action_revision = str(projection.get("action_revision") or "")
            choice = _counterfactual_choice(scenario, action_revision)
            keys = ("politics", "economics", "values", "future") if scenario["kind"] == "social" \
                else ("unit_id", "former_id")
            if any(type(choice.get(key)) is not int for key in keys):
                raise WorldQueryError("counterfactual_requires_complete_final_choice")
            runtime_counterfactual_receipt = _call(
                "semantic_counterfactual", kind=scenario["kind"], expected_revision=action_revision,
                **{key: choice[key] for key in keys})
            if runtime_counterfactual_receipt.get("ok") is not True:
                return runtime_counterfactual_receipt
            runtime_counterfactual_receipt = _semanticize_choice(
                runtime_counterfactual_receipt, _semantic_selector_context(action_revision))
        if scenario and scenario["kind"] in {"action", "deployment"}:
            _, projection = world._projection()
            action_revision = str(projection.get("action_revision") or "")
            context = _semantic_selector_context(action_revision)
            requested = scenario.get("choice_refs", []) if scenario["kind"] == "deployment" else [scenario]
            receipts = []
            for reference in requested:
                choice = _counterfactual_choice({**reference, "kind": scenario["kind"]}, action_revision)
                command = choice["command"]
                keys = {"set_production": ("base_id", "item_id"), "hurry_production": ("base_id",),
                        "upgrade_unit": ("unit_id", "target_prototype_id"),
                        "rehome_unit": ("unit_id", "base_id"), "disband_unit": ("unit_id",),
                        "move_unit": ("unit_id",)}.get(command)
                if keys:
                    if any(type(choice.get(key)) is not int for key in keys):
                        raise WorldQueryError("counterfactual_requires_complete_final_choice")
                    arguments = {key: choice[key] for key in keys}
                    if command == "move_unit" and type(choice.get("target_tile_id")) is int:
                        arguments["target_tile_id"] = choice["target_tile_id"]
                    receipt = _call("semantic_counterfactual", kind=scenario["kind"], command=command,
                                    expected_revision=action_revision, **arguments)
                    if receipt.get("ok") is not True:
                        return receipt
                    receipt = _semanticize_choice(receipt, context)
                else:
                    receipt = {"ok": True, "kind": "action", "action_revision": action_revision,
                               "proposed_action": command, "epistemic_status": "conditional",
                               "executes_action": False}
                if scenario["kind"] == "action":
                    plans = attention.journal.projection_records(attention.scope, "plans", limit=129,
                                                                 statuses={"active"})
                    receipt["relationships"] = action_relationships(
                        world._objects(projection), _semanticize_choice(choice, context), plans)
                receipts.append(receipt)
            runtime_counterfactual_receipt = receipts[0] if scenario["kind"] == "action" else {
                "ok": True, "kind": "deployment", "action_revision": action_revision, "alternatives": receipts}
        target_tile_id = -1
        if mode in {"relation", "route", "reachability"} and origin_ref and \
                world._objects(world._projection()[1]).get(origin_ref, {}).get("kind") == "own_unit":
            identity, projection = world._projection()
            action_revision = str(projection.get("action_revision") or "")
            if mode in {"relation", "route"}:
                target_location = str(
                    world._objects(projection).get(target_ref, {}).get("location_ref")
                    or target_ref
                )
                selectors, _ = _resolve_managed_selectors(
                    action_revision, target_location_ref=target_location)
                target_tile_id = selectors.get("target_tile_id", -1)
            scope_key = str(target_tile_id) if target_tile_id >= 0 else "enumeration"
            cache_key = (
                match_id, session_id, agent_id, perspective_id,
                identity.timeline_id, identity.world_epoch, action_revision,
                origin_ref, scope_key,
            )
            with AIRDROP_RECEIPT_LOCK:
                runtime_airdrop_receipt = AIRDROP_RECEIPT_CACHE.get(cache_key)
            if runtime_airdrop_receipt is None:
                source = world._objects(projection).get(origin_ref, {})
                native_id = None
                if source.get("kind") == "own_unit":
                    selectors, _ = _resolve_managed_selectors(action_revision, own_unit_ref=origin_ref)
                    native_id = selectors.get("unit_id")
                if native_id is not None:
                    arguments: dict[str, object] = {"unit_id": native_id, "maximum_targets": 128}
                    if target_tile_id >= 0:
                        arguments["target_tile_id"] = target_tile_id
                    candidate = _call("semantic_airdrop_targets", **arguments)
                    if candidate.get("ok") is True \
                            and str(candidate.get("action_revision") or "") == action_revision:
                        runtime_airdrop_receipt = candidate
                        with AIRDROP_RECEIPT_LOCK:
                            AIRDROP_RECEIPT_CACHE[cache_key] = candidate
                            while len(AIRDROP_RECEIPT_CACHE) > 64:
                                AIRDROP_RECEIPT_CACHE.pop(next(iter(AIRDROP_RECEIPT_CACHE)))
        site_economy = bool(scenario and scenario["kind"] == "site_economy")
        if (mode == "compare" and not origin_ref and not target_ref or site_economy) and subject_refs:
            identity, projection = world._projection()
            action_revision = str(projection.get("action_revision") or "")
            context = _semantic_selector_context(action_revision)
            target_ids = []
            for ref in list(subject_refs)[:32]:
                item = context["objects"].get(ref, {})
                if item.get("kind") == "location" and ref in context["by_ref"]:
                    target_ids.append(context["by_ref"][ref])
            if target_ids:
                identity, projection = world._projection()
                action_revision = str(projection.get("action_revision") or "")
                cache_key = (
                    match_id, session_id, agent_id, perspective_id,
                    identity.timeline_id, identity.world_epoch, action_revision,
                    "economy" if site_economy else "legality",
                    *(str(value) for value in sorted(set(target_ids))),
                )
                with BASE_SITE_RECEIPT_LOCK:
                    candidate = BASE_SITE_RECEIPT_CACHE.get(cache_key)
                if candidate is None:
                    received = _call(
                        "semantic_base_site_receipts",
                        target_tile_ids=sorted(set(target_ids)),
                        include_economy=site_economy,
                    )
                    if received.get("ok") is True \
                            and str(received.get("action_revision") or "") == action_revision:
                        candidate = received
                        with BASE_SITE_RECEIPT_LOCK:
                            BASE_SITE_RECEIPT_CACHE[cache_key] = received
                            while len(BASE_SITE_RECEIPT_CACHE) > 64:
                                BASE_SITE_RECEIPT_CACHE.pop(
                                    next(iter(BASE_SITE_RECEIPT_CACHE))
                                )
                if isinstance(candidate, Mapping) and candidate.get("ok") is True \
                        and str(candidate.get("action_revision") or "") == action_revision:
                    runtime_base_site_receipts = {
                        ref: {**item, "location_ref": ref, "action_revision": action_revision}
                        for item in candidate.get("items", ()) if isinstance(item, Mapping)
                        for ref in [context["reverse_locations"].get(item.get("tile_id"))]
                        if isinstance(item, Mapping) and ref
                    }
        context_length = int(os.environ.get("SMACX_CONTEXT_LENGTH", "65536"))
        result = world.query(
            mode=mode, subject_refs=subject_refs or (), origin_ref=origin_ref,
            target_ref=target_ref, movement_profile_ref=movement_profile_ref,
            radius=radius, since_cursor=since_cursor, detail=detail,
            continuation=continuation, context_length=context_length,
            runtime_airdrop_receipt=runtime_airdrop_receipt,
            runtime_base_site_receipts=runtime_base_site_receipts,
            scenario_json=scenario_json,
            runtime_counterfactual_receipt=runtime_counterfactual_receipt,
        )
        if result.get("ok") is True:
            identity, projection = world._projection()
            action_revision = str(projection.get("action_revision") or "")
            prefix = (
                match_id, session_id, agent_id, perspective_id,
                identity.timeline_id, identity.world_epoch, action_revision,
            )
            now = time.monotonic()
            address_registry = {}
            if mode == "area" and origin_ref == "world-map":
                shape = world._topology(projection).shape
                address_registry = {f"location-{tile_id}": tile_id
                                    for tile_id in range(shape.width * shape.height // 2)}
            with SEMANTIC_LOCATION_CAPABILITY_LOCK:
                # An exact world query mints a short-lived private binding for
                # the semantic location it just validated. The provider keeps
                # using the opaque ref; it never has to decode its spelling or
                # provide the native tile handle on the action call.
                if target_tile_id >= 0 and isinstance(runtime_airdrop_receipt, Mapping) \
                        and runtime_airdrop_receipt.get("ok") is True \
                        and runtime_airdrop_receipt.get("allowed") is True:
                    SEMANTIC_LOCATION_CAPABILITIES[(*prefix, target_ref)] = (
                        target_tile_id, now + DECISION_TTL_SECONDS,
                    )
                for item in result.get("items", ()):
                    if not isinstance(item, Mapping) or item.get("kind") != "location" \
                            or item.get("epistemic_status") != "unknown":
                        continue
                    ref = str(item.get("object_ref") or "")
                    if ref in address_registry:
                        SEMANTIC_LOCATION_CAPABILITIES[(*prefix, ref)] = (
                            address_registry[ref], now + DECISION_TTL_SECONDS,
                        )
                while len(SEMANTIC_LOCATION_CAPABILITIES) > 4096:
                    SEMANTIC_LOCATION_CAPABILITIES.pop(
                        next(iter(SEMANTIC_LOCATION_CAPABILITIES)), None,
                    )
        return result
    except (WorldQueryError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": {"code": str(exc), "valid_modes": sorted(WORLD_MODES)}}


@mcp.tool(
    description=(
        "Acknowledge a processed batch from the current at-least-once attention lease. "
        "Call only after genuinely considering those events. Acknowledgement records awareness, "
        "not mechanical resolution; blocking focus and incidents remain until resolved."
    )
)
def smac_attention_ack(
    attention_lease_id: str,
    through_cursor: int,
    acknowledged_ids: list[str] | None = None,
) -> dict:
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    try:
        _, _, attention = controller_world_service(
            match_id, session_id=session_id, agent_id=agent_id,
            perspective_id=perspective_id,
        )
        return attention.acknowledge(
            attention_lease_id, through_cursor=through_cursor,
            acknowledged_ids=acknowledged_ids or (),
        )
    except (AttentionError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    description=(
        "Maintain optional bounded cognition scopes. A watch elevates a typed future change; "
        "an operation holds one temporary multi-query problem. These are useful when warranted, "
        "not rituals for trivial decisions. Plans/goals remain durable sovereign memory. "
        "scope_create uses subject_refs and predicate_json {type: proximity|geography|"
        "route_corridor|base_radius|union, radius: 0..16, domain: land|sea|both}. "
        "Use one base/location center, one issued geographic/route ref, or up to 8 scope refs "
        "for union. Watch the returned scope_ref with region_entry/region_exit and "
        "{relationship: hostile}; scope_inspect checks validity; watch_close retires it. "
        "Milestone watches require linked_plan_id and {mode: all|at_least, at_least?: N, "
        "requirements: [{ref, kind: exists|current_field|contains|production_completed|"
        "garrison_count|dependency_valid, field?, value?, count?}]}, at most 16 requirements. "
        "watch_inspect reads current qualified state. plan_health checks explicit journaled "
        "participants ({ref,intended_role?,target_ref?,exclusive?,production_item?,energy_credits?,timing?}) "
        "with timing {start_turn,end_turn}; last_confirmation.dependency_values may record "
        "{ref,field,value} for a declared dependency. It reports conflicts, never selects a plan."
    )
)
def smac_cognition(
    action: Literal["watch_create", "watch_close", "watch_inspect", "scope_create", "scope_inspect", "plan_health", "operation_upsert", "operation_complete"],
    kind: str = "",
    objective: str = "",
    subject_refs: list[str] | None = None,
    predicate_json: str = "{}",
    priority: int = 50,
    expires_turn: int = -1,
    operation_id: str = "",
    linked_goal_id: str = "",
    linked_plan_id: str = "",
    compact_outcome: str = "",
    foreground: bool = True,
) -> dict:
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    if action == "plan_health":
        # Recovery can publish the journal before the lazy world collector
        # finishes restoring owned-unit observations. Do not evaluate current
        # assignments against that earlier materialized projection.
        refresh = _refresh_managed_world()
        if not refresh.get("ok"):
            return refresh
    try:
        scope, world, attention = controller_world_service(
            match_id, session_id=session_id, agent_id=agent_id,
            perspective_id=perspective_id,
        )
        projection_identity, projection = world._projection()
        refs = tuple(subject_refs or ())
        turn_state = next((item for item in projection.get("objects", [])
                           if item.get("kind") == "turn_state"), {})
        turn_field = turn_state.get("fields", {}).get("turn", {})
        current_turn = turn_field.get("value") if isinstance(turn_field, dict) else None
        if action == "plan_health":
            dependencies = attention.semantic_dependency_hashes(projection)
            active = attention.runtime_state(current_world_revision=int(projection["world_revision"]),
                                             current_world_epoch=projection_identity.world_epoch,
                                             object_dependency_hashes=dependencies, current_turn=current_turn)
            snapshot = _call("semantic_snapshot").get("snapshot", {})
            ready = [str(item.get("own_unit_ref")) for item in snapshot.get("ready_unit_refs", [])
                     if isinstance(item, dict)]
            return {"ok": True, "plan_health": attention.plan_health(projection, active["operations"], ready, dependencies)}
        if action == "watch_inspect":
            if len(refs) != 1:
                raise AttentionError("watch_inspect_requires_one_watch_ref")
            if current_turn is not None:
                attention.gc_watches(current_turn)
            return {"ok": True, "watch": attention.inspect_watch(refs[0])}
        if action == "scope_inspect":
            if len(refs) != 1:
                raise AttentionError("scope_inspect_requires_one_scope_ref")
            if current_turn is not None:
                attention.gc_watches(current_turn)
            return {"ok": True, "scope": attention.inspect_scope(refs[0])}
        if action in {"watch_create", "scope_create"}:
            try:
                predicate = json.loads(predicate_json)
            except json.JSONDecodeError as exc:
                raise AttentionError("invalid_watch_predicate_json") from exc
            if not isinstance(predicate, dict):
                raise AttentionError("invalid_watch_predicate_json")
            return {"ok": True, **attention.create_watch(
                "spatial_scope" if action == "scope_create" else kind,
                refs, predicate, priority=priority, current_turn=current_turn,
                expires_turn=(expires_turn if expires_turn >= 0 else None),
                linked_goal_id=linked_goal_id or None, linked_plan_id=linked_plan_id or None,
            )}
        if action == "watch_close":
            if not subject_refs or len(subject_refs) != 1:
                raise AttentionError("watch_close_requires_one_watch_id")
            attention.close_watch(refs[0], current_turn=current_turn)
            return {"ok": True, "watch_id": refs[0], "status": "closed"}
        if action == "operation_complete":
            if not operation_id:
                raise AttentionError("operation_id_required")
            attention.complete_operation(operation_id, compact_outcome)
            cancelled = SpecialistService(
                world.store.store, world.store, scope,
            ).cancel_for_operation(operation_id)
            return {"ok": True, "operation_id": operation_id, "status": "completed",
                    "cancelled_specialist_missions": cancelled}
        if not kind or not objective:
            raise AttentionError("operation_kind_and_objective_required")
        dependencies = attention.semantic_dependency_hashes(projection)
        if any(ref not in dependencies for ref in refs):
            raise AttentionError("unknown_or_cross_perspective_subject_ref")
        return {"ok": True, **attention.upsert_operation(
            operation_id=operation_id or None, kind=kind, objective=objective,
            referenced_world_objects=refs,
            source_world_revision=int(projection["world_revision"]),
            source_world_epoch=projection_identity.world_epoch,
            source_dependency_hash=content_hash({
                ref: dependencies[ref] for ref in refs
            }),
            current_turn=current_turn, linked_plan_id=linked_plan_id or None,
            linked_goal_id=linked_goal_id or None, foreground=foreground,
        )}
    except (AttentionError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc), "watch_kinds": sorted(WATCH_KINDS)}


@mcp.tool(
    description=(
        "Get one stable, action-ordered decision frame. It bundles the current fair-play "
        "state headline with the exact active interaction choices, one selected ready unit's offered "
        "actions selected by stable own_unit_ref, a wait/gap directive, or game-management choices "
        "when no unit decision remains. Native execution can still reject an offered action; read its receipt. "
        "After deliberately deciding that every remaining unit is finished, set finish_ready_units=true "
        "to receive the guarded skip-all-ready choice instead of another individual unit frame. "
        "Use this as the primary agent loop to reduce calls and prevent invalid action order. "
        "The default compact detail avoids repeating stable turn state; request full only when "
        "the comprehensive snapshot is necessary for one strategic decision."
    )
)
def smac_decision(
    own_unit_ref: str = "",
    target_location_ref: str = "",
    target_unit_ref: str = "",
    finish_ready_units: bool = False,
    detail: Literal["compact", "full"] = "compact",
) -> dict:
    authority = _sovereign_gameplay_gate("Decision enumeration")
    if authority:
        return authority
    # Observation runs outside the native UI stack. A decision never waits on
    # provider work, but it does require one coherent perspective projection.
    refresh = _refresh_managed_world()
    if MANAGED_ATTACHED and not refresh.get("ok"):
        return {
            "ok": False,
            "error": {"code": "perspective_observation_unavailable",
                      "detail": refresh.get("error")},
            "gameplay_mutations_blocked": True,
        }
    if finish_ready_units and own_unit_ref:
        return {
            "ok": False,
            "error": {
                "code": "conflicting_decision_focus",
                "message": "Choose either one own_unit_ref or finish_ready_units=true, not both.",
            },
        }
    for _ in range(3):
        snapshot_result = _call("semantic_snapshot")
        snapshot = snapshot_result.get("snapshot", {})
        if not snapshot_result.get("ok") or not isinstance(snapshot, dict) or not snapshot:
            return snapshot_result
        identity = {
            "match_id": snapshot.get("match_id", ""),
            "session_id": snapshot.get("session_id", ""),
            "revision": snapshot.get("revision", ""),
        }
        circuit_key = (str(identity.get("match_id") or ""),
                       str(identity.get("session_id") or ""))
        with ACTION_PROGRESS_LOCK:
            circuit = RUNTIME_CIRCUITS.get(circuit_key)
        if isinstance(circuit, dict):
            return _attach_working_state(_attach_chat_attention({
                "ok": True, "kind": "decision_frame", "identity": identity,
                "turn": snapshot.get("turn"), "year": snapshot.get("year"),
                "phase": "capability_gap", "state": _compact_decision_state(snapshot),
                "focus": {"kind": "execution_circuit", "incident": circuit},
                "choices": [],
                "required_next": {
                    "tool": "smac_report_capability_gap", "stop_after": True,
                    "reason": "The platform stopped a repeated no-progress execution loop.",
                },
            }, identity), identity)
        briefing = _compose_match_briefing(snapshot)
        if not briefing.get("ok"):
            return briefing
        if not briefing.get("acknowledged"):
            return {
                "ok": True,
                "kind": "match_briefing_required",
                "identity": identity,
                "turn": snapshot.get("turn"),
                "year": snapshot.get("year"),
                "briefing_hash": briefing.get("briefing_hash"),
                "change": briefing.get("change"),
                "required_next": {"tool": "smac_match_briefing", "action": "read"},
                "gameplay_mutations_blocked": True,
                "choices": [],
            }
        boundary = _implicit_turn_handoff(snapshot, identity)
        if boundary is not None:
            return _attach_working_state(_attach_chat_attention(
                _attach_briefing_status(boundary, briefing), identity,
            ), identity)
        protocol = snapshot.get("protocol", {})
        phase = protocol.get("phase")
        semantic_context: dict[str, Any] | None = None
        if phase == "wait":
            frame = {
                "ok": True, "kind": "decision_frame", "identity": identity,
                "turn": snapshot.get("turn"), "year": snapshot.get("year"),
                "phase": "wait", "state": _compact_decision_state(snapshot),
                "required_next": {"tool": "smac_wait", "reason": protocol.get("required_action")},
                "choices": [],
            }
            if detail == "full":
                frame["snapshot"] = snapshot
            return _attach_working_state(_attach_chat_attention(
                _attach_briefing_status(frame, briefing), identity,
            ), identity)
        if phase == "capability_gap":
            frame = {
                "ok": True, "kind": "decision_frame", "identity": identity,
                "turn": snapshot.get("turn"), "year": snapshot.get("year"),
                "phase": "capability_gap", "state": _compact_decision_state(snapshot),
                "required_next": {
                    "tool": "smac_report_capability_gap",
                    "reason": protocol.get("required_action"), "stop_after": True,
                },
                "choices": [],
            }
            if detail == "full":
                frame["snapshot"] = snapshot
            return _attach_working_state(_attach_chat_attention(
                _attach_briefing_status(frame, briefing), identity,
            ), identity)
        if phase == "interaction":
            choice_kind = "interaction"
            choice_arguments: dict[str, object] = {}
            focus = {"kind": "interaction", "popup_label": snapshot.get("interaction", {}).get("popup_label", "")}
        else:
            ready_refs = snapshot.get("ready_unit_refs", [])
            if not isinstance(ready_refs, list):
                ready_refs = []
            selected = None
            if finish_ready_units:
                choice_kind = "game_management"
                choice_arguments = {}
                focus = {
                    "kind": "game_management", "purpose": "finish_ready_units",
                    "ready_unit_count": len(ready_refs),
                }
            elif own_unit_ref:
                selected = next(
                    (item for item in ready_refs
                     if isinstance(item, dict)
                     and str(item.get("own_unit_ref") or "") == own_unit_ref),
                    None,
                )
                if selected is None:
                    return {
                        "ok": False,
                        "error": {
                            "code": "unit_not_ready_in_decision_frame",
                            "message": "The requested own_unit_ref is not in the fresh snapshot's ready_unit_refs. Use one returned reference or omit it.",
                        },
                        "identity": identity,
                        "ready_unit_refs": ready_refs,
                    }
            elif ready_refs:
                selected = ready_refs[0]
            if finish_ready_units:
                pass
            elif selected is not None:
                selected_ref = str(selected.get("own_unit_ref") or "")
                try:
                    resolved, semantic_context = _resolve_managed_selectors(
                        str(identity["revision"]), own_unit_ref=selected_ref,
                        target_location_ref=target_location_ref,
                        target_unit_ref=target_unit_ref,
                    )
                except SemanticSelectorError as exc:
                    return {
                        "ok": False,
                        "error": {
                            "code": "ready_unit_reference_unresolved",
                            "message": "The selected semantic reference no longer belongs to this current native frame. Call smac_decision again.",
                            "detail": str(exc),
                        },
                    }
                choice_kind = "unit_actions"
                choice_arguments = resolved
                focus = {"kind": "unit_actions", "unit": selected}
            else:
                choice_kind = "game_management"
                choice_arguments = {}
                focus = {"kind": "game_management"}
        choices_result = _call("semantic_choices", kind=choice_kind, **choice_arguments)
        if not choices_result.get("ok"):
            return choices_result
        choice_identity = {
            "match_id": choices_result.get("match_id", ""),
            "session_id": choices_result.get("session_id", ""),
            "revision": choices_result.get("revision", ""),
        }
        if choice_identity != identity:
            continue
        try:
            if semantic_context is None and _choice_contains_private_selector(
                    choices_result.get("choices", [])):
                semantic_context = _semantic_selector_context(str(identity["revision"]))
            elif semantic_context is None:
                semantic_context = {}
            elif semantic_context.get("action_revision") != identity["revision"]:
                semantic_context = _semantic_selector_context(str(identity["revision"]))
        except SemanticSelectorError as exc:
            return {"ok": False, "error": {
                "code": "semantic_reference_projection_unavailable", "detail": str(exc),
            }}
        decision_id, public_choices = _cache_decision_choices(
            identity, choices_result.get("choices", []), choice_kind=choice_kind,
            choice_arguments=choice_arguments, semantic_context=semantic_context, focus=focus,
            turn=snapshot.get("turn"), year=snapshot.get("year"), phase=phase,
        )
        frame = {
            "ok": True, "kind": "decision_frame", "identity": identity,
            "decision_id": decision_id,
            "turn": snapshot.get("turn"), "year": snapshot.get("year"),
            "phase": phase, "state": _compact_decision_state(snapshot), "focus": focus,
            "required_next": {
                "tool": "smac_execute_choice", "execute_at_most": 1,
                "decision_id": decision_id,
                "then": "Call smac_decision again; never reuse this frame.",
            },
            "choices": public_choices,
            "information": _decision_information(choices_result.get("choices", []), semantic_context),
        }
        if any(row.get("command") in {"give_energy_gift", "propose_human_energy"}
               for row in choices_result.get("choices", ())):
            semantic_context = _semantic_selector_context(str(identity["revision"]))
        preparations = CHOICE_PREPARATIONS.begin(
            choices_result, identity=identity, context=semantic_context,
            kind=choice_kind, selectors=choice_arguments)
        if preparations:
            frame["preparations"] = preparations
            if not public_choices:
                frame["required_next"] = {"tool": "smac_choices", "kind": choice_kind,
                                          "use_returned_preparation": True}
        advisories = _decision_advisories(
            choices_result.get("choices", []), semantic_context=semantic_context,
        )
        if advisories:
            frame["rule_advisories"] = advisories
        if detail == "full":
            frame["snapshot"] = snapshot
        return _attach_working_state(_attach_chat_attention(
            _attach_briefing_status(frame, briefing), identity,
        ), identity)
    return {
        "ok": False,
        "error": {
            "code": "decision_frame_unstable",
            "message": "Game state changed while assembling the decision frame. Call smac_decision again.",
        },
    }


@mcp.tool(description="List owned bases, owned/visible units, contacted factions, acquired technologies, or known nearby map tiles without exposing hidden state. Center tile queries use an opaque tile_id, never coordinates.")
def smac_list(
    kind: Literal["bases", "units", "factions", "technologies", "tiles"],
    scope: Literal["own", "visible"] = "own",
    offset: int = 0,
    limit: int = 100,
    center_tile_id: int = -1,
    radius: int = 3,
) -> dict:
    if kind == "bases":
        return _call("list_bases", offset=offset, limit=limit)
    if kind == "units":
        return _call("list_units", scope=scope, offset=offset, limit=limit)
    if kind == "factions":
        return _call("list_factions")
    if kind == "technologies":
        return _call("list_technologies")
    arguments = {"radius": radius}
    if center_tile_id >= 0:
        arguments["center_tile_id"] = center_tile_id
    return _call("list_tiles", **arguments)


@mcp.tool(description="Enumerate currently legal semantic choices and compact parameter constraints. Select owned actors with own_unit_ref/base_ref and exact world targets with target_location_ref/target_unit_ref from the current perspective. Use returned preparation_ref and option_ref for staged selections; amount is accepted only by an advertised energy prompt. Native IDs and coordinates never cross this managed boundary.")
def smac_choices(
    kind: Literal["interaction", "research", "energy_allocation", "social_engineering", "diplomacy", "council", "unit_design", "production", "base_management", "base_citizens", "unit_actions", "game_management"],
    base_ref: str = "",
    own_unit_ref: str = "",
    target_location_ref: str = "",
    target_unit_ref: str = "",
    preparation_ref: str = "",
    option_ref: str = "",
    amount: int | None = None,
) -> dict:
    authority = _sovereign_gameplay_gate("Native choice enumeration")
    if authority:
        return authority
    if MANAGED_ATTACHED:
        refresh = _refresh_managed_world()
        if not refresh.get("ok"):
            return refresh
    snapshot_result = _call("semantic_snapshot")
    snapshot = snapshot_result.get("snapshot")
    if snapshot_result.get("ok") is not True or not isinstance(snapshot, dict):
        return snapshot_result
    expected_revision = str(snapshot.get("revision") or "")
    prepared = None
    try:
        if preparation_ref:
            if any((base_ref, own_unit_ref, target_location_ref, target_unit_ref)):
                raise PreparationError("preparation_cannot_override_actor")
            semantic_context = _semantic_selector_context(expected_revision)
            prepared = CHOICE_PREPARATIONS.advance(
                preparation_ref, option_ref=option_ref, amount=amount, kind=kind,
                identity={key: snapshot.get(key, "") for key in ("match_id", "session_id", "revision")},
                context=semantic_context)
            if "preparation" in prepared:
                return {"ok": True, "kind": "preparation_frame", **prepared}
            choice_arguments = {**prepared["selectors"], **prepared["selected"]}
        elif option_ref or amount is not None:
            raise PreparationError("preparation_reference_required")
        else:
            choice_arguments, semantic_context = _resolve_managed_selectors(
                expected_revision, own_unit_ref=own_unit_ref, base_ref=base_ref,
                target_location_ref=target_location_ref, target_unit_ref=target_unit_ref,
            )
    except (SemanticSelectorError, PreparationError) as exc:
        return {"ok": False, "error": {
            "code": "invalid_semantic_selector", "detail": str(exc),
            "message": "Use only a current semantic reference from this seat's active world.",
        }}
    result = _call("semantic_choices", kind=kind, **choice_arguments)
    if not result.get("ok"):
        return result
    identity = {
        "match_id": result.get("match_id", ""),
        "session_id": result.get("session_id", ""),
        "revision": result.get("revision", ""),
    }
    if identity != {key: snapshot.get(key, "") for key in ("match_id", "session_id", "revision")}:
        return {"ok": False, "error": {"code": "choice_frame_revision_changed"}}
    raw_choices = bind_numeric_choices(result, choice_arguments)
    if prepared:
        prepared_command = prepared.get("command") or {
            "unit_design": "create_unit_design", "social_engineering": "set_social_engineering",
            "energy_allocation": "set_energy_allocation",
            "unit_upgrade": "upgrade_prototype",
        }.get(prepared["purpose"])
        raw_choices = [row for row in raw_choices if row.get("command") == prepared_command]
        if not raw_choices:
            return {"ok": False, "error": {"code": "prepared_combination_unavailable",
                    "message": "The current native catalog did not validate this combination; obtain a fresh preparation."}}
    decision_id, choices = _cache_decision_choices(
        identity, raw_choices, choice_kind=kind,
        choice_arguments=choice_arguments, semantic_context=semantic_context,
        catalog_information=_decision_information(result.get("choices", []), semantic_context),
    )
    frame = {
        "ok": True, "kind": "choice_frame", "decision_id": decision_id,
        "identity": identity, "choice_kind": kind, "choices": choices,
        "information": _decision_information(result.get("choices", []), semantic_context),
        "required_next": {
            "tool": "smac_execute_choice", "decision_id": decision_id,
            "execute_at_most": 1,
        },
    }
    if not prepared:
        preparations = CHOICE_PREPARATIONS.begin(
            result, identity=identity, context=semantic_context, kind=kind,
            selectors=choice_arguments)
        if preparations:
            frame["preparations"] = preparations
            if not choices:
                frame["required_next"] = {"tool": "smac_choices", "kind": kind,
                                          "use_returned_preparation": True}
    advisories = _decision_advisories(
        result.get("choices", []), semantic_context=semantic_context,
    )
    if advisories:
        frame["rule_advisories"] = advisories
    return frame


def smac_command(
    command: Literal["acknowledge_popup", "respond_to_contact", "continue_diplomacy", "propose_human_relationship", "propose_human_technology", "propose_human_energy", "propose_human_joint_attack", "respond_human_diplomacy", "finish_human_diplomacy", "choose_diplomacy_option", "give_energy_gift", "choose_diplomacy_target", "choose_diplomacy_base_target", "cancel_diplomacy_selection", "respond_to_diplomatic_offer", "respond_to_council_vote_bargain", "respond_to_incoming_vote_offer", "respond_to_territorial_incident", "respond_to_combat_confirmation", "respond_to_nerve_gas", "respond_to_end_turn_confirmation", "respond_to_base_obliteration", "respond_to_supreme_leader", "respond_to_game_over", "advance_endgame_presentation", "advance_technology_presentation", "advance_project_information", "respond_to_design_offer", "respond_to_artifact", "respond_to_monolith", "respond_to_probe_incident", "choose_probe_sabotage_target", "respond_to_probe_sabotage_warning", "choose_captive_leader", "choose_council_proposal", "cast_council_vote", "set_first_base_name", "choose_research_priority", "set_research_priority", "choose_research", "set_energy_allocation", "set_social_engineering", "open_diplomacy", "convene_council", "skip_all_ready_units", "corner_global_energy_market", "create_unit_design", "retire_unit_design", "upgrade_prototype", "set_production", "hurry_production", "nerve_staple", "obliterate_base", "recycle_facility", "rename_base", "set_base_governor", "set_governor_permission", "queue_production", "remove_queued_production", "clear_production_queue", "convert_worker_to_specialist", "assign_specialist_to_tile", "set_specialist_type", "move_unit", "go_to", "go_to_base", "return_to_base", "recover_to_carrier", "board_carrier", "patrol_unit", "build_road_to", "skip_unit", "hold_unit", "sentry_unit", "activate_unit", "upgrade_unit", "auto_explore_unit", "set_unit_on_alert", "automate_air_defense", "automate_former", "set_bombing_run", "set_designated_defender", "use_psi_gate", "execute_probe_mission", "execute_probe_subversion", "board_transport", "remain_boarded", "disembark_unit", "airdrop_unit", "artillery_attack", "launch_missile", "self_destruct_unit", "destroy_terrain_improvement", "rehome_unit", "give_unit", "convoy_resource", "disband_unit", "found_base", "terraform", "save_game", "end_turn"],
    match_id: str,
    session_id: str,
    expected_revision: str,
    response: Literal["", "accept", "decline", "open", "later", "block", "reject", "counter", "yea", "nay", "leave", "investigate", "always", "no_action", "link_technology", "accelerate_production", "forgive", "declare_vendetta", "tolerate", "renounce_pact", "abort", "proceed", "withdraw", "mutual_withdrawal", "refuse", "cancel", "conventional", "commit", "accede", "defy", "finish", "continue"] = "",
    option: str = "",
    phase: str = "",
    automation_mode: Literal["", "full", "roads", "magtubes", "improve_home_base", "farm_solar_road", "farm_mine_road", "remove_fungus", "sensors"] = "",
    target_kind: Literal["", "commlink", "joint_attack"] = "",
    relationship: Literal["", "treaty", "pact", "truce"] = "",
    payment: Literal["", "none", "energy", "technologies"] = "",
    governor_permission: Literal["", "multiple_priorities", "exploration_units", "land_combat_units", "naval_combat_units", "air_combat_units", "native_life_units", "land_defense_units", "air_defense_units", "prototype_units", "transport_units", "probe_units", "terraformer_units", "colony_pods", "facilities", "force_psych", "secret_projects", "hurry_production"] = "",
    faction_id: int = -1,
    target_faction_id: int = -1,
    proposal_id: int = -1,
    candidate_faction_id: int = -1,
    priority: int = -1,
    tech_id: int = -1,
    economy: int = -1,
    psych: int = -1,
    labs: int = -1,
    politics: int = -1,
    economics: int = -1,
    values: int = -1,
    future: int = -1,
    base_id: int = -1,
    target_base_id: int = -1,
    source_base_id: int = -1,
    destination_base_id: int = -1,
    item_id: int = 99999,
    facility_id: int = -1,
    queue_position: int = -1,
    active: int = -1,
    manage_citizens: int = -1,
    manage_production: int = -1,
    new_units_automated: int = -1,
    priority_explore: int = -1,
    priority_discover: int = -1,
    priority_build: int = -1,
    priority_conquer: int = -1,
    tile_index: int = -1,
    specialist_index: int = -1,
    citizen_id: int = -1,
    unit_id: int = -1,
    ready_unit_count: int = -1,
    transport_unit_id: int = -1,
    target_unit_id: int = -1,
    action_id: int = -1,
    amount: int = -1,
    sabotage_target_id: int = -1,
    captive_faction_id: int = -1,
    enhanced: int = 0,
    frame_faction_id: int = 0,
    prototype_id: int = -1,
    source_prototype_id: int = -1,
    target_prototype_id: int = -1,
    chassis_id: int = -1,
    weapon_id: int = -1,
    armor_id: int = -1,
    reactor_id: int = -1,
    ability_id_1: int = -1,
    ability_id_2: int = -1,
    target_tile_id: int = -1,
    former_id: int = -1,
    resource: Literal["", "nutrients", "minerals", "energy"] = "",
    infrastructure: Literal["", "road", "magtube"] = "",
    confirm_disband: int = 0,
    confirm_self_destruct: int = 0,
    confirm_transfer: int = 0,
    confirm_retire: int = 0,
    confirm_upgrade: int = 0,
    confirm_probe_incident: int = 0,
    confirm_atrocity: int = 0,
    confirm_obliteration: int = 0,
    confirm_recycle: int = 0,
    confirm_destruction: int = 0,
    confirm_hostility: int = 0,
    confirm_attack: int = 0,
    confirm_defiance: int = 0,
    confirm_vote_commitment: int = 0,
    confirm_corner_market: int = 0,
    confirm_consume_artifact: int = 0,
    confirm_skip_all_ready: int = 0,
    name: str = "",
    slot: str = "",
) -> dict:
    authority = _sovereign_gameplay_gate("Direct native command")
    if authority:
        return authority
    gap = _pending_capability_gap()
    if gap:
        return {
            "ok": False,
            "error": {
                "code": "capability_gap_latched",
                "message": "This match session reported a missing semantic capability; all further gameplay mutations are blocked.",
            },
            "gap": gap,
            "instruction": "STOP. The orchestrator must extend and test the bridge, restart MCP, and then resume play in a fresh native session.",
        }
    briefing_block = _match_briefing_gate(match_id, session_id)
    if briefing_block:
        return briefing_block
    command_arguments = dict(
        command=command,
        match_id=match_id,
        session_id=session_id,
        expected_revision=expected_revision,
        response=response,
        option=option,
        phase=phase,
        automation_mode=automation_mode,
        target_kind=target_kind,
        relationship=relationship,
        payment=payment,
        governor_permission=governor_permission,
        faction_id=faction_id,
        target_faction_id=target_faction_id,
        proposal_id=proposal_id,
        candidate_faction_id=candidate_faction_id,
        priority=priority,
        tech_id=tech_id,
        economy=economy,
        psych=psych,
        labs=labs,
        politics=politics,
        economics=economics,
        values=values,
        future=future,
        base_id=base_id,
        target_base_id=target_base_id,
        source_base_id=source_base_id,
        destination_base_id=destination_base_id,
        item_id=item_id,
        facility_id=facility_id,
        queue_position=queue_position,
        active=active,
        manage_citizens=manage_citizens,
        manage_production=manage_production,
        new_units_automated=new_units_automated,
        priority_explore=priority_explore,
        priority_discover=priority_discover,
        priority_build=priority_build,
        priority_conquer=priority_conquer,
        tile_index=tile_index,
        specialist_index=specialist_index,
        citizen_id=citizen_id,
        unit_id=unit_id,
        ready_unit_count=ready_unit_count,
        transport_unit_id=transport_unit_id,
        target_unit_id=target_unit_id,
        action_id=action_id,
        amount=amount,
        sabotage_target_id=sabotage_target_id,
        captive_faction_id=captive_faction_id,
        enhanced=enhanced,
        frame_faction_id=frame_faction_id,
        prototype_id=prototype_id,
        source_prototype_id=source_prototype_id,
        target_prototype_id=target_prototype_id,
        chassis_id=chassis_id,
        weapon_id=weapon_id,
        armor_id=armor_id,
        reactor_id=reactor_id,
        ability_id_1=ability_id_1,
        ability_id_2=ability_id_2,
        target_tile_id=target_tile_id,
        former_id=former_id,
        resource=resource,
        infrastructure=infrastructure,
        confirm_disband=confirm_disband,
        confirm_self_destruct=confirm_self_destruct,
        confirm_transfer=confirm_transfer,
        confirm_retire=confirm_retire,
        confirm_upgrade=confirm_upgrade,
        confirm_probe_incident=confirm_probe_incident,
        confirm_atrocity=confirm_atrocity,
        confirm_obliteration=confirm_obliteration,
        confirm_recycle=confirm_recycle,
        confirm_destruction=confirm_destruction,
        confirm_hostility=confirm_hostility,
        confirm_attack=confirm_attack,
        confirm_defiance=confirm_defiance,
        confirm_vote_commitment=confirm_vote_commitment,
        confirm_corner_market=confirm_corner_market,
        confirm_consume_artifact=confirm_consume_artifact,
        confirm_skip_all_ready=confirm_skip_all_ready,
        name=name,
        slot=slot,
    )
    result = _call("semantic_command", **command_arguments)
    if result.get("error", {}).get("code") == "stale_state":
        refreshed = _fresh_unit_choice_for_stale_command(
            command, unit_id, target_tile_id, target_unit_id,
        )
        if refreshed is not None:
            fresh_revision = str(refreshed.get("revision", ""))
            if fresh_revision and fresh_revision != expected_revision:
                command_arguments["expected_revision"] = fresh_revision
                retried = _call("semantic_command", **command_arguments)
                if retried.get("ok"):
                    result = {
                        **retried,
                        "guard_revalidated": True,
                        "previous_revision": expected_revision,
                        "executed_revision": fresh_revision,
                    }
                else:
                    result = retried
        if result.get("error", {}).get("code") == "stale_state":
            result = {
                **result,
                "transient": True,
                "capability_gap": False,
                "instruction": (
                    "This is concurrency, not a missing semantic capability. "
                    "Call smac_wait briefly, then obtain a fresh smac_decision. "
                    "Never report stale_state or revision churn as a capability gap."
                ),
            }
    if command == "convene_council" and result.get("ok") and result.get("queued"):
        return {
            **result,
            "execution": {"action_id": result.get("action_id"), "status": "pending"},
            "next": "Observe the COUNCILISSUES interaction, choose one returned proposal with its bundled ballot, then observe the public result.",
        }
    if (command == "execute_probe_mission" and enhanced == 1
            and result.get("mission") == "sabotage"
            and result.get("ok") and result.get("queued")):
        return {
            **result,
            "execution": {"action_id": result.get("action_id"), "status": "pending"},
            "next": "Observe until the native post-entry sabotage interaction appears, then choose only a returned target. The mission may instead resolve immediately if the native game rejects it.",
        }
    if (command == "execute_probe_mission"
            and result.get("mission") == "free_captured_leader"
            and result.get("ok") and result.get("queued")):
        return {
            **result,
            "execution": {"action_id": result.get("action_id"), "status": "pending"},
            "next": "Observe until the native post-success FREEWHO interaction appears, then choose only a returned captive leader. The mission may instead resolve immediately if it fails.",
        }
    return _await_deferred_action(result)


def _command_payload(choice: dict, identity: dict) -> dict:
    allowed = set(inspect.signature(smac_command).parameters)
    payload = {
        key: value for key, value in choice.items()
        if key in allowed and key not in {"match_id", "session_id", "expected_revision"}
    }
    requires = choice.get("requires")
    if isinstance(requires, dict):
        # Native catalogs may keep destructive confirmations in a private
        # requirements object. Normalize that form here too, so stale-state
        # comparison/rebase is identical to the first opaque execution.
        for key, value in requires.items():
            if key in allowed and isinstance(key, str) \
                    and key.startswith("confirm_") and value in (1, True):
                payload[key] = 1
    payload.update({
        "match_id": str(identity.get("match_id") or ""),
        "session_id": str(identity.get("session_id") or ""),
        "expected_revision": str(identity.get("revision") or ""),
    })
    return payload


def _choice_semantic_key(choice: dict) -> str:
    payload = _command_payload(choice, {})
    for key in ("match_id", "session_id", "expected_revision"):
        payload.pop(key, None)
    # Prices, affected counts, risk and schedules are consent terms even when
    # the native command takes only an accept/reject enum. Revision churn must
    # never rebase acceptance onto a different offer or bulk-upgrade cost.
    argument_keys = set(inspect.signature(smac_command).parameters)
    terms = {key: value for key, value in choice.items()
             if key not in argument_keys | {"id", "requires", "parameters"}}
    return json.dumps({"payload": payload, "terms": terms}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))


def _latch_journal_failure(
    response: dict, *, progress_key: tuple[str, str], choice_label: str,
    journal: dict,
) -> dict:
    """Stop after an accepted native mutation whose canonical write failed."""
    incident = {
        "code": "campaign_journal_write_failed",
        "selected_choice_label": choice_label,
        "message": (
            "The native action completed, but its authoritative campaign-journal "
            "record could not be made. Further mutation is stopped to preserve "
            "recoverable campaign history."
        ),
        "journal_error": str(journal.get("error") or "unknown_journal_error")[:500],
    }
    with ACTION_PROGRESS_LOCK:
        RUNTIME_CIRCUITS[progress_key] = incident
    gap = smac_report_capability_gap(
        screen_or_state="campaign journal durability failure after native mutation",
        intended_decision=choice_label,
        required_observation="the current native state and last verified checkpoint",
        required_action="restore authoritative journal durability before another mutation",
        why_blocked=incident["message"],
    )
    response.update({
        "ok": False,
        "error": {
            "code": "campaign_journal_write_failed",
            "message": incident["message"],
        },
        "incident": incident,
        "native_action_executed": True,
        "required_next": {"stop_after": True, "reason": "Operator recovery is required."},
        "capability_gap": gap.get("gap") if isinstance(gap, dict) else None,
    })
    response.pop("turn_handoff_required", None)
    return response


@mcp.tool(
    description=(
        "Execute exactly one short-lived opaque choice returned by the latest smac_decision "
        "or smac_choices frame. The server owns the native command, parameters, confirmation "
        "flags, and revision guard. Supply text only when that exact choice exposes text_input; "
        "an opening base-name choice uses its native suggested default when text is omitted. "
        "Never invent command names or reuse a consumed decision."
    )
)
def smac_execute_choice(decision_id: str, choice_id: str, text: str = "") -> dict:
    """Expose decision lifecycle and bound consecutive failed submissions."""
    authority = _sovereign_gameplay_gate("Choice execution")
    if authority:
        return authority
    with DECISION_LOCK:
        cached = DECISION_CACHE.get(decision_id)
        identity = dict(cached.get("identity") or {}) if cached else {}
    if MANAGED_ATTACHED:
        match_id, session_id, _, _ = _managed_scope_identity()
    else:
        match_id, session_id = identity.get("match_id", ""), identity.get("session_id", "")
    key = (str(match_id), str(session_id))
    with ACTION_PROGRESS_LOCK:
        circuit = RUNTIME_CIRCUITS.get(key)
    if circuit:
        return {"ok": False, "error": {"code": "repetition_circuit_open",
                "message": circuit["message"]}, "incident": circuit,
                "native_action_executed": False, "execution_status": "not_dispatched",
                "required_next": {"stop_after": True, "reason": "Operator recovery is required."}}

    response = _execute_choice_once(decision_id, choice_id, text)
    error = response.get("error")
    code = error.get("code") if isinstance(error, dict) else error
    boundary_errors = {"unknown_decision", "expired_decision", "consumed_decision",
                       "invalid_choice", "invalid_choice_text", "unexpected_choice_text"}
    with DECISION_LOCK:
        current = DECISION_CACHE.get(decision_id)
        consumed = bool(current.get("consumed")) if current else None
    response["decision_consumed"] = consumed
    if code in boundary_errors:
        response["execution_status"] = "not_dispatched"
        response["native_action_executed"] = False
    elif not response.get("ok"):
        response.setdefault("execution_status", "rejected" if code == "native_action_rejected"
                            else "unverified")
    elif response.get("queued"):
        response["execution_status"] = "pending"
    elif response.get("order") or response.get("persistent"):
        response["execution_status"] = "order_assigned"
    elif response.get("completed"):
        response["execution_status"] = "completed"
    else:
        response["execution_status"] = "accepted"
    execution = response.get("execution")
    if isinstance(execution, dict) and isinstance(execution.get("native_call_attempted"), bool):
        response["native_call_attempted"] = execution["native_call_attempted"]
        if execution["native_call_attempted"] is False and not response.get("ok"):
            response["execution_status"] = "not_dispatched"
            response["native_action_executed"] = False
    if consumed and not response.get("required_next"):
        response["required_next"] = {"tool": "smac_decision",
            "reason": "This decision is consumed, including after rejection. Obtain a fresh frame."}

    # A failure budget is deliberately independent of target and decision IDs.
    # Success resets this submission budget; unchanged-state success loops are
    # separately handled by the action and supervisor progress circuits.
    countable = code in boundary_errors | {"native_action_rejected", "decision_conflict"}
    if not all(key):
        return response
    with ACTION_PROGRESS_LOCK:
        if response.get("ok"):
            FAILED_CHOICE_ATTEMPTS.pop(key, None)
            return response
        if not countable:
            return response
        failures = FAILED_CHOICE_ATTEMPTS.setdefault(key, [])
        failures.append(str(code))
        del failures[:-FAILED_CHOICE_LIMIT]
        history = list(failures)
        if len(history) < FAILED_CHOICE_LIMIT:
            response["failure_budget"] = {"consecutive_failures": len(history),
                "stop_at": FAILED_CHOICE_LIMIT}
            return response
        incident = {"code": "repeated_failed_choice_submissions",
            "failure_codes": history, "attempt_count": len(history),
            "message": "Repeated choice submissions failed despite recovery opportunities. Autonomous play is stopped for operator review."}
        RUNTIME_CIRCUITS[key] = incident
    journal = controller_record_campaign_action(key[0], key[1], {
        "decision_id": decision_id, "choice_id": choice_id,
        "outcome": "failure_circuit_open", "incident": incident,
    }, turn=cached.get("turn") if cached else None, year=cached.get("year") if cached else None)
    gap = smac_report_capability_gap(
        screen_or_state="repeated failed managed choice submissions",
        intended_decision="Resolve the current gameplay decision",
        required_observation="Verified action effects and current decision lifecycle",
        required_action="Repair repeated rejection or decision protocol failures",
        why_blocked=incident["message"],
    )
    response.update({"ok": False, "error": {"code": "failure_circuit_open",
        "message": incident["message"]}, "incident": incident, "journal": journal,
        "capability_gap": gap.get("gap") if isinstance(gap, dict) else None,
        "required_next": {"stop_after": True, "reason": "The incident was automatically reported."}})
    return response


def _execute_choice_once(decision_id: str, choice_id: str, text: str = "") -> dict:
    authority = _sovereign_gameplay_gate("Choice execution")
    if authority:
        return authority
    now = time.monotonic()
    with DECISION_LOCK:
        decision = DECISION_CACHE.get(decision_id)
        if decision is None:
            return {
                "ok": False,
                "error": {"code": "unknown_decision", "message": "Obtain a fresh smac_decision frame."},
                "required_next": {"tool": "smac_decision"},
            }
        if now - float(decision.get("created_monotonic", 0)) > DECISION_TTL_SECONDS:
            DECISION_CACHE.pop(decision_id, None)
            return {
                "ok": False,
                "error": {"code": "expired_decision", "message": "The choice expired; obtain a fresh frame."},
                "required_next": {"tool": "smac_decision"},
            }
        if decision.get("consumed"):
            return {
                "ok": False,
                "error": {"code": "consumed_decision", "message": "This decision was already executed."},
                "required_next": {"tool": "smac_decision"},
            }
        cached_choice = decision.get("choices", {}).get(choice_id)
        if not isinstance(cached_choice, dict):
            return {
                "ok": False,
                "error": {"code": "invalid_choice", "message": "Use one choice_id from this exact decision."},
                "available_choice_ids": sorted(decision.get("choices", {})),
            }
        choice = dict(cached_choice)
        if choice.get("command") in {"set_first_base_name", "rename_base"}:
            supplied = text.strip()
            if not supplied and choice.get("command") == "set_first_base_name":
                supplied = str(choice.get("suggested_name") or "").strip()
            if not supplied or len(supplied) > 24 or any(
                    character in supplied for character in "\r\n\t"):
                return {
                    "ok": False,
                    "error": {
                        "code": "invalid_choice_text",
                        "message": "This choice requires a base name containing 1 through 24 characters and no control whitespace.",
                    },
                    "required_next": {
                        "tool": "smac_execute_choice", "decision_id": decision_id,
                        "choice_id": choice_id, "text_required": True,
                    },
                }
            choice["name"] = supplied
        elif choice.get("command") == "create_unit_design":
            supplied = text.strip()
            if len(supplied.encode("utf-8")) > 31 or any(ord(character) < 32 for character in supplied):
                return {"ok": False, "error": {"code": "invalid_choice_text",
                        "message": "Design names accept at most 31 UTF-8 bytes and no control characters; omit for a native name."}}
            if supplied:
                choice["name"] = supplied
        elif text:
            return {
                "ok": False,
                "error": {
                    "code": "unexpected_choice_text",
                    "message": "The selected legal choice does not accept text.",
                },
                "required_next": {"tool": "smac_execute_choice", "decision_id": decision_id,
                                  "choice_id": choice_id},
            }
        # A decision is single-use even when the native operation rejects it.
        # Recovery always starts from a fresh authoritative frame.
        decision["consumed"] = True
        identity = dict(decision.get("identity") or {})
        choice_label = str(decision.get("choice_labels", {}).get(choice_id) or "Selected choice")

    progress_key = (str(identity.get("match_id") or ""),
                    str(identity.get("session_id") or ""))
    semantic_key = _choice_semantic_key(choice)
    with ACTION_PROGRESS_LOCK:
        previous = ACTION_PROGRESS.get(progress_key, {})
        same_attempt = (
            previous.get("state_fingerprint") == decision.get("state_fingerprint")
            and previous.get("semantic_key") == semantic_key
        )
        attempt_count = int(previous.get("attempt_count") or 0) + 1 if same_attempt else 1
        ACTION_PROGRESS[progress_key] = {
            "state_fingerprint": decision.get("state_fingerprint"),
            "semantic_key": semantic_key,
            "attempt_count": attempt_count,
            "selected_action": choice.get("command"),
            "updated_monotonic": time.monotonic(),
        }
        if attempt_count >= ACTION_REPEAT_LIMIT:
            incident = {
                "code": "repeated_no_progress_choice",
                "selected_action": choice.get("command"),
                "attempt_count": attempt_count,
                "state_fingerprint": decision.get("state_fingerprint"),
                "message": "The same semantic choice repeated without meaningful native-state progress.",
            }
            RUNTIME_CIRCUITS[progress_key] = incident
            journal = controller_record_campaign_action(
                str(identity.get("match_id") or ""),
                str(identity.get("session_id") or ""),
                {
                    "decision_id": decision_id, "choice_id": choice_id,
                    "selected_action": choice.get("command"),
                    "outcome": "repetition_circuit_open",
                    "native_action_executed": False,
                    "incident": incident,
                },
                turn=decision.get("turn"), year=decision.get("year"),
            )
            gap = smac_report_capability_gap(
                screen_or_state="stable semantic decision state",
                intended_decision=str(choice.get("command") or "execute current choice"),
                required_observation="fresh decision frame with meaningful native progress",
                required_action="a native action that changes or resolves the current state",
                why_blocked=(
                    "The platform observed the same semantic choice three times against "
                    "the same meaningful native state and opened its repetition circuit."
                ),
            )
            return {
                "ok": False,
                "error": {"code": "repetition_circuit_open", "message": incident["message"]},
                "executed_choice": {"choice_id": choice_id, "label": choice_label},
                "incident": incident,
                "required_next": {"stop_after": True,
                                  "reason": "The incident was automatically reported."},
                "native_action_executed": False,
                "journal": journal,
                "capability_gap": gap.get("gap") if isinstance(gap, dict) else None,
            }

    result = smac_command(**_command_payload(choice, identity))
    error = result.get("error") if isinstance(result, dict) else None
    error_code = error.get("code") if isinstance(error, dict) else error
    if error_code != "stale_state":
        response = {
            **result,
            "decision_id": decision_id,
            "choice_id": choice_id,
            "executed_choice": {"choice_id": choice_id, "label": choice_label},
        }
        response.pop("command", None)
        if choice.get("command") in {"create_unit_design", "retire_unit_design", "upgrade_prototype"}:
            response = _semanticize_choice(response, {})
        if result.get("ok"):
            after = _call("semantic_snapshot")
            snapshot = after.get("snapshot") if isinstance(after, dict) else None
            after_turn = snapshot.get("turn") if isinstance(snapshot, dict) else decision.get("turn")
            after_year = snapshot.get("year") if isinstance(snapshot, dict) else decision.get("year")
            journal = controller_record_campaign_action(
                str(identity.get("match_id") or ""), str(identity.get("session_id") or ""),
                {
                    "decision_id": decision_id, "choice_id": choice_id,
                    "selected_action": choice.get("command"),
                    "choice_parameters": {
                        key: value for key, value in choice.items()
                        if key not in {"confirm_destructive", "confirm_nerve_gas", "confirm_obliteration"}
                    },
                    "before": {"turn": decision.get("turn"), "year": decision.get("year"),
                               "phase": decision.get("phase")},
                    "after": {"turn": after_turn, "year": after_year,
                              "revision": snapshot.get("revision") if isinstance(snapshot, dict) else None},
                    "native_result": {
                        key: result.get(key) for key in
                        ("command", "completed", "queued", "action_id", "turn", "year")
                        if result.get(key) is not None
                    },
                },
                turn=after_turn, year=after_year,
                commit_reason=(f"Complete turn {after_turn}" if after_turn != decision.get("turn")
                               else ("Checkpoint decision" if choice.get("command") == "save_game" else "")),
            )
            if not journal.get("ok"):
                return _latch_journal_failure(
                    response, progress_key=progress_key,
                    choice_label=choice_label, journal=journal,
                )
            else:
                response["journal"] = journal
            _attach_turn_handoff(response, choice, decision, snapshot)
        else:
            response["journal"] = controller_record_campaign_action(
                str(identity.get("match_id") or ""),
                str(identity.get("session_id") or ""),
                {
                    "decision_id": decision_id, "choice_id": choice_id,
                    "selected_action": choice.get("command"),
                    "outcome": "native_rejected",
                    "error": error,
                },
                turn=decision.get("turn"), year=decision.get("year"),
            )
        return response

    # One server-side semantic rebase is permitted. The model is never asked
    # to spin on a rapidly changing revision counter.
    fresh = _call(
        "semantic_choices", kind=str(decision.get("choice_kind") or ""),
        **dict(decision.get("choice_arguments") or {}),
    )
    if (fresh.get("ok") and fresh.get("match_id") == identity.get("match_id")
            and fresh.get("session_id") == identity.get("session_id")
            and _decision_information(fresh.get("choices", []), None) ==
                _decision_information(decision.get("information", []), None)):
        intended = _choice_semantic_key(choice)
        replacement = next(
            (item for item in bind_numeric_choices(fresh, decision.get("choice_arguments") or {})
             if isinstance(item, dict) and _choice_semantic_key(item) == intended),
            None,
        )
        if replacement is not None:
            refreshed_identity = {
                "match_id": fresh.get("match_id", identity.get("match_id", "")),
                "session_id": fresh.get("session_id", identity.get("session_id", "")),
                "revision": fresh.get("revision", ""),
            }
            rebased = smac_command(**_command_payload(replacement, refreshed_identity))
            if rebased.get("ok"):
                response = {
                    **rebased,
                    "decision_id": decision_id,
                    "choice_id": choice_id,
                    "executed_choice": {"choice_id": choice_id, "label": choice_label},
                    "guard_revalidated": True,
                    "previous_revision": identity.get("revision"),
                    "executed_revision": refreshed_identity.get("revision"),
                }
                response.pop("command", None)
                if choice.get("command") in {"create_unit_design", "retire_unit_design", "upgrade_prototype"}:
                    response = _semanticize_choice(response, {})
                after = _call("semantic_snapshot")
                snapshot = after.get("snapshot") if isinstance(after, dict) else None
                after_turn = snapshot.get("turn") if isinstance(snapshot, dict) else decision.get("turn")
                journal = controller_record_campaign_action(
                    str(refreshed_identity.get("match_id") or ""),
                    str(refreshed_identity.get("session_id") or ""),
                    {
                        "decision_id": decision_id, "choice_id": choice_id,
                        "selected_action": choice.get("command"), "guard_revalidated": True,
                        "before": {"turn": decision.get("turn"), "year": decision.get("year")},
                        "after": {"turn": after_turn,
                                  "year": snapshot.get("year") if isinstance(snapshot, dict) else decision.get("year")},
                    },
                    turn=after_turn,
                    year=snapshot.get("year") if isinstance(snapshot, dict) else decision.get("year"),
                    commit_reason=(f"Complete turn {after_turn}" if after_turn != decision.get("turn") else ""),
                )
                if not journal.get("ok"):
                    return _latch_journal_failure(
                        response, progress_key=progress_key,
                        choice_label=choice_label, journal=journal,
                    )
                response["journal"] = journal
                _attach_turn_handoff(response, choice, decision, snapshot)
                return response
            result = rebased
    conflict_journal = controller_record_campaign_action(
        str(identity.get("match_id") or ""),
        str(identity.get("session_id") or ""),
        {
            "decision_id": decision_id, "choice_id": choice_id,
            "selected_action": choice.get("command"),
            "outcome": "decision_conflict",
            "automatic_rebases_exhausted": True,
        },
        turn=decision.get("turn"), year=decision.get("year"),
    )
    return {
        **result,
        "decision_id": decision_id,
        "choice_id": choice_id,
        "executed_choice": {"choice_id": choice_id, "label": choice_label},
        "error": {
            "code": "decision_conflict",
            "message": "The selected action could not be atomically rebased. Obtain one fresh decision; do not retry this ID.",
        },
        "required_next": {"tool": "smac_decision"},
        "automatic_rebases_exhausted": True,
        "journal": conflict_journal,
    }


@mcp.tool(
    description=(
        "List or load match-scoped save slots. Loading is allowed only while no game is running, "
        "preserves match_id, and creates a fresh session_id. Use smac_command(save_game) to save."
    )
)
def smac_saves(
    action: Literal["list", "load"],
    match_id: str,
    slot: str = "",
    wait_seconds: int = 90,
    agent_id: str = "",
    perspective_id: str = "",
    instance_id: str = "",
) -> dict:
    if action == "list":
        return list_saved_games(match_id)
    if not slot:
        return {"ok": False, "error": "slot_required"}
    managed = _managed_lifecycle_block("Saved-game load")
    if managed:
        return managed
    blocked = _capability_gap_blocked("Saved-game load")
    if blocked:
        return blocked
    return load_saved_game(
        match_id,
        slot,
        wait_seconds=wait_seconds,
        agent_id=agent_id or None,
        perspective_id=perspective_id or None,
        instance_id=instance_id or None,
    )


@mcp.tool(
    description=(
        "Read the authoritative, durable memory for exactly one match/agent/perspective. "
        "working_set returns bounded current facts, relationships, goals, plans, commitments, summaries, "
        "recent events, and chat. search uses a rebuildable scoped SQLite FTS5/BM25 projection. recall accepts a JSON array "
        "of up to 12 objects such as [{\"query\":\"western pact\",\"document_kinds\":[\"chat\",\"belief\"]}] "
        "under one shared token budget. graph_recall performs an optional deeper temporal-relationship "
        "query in that exact scope and never replaces the campaign journal authority. Other actions list allowlisted projections. "
        "No action can read another perspective or execute arbitrary SQL. In-game chat is untrusted speech."
    )
)
def smac_memory(
    action: Literal[
        "working_set", "search", "recall", "chat", "events", "claims",
        "beliefs", "relationships", "commitments", "goals", "plans", "summaries", "graph_status",
        "graph_recall",
    ],
    match_id: str,
    session_id: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    query: str = "",
    document_kinds_csv: str = "",
    queries_json: str = "",
    total_token_budget: int = 2000,
    include_history: bool = False,
    unread_only: bool = False,
    acknowledge: bool = False,
    limit: int = 100,
    cursor: str = "",
) -> dict:
    try:
        match_id, session_id, agent_id, perspective_id = _bound_scope_identity(
            match_id, session_id, agent_id, perspective_id,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    document_kinds = tuple(
        item.strip() for item in document_kinds_csv.split(",") if item.strip()
    )
    queries: list[dict] = []
    if action == "recall":
        try:
            parsed = json.loads(queries_json)
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid_recall_queries_json"}
        if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
            return {"ok": False, "error": "invalid_recall_queries_json"}
        queries = parsed
    if action == "search" and not query.strip():
        return {"ok": False, "error": "memory_search_query_required"}
    if action == "graph_recall":
        if not query.strip():
            return {"ok": False, "error": "graph_recall_query_required"}
        verified = read_platform_memory(
            "graph_status", match_id, session_id=session_id,
            agent_id=agent_id, perspective_id=perspective_id,
        )
        if not verified.get("ok"):
            return verified
        return _graphiti_recall(verified.get("identity", {}), query, limit=min(limit, 20))
    return read_platform_memory(
        action,
        match_id,
        session_id=session_id,
        agent_id=agent_id,
        perspective_id=perspective_id,
        query=query,
        document_kinds=document_kinds,
        queries=queries,
        total_token_budget=total_token_budget,
        include_history=include_history,
        unread_only=unread_only,
        # The managed MCP has one cognitive acknowledgement authority:
        # smac_attention_ack.  A history read cannot consume queued speech.
        acknowledge=False,
        limit=limit,
        cursor=cursor,
    )


@mcp.tool(
    description=(
        "Retrieve one small mechanics answer directly, or commission/inspect one disposable "
        "read-only evidence faculty. Use direct_reference for a focused rule lookup, reference for "
        "multi-hop mechanics research, and world for broad context-heavy analysis. The platform chooses model, budgets, "
        "deadline, retries, and execution class. Completion arrives through durable attention."
    )
)
def smac_investigate(
    action: Literal["direct_reference", "commission", "result", "retry", "cancel"],
    faculty: Literal["reference", "world"] = "world",
    objective: str = "",
    operation_id: str = "",
    subject_refs: list[str] | None = None,
    mission_id: str = "",
) -> dict:
    match_id, session_id, agent_id, perspective_id = _managed_scope_identity()
    try:
        scope, world, _ = controller_world_service(
            match_id, session_id=session_id, agent_id=agent_id,
            perspective_id=perspective_id,
        )
        service = SpecialistService(world.store.store, world.store, scope)
        if action == "direct_reference":
            if not objective.strip():
                raise SpecialistError("reference_query_required")
            direct = read_game_reference(
                "search", query=objective[:2000], limit=4, include_body=True,
                max_content_tokens=2048, max_query_tokens=512,
            )
            if not direct.get("ok"):
                return direct
            evidence = direct.get("evidence") if isinstance(direct.get("evidence"), list) else []
            receipts = [{
                "evidence_ref": "evidence-reference-" + content_hash({
                    "query": objective[:2000], "document_id": item.get("document_id"),
                    "field": item.get("field", "body"),
                    "source_hash": item.get("source_hash"),
                })[:24],
                "document_id": item.get("document_id"),
                "field": item.get("field", "body"),
                "source_hash": item.get("source_hash"),
            } for item in evidence if isinstance(item, dict) and item.get("document_id")][:16]
            return {"ok": True, "mode": "direct_reference", "bounded": True,
                    "result": direct,
                    "evidence_refs": [item["evidence_ref"] for item in receipts],
                    "provenance_receipts": receipts,
                    "dependency_hash": content_hash(direct)}
        if action == "result":
            if not mission_id:
                raise SpecialistError("mission_id_required")
            return service.get(mission_id)
        if action == "retry":
            if not mission_id:
                raise SpecialistError("mission_id_required")
            return service.retry(mission_id)
        if action == "cancel":
            if not mission_id:
                raise SpecialistError("mission_id_required")
            return service.cancel(mission_id)
        if not objective.strip():
            raise SpecialistError("specialist_objective_required")
        corpus_revision = None
        if faculty == "reference":
            reference_status = read_game_reference("status")
            state = reference_status.get("state") \
                if isinstance(reference_status.get("state"), dict) else {}
            corpus_revision = str(state.get("revision") or "")
            if not reference_status.get("ok") or not corpus_revision:
                raise SpecialistError("reference_corpus_revision_unavailable")
        if faculty == "world" and subject_refs:
            _, projection = world._projection()
            known = {str(item["object_ref"]) for item in projection.get("objects", ())}
            if any(ref not in known for ref in subject_refs):
                raise SpecialistError("unknown_specialist_subject_ref")
        commissioned = service.commission(
            faculty=faculty, objective=objective, operation_id=operation_id or None,
            subject_refs=subject_refs or (),
            corpus_revision=corpus_revision,
            model_profile_revision=os.environ.get(
                "SMACX_HARNESS_PROFILE_ID", "installation-helper"),
        )
        # Tiny synthesis jobs often finish while the call is still in flight;
        # never hold the sovereign indefinitely for background work.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            current = service.get(str(commissioned["mission_id"]))
            if current.get("status") != "mission_pending":
                return current
            time.sleep(0.05)
        return commissioned
    except (SpecialistError, WorldQueryError, ValueError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}


@mcp.tool(
    description=(
        "Create or revise one structured, perspective-scoped memory record using a fresh snapshot guard. "
        "record_json schemas: claim={topic,content,asserted_by_actor_id?,about_actor_id?,confidence?,status?,source_event_id?}; "
        "belief={topic,content,confidence,evidence?:[{event_id,stance,weight}]}; "
        "relationship={actor_id,affinity,trust,respect,threat,grievance,obligation,confidence,reasons:[...],source_event_id?}; "
        "commitment={commitment_key,title,terms,status,parties?:[{actor_id,role}],due_turn?,due_year?,source_event_id?,resolution_event_id?}; "
        "goal={goal_key?,title,description,priority,status,due_turn?,due_year?,trigger?,parent_goal_id?,source_event_id?}; "
        "plan={plan_key,title,objective,status,target_refs?,participants?,timing?,dependencies?,intended_role?,contingencies?,last_confirmation?,linked_commitments?,contradictory_evidence?}; "
        "summary={section,content,through_event_id?}, where section is situation, relationships, goals, plans, commitments, recent_events, or chat. "
        "Claims are untrusted assertions; beliefs are the agent's confidence-scored interpretation. "
        "Actor and event references are mechanically restricted to this same fair-play perspective."
    )
)
def smac_memory_update(
    action: Literal["claim", "belief", "relationship", "commitment", "goal", "plan", "summary"],
    match_id: str,
    session_id: str,
    observed_revision: str,
    record_json: str,
    agent_id: str = "",
    perspective_id: str = "",
) -> dict:
    try:
        match_id, session_id, agent_id, perspective_id = _bound_scope_identity(
            match_id, session_id, agent_id, perspective_id,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        record = json.loads(record_json)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_memory_record_json"}
    if not isinstance(record, dict):
        return {"ok": False, "error": "invalid_memory_record_json"}
    return write_platform_memory(
        action,
        match_id,
        session_id,
        observed_revision,
        record,
        agent_id=agent_id,
        perspective_id=perspective_id,
    )


@mcp.tool(
    description=(
        "Read or maintain the canonical perspective-scoped campaign notebook. Collections may "
        "hold notes, hypotheses, suspicions, plans, territories, questions, reminders, or a "
        "short custom collection. list/get are read-only. put/delete require the latest native "
        "session revision. Entries are versioned in the campaign journal and must contain stable "
        "human concepts, never session-local native object IDs."
    )
)
def smac_notebook(
    action: Literal["list", "get", "put", "delete"],
    match_id: str,
    collection: str = "notes",
    key: str = "",
    title: str = "",
    content: str = "",
    tags_csv: str = "",
    status: Literal["active", "resolved", "cancelled"] = "active",
    session_id: str = "",
    observed_revision: str = "",
    agent_id: str = "",
    perspective_id: str = "",
    cursor: str = "",
    limit: int = 24,
    query: str = "",
) -> dict:
    try:
        match_id, session_id, agent_id, perspective_id = _bound_scope_identity(
            match_id, session_id, agent_id, perspective_id,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    ephemeral_reference = SESSION_LOCAL_KNOWLEDGE_REFERENCE.search(content)
    if ephemeral_reference:
        return {
            "ok": False,
            "error": {
                "code": "session_local_notebook_reference",
                "message": "Use stable named concepts rather than native unit/base/prototype IDs.",
            },
        }
    return controller_campaign_notebook(
        action, match_id, collection=collection, key=key, title=title,
        content=content,
        tags=tuple(item.strip() for item in tags_csv.split(",") if item.strip()),
        status=status, session_id=session_id, observed_revision=observed_revision,
        agent_id=agent_id, perspective_id=perspective_id,
        cursor=cursor, limit=limit, query=query,
    )


@mcp.tool(description="Wait briefly for the game state to change, then return a fresh observation. Maximum 30 seconds.")
def smac_wait(seconds: int = 2) -> dict:
    seconds = min(max(seconds, 0), 30)
    before = _call("observe")
    if not before.get("ok"):
        return {
            **before,
            "wait_stage": "initial_observation",
            "instruction": (
                "STOP issuing gameplay actions. The native game bridge is unavailable; "
                "wait for platform recovery or operator attention."
            ),
        }
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.25)
        after = _call("observe")
        if not after.get("ok"):
            return {
                **after,
                "wait_stage": "poll_observation",
                "instruction": (
                    "STOP issuing gameplay actions. The native game bridge became unavailable; "
                    "wait for platform recovery or operator attention."
                ),
            }
        if after != before:
            result = {"ok": True, "changed": True, "observation": after}
            status = _call("status")
            identity = status.get("identity", {}) if isinstance(status, dict) else {}
            return _attach_chat_attention(result, identity if isinstance(identity, dict) else {})
    final = _call("observe")
    if not final.get("ok"):
        return {
            **final,
            "wait_stage": "final_observation",
            "instruction": (
                "STOP issuing gameplay actions. The native game bridge is unavailable; "
                "wait for platform recovery or operator attention."
            ),
        }
    result = {"ok": True, "changed": False, "observation": final}
    status = _call("status")
    identity = status.get("identity", {}) if isinstance(status, dict) else {}
    return _attach_chat_attention(result, identity if isinstance(identity, dict) else {})


@mcp.tool(description="Report one missing semantic capability to the bridge developer and stop play. Do not attempt a UI workaround after calling this.")
def smac_report_capability_gap(
    screen_or_state: str,
    intended_decision: str,
    required_observation: str,
    required_action: str,
    why_blocked: str,
) -> dict:
    snapshot_result = _call("semantic_snapshot")
    snapshot = snapshot_result.get("snapshot", {}) if isinstance(snapshot_result, dict) else {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    protocol = snapshot.get("protocol", {})
    match_id = str(snapshot.get("match_id", ""))
    session_id = str(snapshot.get("session_id", ""))
    key = (match_id, session_id) if match_id and session_id else None
    with ACTION_PROGRESS_LOCK:
        execution_circuit = RUNTIME_CIRCUITS.get(key) if key else None
    explicit_gap = (
        isinstance(protocol, dict) and protocol.get("phase") == "capability_gap"
    ) or isinstance(execution_circuit, dict)
    if not explicit_gap:
        rule = _settlement_rule_explains_request(
            required_action, _latest_rule_advisories(match_id, session_id),
        )
        if rule is not None:
            return {
                "ok": False,
                "error": {
                    "code": "native_rule_explains_unavailable_action",
                    "message": (
                        "Founding a base is implemented, but the native rules reject this "
                        "Colony Pod's current tile. This is not a capability gap."
                    ),
                },
                "recorded": False,
                "gameplay_mutations_blocked": False,
                "rule_advisory": rule,
                "instruction": rule.get("meaning") or (
                    "Move the Colony Pod to a different legal site and request a fresh decision."
                ),
            }
    if not explicit_gap and _revision_conflict_report(
        screen_or_state, intended_decision, required_observation,
        required_action, why_blocked,
    ):
        return {
            "ok": False,
            "error": {
                "code": "transient_revision_conflict_not_capability_gap",
                "message": (
                    "Revision churn is an optimistic-concurrency conflict, not a missing "
                    "semantic capability, and must not latch or stop the match."
                ),
            },
            "recorded": False,
            "gameplay_mutations_blocked": False,
            "instruction": (
                "Obtain a fresh smac_decision; the opaque executor performs one bounded rebase. "
                "Report a capability gap only when smac_decision explicitly returns "
                "phase=capability_gap or a stable choice family lacks a required action."
            ),
        }
    report = {
        "gap_id": f"gap-{uuid.uuid4().hex}",
        "reported_at_unix": time.time(),
        "match_id": match_id,
        "session_id": session_id,
        "revision": str(snapshot.get("revision", "")),
        "turn": snapshot.get("turn"),
        "screen_or_state": screen_or_state,
        "intended_decision": intended_decision,
        "required_observation": required_observation,
        "required_action": required_action,
        "why_blocked": why_blocked,
        "execution_circuit": execution_circuit,
        "snapshot": snapshot_result,
    }
    with CAPABILITY_GAP_LOCK:
        existing = CAPABILITY_GAPS.get(key) if key else None
        if existing:
            report = existing
            recorded = False
        else:
            if key:
                CAPABILITY_GAPS[key] = report
            GAP_LOG.parent.mkdir(parents=True, exist_ok=True)
            with GAP_LOG.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
            recorded = True
    return {
        "ok": True,
        "recorded": recorded,
        "already_latched": not recorded,
        "gap": _capability_gap_summary(report),
        "gameplay_mutations_blocked": bool(key),
        "path": str(GAP_LOG),
        "instruction": "STOP. Commands, launch, new game, and load are now blocked. Wait for the orchestrator to extend and test the bridge, restart MCP, and then resume in a fresh native session.",
    }


@mcp.tool(description="Stop only the isolated SMACX/Proton game processes. This never sends desktop keyboard or mouse input and does not stop the MCP server.")
def smac_stop() -> dict:
    managed = _managed_lifecycle_block("Game stop")
    if managed:
        return managed
    return stop_game()


# A managed seat receives only the in-match semantic surface. Lifecycle and
# unrestricted snapshot utilities belong to the authenticated control plane;
# omitting them here materially reduces every provider request and prevents an
# autonomous player from attempting operations it can never legally perform.
if MANAGED_ATTACHED:
    for _managed_hidden_tool in (
        "smac_status", "smac_capabilities", "smac_launch", "smac_new_game",
        "smac_scenarios", "smac_new_scenario", "smac_observe", "smac_snapshot",
        "smac_lan", "smac_saves", "smac_stop", "smac_list",
    ):
        mcp.remove_tool(_managed_hidden_tool)


if __name__ == "__main__":
    try:
        mcp_port = int(os.environ.get("SMACX_MCP_PORT", "47814"))
    except ValueError as exc:
        raise SystemExit("invalid_smacx_mcp_port") from exc
    if not 1 <= mcp_port <= 65535:
        raise SystemExit("invalid_smacx_mcp_port")
    if MANAGED_ATTACHED:
        # Independent collection continues while the sovereign thinks, waits,
        # or handles communication. Native calls remain bounded and paginated.
        _start_managed_runtime()
    mcp.run(
        "streamable-http",
        host=os.environ.get("SMACX_MCP_HOST", "127.0.0.1"),
        port=mcp_port,
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
    )
