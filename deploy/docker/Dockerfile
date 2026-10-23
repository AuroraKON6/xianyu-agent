# R16: reproducible single-writer image (offline profile first).
# Python pinned to 3.8-slim to match the verified runtime (venv 3.8.5);
# upgrade to a newer python happens only after the container suite is
# green on 3.8 (master plan §2: fix the old baseline first).
FROM python:3.8-slim AS base

# non-root user from the start
RUN useradd --create-home --uid 10001 xianyu
WORKDIR /app

# lock-verified dependencies only (requirements.txt is pinned); NO
# runtime pip/npm installs happen after this layer
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --target /app/deps -r /app/requirements.txt \
    && python -m compileall -q /app/deps
ENV PYTHONPATH="/app:/app/deps" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# application code only — .dockerignore guarantees no .env, no data/,
# no backups/, no node_modules, no .venv, no browser profiles
COPY xianyu_agent/ /app/xianyu_agent/
COPY prompts/ /app/prompts/
COPY web/ /app/web/
COPY migrations/ /app/migrations/

# the state volume must be writable by the non-root user: docker mounts
# named volumes root-owned, so pre-create /state with uid 10001 ownership
RUN mkdir -p /state/data /state/outbox \
    && chown -R 10001:10001 /state

USER xianyu
# workdir is the STATE dir: legacy stores using the relative default
# "data" land inside the persistent volume instead of the read-only /app
WORKDIR /state
# default to the isolated test entrypoint so an accidental `docker run`
# without env cannot touch anything; production overrides the command
ENV XIANYU_TEST_ISOLATION=1 \
    XIANYU_ADAPTER=stub \
    DATA_DIR=/state/data \
    DB_PATH=/state/data/xianyu_data.db
VOLUME ["/state"]
EXPOSE 8788
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8788/health', timeout=3)"]
CMD ["python", "-m", "xianyu_agent"]
