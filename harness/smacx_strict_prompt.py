"""Fail-closed provider-facing system-prompt override for managed Hermes.

The derived image imports this module from an executable venv ``.pth`` line
before the Hermes console entry point. The official pinned runtime remains
responsible for conversations, tools, compression, and provider transport;
only prompt assembly is replaced.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def _install() -> None:
    if os.environ.get("SMACX_STRICT_SYSTEM_PROMPT") != "1":
        return
    path_value = os.environ.get("SMACX_SYSTEM_PROMPT_FILE", "")
    expected = os.environ.get("SMACX_SYSTEM_PROMPT_SHA256", "")
    if not path_value or len(expected) != 64:
        raise RuntimeError("smacx_strict_prompt_configuration_missing")
    path = Path(path_value)

    def load() -> str:
        try:
            value = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError("smacx_strict_prompt_unavailable") from exc
        actual = hashlib.sha256(value.encode("utf-8")).hexdigest()
        if actual != expected:
            raise RuntimeError("smacx_strict_prompt_integrity_failure")
        if not value.strip():
            raise RuntimeError("smacx_strict_prompt_empty")
        return value

    # Importing at interpreter startup ensures later ``from ... import`` sites
    # receive these functions rather than Hermes's additive prompt builder.
    import agent.system_prompt as system_prompt  # type: ignore

    def build_parts(agent, system_message=None):  # noqa: ANN001,ARG001
        return {"stable": load(), "context": "", "volatile": ""}

    def build(agent, system_message=None):  # noqa: ANN001,ARG001
        value = load()
        agent._cached_system_prompt_static = value
        return value

    system_prompt.build_system_prompt_parts = build_parts
    system_prompt.build_system_prompt = build


_install()
