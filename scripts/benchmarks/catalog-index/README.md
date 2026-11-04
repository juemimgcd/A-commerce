# 商品分类分页索引：JMeter JDBC 对照

本计划验证 `list_products()` 中“上架商品 + 分类 + ID 游标 + SKU EXISTS + ID 排序”的主查询。SQL 与服务逻辑一致，返回 `limit + 1 = 21` 条商品；不包含后续 `product_outputs()` 的 SKU/库存/媒体加载，也不包含 HTTP、Redis、应用连接池或 JSON 序列化。结果不能写成接口 P95。

## 固定条件

- 隔离 PostgreSQL 18.6，容器限制 2 CPU / 1 GiB，只绑定本机 55439。
- `commerce_load_index` 数据库，100,000 商品、100 分类、每分类 1,000 商品且 800 上架；每商品 1 个有效 SKU。UUID 用 MD5 确定生成，商品 ID 顺序与分类无关联。
- JMeter 5.6.3，PostgreSQL JDBC 42.7.8；10 线程、10 数据库连接，Constant Throughput Timer 目标 100 查询/秒。这是线程式节流目标，不是开放到达率模型。
- 每轮 2,000 查询，其中每线程前 50 次（共 500）标为 `warmup`，其余 1,500 标为 `category_query`，仅后者参与报告。
- 按固定规则轮换 100 分类和三个 ID 游标，共 300 种组合。每次要求返回 21 行，SQL 异常或断言失败停止测试。
- A1 → B1 → B2 → A2；A 为迁移 0010，B 为迁移 0011。每轮独立 JMeter JVM 并包含预热；不清 PostgreSQL/OS 缓存，测的是热数据库场景。

## 复现

只使用新建的一次性容器；不要连接业务数据库。以下容器名若已存在，不要删除重建，另行选择未占用的名称和端口并同步修改 JMX。

```sh
docker run -d --name commerce-index-20261104 --cpus=2 --memory=1g \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=commerce_load_index \
  -p 127.0.0.1:55439:5432 postgres:18.6
export COMMERCE_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55439/commerce_load_index
export COMMERCE_REDIS_URL=redis://127.0.0.1:55440
export COMMERCE_AMQP_URL=amqp://localhost
export COMMERCE_PROVIDER_KEY=isolated-benchmark-key-00000000000
export COMMERCE_CALLBACK_SECRET=isolated-benchmark-key-00000000000
export PYTHONPATH=src
.venv/bin/alembic upgrade 0010_merchant_ops
docker exec -i commerce-index-20261104 psql -U postgres -d commerce_load_index \
  < scripts/benchmarks/catalog-index/seed.sql
```

本次使用临时解压的 JMeter 5.6.3 与 PyCharm 自带 JBR 25.0.3，未完成 Homebrew 全局安装；JMX 使用 BeanShell，避免内置 Groovy 3 与 Java 25 的兼容问题。复现时可安装 JMeter（macOS 可 `brew install jmeter`），从 Maven Central 获取 `org.postgresql:postgresql:42.7.8` 的 JDBC JAR。每轮使用独立输出文件，不能追加到历史 JTL。

```sh
jmeter -n -t scripts/benchmarks/catalog-index/catalog.jmx \
  -Jsearch_paths=/absolute/path/postgresql-42.7.8.jar \
  -l /absolute/path/A1.jtl -j /absolute/path/A1.log
.venv/bin/alembic upgrade head
# 同样命令分别输出 B1.jtl / B1.log、B2.jtl / B2.log
.venv/bin/alembic downgrade 0010_merchant_ops
# 同样命令输出 A2.jtl / A2.log
.venv/bin/alembic upgrade head
.venv/bin/alembic check
```

`query.sql` 是固定分类和游标的代表查询，可在 A、B 阶段分别加 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` 保存计划。比较 300 组返回商品 ID 和顺序，并检查 JTL 的错误数、实际速率及分位数；JMeter 毫秒整数计时对低延迟分位数精度有限。计时包含 JDBC 执行、连接取得、结果读取，不包含前置 BeanShell 参数生成和后置结果断言。

生产迁移说明：0011 使用项目既有事务式 `CREATE INDEX`，建索引期间会阻塞商品表写入，应安排维护窗口。本次不执行生产迁移；常规分类单列索引保留给包含下架商品的查询与外键相关访问。

依据：[PostgreSQL 多列索引](https://www.postgresql.org/docs/18/indexes-multicolumn.html)、[CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html)、[JMeter CLI](https://jmeter.apache.org/usermanual/get-started.html)、[JDBC Request](https://jmeter.apache.org/usermanual/component_reference.html#JDBC_Request)。
