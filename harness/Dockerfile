# syntax=docker/dockerfile:1.7

# Hermes supplies the long-running conversation/compression engine.  SMACX
# owns the complete provider-facing system prompt through a narrow, audited
# hook installed over this immutable upstream base.
FROM docker.io/nousresearch/hermes-agent:v2026.8.27@sha256:5f23552e16589d291099cd8041233e6200197d225e4b28b22a0463e732d4b843

USER root
COPY harness/smacx_strict_prompt.py /opt/smacx-strict-prompt/smacx_strict_prompt.py
RUN site_packages="$(/opt/hermes/.venv/bin/python -c 'import site; print(site.getsitepackages()[0])')" \
    && printf '%s\n%s\n%s\n' '/opt/hermes' '/opt/smacx-strict-prompt' 'import smacx_strict_prompt' \
        > "${site_packages}/00-smacx-strict-prompt.pth"
