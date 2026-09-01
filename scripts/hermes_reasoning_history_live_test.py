#!/usr/bin/env python3
"""Live Hermes/provider validation for reasoning effort and thinking history.

Required environment:
  SMACX_TEST_PROVIDER_BASE_URL, SMACX_TEST_PROVIDER_MODEL
Optional:
  SMACX_TEST_PROVIDER_API_KEY, SMACX_HERMES_IMAGE

The test uses a local recording proxy. It never prints the API key, prompts,
responses, or raw reasoning; only token/character counts and pass/fail facts.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from smacx_hermes import configure_profile


IMAGE = os.environ.get("SMACX_HERMES_IMAGE", "smacx-agent-harness:dev")
BASE_URL = os.environ.get("SMACX_TEST_PROVIDER_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("SMACX_TEST_PROVIDER_MODEL", "")
API_KEY = os.environ.get("SMACX_TEST_PROVIDER_API_KEY", "")


def _response_metrics(body: bytes, content_type: str) -> dict[str, int]:
    events: list[dict[str, Any]] = []
    if "text/event-stream" in content_type:
        for line in body.decode("utf-8", "replace").splitlines():
            if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                continue
            try:
                events.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue
    else:
        try:
            events = [json.loads(body)]
        except json.JSONDecodeError:
            events = []
    reasoning_chars = content_chars = 0
    usage: dict[str, Any] = {}
    for event in events:
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        for choice in event.get("choices") or []:
            message = choice.get("delta") or choice.get("message") or {}
            reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
            content = message.get("content") or ""
            if isinstance(reasoning, str):
                reasoning_chars += len(reasoning)
            if isinstance(content, str):
                content_chars += len(content)
    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens")
    if not isinstance(reasoning_tokens, int):
        reasoning_tokens = 0
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": reasoning_tokens,
        "reasoning_chars": reasoning_chars,
        "content_chars": content_chars,
    }


class Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _forward(self, method: str) -> None:
                incoming = self.rfile.read(int(self.headers.get("Content-Length", "0"))) \
                    if method == "POST" else None
                request_json = json.loads(incoming) if incoming else None
                suffix = self.path
                if suffix.startswith("/v1"):
                    suffix = suffix[3:]
                target = BASE_URL + suffix
                headers = {"Accept": self.headers.get("Accept", "application/json")}
                if incoming is not None:
                    headers["Content-Type"] = "application/json"
                if API_KEY:
                    headers["Authorization"] = "Bearer " + API_KEY
                upstream = Request(target, data=incoming, headers=headers, method=method)
                try:
                    with urlopen(upstream, timeout=300) as response:
                        status = response.status
                        content_type = response.headers.get("Content-Type", "application/json")
                        data = response.read()
                except HTTPError as exc:
                    status = exc.code
                    content_type = exc.headers.get("Content-Type", "application/json")
                    data = exc.read()
                if request_json is not None:
                    recorder.calls.append({
                        "request": request_json,
                        "status": status,
                        "metrics": _response_metrics(data, content_type),
                    })
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                self._forward("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._forward("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "Recorder":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def generation(preserve: bool) -> dict[str, Any]:
    return {
        "preset": "qwen38-low", "temperature": 1.0, "top_p": 0.95,
        "presence_penalty": 0.0, "max_output_tokens": 2048, "seed": 4242,
        "top_k": 20, "min_p": 0.0, "repetition_penalty": 1.0,
        "extra_parameters": {"chat_template_kwargs": {
            "enable_thinking": True, "preserve_thinking": preserve,
        }},
    }


def configure(root: Path, proxy_url: str, profile_id: str, preserve: bool) -> None:
    profile = configure_profile(
        hermes_root=root, runtime_hermes_root=Path("/opt/data"),
        agent_id="agent-reasoning-history-live", agent_name="Reasoning History Test",
        match_id="match-reasoning-history-live", mcp_url="http://127.0.0.1:9/mcp",
        provider_base_url=proxy_url, model_id=MODEL, profile_id=profile_id,
        reasoning_effort="low", system_prompt="You are a contained reasoning transport test.",
        generation_settings=generation(preserve),
    )
    config_path = root / "profiles" / profile["profile_id"] / "config.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["platform_toolsets"]["cli"] = []
    config["mcp_servers"] = {}
    config["display"]["streaming"] = False
    config_path.write_text(json.dumps(config), encoding="utf-8")


def set_preserve(root: Path, profile_id: str, preserve: bool) -> None:
    path = root / "profiles" / profile_id / "config.yaml"
    config = json.loads(path.read_text(encoding="utf-8"))
    providers = config["custom_providers"]
    provider = providers[0]
    provider["extra_body"]["chat_template_kwargs"]["preserve_thinking"] = preserve
    path.write_text(json.dumps(config), encoding="utf-8")


def run(root: Path, profile_id: str, reasoning: str, query: str) -> None:
    command = [
        "docker", "run", "--rm", "--network", "host",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{root}:/opt/data", "-e", "HOME=/opt/data", "-e", "HERMES_HOME=/opt/data",
        "--entrypoint", "/opt/hermes/.venv/bin/hermes", IMAGE,
        "-p", profile_id, "chat", "--continue", "match-reasoning-history-live",
        "--create-if-missing", "--in",
        f"/opt/data/profiles/{profile_id}/workspace/matches/match-reasoning-history-live",
        "--reasoning", reasoning, "--max-turns", "1", "--query", query, "--quiet",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=420, check=False)
    if completed.returncode != 0:
        raise AssertionError(
            f"Hermes live request failed ({completed.returncode}); "
            f"stdout_tail={completed.stdout[-500:]!r}; stderr_tail={completed.stderr[-500:]!r}"
        )


def completion_for_query(recorder: Recorder, before: int, query: str) -> dict[str, Any]:
    def contains_query(call: dict[str, Any]) -> bool:
        messages = call["request"].get("messages") or []
        return bool(messages and messages[-1].get("role") == "user" and
                    messages[-1].get("content") == query)
    calls = [call for call in recorder.calls[before:] if contains_query(call)]
    if not calls or calls[-1]["status"] >= 300:
        raise AssertionError(f"No accepted primary completion after call {before}")
    return calls[-1]


def history_shape(request: dict[str, Any]) -> dict[str, int]:
    assistant_messages = 0
    reasoning_chars = content_chars = think_markers = 0
    for message in request.get("messages") or []:
        if message.get("role") != "assistant":
            continue
        assistant_messages += 1
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        content = message.get("content") or ""
        if isinstance(reasoning, str):
            reasoning_chars += len(reasoning)
        if isinstance(content, str):
            content_chars += len(content)
            think_markers += content.count("<think>") + content.count("</think>")
    return {
        "assistant_messages": assistant_messages,
        "reasoning_chars": reasoning_chars,
        "content_chars": content_chars,
        "think_markers": think_markers,
    }


def direct_preserve_probe(recorder: Recorder, proxy_url: str, preserve: bool) -> dict[str, Any]:
    synthetic_reasoning = "historical-reasoning-token " * 1200
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Contained chat-template history test."},
            {"role": "user", "content": "Solve a prior problem."},
            {"role": "assistant", "reasoning_content": synthetic_reasoning, "content": "Prior answer."},
            {"role": "user", "content": "Reply with OK."},
        ],
        "stream": False, "max_tokens": 8, "reasoning_effort": "low",
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": preserve},
    }
    before = len(recorder.calls)
    request = Request(
        proxy_url + "/chat/completions",
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urlopen(request, timeout=300) as response:
        response.read()
    calls = recorder.calls[before:]
    if len(calls) != 1 or calls[0]["status"] >= 300:
        raise AssertionError("The direct preserved-thinking probe was not accepted")
    return calls[0]


def main() -> int:
    if not BASE_URL or not MODEL:
        raise SystemExit("Set SMACX_TEST_PROVIDER_BASE_URL and SMACX_TEST_PROVIDER_MODEL")
    with Recorder() as recorder, tempfile.TemporaryDirectory(prefix="smacx-history-live-") as temporary:
        base = Path(temporary)
        proxy_url = f"http://127.0.0.1:{recorder.server.server_port}/v1"
        seed = base / "seed"
        profile_id = "smacx-reasoning-history-live"
        configure(seed, proxy_url, profile_id, False)
        before = len(recorder.calls)
        first_query = "Reason carefully and show a concise derivation: compute 37^41 modulo 1009."
        run(seed, profile_id, "low", first_query)
        first = completion_for_query(recorder, before, first_query)
        historical_reasoning = first["metrics"]["reasoning_chars"]
        if historical_reasoning <= 0:
            raise AssertionError("The live provider returned no observable reasoning content")

        false_root = base / "preserve-false"
        true_root = base / "preserve-true"
        shutil.copytree(seed, false_root)
        shutil.copytree(seed, true_root)
        set_preserve(false_root, profile_id, False)
        set_preserve(true_root, profile_id, True)
        second_query = "Answer with exactly one word: what color is a clear daytime sky?"
        before = len(recorder.calls)
        run(false_root, profile_id, "low", second_query)
        preserve_false = completion_for_query(recorder, before, second_query)
        before = len(recorder.calls)
        run(true_root, profile_id, "low", second_query)
        preserve_true = completion_for_query(recorder, before, second_query)
        false_request = preserve_false["request"]
        true_request = preserve_true["request"]
        false_history = history_shape(false_request)
        true_history = history_shape(true_request)
        if false_request.get("chat_template_kwargs", {}).get("preserve_thinking") is not False:
            raise AssertionError("preserve_thinking=false did not reach the provider")
        if true_request.get("chat_template_kwargs", {}).get("preserve_thinking") is not True:
            raise AssertionError("preserve_thinking=true did not reach the provider")
        false_prompt = preserve_false["metrics"]["prompt_tokens"]
        true_prompt = preserve_true["metrics"]["prompt_tokens"]
        if false_prompt <= 0 or true_prompt <= 0:
            raise AssertionError("Hermes history requests did not report prompt usage")
        if false_history["reasoning_chars"] != 0 or false_history["think_markers"] != 0:
            raise AssertionError("Completed historical reasoning leaked through Hermes with preservation disabled")

        direct_false = direct_preserve_probe(recorder, proxy_url, False)
        direct_true = direct_preserve_probe(recorder, proxy_url, True)
        direct_false_prompt = direct_false["metrics"]["prompt_tokens"]
        direct_true_prompt = direct_true["metrics"]["prompt_tokens"]
        if direct_true_prompt < direct_false_prompt + 500:
            raise AssertionError(
                f"The provider did not measurably honor synthetic preserved thinking: "
                f"false={direct_false_prompt}, true={direct_true_prompt}"
            )

        effort_metrics: dict[str, list[dict[str, int]]] = {"low": [], "xhigh": []}
        effort_prompts = [
            "Solve carefully and give a compact proof: compute 83^57 modulo 2027.",
            "Using each of 2, 3, 7, 8 exactly once with arithmetic operations, construct 24 or prove it impossible.",
            "Give a compact correctness argument for finding the median of two sorted arrays in logarithmic time.",
        ]
        for effort in ("low", "xhigh"):
            for index, effort_prompt in enumerate(effort_prompts):
                effort_root = base / f"effort-{effort}-{index}"
                effort_profile = f"smacx-reasoning-effort-{effort}-{index}"
                configure(effort_root, proxy_url, effort_profile, False)
                before = len(recorder.calls)
                run(effort_root, effort_profile, effort, effort_prompt)
                call = completion_for_query(recorder, before, effort_prompt)
                request = call["request"]
                nested = request.get("reasoning")
                observed = request.get("reasoning_effort") or (
                    nested.get("effort") if isinstance(nested, dict) else None
                )
                if observed != effort:
                    raise AssertionError(f"Requested {effort}, provider received {observed!r}")
                effort_metrics[effort].append(call["metrics"])
        def measure(metrics: dict[str, int]) -> int:
            return metrics["reasoning_tokens"] or metrics["reasoning_chars"]
        low_measure = sum(measure(item) for item in effort_metrics["low"])
        xhigh_measure = sum(measure(item) for item in effort_metrics["xhigh"])
        if xhigh_measure < low_measure * 1.2:
            raise AssertionError(
                f"XHigh was not meaningfully larger across fixed-seed prompts: "
                f"low={low_measure}, xhigh={xhigh_measure}"
            )

        print(json.dumps({"event": "pass", "payload": {
            "preserve_false_prompt_tokens": false_prompt,
            "preserve_true_prompt_tokens": true_prompt,
            "historical_reasoning_chars": historical_reasoning,
            "hermes_history_reasoning_chars": false_history["reasoning_chars"],
            "hermes_history_content_chars": false_history["content_chars"],
            "hermes_history_think_markers": false_history["think_markers"],
            "hermes_history_shapes_match": false_history == true_history,
            "direct_preserve_false_prompt_tokens": direct_false_prompt,
            "direct_preserve_true_prompt_tokens": direct_true_prompt,
            "provider_preserve_setting_honored": True,
            "low_reasoning_measure": low_measure,
            "xhigh_reasoning_measure": xhigh_measure,
            "low_and_xhigh_reached_provider": True,
            "raw_reasoning_not_logged": True,
        }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
