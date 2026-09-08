# LAN HTTPS streaming acceptance (2026-09-08)

## Change

Opt-in `SMACX_LAN_HTTPS_HOST` provisions a local Caddy certificate and redirects
that host's HTTP traffic to HTTPS, preserving path/query. The default published
ports remain HTTP 8080 and HTTPS 443. An explicit alternate published HTTPS port
is supported. Loopback HTTP remains available to the operator CLI. Public DNS
HTTPS retains public certificate issuance, alongside the distinct LAN host.

No root certificates are installed. Caddy's persistent data volume owns its
local CA. IP-literal clients omit SNI, so the configured LAN host is also the
default TLS server name. Without that setting, a real browser test initially
failed its handshake; the repaired configuration passes.

## Evidence

- `python3 scripts/lan_https_config_test.py`: 5 tests passed, including LAN/public
  coexistence, custom published port, unchanged unconfigured behavior, conflicting
  hosts and configuration-injection rejection.
- Real Caddy 2.10.2 image: `caddy adapt --validate` passed with LAN and public
  hosts configured together. No public ACME issuance was attempted in this check.
- Isolated edge on ports 8186/8446: HTTP returned 308 to the configured HTTPS
  address and preserved `/spectate/test?seat=0`. Loopback `/healthz` returned 200.
- `scripts/lan_https_browser_test.cjs`: real Chrome 152.0.7977.64, fresh browser
  profile, headless, no `ignoreHTTPSErrors`, certificate bypass switches, or trust
  installation. The test clicks the real certificate interstitial's Advanced /
  Proceed controls, then checks the served portal origin. Result:

```json
{
  "secureContext": true,
  "audioDecoder": "function",
  "videoDecoder": "function",
  "audioWorkletLoaded": true,
  "opusSupported": true
}
```

- Portal .NET suite: 86 passed. Existing stream selection tests cover HTTPS
  selecting the primary stream and plain nonloopback HTTP selecting compatibility.

## Scope of proof

This proves LAN redirect, TLS warning acceptance, portal access, browser decoder
availability and executable AudioWorklet loading. The existing worker primary
stream enables audio; the HTTPS portal selects that primary stream. No game
worker was running during this checkpoint, so native game audio capture,
transport and audible playback are **not** newly verified here. Browser autoplay
may require clicking the stream. Firefox, Safari, managed browser policy and PWA
installation are not covered by the Chrome result. Certificate exceptions may
need accepting again after certificate renewal.

## Deployment

Main `smacx-agent` stack redeployed with `smacx-agent-edge:lan-https` and
`smacx-portal:lan-https`. Only edge and portal were recreated. Existing control,
harness, worker settings and persistent volumes were preserved; the other
agent's readiness stack was not touched. Temporary validation edge was removed.

After startup, the browser test passed again against the actual port-8080 entry
point, ending at `https://10.26.26.104/` with the same five successful API checks.
`http://127.0.0.1:8080/healthz` returned 200, the portal reported healthy, and
the LAN HTTP spectator path/query redirected to the corresponding HTTPS URL.
An initial probe during container recreation encountered connection refusal;
the reported successful results are from the completed rollout.

Local deployment uses the existing resolved Compose base plus
`runtime/astra/main-expansion.override.yaml` and
`runtime/astra/main-lan-https.override.yaml` in the Astra checkout. Future
deployments should retain these overrides or configure the documented `.env`
settings in their source checkout; the base resolved JSON predates LAN HTTPS.
