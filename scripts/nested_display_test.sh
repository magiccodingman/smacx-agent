#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
nested_display=${SMACX_TEST_DISPLAY:-:99}
display_number=${nested_display#:}
xephyr_log="$project_dir/runtime/xephyr-test.log"

cleanup() {
    DISPLAY="$nested_display" PYTHONPATH="$project_dir/src" \
        timeout --signal=TERM --kill-after=2s 8s \
        python3 -c 'from smacx_controller import stop_game; stop_game(4)' \
        >/dev/null 2>&1 || true
    if [ "${xephyr_pid:-}" ]; then
        kill "$xephyr_pid" >/dev/null 2>&1 || true
        wait "$xephyr_pid" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM

if [ -e "/tmp/.X11-unix/X$display_number" ]; then
    echo "Nested display $nested_display is already in use." >&2
    exit 1
fi

Xephyr "$nested_display" -screen 1280x800 -resizeable -nolisten tcp \
    >"$xephyr_log" 2>&1 &
xephyr_pid=$!

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if DISPLAY="$nested_display" xdpyinfo >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.25
done
if [ "$ready" -ne 1 ]; then
    echo "Xephyr did not become ready; see $xephyr_log" >&2
    exit 1
fi

echo "Contained test display ready at $nested_display."
echo "Running: $*"
DISPLAY="$nested_display" timeout --signal=TERM --kill-after=8s "${SMACX_TEST_TIMEOUT:-180}s" "$@"
