FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv
# Keep the runtime aligned with pyproject.toml and uv.lock (Python 3.13 only).
FROM python:3.13.14-slim@sha256:9662417aace5ae7b8e2609cce472b72a8958e134ba372808abe9cc1a0c0125e6
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-build --no-install-project --extra storage --extra mail
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
RUN useradd --uid 10001 --create-home commerce && mkdir -p /app/var/files && chown -R commerce /app/var
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src PYTHONUNBUFFERED=1
USER commerce
CMD ["uvicorn", "a_commerce.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
