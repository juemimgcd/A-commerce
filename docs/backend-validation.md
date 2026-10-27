# 本地后端回归与验收

2026-10-25。本轮补齐本机可完成的自动化回归、运行指标、监控部署与负载工具。
真实支付、生产凭证、服务器发布、跨主机高可用和 1 万在线容量仍独立验收。

后续同日补入 [Redis Sentinel 部署与自动切主回归](redis-sentinel.md)。下方历史结果表保留该轮 51 项检查；最新切主结果以独立 `sentinel.json` / `sentinel-junit.xml` 为准。

[PostgreSQL 与 RabbitMQ 高可用演练](postgres-rabbitmq-ha.md) 使用 `validate_deployment.py --ha`，
在同一隔离项目中增加主库停止/恢复、同步副本中断、etcd 多数派丢失、quorum 消息恢复、
业务 outbox 补发和切主后备份恢复。原始证据为 `ha.json` / `ha-junit.xml`。

## 自动化回归

在仓库根目录执行：

```sh
sh scripts/check.sh
uv run --no-sync python scripts/test_backend.py -q -W error::ResourceWarning
```

需要 Python 3.13、uv、Docker。入口为每次运行创建独立 Compose 项目、随机凭证、
独立 PostgreSQL/Redis/RabbitMQ 和本机 HTTP API/provider。每轮选择空闲端口，并在
stop/start 期间保持固定。入口不读取业务 `.env`，不复用业务卷；退出只清理本轮项目。
fixture 在 TRUNCATE/FLUSHDB 前检查显式测试标记和 `commerce_test` 数据库。
不要把业务地址手动填入测试环境。

默认收集整个 `tests/`；可追加 pytest 文件参数或 `-k`。JUnit、覆盖率 JSON、
结果摘要和服务日志保存在忽略目录 `var/validation/commerce-test-*/`。

回归覆盖以下边界：

- 并发幂等下单、同键参数冲突、最后一件竞争、多 SKU 缺货整单回滚、报价失效。
- 对象/角色隔离、敏感操作重新认证、账号禁用、改密和重置后的会话撤销。
- 回调验签/时效/金额/商户、重复/乱序付款与物流、迟到付款补偿退款。
- 取消与拣货竞争、两次部分退货、重复验收不重复退款/入库、资金 CSV 导出。
- 真实本地 SMTP TCP 投递、一次性重置令牌、改密作废旧重置凭证。
- 图片解码与下载、孤立对象清理保护已关联媒体、输入大小和响应脱敏。
- 真实 Redis 共享额度与组合扣减；停止 Redis 后查询继续、写入失败关闭、合法回调继续。
- outbox 确认、不可路由、提交后未 ack 重投、异常消息隔离和实际 worker 子进程。
- broker 停止/恢复、到期租约恢复、旧 fence 拒绝、对账补齐付款确认。
- 事件归档、游标 410、固定快照和快照之后的增量事件。
- 实际备份/恢复脚本的新库往返，核对订单、资金、库存、迁移和 SQLite WAL 事实；拒绝覆盖已有恢复库。
- API/后台指标、池耗尽、SQL 错误、两种负载模型和压测数据生成的目标限制。

S3 通过 botocore Stubber 验证 SDK 参数和预签名读取，是契约测试，不能代表实际桶、
网络和权限验收。SMTP 使用本地无认证/无 TLS 的接收器；真实服务的认证、TLS 和送达率另验收。
CI 新增 regression job，其通过后才允许构建发布。本地成功不代表远端 CI 已执行。

## 指标与监控

新增 `commerce_db_acquire_seconds`、`commerce_db_pool_timeouts_total`、
`commerce_db_connections_used`、`commerce_db_pool_size`、`commerce_db_query_seconds`、
`commerce_db_errors_total`、`commerce_catalog_cache_total`、`commerce_catalog_origin_seconds`
和 `commerce_outbox_publish_total`，不使用 SQL 文本、参数、凭证、用户/IP/SKU 作为标签。

获取连接耗时包含排队和新连接建立；SQL 耗时包含锁等待，但不等于独立锁等待诊断。
池计时使用当前固定 SQLAlchemy 版本的 `_do_get` 扩展入口，升级该依赖需复跑池耗尽回归。
后台容器以 `COMMERCE_METRICS_PORT=9100` 导出本进程指标，不映射宿主机；宿主机默认 0 关闭。
API 使用内部 `/metrics`，公开网关仍隐藏该路径。

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml \
  -f deploy/compose.monitoring.yaml up -d --wait
```

Prometheus 默认监听 `127.0.0.1:59090`，Alertmanager 监听 `127.0.0.1:59093`。
抓取三个 API、relay、三组 worker 和 RabbitMQ，以及六个 Redis 数据节点、三个 Sentinel，
共 17 个目标。Redis exporter 的认证、指标边界与配置升级见 [Redis 监控](redis-sentinel.md#redis-监控)。RabbitMQ 启用
`rabbitmq_prometheus` 并抓取 per-object 指标；配置依据为
[RabbitMQ 官方文档](https://www.rabbitmq.com/docs/prometheus)和
[Prometheus 配置文档](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)。

规则覆盖采集中断、后台心跳、积压、资金复核、池超时、HTTP 5xx 和隔离队列。
数据库快照在多个 API 重复导出，聚合先取 max 去重；`tests/prometheus-rules.yml`
验证采集中断和去重。Alertmanager 的 local 接收器只接收和展示，不向任何人发消息。
生产外部通知、数据库/缓存 exporter、完整仪表盘和 trace 仍需单独配置。

## 全容器验收

```sh
uv run --no-sync python scripts/validate_deployment.py
```

入口创建随机独立项目、凭证、卷、loopback 端口和网段，构建本机镜像并依次：

1. 检查 Prometheus/Alertmanager 配置及规则样例，等待 17 个指标目标健康。
2. 验证两组 Redis 的哨兵发现与复制；在独立库生成数据，执行真实切主、原主恢复与无副本失败关闭回归，然后执行完整购买负载。
3. 核对订单占用与库存、实收与资金分录一致。
4. 停止一个 API，等待失败告警到达 Alertmanager，检查剩余实例仍可查询。
5. 恢复实例并执行滚动脚本，等待告警解除。
6. 保存 `var/validation/commerce-acceptance-*/result.json`、`load.json` 和日志，清理本轮卷与容器。

默认四个用户、10 秒注入、0.1–0.3 秒思考时间，是较快的部署冒烟。可用
`--load-users` / `--load-duration` 调整规模；这些参数不会自动构成容量验收。
在途完整路径会在注入结束后继续到有界结果，报告按实际墙钟时间计算 RPS/TPS。
凭证和用户会话清单只在临时目录及本轮卷中，不写入报告。

## 负载与数据工具

`scripts/seed_load.py` 只允许 development、显式 `COMMERCE_LOAD_ISOLATED=1`、空的
`commerce_load_*` 数据库，需先迁移。支持最多 10,000 个独立用户、100,000 个商品；
每用户独立会话/地址，清单权限 0600，拒绝覆盖。Compose 的 `COMMERCE_DATABASE_NAME`
可指定新库，业务默认仍是 `commerce`。

```sh
# 先在独立压测部署中配置 commerce_load_* 数据库，不能指向业务库。
PYTHONPATH=src COMMERCE_LOAD_ISOLATED=1 uv run --no-sync python scripts/seed_load.py \
  --users 100 --products 1000 --output var/load/users.json

PYTHONPATH=src uv run --no-sync python scripts/load_backend.py \
  --base-url http://127.0.0.1:58000 --dataset var/load/users.json \
  --users 100 --duration 60 --mode users --output var/load/users-report.json

PYTHONPATH=src uv run --no-sync python scripts/load_backend.py \
  --base-url http://127.0.0.1:58000 --dataset var/load/users.json \
  --users 100 --duration 60 --mode arrival --arrival-rate 2 \
  --output var/load/arrival-report.json
```

`users` 是固定独立用户循环；`arrival` 按固定路径/秒注入，槽位不足记
`dropped_journeys`，不因服务变慢降低计划到达率。到达率不是 HTTP RPS。
每路径浏览、私有查询、购物车、报价、部分下单/模拟付款或放弃，付款后查询实际订单。
默认操作间思考 3–7 秒、17 次浏览、25% 报价继续下单、70% 订单继续付款、10% 选择第一个
热点商品，其余分散到生成的目录。参数是工程假设；实际请求比例看 `route_counts`，
不能直接称为已校准的 85/10/4/1 流量。

报告包含状态码、RPS、新订单 TPS、付款确认/未确认、失败路径、丢弃路径和 p50/p95/p99。
延迟最多使用 100,000 个 reservoir 样本估计并报告样本数，不含思考时间。
未知下单结果只查询原幂等键。拒绝、失败、未确认支付和丢弃路径让 CLI 返回非零，
不能把 429/503 当成通过。CLI 仅接受 loopback；远端压测需要独立入口与授权。

Python/HTTPX 负载端与服务端同机争用资源，不能代表独立压测机。正式 M6 仍需按
capacity-plan.md 的数据、路径比例、思考时间、30 分钟稳定和 2 小时持续运行执行，
记录负载端饱和、资源、队列以及故障后的资金库存。

## 本轮修复

- SQLite 上下文只负责事务，不关闭连接；模拟提供方和备份脚本改为显式关闭。
  ResourceWarning 按回归错误处理，避免长期累积连接资源。
- aio-pika publish timeout 不覆盖 robust channel 等待重连；relay 增加整次发布 5 秒
  总预算，失败结束当前发送批次并保留 outbox 恢复责任，避免一次故障连续等待
  50 × 5 秒而阻塞扫描、心跳和退出。

这些验证不能替代真实支付/邮件/S3、跨主机高可用、掉电持久性、万级容量、生产恢复目标
或服务器发布。本轮未提交、推送或发布。

## 2026-10-25 实际检查记录

环境：macOS Apple Silicon，Python 3.13.14，Docker 29.9.2。正式服务器与真实支付未参与。

| 检查 | 实际结果 |
|---|---|
| 冻结安装、Ruff lint/format、严格 mypy、compileall | 通过；mypy 覆盖 72 个源文件 |
| 自动化业务/基础设施回归 | 51 passed，56.48 秒，零警告；ResourceWarning 按错误处理 |
| 空库 Alembic 升级及模型一致性 | 升级至 `0005_case_fence`，无待生成迁移 |
| Python 本机依赖审计 | `No known vulnerabilities found`，不代表容器 OS 镜像安全审计 |
| Shell 语法、Git 空白检查 | 通过 |
| 最终源码 ARM64 全容器部署 | 通过，三个 API、relay、三组 worker 和 RabbitMQ 共八个指标目标正常 |
| Prometheus/Alertmanager 配置和规则样例 | 通过，9 条规则、2 组样例 |
| 完整购买冒烟 | 4 个独立用户、226 次请求，9 单中 6 单付款确认、3 单按场景放弃；无失败或丢弃路径 |
| 负载后数据库核对 | 库存占用差异 0，实收/资金分录差异 0 |
| 实例中断、告警及恢复 | 采集失败告警实际到达 Alertmanager，剩余实例 10 次目录查询成功；滚动恢复通过，告警解除 |

自动化原始证据：`var/validation/commerce-test-20261025t042147z-b51a3a/`。
最终源码容器证据：`var/validation/commerce-acceptance-20261025t042028z-3ce5e6/`。
本轮负载思考时间为 0.1–0.3 秒，只是部署冒烟，不作为容量成绩。
覆盖率工具报告进程内语句覆盖率 72%；子进程执行未合并，覆盖率不是交易正确性验收结论。
