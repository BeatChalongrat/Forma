FROM python:3.12-slim
WORKDIR /app/backend
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv && uv pip install --system -e .
ENV FORMA_TARGET_SCHEMA_DIR=/app/target_schema FORMA_POLICIES_DIR=/app/policies
EXPOSE 8000
CMD ["forma", "serve", "--host", "0.0.0.0", "--port", "8000"]
