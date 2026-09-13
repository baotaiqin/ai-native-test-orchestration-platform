# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system --gid 10002 demo \
    && useradd --system --uid 10002 --gid demo --home-dir /nonexistent --shell /usr/sbin/nologin demo

COPY --chown=10002:10002 demo ./demo

USER 10002:10002

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).read()"]

CMD ["python", "-m", "demo.server", "--host", "0.0.0.0", "--port", "8765"]
