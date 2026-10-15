# ZooTodo: one container, stdlib Python only (no pip install).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    ZOOTODO_DB=/data/zootodo.db

WORKDIR /app
COPY server.py index.html ./

# run as an unprivileged user; the SQLite database lives in the /data volume
RUN useradd --system --uid 10001 --home-dir /app zootodo \
    && mkdir /data && chown zootodo /data
USER zootodo
VOLUME /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/', timeout=4)"

# python as PID 1 ignores SIGTERM; SIGINT raises KeyboardInterrupt and exits right away
STOPSIGNAL SIGINT
CMD ["python", "server.py"]
