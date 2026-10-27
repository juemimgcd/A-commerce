# Redis Sentinel 部署与故障验收

缓存与限流各一主两从，三个 Sentinel 同时监控两组，每组 quorum=2。
应用通过 `redis.asyncio.sentinel.Sentinel.master_for()` 连接当前主节点，读写均走主节点。
`deploy/deploy.sh`、`deploy/roll-api.sh` 和全容器验收默认加载哨兵覆盖配置。
裸 `compose.yaml` 仅用于宿主机直连开发，不具备自动切主。

```text
                          Sentinel 1 / 2 / 3
                           监控、选举、发现
                         /                 \
API / worker → commerce-cache          commerce-rate
                主 + 两副本              主 + 两副本
                allkeys-lru              noeviction + AOF everysec
                                         至少一个健康副本才写
```

同一主机上的九个 Redis/Sentinel 容器用于复现故障和验证应用接入。
它能应对单个 Redis 进程故障，不能应对整台宿主机故障。
跨主机部署时，将每组的三个数据节点及三个 Sentinel 分散到至少三个故障域，
提供所有应用、Redis、Sentinel 均可访问的内部 DNS/地址和端口。
容器服务名只适用于当前 Compose 网络，不能直接复制给宿主机客户端。

## 启动

要求 Docker Compose >= 2.24.4（使用 `!reset` 删除 Redis 的宿主机映射端口）。
首次执行 `uv run --no-sync python scripts/configure_local.py`；已有 `.env` 执行：

```sh
uv run --no-sync python scripts/configure_local.py --redis-secrets
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml build api
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml up -d --wait
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml exec -T api python scripts/check_redis.py
```

凭证生成器只追加缺失的 `REDIS_CACHE_PASSWORD`、`REDIS_RATE_PASSWORD`、
`REDIS_SENTINEL_PASSWORD`，每项独立随机 64 位十六进制，文件权限 0600，不输出密码。
不要使用 `.env.example` 的占位值启动服务。三个 Sentinel 彼此认证，主从复制也认证。
Redis 和 Sentinel 不映射宿主机端口。Redis/Sentinel 配置文件包含密码，位于各自数据卷中，
不要输出完整 `docker compose config`、`redis.conf`、`sentinel.conf` 到公开日志。

配置 `COMMERCE_REDIS_SENTINEL__NODES` 为至少三个 `[host, port]` 的 JSON 数组即启用发现；
同时需要 `__PASSWORD`、`__CACHE_PASSWORD`、`__RATE_PASSWORD`，以及不同的
`__CACHE_MASTER` / `__RATE_MASTER`。部署覆盖文件自动注入这些字段。
配置错误不会静默回退到单节点 URL；空节点列表才使用直连开发模式。

已有单节点环境首次迁移需要维护窗口：先停止网关和全部应用写入进程，再带覆盖文件
重建 Redis 容器并增加副本、Sentinel，保留原 `redis-rate` 卷和所有业务卷，
执行 `check_redis.py` 成功后启动应用。`deploy.sh` 的 `--no-recreate` 不会替你完成
这次拓扑迁移，检查失败时不要绕过门禁。不要执行 `down --volumes` 删除业务数据。

## 故障语义

- 主节点连续约 5 秒不可达后开始故障判定与多数派选举，实际恢复还包括选举、复制、
  客户端重新连接。请求在窗口中仍可能失败，没有零停机保证。
- 限流 Redis 无法发现、连接或没有健康副本时，普通写入返回
  `503 RATE_LIMIT_UNAVAILABLE` / `Retry-After: 1`。整个限流调用包括发现的预算为 350ms。
  查询和验签通过的回调继续沿用已有降级策略。
- 客户端禁止自动重放不确定是否执行成功的命令，避免 Lua 扣额度被重放；下一次请求
  会重新建立连接并发现主节点。读写均不使用副本，避免主动读取复制滞后的额度。
- 限流组所有节点配置 `min-replicas-to-write 1`、`min-replicas-max-lag 5`。
  孤立主节点没有健康副本后拒绝写入；这不是共识写入协议。
  Redis 复制是异步的，AOF 每秒刷盘与 Sentinel 都不能保证零数据损失。
  切主可能丢失最近的限流扣减，库存、资金和幂等仍以 PostgreSQL 事务为准。
- 每节点独立数据卷；Sentinel 必须能够重写配置。启动脚本仅在配置不存在时生成完整配置，
  重启仅更新下述受管理参数，不强行恢复最初的主从角色。原主恢复后由 Sentinel 将其改为副本。
- 密码和节点地址修改需要协调更新已有卷中的配置，不能仅修改环境变量后重启。
  首次生成逻辑不会覆盖已保存的运行拓扑；凭证轮换应单独安排维护流程。

## 内存与慢日志配置

基础部署和 Sentinel 部署均支持以下环境变量；Sentinel 模式下，cache/rate 组的三个节点分别使用同一组预算：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `REDIS_CACHE_MAXMEMORY` | `128mb` | 商品缓存的单节点内存预算 |
| `REDIS_RATE_MAXMEMORY` | `128mb` | 限流计数的单节点内存预算 |
| `REDIS_SLOWLOG_MICROSECONDS` | `10000` | 超过 10ms 的命令执行写入慢日志 |
| `REDIS_SLOWLOG_MAX_LEN` | `128` | 最多保留的慢日志条数 |

内存参数接受正整数及小写 `b/k/kb/m/mb/g/gb` 后缀；慢日志参数接受正整数。
`maxmemory` 不是容器总内存上限，还需为复制缓冲、持久化及进程开销预留资源。
限流组使用 `noeviction`，内存不足会拒绝写入；不要通过淘汰限流键绕过此保护。

修改部署环境变量后，需要重建对应容器以注入新环境；仅 `docker compose restart`
不会刷新环境变量。应按当前角色逐个操作，先副本、确认复制健康，再维护主节点，
避免同时中断所有同步来源。降低预算可能引起缓存淘汰或限流拒写，应先检查实际用量。

每次启动通过同卷临时文件原子替换，只更新 `maxmemory` 和两项慢日志设置，
保留 Sentinel 写入的 `replicaof`、拓扑、认证及其他配置。已有数据卷也会采用新预算。
Sentinel 启动时显式保留 `deny-scripts-reconfig yes`，禁止运行时重配置通知/切主脚本；
这不替代 ACL，也不是通用 SCRIPT/CONFIG 禁令。

未改变持久化策略：未覆盖过的配置采用 Redis 7.4 默认 RDB 保存规则；rate 组开启
AOF everysec，默认使用 RDB 格式 AOF 基础文件，自动重写阈值仍为 100% 和 64mb。
已有卷曾被手动配置时，以实际 `CONFIG GET` 为准。小数据集仍可能因频繁写入产生大 AOF。

## Redis 监控

将 `deploy/compose.monitoring.yaml` 与基础配置、Sentinel 覆盖配置一起使用。
固定版本及镜像摘要的 `oliver006/redis_exporter` 按 cache、rate、Sentinel 启动三个
独立认证的 exporter，通过 `/scrape` 分别采集六个数据节点和三个 Sentinel。
Prometheus 现在共 17 个抓取目标；Alertmanager 是告警接收端，不计入此数。

exporter 不映射宿主机端口；密码通过环境注入，不放进 Prometheus 配置、目标 URL 或标签。
多目标 `/scrape` 必须限制在可信容器网络内，不应发布到公网；当前沿用组密码，
未引入独立监控 ACL 用户。不开启键值扫描、客户端列表或配置内容导出，不采集慢命令参数。

节点以稳定服务名采集，主从角色从实际 Redis/Sentinel 指标判断，切主后不需修改抓取配置。
告警覆盖节点/采集异常、内存 80% 与限流组 90%、OOM/MISCONF/NOREPLICAS、复制链路、
副本 ACK 延迟、缺少副本、RDB/AOF 失败、fsync 延迟、慢命令、Sentinel quorum/拓扑/TILT。
阈值是初始运维预算，需按实际流量调整；告警仍投递到现有本地 Alertmanager 接收器。

`redis_errors_total{err="..."}` 在首次发生相应类型错误后才出现，不预先导出零值。
拒写告警同时检查五分钟增量和首次观测到的非零计数，避免第一批错误没有零值基线而漏报。
首次观测分支在五分钟历史中已有该序列后退出，无新增错误时解除；持续增长仍由增量分支处理。
首次启用监控或长时间采集中断后，已有的非零历史计数也可能触发这段保守告警窗口，
不能仅凭此分支断言错误刚刚发生。错误类型仍限定为 OOM、MISCONF、NOREPLICAS。

常用 PromQL（使用 `redis_group`、`instance` 区分组和节点）：

```promql
# 实际 Redis 可达性；up 只说明 exporter 抓取是否成功
redis_up{job=~"commerce-redis-.*"}

# 单节点内存占预算比例
redis_memory_used_bytes{redis_group=~"cache|rate"} / redis_memory_max_bytes

# 缓存组服务端命中率，流量为零时没有有意义的命中率
sum(rate(redis_keyspace_hits_total{redis_group="cache"}[5m]))
/ clamp_min(sum(rate(redis_keyspace_hits_total{redis_group="cache"}[5m]))
+ sum(rate(redis_keyspace_misses_total{redis_group="cache"}[5m])), 0.000001)

# AOF 当前大小、重写耗时与延迟刷盘次数
redis_aof_current_size_bytes{redis_group="rate"}
redis_aof_last_rewrite_duration_sec{redis_group="rate"}
redis_aof_delayed_fsync{redis_group="rate"}

# Sentinel 是否具有故障切换所需的 quorum 和多数派
redis_sentinel_master_ckquorum_status
```

`connected_slave_lag_seconds` 是副本 ACK 间隔，不是业务数据的精确延迟；
复制 offset 指标可辅助定位复制进度差距。`slowlog_length` 是有界日志长度，不是累计次数，
慢命令告警使用递增的 `slowlog_last_id`。慢日志不包含客户端网络和排队耗时；
`aof_delayed_fsync` 与重写耗时也不能冒充每次 fsync 的延迟分布。

## 自动化验证

```sh
sh scripts/check.sh
uv run --no-sync python scripts/test_backend.py -q -W error::ResourceWarning
uv run --no-sync python scripts/validate_deployment.py
```

最后一条命令建立独立 `commerce-acceptance-*` 项目，使用随机凭证与独立卷，
从当前代码构建镜像，运行真实 Redis/Sentinel 后清理自己的环境。
通过 `tests/test_sentinel.py` 验证未认证访问被拒绝、一个 Sentinel 停机、两组实际自动切主、
两个已有连接池恢复、API 不重启、切主窗口读写行为、原主归队及配置跨重启保存、
所有副本失联时限流失败关闭、恢复后多个池共享额度。
全容器验收同时验证原有支付模拟链路、库存/资金一致性、监控和 API 滚动替换。
监控验收在初始启动及 Sentinel 故障恢复后，逐一核对九个预期节点的
`redis_up=1` 和 `redis_exporter_last_scrape_error=0`，且必须获得检查开始之后的新样本。
缺失、认证失败、采集错误、旧样本或错误的节点集合均不能通过；HTTP 抓取成功不代替 Redis 健康。
节点清单分别保存在报告的 `redis_monitoring_initial` 和 `redis_monitoring_after_failover`。

报告位于 `var/validation/commerce-acceptance-*/sentinel.json` 和 `sentinel-junit.xml`。
`check_redis.py` 检查每个 Sentinel 的 quorum、两个主节点及各两条在线复制连接；
它是部署门禁，不以单独的 PING 证明整个集群健康。

## 2026-10-27 监控补齐验证

本机隔离项目 `commerce-acceptance-20261027t105157z-67c5da` 完整验收通过：

- `sh scripts/check.sh`、Shell 语法、基础与 HA Compose 合并检查通过。
- Prometheus 配置及 22 条规则检查通过，已有规则样例通过；13 条新增 Redis 告警实际求值健康。
- 17 个抓取目标正常；九个 Redis/Sentinel 节点均有 `redis_up=1`，切主后继续正常采集。
- 现有 Sentinel 验证 7 passed；两组旧主重新加入并重启后保持副本角色。
- 226 次购买流程请求，9 张订单、6 次模拟付款确认；库存与资金核对差异均为 0。
- 原有采集失败告警送达/解除、API 滚动恢复通过。未逐条注入 Redis OOM 或磁盘故障。

原始证据在 `var/validation/commerce-acceptance-20261027t105157z-67c5da/`：
`result.json`、`sentinel.json`、`redis-metrics-after-failover.json`、`redis-rules.json`。
临时容器及卷已清理；未修改测试文件，未部署生产环境。

同日 PR review 修复后的验收项目为 `commerce-acceptance-20261027t111220z-bcc905`。
初始及切主恢复后的九节点指标核对、7 项 Sentinel 验证、购买核对及滚动恢复全部通过。
现有故障演练产生首批 NOREPLICAS 错误后，旧增量表达式返回空，新告警实际进入 firing；
证据为该目录的 `redis-old-rejection-rule.json` 和 `redis-first-rejection-alert.json`。
`exporter-up-redis-down.json` 记录了 HTTP 抓取成功但 Sentinel 不可达的场景。

## 2026-10-25 本机实测

使用当前源码构建的 ARM64 镜像，macOS Docker 隔离项目
`commerce-acceptance-20261025t043328z-b248de`，没有连接原业务数据或真实支付。

| 检查 | 结果 |
|---|---|
| Ruff、格式、严格 mypy、compileall、Shell 语法 | 通过，mypy 75 个源文件 |
| 业务回归与配置校验 | 56 passed；真实哨兵用例在独立全容器环境执行 |
| 哨兵专项（含凭证补齐、配置拒绝与完整故障演练） | 7 passed，63.24 秒 |
| 缓存主故障、一个 Sentinel 同时停机 | `redis` → `redis-cache-replica-2`，已有客户端约 6.646 秒恢复 |
| 限流主故障、一个 Sentinel 同时停机 | `redis-rate` → `redis-rate-replica-2`，已有客户端约 8.228 秒恢复 |
| 旧主归队、旧主及 Sentinel 重启 | 保持新主，原主作为副本；应用 API 容器保持不变 |
| 无健康副本 | 写入返回 503，查询继续；副本恢复后写入和共享额度正常 |
| 切主后的完整购买冒烟 | 227 次请求、9 单（6 单模拟付款确认、3 单按场景放弃），无失败或丢弃路径 |
| 库存/资金核对、监控告警、API 滚动替换 | 差异均为 0；8 个抓取目标正常，告警实际接收并解除，滚动通过 |

恢复时间包含停止容器、探测、选举及客户端读写验证，是本轮观测值，不是服务 SLA。
复制标记检查使用 WAIT 屏障保证测试数据已到达副本；应用业务没有同步复制或零丢失承诺。
证据：`var/validation/commerce-acceptance-20261025t043328z-b248de/sentinel.json`、
`sentinel-junit.xml`；原有回归 `var/validation/commerce-test-20261025t043328z-f5b543/`。
完整验收 `result.json` 为 passed，临时容器、网络和卷已清理。CI 配置已接入相同入口，
本轮没有提交、推送或触发远端 CI，也没有部署正式服务器。

依据：[Redis Sentinel 官方文档](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/)、
[Sentinel 客户端协议](https://redis.io/docs/latest/develop/reference/sentinel-clients/)。
