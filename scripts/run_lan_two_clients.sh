#!/bin/sh
set -eu

: "${WINE:?}"
: "${SMACX_TEST_WINEPREFIX:?}"
: "${SMACX_TEST_HOST_GAME:?}"
: "${SMACX_TEST_JOIN_GAME:?}"
: "${SMACX_TEST_TOKEN:?}"

stop_children() {
    if [ "${join_pid:-}" ]; then kill "$join_pid" 2>/dev/null || true; fi
    if [ "${host_pid:-}" ]; then kill "$host_pid" 2>/dev/null || true; fi
}
trap stop_children EXIT HUP INT TERM

(
    # Both isolated processes use the disposable import-patched executable;
    # their bridge ports, match/session identities, displays, and process
    # state remain separate. The canonical Steam executable is deliberately
    # never import-patched in place.
    cd "$SMACX_TEST_JOIN_GAME"
    exec env \
        DISPLAY="${SMACX_TEST_HOST_DISPLAY:-$DISPLAY}" \
        WINEPREFIX="$SMACX_TEST_WINEPREFIX" \
        SMACX_AGENT_ENABLE=1 \
        SMACX_AGENT_TOKEN="$SMACX_TEST_TOKEN" \
        SMACX_AGENT_PORT="$SMACX_TEST_HOST_PORT" \
        SMACX_AGENT_MATCH_ID="$SMACX_TEST_HOST_MATCH_ID" \
        SMACX_AGENT_SESSION_ID="$SMACX_TEST_HOST_SESSION_ID" \
        SMACX_AGENT_INITIAL_RESEARCH_PRIORITY=1 \
        SMACX_AGENT_TEST_MODE=1 \
        SMACX_AGENT_TEST_LAN_HOST=1 \
        "$WINE" terranx.exe -windowed
) >>"$SMACX_TEST_HOST_LOG" 2>&1 &
host_pid=$!

sleep 5

(
    cd "$SMACX_TEST_JOIN_GAME"
    exec env \
        DISPLAY="${SMACX_TEST_JOIN_DISPLAY:-$DISPLAY}" \
        WINEPREFIX="$SMACX_TEST_WINEPREFIX" \
        SMACX_AGENT_ENABLE=1 \
        SMACX_AGENT_TOKEN="$SMACX_TEST_TOKEN" \
        SMACX_AGENT_PORT="$SMACX_TEST_JOIN_PORT" \
        SMACX_AGENT_MATCH_ID="$SMACX_TEST_JOIN_MATCH_ID" \
        SMACX_AGENT_SESSION_ID="$SMACX_TEST_JOIN_SESSION_ID" \
        SMACX_AGENT_INITIAL_RESEARCH_PRIORITY=1 \
        SMACX_AGENT_TEST_MODE=1 \
        SMACX_AGENT_TEST_LAN_HOST=1 \
        "$WINE" terranx.exe -windowed
) >>"$SMACX_TEST_JOIN_LOG" 2>&1 &
join_pid=$!

wait "$host_pid"
wait "$join_pid"
