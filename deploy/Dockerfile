FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv
# Keep the runtime aligned with pyproject.toml and uv.lock (Python 3.13 only).
FROM python:3.15.1-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
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
