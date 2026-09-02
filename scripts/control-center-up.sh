#!/bin/sh
set -eu

docker_socket=${SMACX_DOCKER_SOCKET:-/var/run/docker.sock}
if [ ! -S "$docker_socket" ]; then
    echo "Docker socket is unavailable: $docker_socket" >&2
    exit 1
fi

SMACX_DOCKER_GID=$(stat -c '%g' "$docker_socket")
export SMACX_DOCKER_GID

edge_secret_dir=${SMACX_EDGE_SECRET_DIR:-./runtime/edge-secrets}
mkdir -p "$edge_secret_dir"
SMACX_EDGE_SECRET_GID=${SMACX_EDGE_SECRET_GID:-$(id -g)}
export SMACX_EDGE_SECRET_GID
if [ ! -e "$edge_secret_dir/ddns-token" ]; then
    : > "$edge_secret_dir/ddns-token"
    chmod 640 "$edge_secret_dir/ddns-token"
fi

# Compose understands spaces and apostrophes in its .env parser; POSIX shell
# sourcing does not. Resolve the mounted source through Compose when the caller
# did not export it explicitly, so the ordinary checked-in launcher works with
# the game's default Steam directory name.
if [ -z "${SMACX_GAME_SOURCE:-}" ]; then
    SMACX_GAME_SOURCE=$(docker compose -f compose.yaml config --format json \
        | python3 -c 'import json,sys; c=json.load(sys.stdin); print(next(v["source"] for v in c["services"]["knowledge-service"]["volumes"] if v["target"]=="/game-source"))')
    export SMACX_GAME_SOURCE
fi
: "${SMACX_GAME_SOURCE:?Set SMACX_GAME_SOURCE to the directory containing terranx.exe}"
if [ ! -f "$SMACX_GAME_SOURCE/terranx.exe" ]; then
    echo "SMACX_GAME_SOURCE does not contain terranx.exe: $SMACX_GAME_SOURCE" >&2
    exit 2
fi

services="knowledge-service control-api control-center edge ddns"
# Proton sealing and Blazor AOT-style optimization are memory-intensive build
# phases. Keep first-run builds deterministic on small home-lab hosts instead
# of letting Compose build all images concurrently.
COMPOSE_PARALLEL_LIMIT=${COMPOSE_PARALLEL_LIMIT:-1}
export COMPOSE_PARALLEL_LIMIT
if [ -n "${SMACX_LAN_NETWORK:-}" ]; then
    set -- -f compose.yaml -f compose.lan.yaml
else
    set -- -f compose.yaml
fi

case "${SMACX_VIRTUAL_LAN:-none}" in
    none|"") ;;
    tailscale)
        if [ -z "${SMACX_LAN_NETWORK:-}" ] || [ -z "${SMACX_PLAYER_LAN_SUBNET:-}" ]; then
            echo "Tailscale transport requires SMACX_LAN_NETWORK and SMACX_PLAYER_LAN_SUBNET." >&2
            exit 2
        fi
        set -- "$@" -f compose.tailscale.yaml
        services="knowledge-service control-api control-center edge ddns tailscale-router"
        ;;
    *)
        echo "Unsupported SMACX_VIRTUAL_LAN: ${SMACX_VIRTUAL_LAN}" >&2
        exit 2
        ;;
esac

docker compose "$@" --profile build build knowledge-service
docker compose "$@" --profile build build control-api
docker compose "$@" --profile build build control-center
docker compose "$@" --profile build build edge
docker compose "$@" --profile build build ddns
docker compose "$@" --profile build build worker-image
docker compose "$@" --profile build build harness-image
# Word splitting is deliberate: this is a fixed internal service list, never
# operator-provided input.
# shellcheck disable=SC2086
docker compose "$@" up -d $services
# shellcheck disable=SC2086
docker compose "$@" ps $services
