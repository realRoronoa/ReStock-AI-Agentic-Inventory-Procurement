# ReStock AI backend.
#
# NOT VERIFIED — the Docker daemon was not running in the environment where this
# was written, so this image has never been built or run. Treat it as a
# considered starting point, not a tested artefact.

FROM python:3.11-slim AS base

# - PYTHONDONTWRITEBYTECODE: no .pyc litter in a layer that is thrown away.
# - PYTHONUNBUFFERED: logs reach the container log immediately rather than
#   sitting in a buffer, which matters when diagnosing a payout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq for psycopg when running against PostgreSQL. curl is used by the
# healthcheck below.
RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so a code change does not invalidate the install layer.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini pytest.ini ./
COPY alembic ./alembic
COPY app ./app

# Run as a non-root user. A process that only needs to serve HTTP has no
# business being able to write to the image.
RUN useradd --create-home --uid 10001 restock \
    && chown -R restock:restock /app
USER restock

EXPOSE 8000

# Hits the real health endpoint, which performs a database round-trip, so an
# unhealthy database marks the container unhealthy rather than silently serving
# 503s.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Migrations are NOT run here. Running `alembic upgrade head` on container start
# means every replica races to migrate on a scale-up. Run it as a one-off task
# or an init container instead — see docker-compose.yml, which does exactly that.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
