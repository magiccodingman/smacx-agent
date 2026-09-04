#!/usr/bin/env python3
"""Attempt-local OpenAI-compatible provider metering boundary.

Hermes owns the specialist agent loop. SMACX owns the hard execution lease.
The boundary reserves a conservative upper bound before forwarding each request,
then replaces that reservation with provider-reported usage. A provider that
omits usage is charged the reservation, so missing telemetry cannot turn a hard
budget into an unbounded best-effort limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


_HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
})


class ProviderLeaseError(RuntimeError):
    """Raised when a request cannot be admitted under the attempt lease."""


@dataclass(frozen=True)
class ProviderUsage:
    provider_calls: int
    prompt_tokens: int
    completion_tokens: int
    provider_tokens: int
    peak_context_tokens: int
    reserved_tokens: int
    violation: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_calls": self.provider_calls,
            "provider_calls": self.provider_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.provider_tokens,
            "provider_tokens": self.provider_tokens,
            "peak_context_tokens": self.peak_context_tokens,
            "reserved_tokens": self.reserved_tokens,
            "lease_violation": self.violation,
            "usage_source": "smacx_attempt_meter",
        }


class ProviderLeaseMeter:
    """Thread-safe admission and accounting for one disposable attempt."""

    def __init__(self, *, call_budget: int, token_budget: int,
                 context_ceiling: int, output_ceiling: int) -> None:
        self.call_budget = max(1, int(call_budget))
        self.token_budget = max(1, int(token_budget))
        self.context_ceiling = max(1, int(context_ceiling))
        self.output_ceiling = max(1, int(output_ceiling))
        self._lock = threading.RLock()
        self._calls = 0
        self._prompt = 0
        self._completion = 0
        self._total = 0
        self._peak = 0
        self._reserved = 0
        self._violation: str | None = None
        self._last_request: bytes | None = None
        self._last_payload: dict[str, Any] | None = None
        self._last_prompt_tokens: int | None = None

    @staticmethod
    def _output_cap(payload: Mapping[str, Any]) -> int:
        for key in ("max_completion_tokens", "max_tokens", "max_output_tokens"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return max(0, value)
        return 0

    def _prompt_upper_bound(self, body: bytes, payload: Mapping[str, Any]) -> int:
        # A tokenizer token cannot encode less than one input byte. For an
        # appended Hermes transcript, reuse the last provider-measured prompt
        # and charge every new byte plus a generous message-format allowance.
        # Falling back to the complete request byte count is deliberately
        # conservative and is used for the first call or non-prefix mutation.
        previous = self._last_request
        previous_payload = self._last_payload
        previous_tokens = self._last_prompt_tokens
        if previous_payload is not None and previous_tokens is not None:
            old_messages = previous_payload.get("messages")
            new_messages = payload.get("messages")
            stable_envelope = all(
                previous_payload.get(key) == payload.get(key)
                for key in ("model", "tools", "tool_choice")
            )
            if (stable_envelope and isinstance(old_messages, list)
                    and isinstance(new_messages, list)
                    and len(new_messages) >= len(old_messages)
                    and new_messages[:len(old_messages)] == old_messages):
                appended = new_messages[len(old_messages):]
                # Provider-reported prompt use is authoritative for the stable
                # prefix. Charge every UTF-8 byte of appended messages plus a
                # fixed allowance for chat-template framing.
                extra = len(json.dumps(appended, ensure_ascii=False,
                                       separators=(",", ":")).encode())
                return previous_tokens + extra + 256 + 64 * len(appended)
        if previous is not None and previous_tokens is not None:
            prefix = 0
            limit = min(len(previous), len(body))
            while prefix < limit and previous[prefix] == body[prefix]:
                prefix += 1
            # JSON request tails can change before the appended messages. Only
            # reuse the measured prefix when most of the old body is identical.
            if prefix >= int(len(previous) * 0.75):
                changed = max(0, len(body) - prefix)
                return previous_tokens + changed + 512
        return len(body) + 1024

    def admit(self, body: bytes, payload: Mapping[str, Any]) -> int:
        with self._lock:
            if self._violation:
                raise ProviderLeaseError(self._violation)
            if self._reserved:
                self._violation = "concurrent_provider_request_rejected"
                raise ProviderLeaseError(self._violation)
            if self._calls >= self.call_budget:
                self._violation = "provider_call_budget_exhausted"
                raise ProviderLeaseError(self._violation)
            output = self._output_cap(payload)
            if output <= 0 or output > self.output_ceiling:
                self._violation = "provider_output_ceiling_exceeded"
                raise ProviderLeaseError(self._violation)
            prompt_upper = self._prompt_upper_bound(body, payload)
            if prompt_upper + output > self.context_ceiling:
                self._violation = "provider_context_ceiling_exceeded"
                raise ProviderLeaseError(self._violation)
            reservation = prompt_upper + output
            if self._total + reservation > self.token_budget:
                self._violation = "provider_token_budget_exhausted"
                raise ProviderLeaseError(self._violation)
            self._calls += 1
            self._reserved = reservation
            self._last_request = bytes(body)
            self._last_payload = dict(payload)
            return reservation

    def settle(self, *, prompt_tokens: int | None, completion_tokens: int | None,
               total_tokens: int | None) -> None:
        with self._lock:
            reservation = self._reserved
            self._reserved = 0
            if total_tokens is None or total_tokens < 0:
                charged = reservation
                prompt = max(0, reservation - self.output_ceiling)
                completion = max(0, reservation - prompt)
            else:
                prompt = max(0, int(prompt_tokens or 0))
                completion = max(0, int(completion_tokens or 0))
                charged = max(0, int(total_tokens))
                if charged > reservation:
                    # The upstream violated its declared cap or its tokenizer
                    # exceeded the byte-safe admission bound. Reject the result
                    # and stop the process; never silently accept overspend.
                    self._violation = "provider_usage_exceeded_reservation"
            self._prompt += prompt
            self._completion += completion
            self._total += charged
            self._peak = max(self._peak, prompt)
            self._last_prompt_tokens = prompt if prompt > 0 else None
            if self._total > self.token_budget:
                self._violation = "provider_token_budget_exhausted"

    def fail_request(self) -> None:
        with self._lock:
            # A dispatched upstream request counts as a provider call. It may
            # have consumed compute even when no usable usage response arrived.
            # Charge the safe reservation so retries remain bounded.
            if self._reserved:
                reservation = self._reserved
                self._reserved = 0
                self._total += reservation

    def snapshot(self) -> ProviderUsage:
        with self._lock:
            return ProviderUsage(
                self._calls, self._prompt, self._completion, self._total,
                self._peak, self._reserved, self._violation,
            )


def _usage_from_json(value: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = value.get("usage")
    if not isinstance(usage, Mapping):
        return None, None, None
    return (
        int(usage["prompt_tokens"]) if isinstance(usage.get("prompt_tokens"), int) else None,
        int(usage["completion_tokens"]) if isinstance(usage.get("completion_tokens"), int) else None,
        int(usage["total_tokens"]) if isinstance(usage.get("total_tokens"), int) else None,
    )


def _usage_from_sse(body: bytes) -> tuple[int | None, int | None, int | None]:
    found: tuple[int | None, int | None, int | None] = (None, None, None)
    for line in body.splitlines():
        if not line.startswith(b"data: ") or line == b"data: [DONE]":
            continue
        try:
            value = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping) and isinstance(value.get("usage"), Mapping):
            found = _usage_from_json(value)
    return found


class AttemptProviderProxy:
    """Loopback server exposing one metered OpenAI-compatible provider."""

    def __init__(self, upstream_base_url: str, meter: ProviderLeaseMeter) -> None:
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.meter = meter
        self._trace_lock = threading.RLock()
        self._trace_exchanges: list[dict[str, Any]] = []
        owner = self

        def upstream_url(request_path: str) -> str:
            """Join provider paths without duplicating an OpenAI base prefix.

            Hermes providers may address the proxy as either
            ``/chat/completions`` or ``/v1/chat/completions``. Administrators
            likewise commonly save an upstream base ending in ``/v1``. The
            metering boundary must preserve exactly one such prefix.
            """
            base = urlsplit(owner.upstream_base_url)
            incoming = urlsplit(request_path)
            base_path = base.path.rstrip("/")
            incoming_path = "/" + incoming.path.lstrip("/")
            if base_path and (
                incoming_path == base_path
                or incoming_path.startswith(base_path + "/")
            ):
                path = incoming_path
            else:
                path = base_path + incoming_path
            return urlunsplit((base.scheme, base.netloc, path,
                               incoming.query, incoming.fragment))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def _error(self, status: int, reason: str) -> None:
                body = json.dumps({"error": {"message": "smacx_specialist_" + reason,
                                               "type": "specialist_lease"}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                try:
                    payload = json.loads(body)
                    if not isinstance(payload, Mapping):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    self._error(400, "invalid_provider_request")
                    return
                try:
                    owner.meter.admit(body, payload)
                except ProviderLeaseError as exc:
                    self._error(429, str(exc))
                    return
                headers = {key: value for key, value in self.headers.items()
                           if key.casefold() not in _HOP_HEADERS}
                headers["Content-Length"] = str(len(body))
                request = Request(upstream_url(self.path), data=body,
                                  headers=headers, method="POST")
                try:
                    with urlopen(request, timeout=300) as response:
                        response_body = response.read()
                        status = response.status
                        response_headers = dict(response.headers.items())
                except HTTPError as exc:
                    response_body = exc.read()
                    status = exc.code
                    response_headers = dict(exc.headers.items())
                except (URLError, TimeoutError, OSError):
                    owner.meter.fail_request()
                    self._error(502, "provider_transport_failed")
                    return
                content_type = response_headers.get("Content-Type", "application/json")
                try:
                    request_capture: Any = json.loads(body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    request_capture = {"unparsed_bytes": len(body)}
                try:
                    if "text/event-stream" in content_type:
                        response_capture = []
                        for line in response_body.splitlines():
                            if not line.startswith(b"data: ") or line == b"data: [DONE]":
                                continue
                            try:
                                response_capture.append(json.loads(line[6:]))
                            except json.JSONDecodeError:
                                response_capture.append({"unparsed_event_bytes": len(line)})
                    else:
                        response_capture = json.loads(response_body)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    response_capture = {"unparsed_bytes": len(response_body)}
                with owner._trace_lock:
                    owner._trace_exchanges.append({
                        "kind": "provider_exchange",
                        "sequence": len(owner._trace_exchanges) + 1,
                        "path": self.path,
                        "status": int(status),
                        "request": request_capture,
                        "response": response_capture,
                    })
                if 200 <= status < 300:
                    try:
                        usage = (_usage_from_sse(response_body)
                                 if "text/event-stream" in content_type
                                 else _usage_from_json(json.loads(response_body)))
                    except (json.JSONDecodeError, TypeError):
                        usage = (None, None, None)
                    owner.meter.settle(prompt_tokens=usage[0], completion_tokens=usage[1],
                                       total_tokens=usage[2])
                else:
                    owner.meter.fail_request()
                self.send_response(status)
                for key, value in response_headers.items():
                    if key.casefold() not in _HOP_HEADERS:
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="specialist-provider-meter", daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def trace_exchanges(self) -> list[dict[str, Any]]:
        """Return provider-visible requests and replies for diagnostic traces."""
        with self._trace_lock:
            return [dict(item) for item in self._trace_exchanges]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
