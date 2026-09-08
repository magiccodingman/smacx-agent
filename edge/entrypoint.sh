#!/bin/sh
set -eu

hostname="${SMACX_PUBLIC_HOSTNAME:-}"
lan_host="${SMACX_LAN_HTTPS_HOST:-}"
lan_port="${SMACX_LAN_HTTPS_PORT:-443}"
config=/tmp/Caddyfile

# These values become Caddyfile tokens. Accept only explicit DNS names/IPv4,
# never arbitrary request Host headers or configuration fragments.
for host in "$hostname" "$lan_host"; do
    case "$host" in
        *[!a-zA-Z0-9.-]*|.*|-*) echo "HTTPS hosts must be DNS names or IPv4 addresses without scheme, path, or port." >&2; exit 2;;
    esac
done
case "$lan_port" in
    ''|*[!0-9]*) echo "SMACX_LAN_HTTPS_PORT must be a port number." >&2; exit 2;;
esac
if [ "$lan_port" -lt 1 ] || [ "$lan_port" -gt 65535 ]; then
    echo "SMACX_LAN_HTTPS_PORT must be between 1 and 65535." >&2; exit 2
fi
if [ -n "$lan_host" ] && [ "$lan_host" = "$hostname" ]; then
    echo "LAN and public HTTPS hosts must differ." >&2; exit 2
fi

cat >"$config" <<'EOF'
{
    admin off
    auto_https disable_redirects
    skip_install_trust
EOF
# Browsers omit SNI for IP literals. Select the configured LAN certificate
# for those handshakes; named public hosts still select their own certificate.
if [ -n "$lan_host" ]; then
    printf '    default_sni %s\n' "$lan_host" >>"$config"
fi
cat >>"$config" <<'EOF'
}

http://:80 {
EOF

if [ -n "$lan_host" ]; then
    lan_authority="$lan_host"
    if [ "$lan_port" != 443 ]; then lan_authority="$lan_host:$lan_port"; fi
    cat >>"$config" <<EOF
    @lan host $lan_host
    redir @lan https://$lan_authority{uri} 308
EOF
fi
if [ -n "$hostname" ]; then
    cat >>"$config" <<EOF
    @public host $hostname
    redir @public https://$hostname{uri} 308
EOF
fi
cat >>"$config" <<'EOF'
    reverse_proxy control-center:8080
}
EOF

if [ -n "$lan_host" ]; then
    cat >>"$config" <<EOF

https://$lan_host {
    tls internal
    reverse_proxy control-center:8080
}
EOF
fi

if [ -n "$hostname" ]; then
    cat >>"$config" <<EOF

$hostname {
    reverse_proxy control-center:8080
}
EOF
fi

exec caddy run --config "$config" --adapter caddyfile
