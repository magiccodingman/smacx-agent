#!/bin/sh
set -eu

: "${SMACX_TEST_GAME_SOURCE:?set SMACX_TEST_GAME_SOURCE to the legal game directory}"
: "${SMACX_TEST_PROTON_SOURCE:?set SMACX_TEST_PROTON_SOURCE to a Proton distribution}"
: "${SMACX_TEST_DIRECTX_REDIST:?set SMACX_TEST_DIRECTX_REDIST to the DirectX redistributable}"

repository_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
suffix=$(tr -d '-' </proc/sys/kernel/random/uuid | cut -c1-10)
octet_hex=$(printf '%s' "$suffix" | cut -c1-2)
octet=$((0x$octet_hex))
network="smacx-human-host-live-$suffix"
runner="smacx-human-host-runner-$suffix"
socket=${SMACX_DOCKER_SOCKET:-/var/run/docker.sock}
socket_gid=$(stat -c '%g' "$socket")

cleanup() {
    docker rm -f "$runner" >/dev/null 2>&1 || true
    docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker network create -d macvlan \
    --subnet="198.18.$octet.0/24" \
    --label io.smacx.managed=true \
    --label io.smacx.purpose=human-hosted-lan-live-test \
    "$network" >/dev/null

docker run --rm --name "$runner" --network "$network" \
    --group-add "$socket_gid" \
    -e SMACX_DOCKER_SOCKET=/var/run/docker.sock \
    -e SMACX_TEST_NETWORK="$network" \
    -e SMACX_TEST_KEEP_ON_FAILURE="${SMACX_TEST_KEEP_ON_FAILURE:-0}" \
    -e SMACX_TEST_GAME_SOURCE="$SMACX_TEST_GAME_SOURCE" \
    -e SMACX_TEST_PROTON_SOURCE="$SMACX_TEST_PROTON_SOURCE" \
    -e SMACX_TEST_DIRECTX_REDIST="$SMACX_TEST_DIRECTX_REDIST" \
    -e PYTHONPATH=/workspace/src:/workspace/scripts \
    -v "$socket:/var/run/docker.sock" \
    -v "$repository_root:/workspace:ro" \
    -w /workspace \
    smacx-agent-control:dev \
    python3 scripts/human_hosted_lan_live_test.py
