#!/bin/sh
# 仅执行格式、静态类型与编译检查；不创建测试、不调用支付服务。
set -eu
uv sync --frozen --no-build --extra storage --extra mail
uv run --no-sync ruff check src scripts alembic tests deploy/patroni/bootstrap.py
uv run --no-sync ruff format --check src scripts alembic tests deploy/patroni/bootstrap.py
uv run --no-sync mypy src scripts alembic
uv run --no-sync python -m compileall -q src scripts alembic deploy/patroni
