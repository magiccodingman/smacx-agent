#!/bin/sh
set -eu

provider="${SMACX_DDNS_PROVIDER:-off}"
hostname="${SMACX_DDNS_HOSTNAME:-${SMACX_PUBLIC_HOSTNAME:-}}"
token="${SMACX_DDNS_TOKEN:-}"
if [ -n "${SMACX_DDNS_TOKEN_FILE:-}" ] && [ -r "$SMACX_DDNS_TOKEN_FILE" ]; then
    token="$(cat "$SMACX_DDNS_TOKEN_FILE")"
fi
interval="${SMACX_DDNS_INTERVAL_SECONDS:-300}"

touch /tmp/ddns-ready
if [ "$provider" = "off" ] || [ -z "$hostname" ] || [ -z "$token" ]; then
    echo "SMACX dynamic DNS is idle; configure provider, hostname, and token to activate it."
    while :; do sleep 3600; done
fi

case "$interval" in *[!0-9]*|'') echo "Invalid DDNS interval" >&2; exit 2;; esac
if [ "$interval" -lt 60 ]; then interval=60; fi

while :; do
    case "$provider" in
        duckdns)
            domain="${hostname%%.*}"
            curl --fail --silent --show-error --max-time 30 \
                "https://www.duckdns.org/update?domains=${domain}&token=${token}&ip=" >/dev/null
            ;;
        dynu)
            curl --fail --silent --show-error --max-time 30 \
                -u "${SMACX_DDNS_USERNAME:-$hostname}:$token" \
                "https://api.dynu.com/nic/update?hostname=${hostname}" >/dev/null
            ;;
        freedns)
            curl --fail --silent --show-error --max-time 30 \
                "https://freedns.afraid.org/dynamic/update.php?${token}" >/dev/null
            ;;
        *) echo "Unsupported SMACX_DDNS_PROVIDER: $provider" >&2; exit 2;;
    esac
    date -u +%FT%TZ > /tmp/ddns-last-success
    sleep "$interval"
done
