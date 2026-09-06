# syntax=docker/dockerfile:1.7

# Hermes supplies the long-running conversation/compression engine.  SMACX
# owns the complete provider-facing system prompt through a narrow, audited
# hook installed over this immutable upstream base.
FROM docker.io/nousresearch/hermes-agent:v2026.8.31@sha256:64923faeae267792bf9bf87fe3b4c4869e35004e360c7df01730ad801b74d524

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends zstd \
    && rm -rf /var/lib/apt/lists/* \
    && /opt/hermes/.venv/bin/python -c "from importlib.metadata import version; assert version('mcp') == '2.0.0'"
COPY harness/smacx_strict_prompt.py /opt/smacx-strict-prompt/smacx_strict_prompt.py
COPY src/smacx_context_policy.py /opt/smacx-strict-prompt/smacx_context_policy.py
COPY src/ /opt/smacx/src/
COPY harness/patch_hermes_repetition.py /tmp/patch_hermes_repetition.py
COPY harness/patch_hermes_diagnostics.py /tmp/patch_hermes_diagnostics.py
COPY harness/patch_hermes_mcp_watcher.py /tmp/patch_hermes_mcp_watcher.py
COPY harness/patch_hermes_stream_metadata.py /tmp/patch_hermes_stream_metadata.py
RUN /opt/hermes/.venv/bin/python /tmp/patch_hermes_repetition.py \
    && /opt/hermes/.venv/bin/python /tmp/patch_hermes_diagnostics.py \
    && /opt/hermes/.venv/bin/python /tmp/patch_hermes_mcp_watcher.py \
    && /opt/hermes/.venv/bin/python /tmp/patch_hermes_stream_metadata.py \
    && rm /tmp/patch_hermes_repetition.py /tmp/patch_hermes_diagnostics.py /tmp/patch_hermes_mcp_watcher.py \
    && rm /tmp/patch_hermes_stream_metadata.py \
    && /opt/hermes/.venv/bin/python -m py_compile /opt/hermes/agent/chat_completion_helpers.py /opt/hermes/agent/conversation_loop.py
RUN site_packages="$(/opt/hermes/.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')" \
    && printf '%s\n%s\n%s\n%s\n' '/opt/hermes' '/opt/smacx/src' '/opt/smacx-strict-prompt' 'import smacx_strict_prompt' \
        > "${site_packages}/00-smacx-strict-prompt.pth"
