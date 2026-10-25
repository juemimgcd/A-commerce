"""首次建库；只在 Patroni 初始化空集群时执行，不接管已有单节点卷。"""

import os
import sys

import psycopg
from psycopg import sql

with psycopg.connect(sys.argv[1], autocommit=True) as connection:
    # 集群管理员与应用账号分离，应用账号拥有业务库但没有超级用户权限。
    connection.execute(
        sql.SQL("CREATE ROLE commerce LOGIN PASSWORD {} CREATEDB").format(
            sql.Literal(os.environ["POSTGRES_PASSWORD"])
        )
    )
    connection.execute(
        sql.SQL("CREATE DATABASE {} OWNER commerce").format(
            sql.Identifier(os.environ.get("COMMERCE_DATABASE_NAME", "commerce"))
        )
    )
