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
RUN sh scripts/ci/build-web.sh

FROM python:3-slim AS selkies-python-build
RUN python3 -m pip install --no-cache-dir build
WORKDIR /src/selkies
COPY --from=selkies-web-build /src/selkies/src ./src
COPY --from=selkies-web-build /src/selkies/README.md /src/selkies/pyproject.toml ./
RUN python3 -m build --wheel

FROM debian:trixie-slim AS worker

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        cabextract ca-certificates fluxbox novnc poppler-utils procps python3 socat tini \
        python3-pip python3-venv pulseaudio websockify wine wine32:i386 wine64 \
        x11-utils x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx /run/smacx

COPY --from=bridge-build /build/thinker.dll /opt/smacx/bridge/thinker.dll
COPY --from=bridge-build /build/thinker.exe /opt/smacx/bridge/thinker.exe
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

ENV PATH="/opt/selkies-venv/bin:${PATH}"
COPY worker/entrypoint.py worker/healthcheck.py worker/inspect_source.py \
    worker/extract_reference.py worker/list_scenarios.py worker/alsa-null.conf \
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
    SMACX_VIEW_ENABLE=0 \
    SMACX_VIEW_MODE=view-only \
    SMACX_STREAM_BACKEND=selkies

VOLUME ["/var/lib/smacx"]
EXPOSE 47814 6080
HEALTHCHECK --interval=10s --timeout=3s --start-period=90s --retries=6 \
    CMD ["python3", "/opt/smacx/healthcheck.py"]

USER smacx
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/smacx/entrypoint.py"]
