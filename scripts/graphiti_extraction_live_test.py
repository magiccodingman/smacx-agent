#!/usr/bin/env python3
"""Opt-in live structured-extraction check through the production Graphiti adapter."""

from __future__ import annotations

import asyncio
import json
import os
import time

from pydantic import BaseModel
from graphiti_core.prompts.models import Message
from smacx_graphiti import GraphitiRuntimeConfig, create_graphiti_llm_client


class Fact(BaseModel):
    subject: str
    relationship: str
    object: str


async def run() -> int:
    base_url = os.environ.get("SMACX_TEST_PROVIDER_BASE_URL", "").rstrip("/")
    model = os.environ.get("SMACX_TEST_PROVIDER_MODEL", "")
    if not base_url or not model:
        raise SystemExit("Set SMACX_TEST_PROVIDER_BASE_URL and SMACX_TEST_PROVIDER_MODEL")
    config = GraphitiRuntimeConfig(
        fingerprint="live", profile_id="live", display_name="Live extraction",
        llm_base_url=base_url,
        llm_api_key=os.environ.get("SMACX_TEST_PROVIDER_API_KEY", "local"),
        llm_model=model, reasoning_effort=os.environ.get("SMACX_TEST_REASONING", "medium"),
        generation_settings={
            "preset": "custom", "temperature": 0.2, "max_output_tokens": 512,
            "extra_parameters": {"chat_template_kwargs": {
                "enable_thinking": True, "preserve_thinking": False,
            }},
        }, embed_base_url="http://unused/v1", embed_api_key="local",
        embed_model="unused", embed_dim=2048,
    )
    client, _ = create_graphiti_llm_client(config)
    started = time.monotonic()
    try:
        result = await client.generate_response([
            Message(role="system", content="Extract exactly the supplied public relationship."),
            Message(role="user", content="Public fact: Deirdre has a treaty with Lal."),
        ], response_model=Fact, max_tokens=512, prompt_name="smacx.live.readiness")
    finally:
        await client.client.close()
    valid = isinstance(result, dict) and all(
        isinstance(result.get(key), str) and result[key].strip()
        for key in ("subject", "relationship", "object")
    )
    if not valid:
        raise AssertionError("live endpoint did not return valid structured extraction")
    print(json.dumps({"event": "pass", "payload": {
        "production_graphiti_adapter": True, "structured_output": True,
        "reasoning_effort": config.reasoning_effort,
        "preserve_thinking": False,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
