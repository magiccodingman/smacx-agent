#!/usr/bin/env bash
set -euo pipefail

suffix="$$"
site_a="smacx-route-a-${suffix}"
site_b="smacx-route-b-${suffix}"
router="smacx-route-router-${suffix}"
server="smacx-route-server-${suffix}"
client="smacx-route-client-${suffix}"
image="alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40"

cleanup() {
  docker rm -f "${client}" "${server}" "${router}" >/dev/null 2>&1 || true
  docker network rm "${site_a}" "${site_b}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create --subnet 172.29.231.0/24 "${site_a}" >/dev/null
docker network create --subnet 172.29.232.0/24 "${site_b}" >/dev/null
docker run -d --name "${router}" --network "${site_a}" --ip 172.29.231.254 \
  --cap-add NET_ADMIN --sysctl net.ipv4.ip_forward=1 "${image}" sleep 120 >/dev/null
docker network connect --ip 172.29.232.254 "${site_b}" "${router}"

docker run -d --name "${server}" --network "${site_a}" --ip 172.29.231.10 \
  --cap-add NET_ADMIN "${image}" sh -c \
  "ip route add 172.29.232.0/24 via 172.29.231.254; nc -l -p 47624 > /tmp/tcp & nc -u -l -p 2350 > /tmp/udp & sleep 120" >/dev/null
docker run -d --name "${client}" --network "${site_b}" --ip 172.29.232.10 \
  --cap-add NET_ADMIN "${image}" sh -c \
  "ip route add 172.29.231.0/24 via 172.29.232.254; sleep 120" >/dev/null

sleep 1
docker exec "${client}" sh -c "printf tcp-enumeration | nc -w 3 172.29.231.10 47624"
docker exec "${client}" sh -c "printf udp-gameplay | nc -u -w 1 172.29.231.10 2350"
sleep 1
docker exec "${server}" sh -c "test \"\$(cat /tmp/tcp)\" = tcp-enumeration"
docker exec "${server}" sh -c "test \"\$(cat /tmp/udp)\" = udp-gameplay"

printf '%s\n' '{"event":"pass","payload":{"routed_tcp_47624":true,"routed_udp_2350":true,"two_subnets":true,"explicit_host_address":true}}'
