#!/bin/sh
set -eu

docker_socket=${SMACX_DOCKER_SOCKET:-/var/run/docker.sock}
if [ ! -S "$docker_socket" ]; then
    echo "Docker socket is unavailable: $docker_socket" >&2
    exit 1
fi

SMACX_DOCKER_GID=$(stat -c '%g' "$docker_socket")
export SMACX_DOCKER_GID

if [ -n "${SMACX_LAN_NETWORK:-}" ]; then
    set -- -f compose.yaml -f compose.lan.yaml
else
    set -- -f compose.yaml
fi

docker compose "$@" --profile build build control-center worker-image
docker compose "$@" --profile build pull harness-image
docker compose "$@" up -d control-center
docker compose "$@" ps control-center
