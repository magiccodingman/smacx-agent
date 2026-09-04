#!/usr/bin/env python3
"""Installation-managed disposable Hermes specialist supervisor.

SMACX owns mission authority and process leashes. Each attempt gets a fresh
Hermes home, exact system prompt, one stdio MCP instrument, and no game or
sovereign volume. The child session is never reused by another mission.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from smacx_control import SecretVault
from smacx_generation import normalize_generation_settings, openai_extra_body
from smacx_provider_meter import AttemptProviderProxy, ProviderLeaseMeter
from smacx_specialists import (
    SpecialistError, SpecialistService, SpecialistTraceStore, extract_result,
    mission_prompt, system_prompt,
)
from smacx_store import MemoryScope, SmacxStore
from smacx_world_store import WorldStore
from smacx_world_types import canonical_json, content_hash


class SpecialistSupervisor:
    def __init__(self, *, database: Path, secret_root: Path,
                 snapshot_root: Path, trace_root: Path,
                 reference_url: str, poll_seconds: float = 0.5) -> None:
        self.store = SmacxStore(database)
        self.world_store = WorldStore(self.store, snapshot_root)
        self.vault = SecretVault(self.store, secret_root)
        self.trace_store = SpecialistTraceStore(trace_root)
        self.reference_url = reference_url.rstrip("/")
        self.poll_seconds = min(max(float(poll_seconds), 0.1), 10.0)
        self.owner = "specialist-supervisor-" + uuid.uuid4().hex
        self.stop_event = threading.Event()
        self.children: dict[str, subprocess.Popen[str]] = {}
        self.children_lock = threading.RLock()
        self.futures: dict[str, Future[None]] = {}
        self.executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="smacx-specialist")
        self._last_seat = ""

    @staticmethod
    def _scope(mission: Mapping[str, Any]) -> MemoryScope:
        return MemoryScope(str(mission["match_id"]), str(mission["agent_id"]),
                           str(mission["perspective_id"]))

    def _service(self, mission: Mapping[str, Any]) -> SpecialistService:
        return SpecialistService(self.store, self.world_store, self._scope(mission))

    def reconcile(self) -> int:
        # Repair the narrow crash window between atomic snapshot+pin creation
        # and specialist mission insertion before scanning mission lifecycles.
        total = self.world_store.gc_orphaned_specialist_snapshot_pins()
        with self.store._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT DISTINCT m.* FROM specialist_missions m JOIN specialist_attempts a "
                "ON a.mission_id=m.mission_id WHERE m.status='active' AND a.status IN "
                "('starting','running','validating') AND (a.runtime_owner<>? OR "
                "a.heartbeat_expires_unix<=?)", (self.owner, time.time()),
            ).fetchall()]
        for mission in rows:
            total += self._service(mission).reconcile_orphans(self.owner)
        with self.store._connect() as connection:
            scopes = [dict(row) for row in connection.execute(
                "SELECT DISTINCT match_id,agent_id,perspective_id FROM specialist_missions"
            ).fetchall()]
        for scope in scopes:
            self._service(scope).reconcile_terminal_attention()
        return total

    def _publish_health(self, status: str = "ready") -> None:
        with self.children_lock:
            child_count = len(self.children)
        value = canonical_json({
            "status": status, "owner": self.owner, "process_id": os.getpid(),
            "active_children": child_count, "heartbeat_unix": time.time(),
        })
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO control_settings(setting_key,value_json,updated_unix) VALUES"
                "('specialist.supervisor_health',?,?) ON CONFLICT(setting_key) DO UPDATE SET "
                "value_json=excluded.value_json,updated_unix=excluded.updated_unix",
                (value, time.time()),
            )

    def _setting(self, name: str) -> Any:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT value_json FROM control_settings WHERE setting_key=?", (name,),
            ).fetchone()
        return json.loads(row["value_json"]) if row else None

    def _reference_revision(self) -> str:
        try:
            request = Request(self.reference_url + "/api/status", headers={
                "Accept": "application/json",
            })
            with urlopen(request, timeout=10) as response:
                value = json.loads(response.read(1_000_001))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise SpecialistError("reference_corpus_revision_unavailable") from exc
        state = value.get("state") if isinstance(value, Mapping) else None
        revision = state.get("revision") if isinstance(state, Mapping) else None
        if not revision:
            raise SpecialistError("reference_corpus_revision_unavailable")
        return str(revision)

    def _provider_profile(self, mission: Mapping[str, Any]) -> dict[str, Any]:
        try:
            profile = json.loads(str(mission.get("model_profile_json") or "{}"))
        except json.JSONDecodeError as exc:
            raise SpecialistError("specialist_helper_profile_invalid") from exc
        if not isinstance(profile, dict) or content_hash(profile) != str(
                mission["model_profile_revision"]):
            raise SpecialistError("specialist_helper_profile_integrity_failure")

        provider_id = str(profile.get("provider_id") or "")
        model_id = str(profile.get("model_id") or "")
        with self.store._connect() as connection:
            provider = connection.execute(
                "SELECT * FROM model_providers WHERE provider_id=? AND status!='disabled'",
                (provider_id,),
            ).fetchone()
            model = connection.execute(
                "SELECT context_length FROM provider_models WHERE provider_id=? AND model_id=?",
                (provider_id, model_id),
            ).fetchone()
        if not provider or not model_id:
            raise SpecialistError("specialist_provider_unavailable")
        context_length = (profile.get("context_length") or provider["context_length_override"]
                          or (model["context_length"] if model else None))
        if context_length is None or int(context_length) < 65_536:
            raise SpecialistError("specialist_context_length_unavailable")
        api_key = ""
        if provider["api_key_secret_id"]:
            api_key = self.vault.read(
                str(provider["api_key_secret_id"]),
                purpose=f"provider.{provider_id}.api_key",
            )
        generation = normalize_generation_settings(
            profile.get("generation_settings") if isinstance(
                profile.get("generation_settings"), Mapping) else None,
        )
        return {
            "profile_id": str(profile.get("profile_id") or "installation-helper"),
            "profile_revision": str(profile.get("profile_fingerprint")
                                    or content_hash(profile)),
            "provider_id": provider_id, "base_url": str(provider["base_url"]),
            "model_id": model_id, "api_key": api_key,
            "reasoning_effort": str(profile.get("reasoning_effort") or "low"),
            "context_length": min(int(context_length), int(mission["context_token_ceiling"])),
            "generation": generation,
        }

    def _attempt_files(self, root: Path, mission: Mapping[str, Any],
                       attempt_id: str, profile: Mapping[str, Any], *,
                       provider_base_url: str | None = None,
                       request_output_ceiling: int | None = None) -> dict[str, Path]:
        root.mkdir(parents=True, exist_ok=True)
        (root / "logs").mkdir()
        (root / "sessions").mkdir()
        workspace = root / "workspace"
        workspace.mkdir()
        prompt_version, prompt = system_prompt(str(mission["faculty"]))
        prompt_path = root / "specialist-system.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        prompt_path.chmod(0o400)
        capability = {
            "schema": "smacx.specialist-capability.v2",
            "nonce": secrets.token_hex(32), "mission_id": mission["mission_id"],
            "attempt_id": attempt_id, "faculty": mission["faculty"],
            "instrument": f"{mission['faculty']}_query",
            "match_id": mission["match_id"], "agent_id": mission["agent_id"],
            "perspective_id": mission["perspective_id"],
            "timeline_id": mission["timeline_id"], "world_epoch": mission["world_epoch"],
            "world_revision": int(mission["source_world_revision"]),
            "observation_cursor": int(mission["observation_cursor"]),
            "world_snapshot_id": mission.get("world_snapshot_id"),
            "world_view_hash": mission.get("world_view_hash"),
            "expires_unix": float(mission["deadline_unix"]),
        }
        capability_path = root / "capability.json"
        capability_raw = canonical_json(capability)
        capability_path.write_text(capability_raw, encoding="utf-8")
        capability_path.chmod(0o400)
        event_log = root / "mcp-events.jsonl"
        event_log.touch(mode=0o600)
        usage_path = root / "usage.json"
        result_path = root / "result.txt"
        env_path = root / ".env"
        if profile["api_key"]:
            env_path.write_text("SMACX_SPECIALIST_PROVIDER_KEY=" + str(profile["api_key"]) + "\n",
                                encoding="utf-8")
        else:
            env_path.touch()
        env_path.chmod(0o600)

        python = os.environ.get("SMACX_SPECIALIST_PYTHON", sys.executable)
        mcp_script = os.environ.get(
            "SMACX_SPECIALIST_MCP_SCRIPT",
            str(Path(__file__).with_name("smacx_specialist_mcp.py")),
        )
        server_name = f"specialist-{mission['faculty']}"
        child_env = {
            "SMACX_DB_PATH": str(self.store.path),
            "SMACX_WORLD_SNAPSHOT_ROOT": str(self.world_store.root),
            "SMACX_REFERENCE_URL": self.reference_url,
            "SMACX_SPECIALIST_MISSION_ID": str(mission["mission_id"]),
            "SMACX_SPECIALIST_ATTEMPT_ID": attempt_id,
            "SMACX_SPECIALIST_CAPABILITY_FILE": str(capability_path),
            "SMACX_SPECIALIST_CAPABILITY_HASH": hashlib.sha256(
                capability_raw.encode()).hexdigest(),
            "SMACX_SPECIALIST_EVENT_LOG": str(event_log),
        }
        extra_body = openai_extra_body(profile["generation"])
        effective_base_url = (provider_base_url or str(profile["base_url"])).rstrip("/")
        provider = {
            "name": profile["model_id"], "base_url": effective_base_url,
            "model": profile["model_id"], "models": {profile["model_id"]: {}},
            "models_discovered": True,
        }
        if profile["api_key"]:
            provider["key_env"] = "SMACX_SPECIALIST_PROVIDER_KEY"
        if extra_body:
            provider["extra_body"] = extra_body
        config = {
            "_config_version": 39,
            "model": {"default": profile["model_id"], "provider": "custom",
                      "base_url": effective_base_url,
                      "context_length": int(profile["context_length"]),
                      # The mission output budget constrains the validated JSON
                      # returned to the sovereign. Hermes also needs temporary
                      # reasoning/tool-loop output headroom; cumulative provider
                      # tokens and per-request context remain independently hard
                      # metered at the attempt-local provider boundary.
                      "max_tokens": int(request_output_ceiling
                                        or mission["output_token_budget"]),
                      "reasoning_echo": profile["generation"].get(
                          "reasoning_continuity") == "current_episode"},
            "custom_providers": [provider],
            "agent": {"max_turns": int(mission["provider_call_budget"]),
                      "reasoning_effort": profile["reasoning_effort"],
                      "disabled_toolsets": ["terminal", "file", "web", "browser", "memory",
                                            "delegation", "skills", "todo", "code_execution"]},
            "memory": {"memory_enabled": False, "user_profile_enabled": False},
            "auxiliary": {"title_generation": {"enabled": False}},
            "tools": {"tool_search": {"enabled": "off"}},
            "display": {"quiet": True, "show_reasoning": False, "streaming": False},
            "platform_toolsets": {"cli": [server_name]},
            "known_builtin_toolsets": {"cli": []},
            "mcp_servers": {server_name: {
                "command": python, "args": [mcp_script], "env": child_env,
                "enabled": True,
                # A specialist has exactly one model-facing instrument. MCP's
                # optional resource/prompt utility surfaces are deliberately
                # absent rather than merely discouraged in its prompt.
                "tools": {"resources": False, "prompts": False},
            }},
        }
        (root / "config.yaml").write_text(canonical_json(config), encoding="utf-8")
        return {"prompt": prompt_path, "capability": capability_path,
                "events": event_log, "usage": usage_path, "result": result_path,
                "workspace": workspace, "system_prompt_version": Path(prompt_version)}

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _kill(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _trace_rows(self, mission: Mapping[str, Any], attempt_id: str,
                    profile: Mapping[str, Any], files: Mapping[str, Path],
                    process_result: Mapping[str, Any], *,
                    validated_result: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = [{
            "kind": "mission_envelope", "mission": {
                key: mission.get(key) for key in (
                    "mission_id", "faculty", "normalized_objective", "subject_refs_json",
                    "timeline_id", "world_epoch", "source_world_revision",
                    "observation_cursor", "world_snapshot_id", "world_view_hash",
                    "corpus_revision", "execution_class", "tool_budget",
                    "provider_call_budget", "provider_token_budget", "context_token_ceiling",
                    "output_token_budget", "system_prompt_version", "system_prompt_hash",
                    "tool_contract_version", "tool_contract_hash")},
            "profile": {key: profile.get(key) for key in (
                "profile_id", "profile_revision", "provider_id", "model_id",
                "reasoning_effort", "context_length", "generation")},
        }]
        try:
            for line in files["events"].read_text(encoding="utf-8").splitlines():
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
        except (OSError, json.JSONDecodeError):
            rows.append({"kind": "trace_warning", "error": "invalid_mcp_event_log"})
        rows.append({"kind": "attempt_outcome", **dict(process_result)})
        if validated_result is not None:
            rows.append({"kind": "validated_result", "result": dict(validated_result)})
        return rows

    def _run_attempt(self, mission: dict[str, Any], attempt_id: str) -> None:
        service = self._service(mission)
        policy = service._policy()
        capture_warning = self._setting("specialist.trace_warning")
        trace_capture = bool(policy.get("trace_capture", True)) \
            and not isinstance(capture_warning, Mapping)
        profile: dict[str, Any] = {}
        files: dict[str, Path] = {}
        outcome = "provider_failed"
        reason = "specialist attempt did not start"
        trace: dict[str, Any] | None = None
        process_result: dict[str, Any] = {}
        with tempfile.TemporaryDirectory(prefix="smacx-specialist-attempt-") as raw:
            root = Path(raw)
            process: subprocess.Popen[str] | None = None
            proxy: AttemptProviderProxy | None = None
            try:
                profile = self._provider_profile(mission)
                configured_request_output = profile["generation"].get("max_output_tokens")
                request_output_ceiling = min(
                    int(configured_request_output or 16_384),
                    32_768,
                    max(128, int(profile["context_length"]) // 2),
                    int(mission["provider_token_budget"]),
                )
                meter = ProviderLeaseMeter(
                    call_budget=int(mission["provider_call_budget"]),
                    token_budget=int(mission["provider_token_budget"]),
                    context_ceiling=int(mission["context_token_ceiling"]),
                    output_ceiling=request_output_ceiling,
                )
                proxy = AttemptProviderProxy(str(profile["base_url"]), meter)
                proxy.start()
                files = self._attempt_files(
                    root, mission, attempt_id, profile, provider_base_url=proxy.base_url,
                    request_output_ceiling=request_output_ceiling,
                )
                hermes = os.environ.get("SMACX_HERMES_EXECUTABLE", "/opt/hermes/hermes")
                if not Path(hermes).exists():
                    hermes = shutil.which("hermes") or hermes
                hermes_path = Path(hermes)
                # Current Hermes installations may expose either an
                # executable Python entry point or a shell wrapper that
                # activates its private venv.  A basename of ``hermes`` does
                # not identify its language.  Honor executable shebangs and
                # use our interpreter only for a non-executable .py source.
                command = ([sys.executable, hermes]
                           if hermes_path.suffix == ".py"
                           and hermes_path.is_file()
                           and not os.access(hermes_path, os.X_OK)
                           else [hermes])
                command += ["-z", mission_prompt(mission), "--usage-file", str(files["usage"]),
                            "--reasoning", str(profile["reasoning_effort"]),
                            "--toolsets", f"specialist-{mission['faculty']}",
                            "--in", str(files["workspace"]), "--ignore-rules"]
                env = dict(os.environ)
                env.update({
                    "HERMES_HOME": str(root), "SMACX_SPECIALIST_STRICT_PROMPT": "1",
                    "SMACX_STRICT_SYSTEM_PROMPT": "0",
                    "SMACX_SYSTEM_PROMPT_FILE": str(files["prompt"]),
                    "SMACX_SYSTEM_PROMPT_SHA256": str(mission["system_prompt_hash"]),
                    "SMACX_CONTEXT_LENGTH": str(profile["context_length"]),
                })
                process = subprocess.Popen(
                    command, cwd=files["workspace"], env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                )
                with self.children_lock:
                    self.children[attempt_id] = process
                service.heartbeat_attempt(attempt_id, self.owner, process_id=process.pid)
                deadline = min(float(mission["deadline_unix"]), time.time() + 86_400)
                while process.poll() is None:
                    if self.stop_event.wait(0.25):
                        outcome, reason = "cancelled", "installation_shutdown"
                        self._kill(process)
                        break
                    with self.store._connect() as connection:
                        active = connection.execute(
                            "SELECT status FROM specialist_missions WHERE mission_id=?",
                            (mission["mission_id"],),
                        ).fetchone()
                    if not active or active["status"] != "active":
                        outcome, reason = "cancelled", "cancelled_by_parent"
                        self._kill(process)
                        break
                    if time.time() >= deadline:
                        outcome, reason = "timed_out", "specialist_wall_time_exhausted"
                        self._kill(process)
                        break
                    lease = meter.snapshot()
                    if lease.violation:
                        outcome, reason = "token_budget_exhausted", lease.violation
                        self._kill(process)
                        break
                    service.heartbeat_attempt(attempt_id, self.owner, process_id=process.pid)
                stdout, stderr = process.communicate(timeout=5)
                files["result"].write_text(stdout, encoding="utf-8")
                usage = meter.snapshot().as_dict()
                hermes_usage = self._read_json(files["usage"])
                if (not int(usage.get("provider_calls") or 0)
                        and os.environ.get("SMACX_SPECIALIST_TEST_USAGE_FALLBACK") == "1"):
                    # Deterministic lifecycle fixtures replace Hermes with a
                    # local process that never reaches the provider boundary.
                    # Production attempts never trust a child-authored usage
                    # file in place of the wire meter.
                    usage.update({
                        "api_calls": int(hermes_usage.get("api_calls") or 0),
                        "provider_calls": int(hermes_usage.get("api_calls") or 0),
                        "total_tokens": int(hermes_usage.get("total_tokens") or 0),
                        "provider_tokens": int(hermes_usage.get("total_tokens") or 0),
                        "peak_context_tokens": int(
                            hermes_usage.get("peak_context_tokens") or 0),
                        "usage_source": "test_process_fixture",
                    })
                usage["hermes_reported_usage"] = hermes_usage
                process_result = {"returncode": process.returncode,
                                  "stdout": stdout, "stderr": stderr, "usage": usage,
                                  "provider_exchanges": proxy.trace_exchanges()}
                if meter.snapshot().violation:
                    outcome, reason = "token_budget_exhausted", str(
                        meter.snapshot().violation)
                elif outcome in {"cancelled", "timed_out"}:
                    pass
                elif process.returncode != 0:
                    lowered = stderr.casefold()
                    if "tool_budget" in lowered:
                        outcome, reason = "tool_budget_exhausted", "specialist_tool_budget_exhausted"
                    elif "mcp" in lowered or "specialist_" in lowered:
                        outcome, reason = "mcp_failed", stderr[-1000:] or "specialist_mcp_failed"
                    else:
                        outcome, reason = "provider_failed", stderr[-1000:] or "specialist_provider_failed"
                else:
                    provider_calls = int(usage.get("api_calls") or usage.get("provider_calls") or 0)
                    provider_tokens = int(usage.get("total_tokens") or usage.get("provider_tokens") or 0)
                    if provider_calls > int(mission["provider_call_budget"]):
                        outcome, reason = "token_budget_exhausted", "provider_call_budget_exhausted"
                    elif provider_tokens > int(mission["provider_token_budget"]):
                        outcome, reason = "token_budget_exhausted", "provider_token_budget_exhausted"
                    else:
                        try:
                            result = extract_result(stdout)
                            service.mark_validating(attempt_id, self.owner)
                            if trace_capture:
                                trace = self.trace_store.write(
                                    mission, attempt_id,
                                    self._trace_rows(
                                        mission, attempt_id, profile, files, process_result,
                                        validated_result=result,
                                    ),
                                    outcome="completed", generation=self.store.checkpoint_generation(
                                        str(mission["match_id"])),
                                )
                            service.accept_attempt(
                                str(mission["mission_id"]), attempt_id, result,
                                usage=usage, trace=trace,
                                current_corpus_revision=(
                                    self._reference_revision()
                                    if mission["faculty"] == "reference" else None),
                            )
                            if trace_capture:
                                self.trace_store.gc(
                                    self.store,
                                    success_generations=int(
                                        policy["trace_success_generations"]),
                                    failed_generations=int(
                                        policy["trace_failed_generations"]),
                                    byte_ceiling=int(policy["trace_byte_ceiling"]),
                                    high_retention=bool(policy["trace_high_retention"]),
                                )
                            return
                        except SpecialistError as exc:
                            failure = str(exc)
                            if failure == "specialist_output_budget_exhausted":
                                outcome = "token_budget_exhausted"
                            elif failure.startswith((
                                    "invalid_specialist", "specialist_returned",
                                    "specialist_claim_uses_unretrieved_evidence")):
                                outcome = "invalid_schema"
                            else:
                                outcome = "provider_failed"
                            reason = str(exc)
                if files and trace_capture:
                    trace = self.trace_store.write(
                        mission, attempt_id,
                        self._trace_rows(mission, attempt_id, profile, files, process_result),
                        outcome=outcome, generation=self.store.checkpoint_generation(
                            str(mission["match_id"])),
                    )
            except SpecialistError as exc:
                reason = str(exc)
            except Exception as exc:  # supervisor boundary: durable typed failure
                reason = f"{type(exc).__name__}:{exc}"[:1000]
            finally:
                if process is not None:
                    self._kill(process)
                if proxy is not None:
                    proxy.close()
                with self.children_lock:
                    self.children.pop(attempt_id, None)
        try:
            result = service.fail_attempt(
                str(mission["mission_id"]), attempt_id, outcome, reason,
                allow_retry=not self.stop_event.is_set(), schema_repair=True,
                usage=(process_result.get("usage")
                       if isinstance(process_result.get("usage"), Mapping) else {}),
            )
            if result["status"] == "failed" and mission.get("world_snapshot_id"):
                self.world_store.unpin_snapshot(
                    str(mission["world_snapshot_id"]), "specialist_mission",
                    str(mission["mission_id"]),
                )
        except SpecialistError:
            pass
        finally:
            # Cancellation can win the mission CAS while the process is being
            # reaped. Its diagnostic trace still belongs to the attempt even
            # though no late outcome is allowed to rewrite durable mission
            # state or become model-visible.
            if trace:
                try:
                    service.record_trace(str(mission["mission_id"]), attempt_id, trace)
                except SpecialistError:
                    pass
            if trace_capture:
                self.trace_store.gc(
                    self.store,
                    success_generations=int(policy["trace_success_generations"]),
                    failed_generations=int(policy["trace_failed_generations"]),
                    byte_ceiling=int(policy["trace_byte_ceiling"]),
                    high_retention=bool(policy["trace_high_retention"]),
                )

    def _available(self) -> tuple[int, list[dict[str, Any]]]:
        policy = self._policy_service()._policy()
        cap = int(policy["installation_concurrency"])
        with self.store._connect() as connection:
            expired = [dict(row) for row in connection.execute(
                "SELECT * FROM specialist_missions WHERE status IN ('queued','retry_wait') "
                "AND deadline_unix<=?", (time.time(),),
            ).fetchall()]
        for mission in expired:
            self._service(mission).expire(str(mission["mission_id"]))
        with self.store.transaction() as connection:
            active = int(connection.execute(
                "SELECT COUNT(*) FROM specialist_missions WHERE status='active'"
            ).fetchone()[0])
            seat_active = {
                f"{row['match_id']}:{row['perspective_id']}": int(row["count"])
                for row in connection.execute(
                    "SELECT match_id,perspective_id,COUNT(*) AS count FROM specialist_missions "
                    "WHERE status='active' GROUP BY match_id,perspective_id"
                )
            }
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM specialist_missions WHERE status IN ('queued','retry_wait') "
                "AND deadline_unix>? ORDER BY created_unix", (time.time(),),
            ).fetchall()]
        # Round-robin seat ordering prevents one prolific player from starving
        # all others while preserving FIFO within a seat.
        seats: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            seat = f"{row['match_id']}:{row['perspective_id']}"
            if seat_active.get(seat, 0) < int(policy["seat_concurrency"]):
                seats.setdefault(seat, []).append(row)
        ordered_seats = sorted(seats)
        if ordered_seats and self._last_seat in ordered_seats:
            pivot = ordered_seats.index(self._last_seat) + 1
            ordered_seats = ordered_seats[pivot:] + ordered_seats[:pivot]
        fair_rows: list[dict[str, Any]] = []
        remaining_slots = {
            seat: max(0, int(policy["seat_concurrency"]) - seat_active.get(seat, 0))
            for seat in ordered_seats
        }
        while any(seats.get(seat) and remaining_slots[seat] > 0
                  for seat in ordered_seats):
            for seat in ordered_seats:
                if seats.get(seat) and remaining_slots[seat] > 0:
                    fair_rows.append(seats[seat].pop(0))
                    remaining_slots[seat] -= 1
        return max(0, cap - active), fair_rows

    def _policy_service(self) -> SpecialistService:
        with self.store._connect() as connection:
            row = connection.execute(
                "SELECT match_id,agent_id,perspective_id FROM perspectives LIMIT 1"
            ).fetchone()
        if not row:
            # _policy does not inspect scope, so a syntactically valid placeholder
            # is safe and avoids a second policy implementation.
            scope = MemoryScope("install-policy", "install-policy", "install-policy")
        else:
            scope = MemoryScope(row["match_id"], row["agent_id"], row["perspective_id"])
        return SpecialistService(self.store, self.world_store, scope)

    def run(self, *, once: bool = False) -> int:
        self.reconcile()
        while not self.stop_event.is_set():
            self._publish_health()
            self.futures = {key: future for key, future in self.futures.items()
                            if not future.done()}
            slots, rows = self._available()
            for mission in rows[:slots]:
                service = self._service(mission)
                try:
                    attempt = service.begin_attempt(str(mission["mission_id"]), self.owner)
                except SpecialistError:
                    continue
                attempt_id = str(attempt["attempt_id"])
                self._last_seat = f"{mission['match_id']}:{mission['perspective_id']}"
                self.futures[attempt_id] = self.executor.submit(
                    self._run_attempt, attempt, attempt_id,
                )
            if once:
                for future in list(self.futures.values()):
                    future.result()
                return 0
            self.stop_event.wait(self.poll_seconds)
        return 0

    def shutdown(self) -> None:
        self.stop_event.set()
        with self.children_lock:
            children = list(self.children.values())
        for process in children:
            self._kill(process)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._publish_health("stopped")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smacx-specialist-supervisor")
    parser.add_argument("--database", default=os.environ.get(
        "SMACX_DB_PATH", "/var/lib/smacx/smacx.sqlite3"))
    parser.add_argument("--secret-root", default=os.environ.get(
        "SMACX_SECRET_ROOT", "/var/lib/smacx/secrets"))
    parser.add_argument("--snapshot-root", default=os.environ.get(
        "SMACX_WORLD_SNAPSHOT_ROOT", "/var/lib/smacx/world-snapshots"))
    parser.add_argument("--trace-root", default=os.environ.get(
        "SMACX_SPECIALIST_TRACE_ROOT", "/var/lib/smacx/specialist-traces"))
    parser.add_argument("--reference-url", default=os.environ.get(
        "SMACX_REFERENCE_URL", "http://knowledge-service:8090"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    supervisor = SpecialistSupervisor(
        database=Path(args.database), secret_root=Path(args.secret_root),
        snapshot_root=Path(args.snapshot_root), trace_root=Path(args.trace_root),
        reference_url=args.reference_url,
    )

    def stop(_signum: int, _frame: Any) -> None:
        supervisor.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return supervisor.run(once=args.once)
    finally:
        supervisor.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
