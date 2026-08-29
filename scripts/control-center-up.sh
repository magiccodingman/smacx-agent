#!/bin/sh
set -eu

docker_socket=${SMACX_DOCKER_SOCKET:-/var/run/docker.sock}
if [ ! -S "$docker_socket" ]; then
    echo "Docker socket is unavailable: $docker_socket" >&2
    exit 1
fi

SMACX_DOCKER_GID=$(stat -c '%g' "$docker_socket")
export SMACX_DOCKER_GID

services="control-center"
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
        services="control-center tailscale-router"
        ;;
    *)
        echo "Unsupported SMACX_VIRTUAL_LAN: ${SMACX_VIRTUAL_LAN}" >&2
        exit 2
        ;;
esac

docker compose "$@" --profile build build control-center worker-image
docker compose "$@" --profile build pull harness-image
# Word splitting is deliberate: this is a fixed internal service list, never
# operator-provided input.
# shellcheck disable=SC2086
docker compose "$@" up -d $services
# shellcheck disable=SC2086
docker compose "$@" ps $services
