#!/usr/bin/env python3
"""Probe OpenAI-compatible tool-call output without Hermes or a game worker.

The historical SMACX endurance run produced six JSON-valid but schema-invalid
deferred calls.  This utility sends equivalent calls directly to a provider,
captures the raw Chat Completions wire response, assembles streamed argument
fragments, and verifies that every nested argument survived.

No MCP server, Hermes process, game process, or portal is started.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BRIDGE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "tool_search",
            "description": "Search additional tools loaded on demand.",
            "parameters": {
                "type": "object",
                "properties": {
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer"},
                },
                "required": ["queries"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_describe",
            "description": "Load full JSON schemas for deferred tools.",
            "parameters": {
                "type": "object",
                "properties": {
                    "names": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["names"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tool_call",
            "description": (
                "Invoke a deferred tool by name with the given arguments. "
                "The nested argument shape must match that tool's schema."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                "required": ["name", "arguments"],
            },
        },
    },
]


@dataclass(frozen=True)
class ExpectedCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ProbeCase:
    case_id: str
    description: str
    calls: tuple[ExpectedCall, ...]


_MATCH = "match-provider-wire-probe"
_SESSION = "session-provider-wire-probe"
_REVISION = "16893526205145507771"

CASES: dict[str, ProbeCase] = {
    "command_full": ProbeCase(
        "command_full",
        "Historical empty smac_command call",
        (ExpectedCall("mcp__smacx__smac_command", {
            "command": "acknowledge_popup",
            "match_id": _MATCH,
            "session_id": _SESSION,
            "expected_revision": _REVISION,
        }),),
    ),
    "command_guard": ProbeCase(
        "command_guard",
        "Historical command that retained command/revision but lost match/session",
        (ExpectedCall("mcp__smacx__smac_command", {
            "command": "acknowledge_popup",
            "expected_revision": _REVISION,
            "match_id": _MATCH,
            "session_id": _SESSION,
        }),),
    ),
    "lan_status": ProbeCase(
        "lan_status",
        "Historical empty smac_lan call",
        (ExpectedCall("mcp__smacx__smac_lan", {"action": "status"}),),
    ),
    "list_chat_parallel": ProbeCase(
        "list_chat_parallel",
        "Historical parallel smac_list and smac_chat calls with empty arguments",
        (
            ExpectedCall("mcp__smacx__smac_list", {"kind": "factions"}),
            ExpectedCall("mcp__smacx__smac_chat", {"action": "list"}),
        ),
    ),
    "memory_search": ProbeCase(
        "memory_search",
        "Historical smac_memory call that used invented fields and lost action/match_id",
        (ExpectedCall("mcp__smacx__smac_memory", {
            "action": "search",
            "match_id": _MATCH,
            "query": "match overview",
        }),),
    ),
}


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def build_request(
    case: ProbeCase,
    *,
    model: str,
    stream: bool,
    max_tokens: int,
    preserve_thinking: bool,
    seed: int | None,
) -> dict[str, Any]:
    rendered = [
        {"name": call.name, "arguments": call.arguments}
        for call in case.calls
    ]
    plural = "calls" if len(rendered) != 1 else "call"
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a provider tool-call wire conformance probe. "
                    "Follow the requested tool calls exactly. Do not omit, rename, "
                    "summarize, or infer any argument. Emit no prose."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Emit exactly {len(rendered)} tool_call {plural} in the listed "
                    "order. The outer function is tool_call. Its JSON arguments must "
                    "contain name and arguments exactly as supplied here:\n"
                    f"{json.dumps(rendered, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "tools": BRIDGE_TOOLS,
        "tool_choice": {"type": "function", "function": {"name": "tool_call"}},
        "parallel_tool_calls": True,
        "stream": stream,
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 0.0,
        "repetition_penalty": 1.0,
        "reasoning_effort": "low",
        "chat_template_kwargs": {
            "enable_thinking": True,
            "preserve_thinking": preserve_thinking,
        },
        "max_tokens": max_tokens,
    }
    if seed is not None:
        body["seed"] = seed
    return body


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/chat/completions"


def _request_headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _stream_events(response: Any, capture: Any | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if capture is not None:
            capture.write(_compact({"kind": "wire_line", "line": line}) + "\n")
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            events.append({"_wire_json_error": str(exc), "_wire_data": data})
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            events.append({"_wire_shape_error": "SSE data was not an object", "value": event})
    return events


def request_provider(
    url: str,
    body: dict[str, Any],
    *,
    api_key: str | None,
    timeout: float,
    capture: Any | None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    encoded = _compact(body).encode("utf-8")
    request = Request(url, data=encoded, headers=_request_headers(api_key), method="POST")
    if capture is not None:
        capture.write(_compact({"kind": "request", "url": url, "body": body}) + "\n")
    try:
        with urlopen(request, timeout=timeout) as response:
            if body.get("stream"):
                return _stream_events(response, capture), None
            raw = response.read().decode("utf-8", errors="replace")
            if capture is not None:
                capture.write(_compact({"kind": "wire_body", "body": raw}) + "\n")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("provider JSON body was not an object")
            return [], parsed
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:8000]
        raise RuntimeError(f"provider HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"provider connection failed: {exc.reason}") from exc


def assemble_stream_tool_calls(events: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    slots: dict[tuple[int, int], dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for event_number, event in enumerate(events):
        if "_wire_json_error" in event or "_wire_shape_error" in event:
            issues.append({"kind": "wire_json_error", "event": event_number, "detail": event})
            continue
        choices = event.get("choices")
        if not isinstance(choices, list):
            # Usage-only chunks are valid.
            if "usage" not in event:
                issues.append({"kind": "wire_shape_error", "event": event_number, "detail": "missing choices"})
            continue
        for choice_position, choice in enumerate(choices):
            if not isinstance(choice, dict):
                issues.append({"kind": "wire_shape_error", "event": event_number, "detail": "choice not object"})
                continue
            choice_index = choice.get("index", choice_position)
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and finish_reason != "tool_calls":
                issues.append({
                    "kind": "provider_wire_finish_reason_failure",
                    "event": event_number,
                    "detail": (
                        f"provider finished with {finish_reason!r} after emitting "
                        "a forced tool call; expected 'tool_calls'"
                    ),
                })
            delta = choice.get("delta") or {}
            tool_calls = delta.get("tool_calls") if isinstance(delta, dict) else None
            if tool_calls is None:
                continue
            if not isinstance(tool_calls, list):
                issues.append({"kind": "wire_shape_error", "event": event_number, "detail": "delta.tool_calls not array"})
                continue
            for call_position, fragment in enumerate(tool_calls):
                if not isinstance(fragment, dict):
                    issues.append({"kind": "wire_shape_error", "event": event_number, "detail": "tool-call fragment not object"})
                    continue
                call_index = fragment.get("index", call_position)
                slot = slots.setdefault((int(choice_index), int(call_index)), {
                    "id": "", "type": "function", "function": {"name": "", "arguments": ""},
                })
                if fragment.get("id"):
                    slot["id"] += str(fragment["id"])
                if fragment.get("type"):
                    slot["type"] = fragment["type"]
                function = fragment.get("function") or {}
                if not isinstance(function, dict):
                    issues.append({"kind": "wire_shape_error", "event": event_number, "detail": "function fragment not object"})
                    continue
                if function.get("name") is not None:
                    slot["function"]["name"] += str(function["name"])
                if function.get("arguments") is not None:
                    if not isinstance(function["arguments"], str):
                        issues.append({
                            "kind": "wire_shape_error", "event": event_number,
                            "detail": "streamed function.arguments fragment was not a string",
                        })
                    else:
                        slot["function"]["arguments"] += function["arguments"]
    return [slots[key] for key in sorted(slots)], issues


def nonstream_tool_calls(response: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return [], [{"kind": "wire_shape_error", "detail": "response has no choices"}]
    finish_reason = choices[0].get("finish_reason") if isinstance(choices[0], dict) else None
    if finish_reason != "tool_calls":
        issues.append({
            "kind": "provider_wire_finish_reason_failure",
            "detail": (
                f"provider finished with {finish_reason!r} after emitting a forced "
                "tool call; expected 'tool_calls'"
            ),
        })
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list):
        issues.append({"kind": "wire_shape_error", "detail": "message.tool_calls is absent or not an array"})
        return [], issues
    return calls, issues


def validate_calls(case: ProbeCase, calls: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    if len(calls) != len(case.calls):
        issues.append({
            "kind": "provider_wire_schema_failure",
            "detail": f"expected {len(case.calls)} tool call(s), received {len(calls)}",
        })
    observed: list[dict[str, Any]] = []
    for index, expected in enumerate(case.calls):
        if index >= len(calls):
            break
        call = calls[index]
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            issues.append({"kind": "wire_shape_error", "call": index, "detail": "function missing"})
            continue
        outer_name = function.get("name")
        raw_arguments = function.get("arguments")
        parsed: Any = raw_arguments
        if isinstance(raw_arguments, str):
            try:
                parsed = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                issues.append({
                    "kind": "provider_wire_syntax_failure", "call": index,
                    "detail": str(exc), "raw_arguments": raw_arguments,
                })
                continue
        if not isinstance(parsed, dict):
            issues.append({
                "kind": "provider_wire_schema_failure", "call": index,
                "detail": "outer function arguments are not an object", "observed": parsed,
            })
            continue
        nested_name = parsed.get("name")
        nested_arguments = parsed.get("arguments")
        outer_extras = {
            key: value for key, value in parsed.items()
            if key not in {"name", "arguments"}
        }
        observed.append({
            "outer_function": outer_name,
            "name": nested_name,
            "arguments": nested_arguments,
            "outer_extra_arguments": outer_extras,
        })
        if outer_name != "tool_call":
            issues.append({"kind": "provider_wire_schema_failure", "call": index, "detail": f"outer function was {outer_name!r}"})
        if nested_name != expected.name:
            issues.append({
                "kind": "provider_wire_schema_failure", "call": index,
                "detail": f"expected nested name {expected.name!r}, received {nested_name!r}",
            })
        if not isinstance(nested_arguments, dict):
            flattened = {
                key: outer_extras[key]
                for key in expected.arguments
                if key in outer_extras
            }
            issues.append({
                "kind": (
                    "provider_wire_nested_object_flattening"
                    if flattened else "provider_wire_schema_failure"
                ),
                "call": index,
                "detail": (
                    "provider replaced nested arguments with a scalar and moved "
                    "the underlying tool fields into the outer tool_call object"
                    if flattened else "nested arguments are absent or not an object"
                ),
                "flattened_fields": flattened,
            })
            continue
        missing = [key for key in expected.arguments if key not in nested_arguments]
        changed = {
            key: {"expected": value, "observed": nested_arguments.get(key)}
            for key, value in expected.arguments.items()
            if key in nested_arguments and nested_arguments[key] != value
        }
        if missing or changed:
            issues.append({
                "kind": "provider_wire_schema_failure", "call": index,
                "detail": "nested arguments were lost or changed",
                "missing": missing, "changed": changed,
            })
    provider_failure = any(issue["kind"].startswith("provider_wire_") for issue in issues)
    return {
        "case": case.case_id,
        "description": case.description,
        "passed": not issues,
        "provider_side_reproduction": provider_failure,
        "observed": observed,
        "issues": issues,
    }


def run_probe(arguments: argparse.Namespace) -> int:
    selected = list(CASES) if arguments.case == "all" else [arguments.case]
    url = _chat_completions_url(arguments.base_url)
    api_key = os.environ.get(arguments.api_key_env) if arguments.api_key_env else None
    capture = None
    if arguments.capture:
        capture_path = Path(arguments.capture).expanduser().resolve()
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture = capture_path.open("a", encoding="utf-8")
    results: list[dict[str, Any]] = []
    try:
        for run_number in range(1, arguments.runs + 1):
            for case_id in selected:
                case = CASES[case_id]
                body = build_request(
                    case,
                    model=arguments.model,
                    stream=arguments.stream,
                    max_tokens=arguments.max_tokens,
                    preserve_thinking=arguments.preserve_thinking,
                    seed=arguments.seed,
                )
                started = time.monotonic()
                try:
                    events, response = request_provider(
                        url, body, api_key=api_key, timeout=arguments.timeout,
                        capture=capture,
                    )
                    if arguments.stream:
                        calls, issues = assemble_stream_tool_calls(events)
                    else:
                        calls, issues = nonstream_tool_calls(response or {})
                    result = validate_calls(case, calls, issues)
                except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                    result = {
                        "case": case.case_id,
                        "description": case.description,
                        "passed": False,
                        "provider_side_reproduction": False,
                        "observed": [],
                        "issues": [{"kind": "request_failure", "detail": str(exc)}],
                    }
                result["run"] = run_number
                result["stream"] = arguments.stream
                result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                results.append(result)
                print(_compact(result), flush=True)
                if capture is not None:
                    capture.write(_compact({"kind": "result", "result": result}) + "\n")
                    capture.flush()
    finally:
        if capture is not None:
            capture.close()
    summary = {
        "event": "provider_tool_call_probe_summary",
        "requests": len(results),
        "passed": sum(1 for result in results if result["passed"]),
        "failed": sum(1 for result in results if not result["passed"]),
        "provider_side_reproductions": sum(
            1 for result in results if result["provider_side_reproduction"]
        ),
        "interpretation": (
            "Any provider_side_reproduction is present in the raw provider response "
            "before Hermes, MCP, or the game can modify it. A clean run does not prove "
            "the historical client path was correct; that old run did not retain raw SSE."
        ),
    }
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed"] else 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1"))
    result.add_argument("--model", default=os.environ.get("OPENAI_MODEL", ""), required=not bool(os.environ.get("OPENAI_MODEL")))
    result.add_argument("--api-key-env", default="OPENAI_API_KEY", help="Environment variable containing an optional API key; its value is never logged")
    result.add_argument("--case", choices=["all", *CASES], default="all")
    result.add_argument("--runs", type=int, default=1)
    result.add_argument("--max-tokens", type=int, default=1024)
    result.add_argument("--timeout", type=float, default=120.0)
    result.add_argument("--seed", type=int)
    result.add_argument("--capture", help="Append requests, raw wire lines, and results to a JSONL file")
    result.add_argument("--stream", action=argparse.BooleanOptionalAction, default=True)
    result.add_argument(
        "--preserve-thinking", action=argparse.BooleanOptionalAction, default=True,
        help="Default true to reproduce the historical run; current SMACX profiles use false",
    )
    return result


def main() -> int:
    arguments = parser().parse_args()
    if arguments.runs < 1:
        raise SystemExit("--runs must be at least 1")
    if arguments.max_tokens < 64:
        raise SystemExit("--max-tokens must be at least 64")
    return run_probe(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
