FROM ghcr.io/astral-sh/uv:0.12.2@sha256:069a51314a7bb6031777a9273205fe1b0b19e914ef418207d1338b268df641dd AS uv
FROM postgres:18.6@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722
COPY --from=uv /uv /usr/local/bin/uv
# 仅在 Linux 镜像安装解释器；Python 依赖使用独立的带哈希锁定清单。
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/patroni/requirements.txt /opt/patroni/requirements.txt
RUN uv venv --python /usr/bin/python3 /opt/patroni/venv \
    && uv pip sync --python /opt/patroni/venv/bin/python --require-hashes /opt/patroni/requirements.txt
COPY deploy/patroni/patroni.yml deploy/patroni/bootstrap.py /opt/patroni/
ENV PATH="/opt/patroni/venv/bin:$PATH"
USER postgres
ENTRYPOINT ["patroni", "/opt/patroni/patroni.yml"]
