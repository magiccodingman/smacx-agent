#!/bin/sh
set -eu

systemctl --user --no-pager --full status smacx-agent-mcp.service || true
"$HOME/.hermes/hermes-agent/venv/bin/hermes" mcp list

