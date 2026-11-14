# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.12-slim-trixie
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml constraints.txt README.md ./
COPY bot/ ./bot/
RUN python -m pip wheel --wheel-dir /wheels --constraint constraints.txt .

FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DOWNLOAD_DIR=/app/downloads CACHE_DB_PATH=/app/data/cache.sqlite3
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 bot \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /app --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data /app/downloads \
    && chown bot:bot /app/data /app/downloads \
    && chmod 700 /app/data /app/downloads
RUN --mount=type=bind,from=builder,source=/wheels,target=/wheels \
    python -m pip install --no-index --find-links=/wheels tiktok-ultra-downloader \
    && python -m pip check
WORKDIR /app
USER 10001:10001
STOPSIGNAL SIGTERM
CMD ["python", "-m", "bot.main"]
