#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
secret_dir="${SMACX_GRAPHITI_SECRET_DIR:-${repo_root}/runtime/graphiti-secrets}"
docker_socket="${SMACX_DOCKER_SOCKET:-/var/run/docker.sock}"
if [[ ! -S "${docker_socket}" ]]; then
  echo "Docker socket is unavailable: ${docker_socket}" >&2
  exit 1
fi
SMACX_DOCKER_GID="$(stat -c '%g' "${docker_socket}")"
export SMACX_DOCKER_GID
export COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-1}"

install -d -m 700 "${secret_dir}"
if [[ ! -e "${secret_dir}/falkordb_password" ]]; then
  if ! command -v openssl >/dev/null 2>&1; then
    echo "OpenSSL is required to initialize the local FalkorDB password." >&2
    exit 2
  fi
  graphiti_password="$(openssl rand -hex 32)"
  umask 077
  printf '%s\n' "${graphiti_password}" > "${secret_dir}/falkordb_password"
  unset graphiti_password
  echo "Initialized private Graphiti database credentials in ${secret_dir}."
fi

required=(falkordb_password)
for name in "${required[@]}"; do
  path="${secret_dir}/${name}"
  if [[ ! -f "${path}" || -L "${path}" || ! -s "${path}" ]]; then
    echo "Missing non-empty regular secret file: ${path}" >&2
    exit 2
  fi
  chmod 640 "${path}"
done
SMACX_GRAPHITI_SECRET_GID="$(stat -c '%g' "${secret_dir}/falkordb_password")"
export SMACX_GRAPHITI_SECRET_GID

export SMACX_GRAPHITI_SECRET_DIR="${secret_dir}"
docker compose -f "${repo_root}/compose.yaml" --profile graphiti up -d --build \
  knowledge-service control-api control-center graphiti-db graphiti-projector
docker compose -f "${repo_root}/compose.yaml" --profile graphiti ps \
  knowledge-service control-api control-center graphiti-db graphiti-projector
