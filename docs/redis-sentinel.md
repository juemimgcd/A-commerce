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
- 每节点独立数据卷；Sentinel 必须能够重写配置。启动脚本仅在配置不存在时生成，
  不在重启时强行恢复最初的主从角色。原主恢复后由 Sentinel 将其改为副本。
- 密码和节点地址修改需要协调更新已有卷中的配置，不能仅修改环境变量后重启。
  首次生成逻辑不会覆盖已保存的运行拓扑；凭证轮换应单独安排维护流程。

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

报告位于 `var/validation/commerce-acceptance-*/sentinel.json` 和 `sentinel-junit.xml`。
`check_redis.py` 检查每个 Sentinel 的 quorum、两个主节点及各两条在线复制连接；
它是部署门禁，不以单独的 PING 证明整个集群健康。

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
