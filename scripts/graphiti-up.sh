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

required=(neo4j_auth neo4j_password llm_api_key embed_api_key)
for name in "${required[@]}"; do
  path="${secret_dir}/${name}"
  if [[ ! -f "${path}" || -L "${path}" || ! -s "${path}" ]]; then
    echo "Missing non-empty regular secret file: ${path}" >&2
    exit 2
  fi
  chmod 600 "${path}"
done

if [[ "$(head -n 1 "${secret_dir}/neo4j_auth")" != neo4j/* ]]; then
  echo "neo4j_auth must contain neo4j/<password>." >&2
  exit 2
fi
if [[ "$(cut -d/ -f2- "${secret_dir}/neo4j_auth")" != "$(cat "${secret_dir}/neo4j_password")" ]]; then
  echo "neo4j_auth and neo4j_password do not contain the same password." >&2
  exit 2
fi

for variable in SMACX_GRAPHITI_LLM_BASE_URL SMACX_GRAPHITI_LLM_MODEL \
  SMACX_GRAPHITI_EMBED_BASE_URL SMACX_GRAPHITI_EMBED_MODEL SMACX_GRAPHITI_EMBED_DIM; do
  if [[ -z "${!variable:-}" ]]; then
    echo "Required environment variable is unset: ${variable}" >&2
    exit 2
  fi
done

export SMACX_GRAPHITI_SECRET_DIR="${secret_dir}"
docker compose -f "${repo_root}/compose.yaml" --profile graphiti up -d --build \
  control-api control-center graphiti-db graphiti-projector
docker compose -f "${repo_root}/compose.yaml" --profile graphiti ps \
  control-api control-center graphiti-db graphiti-projector
