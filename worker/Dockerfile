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

FROM debian:trixie-slim AS worker

RUN dpkg --add-architecture i386 \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        cabextract ca-certificates fluxbox novnc procps python3 socat tini \
        websockify wine wine32:i386 wine64 x11-utils x11vnc xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx /run/smacx

COPY --from=bridge-build /build/thinker.dll /opt/smacx/bridge/thinker.dll
COPY --from=bridge-build /build/thinker.exe /opt/smacx/bridge/thinker.exe
COPY worker/entrypoint.py worker/healthcheck.py worker/alsa-null.conf \
    worker/fluxbox-init worker/fluxbox-overlay /opt/smacx/

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
    SMACX_VIEW_MODE=view-only

VOLUME ["/var/lib/smacx"]
EXPOSE 47814 6080
HEALTHCHECK --interval=10s --timeout=3s --start-period=90s --retries=6 \
    CMD ["python3", "/opt/smacx/healthcheck.py"]

USER smacx
ENTRYPOINT ["/usr/bin/tini", "--", "python3", "/opt/smacx/entrypoint.py"]
