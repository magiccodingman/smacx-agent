#!/usr/bin/env python3
"""Opt-in live vLLM prefix-cache measurement without recording prompt content."""

from __future__ import annotations

import argparse
import json
import re
import time
from urllib.request import Request, urlopen
import uuid


METRIC = re.compile(
    r'^vllm:prompt_tokens_by_source_total\{[^}]*source="local_cache_hit"[^}]*\}\s+([0-9.eE+-]+)$',
    re.MULTILINE,
)


def metric(base_url: str) -> float:
    endpoint = base_url.rstrip("/").removesuffix("/v1") + "/metrics"
    with urlopen(endpoint, timeout=10) as response:
        text = response.read(8_000_000).decode("utf-8", "replace")
    values = [float(value) for value in METRIC.findall(text)]
    if not values:
        raise RuntimeError("vllm_local_cache_hit_metric_unavailable")
    return sum(values)


def invoke(base_url: str, model: str, messages: list[dict[str, str]]) -> dict:
    body = json.dumps({
        "model": model, "messages": messages, "stream": False,
        "max_tokens": 1, "temperature": 0, "seed": 1729,
        "chat_template_kwargs": {"enable_thinking": False, "preserve_thinking": False},
    }, separators=(",", ":")).encode()
    request = Request(base_url.rstrip("/") + "/chat/completions", data=body,
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    nonce = uuid.uuid4().hex
    # Unique material avoids mistaking another workload's cache for this test.
    # The second request changes only the final volatile tail.
    stable = (f"SMACX prefix contract {nonce}. " * 700).strip()
    prefix = [
        {"role": "system", "content": "Stable sovereign system contract."},
        {"role": "user", "content": stable},
        {"role": "assistant", "content": "Acknowledged stable durable episode prefix."},
    ]
    before = metric(args.base_url)
    first = invoke(args.base_url, args.model,
                   [*prefix, {"role": "user", "content": "runtime revision one"}])
    after_warm = metric(args.base_url)
    second = invoke(args.base_url, args.model,
                    [*prefix, {"role": "user", "content": "runtime revision two"}])
    after_reuse = metric(args.base_url)
    warm_hits = after_warm - before
    reuse_hits = after_reuse - after_warm
    usage = second.get("usage") if isinstance(second, dict) else {}
    if reuse_hits <= 0:
        raise AssertionError("second stable-prefix request recorded no local cache hits")
    print(json.dumps({
        "schema": "smacx.prefix-cache-benchmark.v1",
        "recorded_unix": time.time(),
        "model": args.model,
        "first_request_cache_hit_tokens": int(warm_hits),
        "second_request_cache_hit_tokens": int(reuse_hits),
        "second_request_prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
        "passed": True,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
