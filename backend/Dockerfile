# FORMA API / worker image (ADR-016). One image, two commands:
#   forma serve  (API)      forma worker  (job worker; FORMA_JOB_BACKEND=arq needs Redis)
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/backend
COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
RUN pip install uv && uv pip install --system .
COPY target_schema /app/target_schema
COPY policies /app/policies
ENV FORMA_TARGET_SCHEMA_DIR=/app/target_schema FORMA_POLICIES_DIR=/app/policies FORMA_DATA_DIR=/data
RUN useradd --create-home --uid 10001 forma && mkdir -p /data && chown -R forma:forma /data /app
USER forma
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
CMD ["forma", "serve", "--host", "0.0.0.0", "--port", "8000"]
