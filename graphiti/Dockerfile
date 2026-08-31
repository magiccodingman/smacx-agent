# syntax=docker/dockerfile:1.7

FROM python:3.13-slim@sha256:7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin smacx \
    && install -d -o smacx -g smacx /opt/smacx/src /var/lib/smacx

COPY requirements-graphiti.txt /tmp/requirements-graphiti.txt
RUN pip install --no-cache-dir -r /tmp/requirements-graphiti.txt \
    && rm /tmp/requirements-graphiti.txt

COPY src/smacx_store.py src/smacx_generation.py src/smacx_graphiti.py src/smacx_graphiti_worker.py /opt/smacx/src/

ENV PYTHONPATH=/opt/smacx/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GRAPHITI_TELEMETRY_ENABLED=false

USER smacx
ENTRYPOINT ["python", "-m", "smacx_graphiti_worker"]
CMD ["--database", "/var/lib/smacx/smacx.sqlite3"]
