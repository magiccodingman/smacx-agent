# syntax=docker/dockerfile:1.7

# Debian 13's original SQLite package contains an upstream WAL-reset bug. The
# platform's event and memory store relies on WAL, so build the fixed release
# used by the reference Hermes deployment rather than accepting silent risk to
# long-running match history.
FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132 AS sqlite-build

ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_SHA256=0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c
RUN apt-get -o Acquire::Retries=3 update \
    && apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        build-essential ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && (curl -fsSL --retry 1 --retry-all-errors --connect-timeout 15 --max-time 60 \
        -o /tmp/sqlite.tar.gz \
        "https://sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" \
        || curl -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 120 \
        -o /tmp/sqlite.tar.gz \
        "https://sources.buildroot.net/sqlite/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz") \
    && printf '%s  %s\n' "${SQLITE_SHA256}" /tmp/sqlite.tar.gz > /tmp/sqlite.sha256 \
    && sha256sum -c /tmp/sqlite.sha256 \
    && tar -xzf /tmp/sqlite.tar.gz -C /tmp \
    && cd "/tmp/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}" \
    && CFLAGS="-O2 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_COLUMN_METADATA \
        -DSQLITE_ENABLE_UNLOCK_NOTIFY -DSQLITE_ENABLE_DBSTAT_VTAB \
        -DSQLITE_ENABLE_MATH_FUNCTIONS -DSQLITE_SECURE_DELETE \
        -DSQLITE_THREADSAFE=1 -DSQLITE_MAX_VARIABLE_NUMBER=250000" \
        ./configure --prefix=/opt/sqlite-fixed --disable-static \
    && make -j2 \
    && make install

FROM debian:trixie-slim@sha256:d7e12182ce18b85b93007c1dedf31f2d29e01ccf3182cc4017c709b6259bc132

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /var/lib/smacx /opt/smacx/src /opt/smacx/control_center/static

COPY --from=sqlite-build /opt/sqlite-fixed/lib/libsqlite3.so.3.53.4 /usr/local/lib/
RUN ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so.0 \
    && ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so \
    && printf '/usr/local/lib\n' > /etc/ld.so.conf.d/000-sqlite-fixed.conf \
    && ldconfig \
    && python3 -c "import sqlite3,sys; v=sqlite3.sqlite_version_info; sys.exit(1) if v < (3,51,3) else None; db=sqlite3.connect(':memory:'); db.execute('CREATE VIRTUAL TABLE docs USING fts5(content)'); db.close()"

RUN python3 -m venv /opt/smacx/mcp-venv \
    && /opt/smacx/mcp-venv/bin/pip install --no-cache-dir "mcp==2.0.0"

COPY src/smacx_store.py src/smacx_control.py src/smacx_control_server.py \
    src/smacx_docker.py src/smacx_worker_manager.py src/smacx_operations.py \
    src/smacx_harness_manager.py src/smacx_hermes.py src/smacx_controller.py \
    src/smacx_mcp.py src/smacx_reference.py src/smacx_capabilities.py /opt/smacx/src/
COPY knowledge/ /opt/smacx/knowledge/
COPY control_center/static/ /opt/smacx/control_center/static/
COPY --chmod=0755 control_center/smacx-control /usr/local/bin/smacx-control
COPY --chmod=0755 control_center/smacx-mcp /usr/local/bin/smacx-mcp

ENV PYTHONPATH=/opt/smacx/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMACX_CONTROL_DATA=/var/lib/smacx \
    SMACX_CONTROL_HOST=0.0.0.0 \
    SMACX_CONTROL_PORT=8080

WORKDIR /opt/smacx
VOLUME ["/var/lib/smacx"]
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=4 \
    CMD ["python3", "-c", "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2)); raise SystemExit(0 if data.get('ok') else 1)"]

USER smacx
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["smacx-control", "serve"]
