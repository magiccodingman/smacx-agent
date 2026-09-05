# syntax=docker/dockerfile:1.7

FROM debian:trixie-slim AS bridge-build

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates cmake g++-mingw-w64-i686-posix gcc-mingw-w64-i686-posix ninja-build \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY bridge/ /src/bridge/
RUN cmake -S /src/bridge -B /build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build -j2

# Build Selkies from an immutable upstream revision.  The generated browser
# bundle is part of the Python wheel, so workers need only one private stream
# port and no public STUN/TURN infrastructure for the default WebSocket mode.
FROM node:26-alpine AS selkies-web-build
# This is the final upstream revision before the capture stack moved to the
# not-yet-published pixelflux/pcmflux 2.1 line.  It retains the current
# single-port WebSocket transport while resolving its native wheels from the
# released 2.0 series, which makes a clean reproducible build possible.
ARG SELKIES_COMMIT=be53b2c39670ccd1432fe50ebcd6d0ade72ce80a
RUN apk add --no-cache git
WORKDIR /src/selkies
RUN git init \
    && git remote add origin https://github.com/selkies-project/selkies.git \
    && git fetch --depth 1 origin "${SELKIES_COMMIT}" \
    && git checkout --detach FETCH_HEAD
COPY worker/patches/selkies-http-lan.patch /tmp/selkies-http-lan.patch
RUN git apply --check /tmp/selkies-http-lan.patch \
    && git apply /tmp/selkies-http-lan.patch
RUN sh scripts/ci/build-web.sh

FROM python:3-slim AS selkies-python-build
RUN python3 -m pip install --no-cache-dir build
WORKDIR /src/selkies
COPY --from=selkies-web-build /src/selkies/src ./src
COPY --from=selkies-web-build /src/selkies/README.md /src/selkies/pyproject.toml ./
RUN python3 -m build --wheel

# SMACX multiplayer depends on the legacy DirectPlay components in Microsoft's
# DirectX end-user redistributable. Microsoft removed this legacy download from
# its live CDN, so fetch the archived original Microsoft URL used by Winetricks
# and pin its exact digest. The project never vendors the binary, and operators
# do not have to hunt down a second runtime asset by hand.
FROM debian:trixie-slim AS directx-runtime
ARG DIRECTX_URL=https://web.archive.org/web/20100205000000id_/https://download.microsoft.com/download/E/E/1/EE17FF74-6C45-4575-9CF4-7FC2597ACD18/directx_feb2010_redist.exe
ARG DIRECTX_SHA256=f6d191e89a963d7cca34f169d30f49eab99c1ed3bb92da73ec43617caaa1e93f
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /opt/directx \
    && curl --fail --location --retry 3 --retry-all-errors \
        --connect-timeout 15 --max-time 300 \
        --output /opt/directx/directx_feb2010_redist.exe "${DIRECTX_URL}" \
    && printf '%s  %s\n' "${DIRECTX_SHA256}" /opt/directx/directx_feb2010_redist.exe \
        | sha256sum -c - \
    && rm -rf /var/lib/apt/lists/*

# GE-Proton is an open-source compatibility distribution published as a
# versioned GitHub release by its upstream maintainer. Seal one verified build
# into the worker image so an operator supplies only the legal game directory;
# Steam, a host Wine prefix, and a host Proton installation are not runtime
# dependencies. GE-Proton10-34 is deliberately pinned because its native
# bridge, DirectPlay, save/recovery, and semantic opening were certified here.
FROM debian:trixie-slim AS proton-runtime
ARG PROTON_URL=https://github.com/GloriousEggroll/proton-ge-custom/releases/download/GE-Proton10-34/GE-Proton10-34.tar.gz
ARG PROTON_SHA512=9fd0b2cfbd501c0b5c892239c392c7283a029b5e5d5a77d3f85b0ce190d555456241a18eebca16b53f094b403499201c13550a3f0b9b365e1a5eb5737cbb7303
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /opt/proton /tmp/proton \
    && curl --fail --location --retry 3 --retry-all-errors \
        --connect-timeout 15 --max-time 600 --output /tmp/proton.tar.gz "${PROTON_URL}" \
    && printf '%s  %s\n' "${PROTON_SHA512}" /tmp/proton.tar.gz | sha512sum -c - \
    && tar -xzf /tmp/proton.tar.gz -C /opt/proton --strip-components=1 \
    && test -f /opt/proton/proton \
    && test -f /opt/proton/files/bin/wine \
    && test -f /opt/proton/files/bin/wineserver \
    && rm -rf /tmp/proton /tmp/proton.tar.gz /var/lib/apt/lists/*

FROM debian:trixie-slim AS worker

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        cabextract ca-certificates fluxbox novnc poppler-utils procps python3 socat tini zstd \
        python3-pip python3-venv pulseaudio websockify wine wine32:i386 wine64 \
        x11-utils x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx /run/smacx /opt/smacx/prepared

COPY --from=directx-runtime /opt/directx/directx_feb2010_redist.exe /opt/smacx/redist/directx_feb2010_redist.exe
# The compatibility runtime comes from the verified upstream release stage;
# no game worker mounts a host Proton tree.
COPY --from=proton-runtime /opt/proton /opt/proton
RUN test -f /opt/proton/proton \
    && test -f /opt/proton/files/bin/wine \
    && test -f /opt/proton/files/bin/wineserver \
    && sed -i 's|self.dist_lock = FileLock(self.path("dist.lock"), timeout=-1)|self.dist_lock = FileLock(os.environ.get("SMACX_PROTON_DIST_LOCK", self.path("dist.lock")), timeout=-1)|' /opt/proton/proton \
    && grep -Fq 'SMACX_PROTON_DIST_LOCK' /opt/proton/proton
COPY --from=selkies-web-build /src/selkies/LICENSE /opt/smacx/licenses/selkies-MPL-2.0.txt
COPY --from=selkies-python-build /src/selkies/dist/*.whl /tmp/selkies/
RUN python3 -m venv /opt/selkies-venv \
    && /opt/selkies-venv/bin/python -m pip install --no-cache-dir \
        pixelflux==2.0.0 pcmflux==2.0.0 \
    && /opt/selkies-venv/bin/python -m pip install --no-cache-dir /tmp/selkies/*.whl \
    && cp /opt/selkies-venv/lib/python*/site-packages/pixelflux-2.0.0.dist-info/licenses/LICENSE \
        /opt/smacx/licenses/pixelflux-MPL-2.0.txt \
    && cp /opt/selkies-venv/lib/python*/site-packages/pcmflux-2.0.0.dist-info/licenses/LICENSE \
        /opt/smacx/licenses/pcmflux-MPL-2.0.txt \
    && rm -rf /tmp/selkies

# Native changes must not invalidate the pinned compatibility runtime and
# installed streaming dependencies above.
COPY --from=bridge-build /build/thinker.dll /opt/smacx/bridge/thinker.dll
COPY --from=bridge-build /build/thinker.exe /opt/smacx/bridge/thinker.exe

ENV PATH="/opt/selkies-venv/bin:${PATH}"
COPY worker/entrypoint.py worker/healthcheck.py worker/inspect_source.py \
    worker/list_scenarios.py worker/compact_saves.py worker/alsa-null.conf \
    worker/fluxbox-init worker/fluxbox-overlay worker/thinker.ini \
    worker/modmenu.txt /opt/smacx/

ENV DISPLAY=:99 \
    WINEARCH=win64 \
    WINEPREFIX=/var/lib/smacx/prefix \
    WINEDEBUG=-all \
    ALSA_CONFIG_PATH=/opt/smacx/alsa-null.conf \
    SMACX_GAME_SOURCE=/game-source \
    SMACX_WORKER_ROOT=/var/lib/smacx \
    SMACX_AGENT_PORT=47813 \
    SMACX_BRIDGE_PROXY_PORT=47814 \
    SMACX_VIEW_PORT=6080 \
    SMACX_COMPAT_VIEW_PORT=6081 \
    SMACX_VIEW_ENABLE=0 \
    SMACX_VIEW_MODE=view-only \
    SMACX_STREAM_BACKEND=selkies

VOLUME ["/var/lib/smacx"]
EXPOSE 47814 6080 6081
HEALTHCHECK --interval=10s --timeout=3s --start-period=90s --retries=6 \
    CMD ["python3", "/opt/smacx/healthcheck.py"]

USER smacx
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/smacx/entrypoint.py"]
