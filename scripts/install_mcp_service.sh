#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
unit_source="$project_dir/systemd/smacx-agent-mcp.service"
unit_target="$HOME/.config/systemd/user/smacx-agent-mcp.service"
python_bin=${SMACX_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}

if [ ! -x "$python_bin" ]; then
    python_bin=$(command -v python3 || true)
fi
if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
    echo "No Python interpreter found; set SMACX_PYTHON to the MCP environment's Python." >&2
    exit 1
fi

escape_sed_replacement() {
    printf '%s' "$1" | sed 's/[\/&]/\\&/g'
}

project_escaped=$(escape_sed_replacement "$project_dir")
python_escaped=$(escape_sed_replacement "$python_bin")
unit_tmp=$(mktemp)
trap 'rm -f -- "$unit_tmp"' EXIT HUP INT TERM
sed \
    -e "s/@PROJECT_DIR@/$project_escaped/g" \
    -e "s/@PYTHON@/$python_escaped/g" \
    "$unit_source" >"$unit_tmp"

install -D -m 0644 "$unit_tmp" "$unit_target"
systemctl --user daemon-reload
systemctl --user enable --now smacx-agent-mcp.service
systemctl --user --no-pager --full status smacx-agent-mcp.service
