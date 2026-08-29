# syntax=docker/dockerfile:1.7
FROM tailscale/tailscale:stable@sha256:8c42c4574ab066384fcb72f69e086a2ff1dd3652eb6f56856cee34bcf0d2f680
COPY --chmod=0755 transport/tailscale-router-entrypoint.sh /usr/local/bin/smacx-tailscale-router
ENTRYPOINT ["/usr/local/bin/smacx-tailscale-router"]
