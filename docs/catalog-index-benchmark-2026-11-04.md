# 商品分类分页联合索引与 JMeter 对照（2026-11-04）

## 改动与原因

新增 `ix_products_active_category_id`：`products (category_id, id) WHERE active IS TRUE`，并在 ORM 与 Alembic 迁移 `0011_catalog_category_index` 中保持一致。

`services/catalog.py:list_products()` 公开目录按分类等值筛选、按商品 ID 游标过滤并排序，且始终要求商品上架。原有分类单列索引不提供 ID 顺序；代表查询原计划选择主键有序扫描，再过滤分类和上架状态。新索引将分类筛选、游标范围和顺序放进同一访问路径。单列分类索引保留；本次没有更改 SQL、缓存行为或业务数据。

## 环境与方法

- macOS Apple Silicon 上的 Docker PostgreSQL 18.6 / aarch64，独立容器 `commerce-index-20261104`，2 CPU、1 GiB，数据库 `commerce_load_index`。数据库与发压器共用宿主资源。
- 合成数据：100,000 商品、100 分类，各分类 1,000 商品中 800 上架；每商品 1 SKU，1 个有效店铺。商品 UUID 顺序与分类无关联。
- Apache JMeter 5.6.3，PostgreSQL JDBC 42.7.8，JBR 25.0.3。10 线程，连接池 10，目标 100 SQL 查询/秒；Prepared Select Statement。
- 每轮 2,000 查询，各线程前 50 次预热，共剔除 500 次，统计剩余 1,500 次。100 分类 × 3 游标位置；每次断言返回 21 行。
- 运行顺序 A1 → B1 → B2 → A2，A 为迁移 0010（无新增索引），B 为迁移 0011。每轮独立 JVM、相同计划及预热，不清数据库/OS 缓存。
- 测量包含 JDBC 执行、连接获取和结果读取；不含参数生成与结果断言，不经过 Redis/HTTP，也不含 `product_outputs()` 的后续关联加载。它是数据库主查询对照，不是接口、容量或生产 SLA 测试。

## 结果

| 轮次 | 新索引 | 统计样本 | 失败 | 均值 ms | P50 ms | P95 ms | P99 ms | 最大 ms | 全轮实际查询/s |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A1 | 无 | 1,500 | 0 | 3.987 | 3 | 8 | 13 | 30 | 99.99 |
| B1 | 有 | 1,500 | 0 | 2.481 | 2 | 6 | 10 | 19 | 100.07 |
| B2 | 有 | 1,500 | 0 | 2.660 | 2 | 6 | 11 | 24 | 100.01 |
| A2 | 无 | 1,500 | 0 | 3.987 | 3 | 8 | 14 | 33 | 100.05 |

两组配对的 P95 都由 8 ms 到 6 ms（降低 25%）；均值分别降低约 37.8%、33.3%。正式四轮共 8,000 次查询（含 2,000 次预热）全部成功。分位数按 JTL `elapsed` 的最近秩计算，单位为毫秒整数；小绝对延迟下百分比受计时精度影响，不外推其他数据分布和负载。

“全轮实际查询/s”按全部 2,000 个样本的首个开始到最后结束计算。各线程预热与统计阶段交错，单独统计样本数除以其首尾时间跨度约为 90.07–90.41/s；该跨度内仍混有其他线程的预热请求，因此不能把统计窗口说成严格的 100 次/秒开放到达率实验。JMeter Constant Throughput Timer 是线程式节流。

## 执行计划与正确性

代表查询：分类 42、游标 `55555555-5555-5555-5555-555555555555`、LIMIT 21。

| 观测 | 无新增索引 | 有新增索引 |
| --- | --- | --- |
| 商品访问路径 | `pk_products` | `ix_products_active_category_id` |
| 商品扫描额外过滤行数 | 2,823 | 无额外过滤 |
| 顶层 Shared Hit Blocks | 2,943 | 109 |
| 单次 EXPLAIN 执行时间 | 3.648 ms | 0.331 ms |

单次 EXPLAIN 只用于解释访问路径和扫描量，不用作负载测试收益。新索引占 3,904 KiB，商品堆表约 33 MiB；未测写入开销。

300 组分类与游标查询在新增索引前后返回的 21 个商品 ID 及顺序完全一致。迁移 upgrade、downgrade、再次 upgrade 成功；`alembic check` 无差异。`scripts/check.sh` 的 Ruff、格式、Mypy 和编译检查通过。没有修改既有测试文件。

## 证据与复现

- [JMeter 计划及复现说明](../scripts/benchmarks/catalog-index/README.md)
- [JMX](../scripts/benchmarks/catalog-index/catalog.jmx)、[合成数据 SQL](../scripts/benchmarks/catalog-index/seed.sql)、[代表查询](../scripts/benchmarks/catalog-index/query.sql)
- 本地原始证据（gitignored）：`var/validation/catalog-index-20261104/` 下的 A1/B1/B2/A2 JTL、日志、`summary.json`、`plan-before.json`、`plan-after.json`、`results-before.json`、`results-after.json`。
- 正式运行前出现的 Java 25/Groovy 兼容错误、ResultSet 限制配置错误已修正；失败启动记录以 `setup-*` 和 `debug*` 单独保留，不计入正式样本，也不代表应用 SQL 失败。

本次仅对隔离数据库应用迁移，未发布或迁移业务数据库。迁移采用事务式 CREATE INDEX，会在建索引期间阻塞商品表写入，实际发布需安排维护窗口。
