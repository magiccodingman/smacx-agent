#!/bin/sh
set -eu

hostname="${SMACX_PUBLIC_HOSTNAME:-}"
config=/tmp/Caddyfile

case "$hostname" in
    *://*|*/*|*:*) echo "SMACX_PUBLIC_HOSTNAME must be a hostname without scheme, path, or port." >&2; exit 2;;
esac

cat >"$config" <<'EOF'
{
    admin off
    auto_https disable_redirects
}

http://:80 {
    reverse_proxy control-center:8080
}
EOF

if [ -n "$hostname" ]; then
    cat >>"$config" <<EOF

$hostname {
    reverse_proxy control-center:8080
}
EOF
fi

exec caddy run --config "$config" --adapter caddyfile
