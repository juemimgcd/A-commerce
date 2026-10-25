# PostgreSQL 与 RabbitMQ 本地高可用演练

2026-10-25。`deploy/compose.ha.yaml` 在现有 Compose + Redis Sentinel 上增加
PostgreSQL 与 RabbitMQ 三节点集群。应用、迁移、后台任务和备份工具都使用可切换入口。
本轮范围是同一台 Docker 主机内的节点故障与恢复；跨服务器部署、真实支付和容量验收另行进行。

## 拓扑与故障行为

| 服务 | 配置 | 故障时的行为 |
|---|---|---|
| PostgreSQL 18.6 | 三个 Patroni 4.1.5 节点；严格同步复制，至少一个同步副本；独立持久卷 | 主节点停止后自动选主；旧主使用 rewind 重新作为副本加入 |
| etcd 3.6.5 | 三节点，共识需至少两节点；独立内部网络和持久卷 | 单节点故障可继续协调；失去多数派后 Patroni 主节点降级 |
| RabbitMQ 4.4.0 | 三节点、统一 cookie、固定节点名；九个业务队列均为三成员 quorum queue | 单节点故障后选出新队列 leader；失去多数派时不能确认发布 |
| 客户端 | asyncpg 多主机 + `target_session_attrs=read-write`；AMQP 使用三节点共享 DNS 名 | 新数据库连接选择可写主节点；AMQP robust connection 自动重连并恢复声明/订阅 |

数据库写入要等待同步副本。两个副本都停止时，严格同步模式不会自动退化成异步提交。
请求超时不等于事务一定回滚：客户端仍应按原幂等键查询结果。连接池不会替应用重放事务。
该边界依据 [Patroni 同步复制文档](https://patroni.readthedocs.io/en/latest/replication_modes.html)。

asyncpg 原生多地址模式共用一个总连接超时，无响应的首个地址可能用完预算。
`conf/database.py` 通过 SQLAlchemy 公共 `async_creator` 接口为每个地址提供 2 秒预算，
优先重试上次成功的地址，每次新连接仍检查可写角色；所有地址不可用时返回可重试的 503。
测试使用一个真实接受 TCP 但不响应的本地端口，确认客户端能继续连接第二个数据库地址。

RabbitMQ 消息必须收到 publisher confirm 才标记 outbox 已发送；故障期间保留未确认记录，
恢复后重试。未 ack 的消费允许重新投递，业务幂等和数据库状态负责避免重复资金操作。
参见 [quorum queues](https://www.rabbitmq.com/docs/quorum-queues) 和
[可靠性指南](https://www.rabbitmq.com/docs/reliability)。

当前 RabbitMQ 的 `consumer_disconnected_timeout` 为默认 60,000 ms：消费所在节点失联后，
未 ack 消息会等待失联判定窗口再返回队列。测试等待并记录完整消息恢复时间，
不把队列 leader 选举耗时当作全部消息恢复耗时。此行为见
[失联消费者等待期](https://www.rabbitmq.com/docs/quorum-queues#disconnected-consumer-node-timeout)。

`ha-ready` 在数据库复制和 RabbitMQ 三节点就绪后才允许迁移及应用启动，避免队列第一次
声明时只有单个成员。`scripts/check_ha.py` 进一步核对可写主节点、两个复制连接、同步设置，
以及九个业务队列的三个在线成员。只检查进程存活不能替代这项门禁。

## 一条命令复现

在仓库根目录，Docker 正常运行并安装 Python 3.13、uv 后执行：

```sh
sh scripts/check.sh
uv run --no-sync python scripts/validate_deployment.py --ha
```

入口自行生成独立 `commerce-acceptance-*` 项目、随机凭证、临时数据、端口和卷，构建应用与
Patroni 镜像。它不读取业务 `.env`，不复用业务卷，结束时清理本轮容器和卷。
完整日志、`ha.json`、`ha-junit.xml`、`sentinel.json`、`load.json` 与 `result.json`
保留在 `var/validation/commerce-acceptance-*/`。失败也保存日志并清理。
普通业务回归仍运行 `uv run --no-sync python scripts/test_backend.py -q -W error::ResourceWarning`。

HA 测试只允许验收项目和 `commerce_load_acceptance` 数据库，实际执行以下操作：

1. 先提交订单，再保持一笔未提交的库存修改；停止一个 etcd 节点和当前 PostgreSQL 主节点。
   等待自动切主，验证已有连接池恢复、订单与原幂等键保留、未提交修改回滚。
2. 恢复并再次重启旧主，检查它作为副本重新加入且没有抢回主角色。
3. 停止两个数据库副本，验证写入等待；恢复副本后按原幂等键取回结果。
4. 停止两个 etcd 节点，验证数据库不再接受正常业务访问；恢复多数派后重新可写。
5. 发布并确认 30 条持久消息，留下一个未 ack 的消费，停止实际队列 leader 和客户端所连节点。
   验证同一客户端自动重连、消息全部恢复、未 ack 消息标为重新投递，再恢复旧节点。
6. 停止两个 RabbitMQ 节点，下单后确认 outbox 未标记发送成功；恢复集群，等待实际 relay/worker
   补齐支付会话，完成模拟付款并核对订单和一条资金分录。
7. 使用多地址数据库入口备份，恢复到新的隔离数据库，核对付款记录后删除该恢复库。

同轮还执行 Redis 切主、购买负载、资金/库存核对、API 停机告警和滚动恢复等部署检查。
测试用的是停止容器，不是伪造数据库返回值或模拟消息客户端。

## 2026-10-25 本地验证结果

最终 HA 验收项目为 `commerce-acceptance-20261025t052923z-ba1cb2`，
`result.json` 为 `passed`，入口退出码为 0。环境为 macOS Apple Silicon 上的 Docker。

| 检查 | 实际结果 |
|---|---|
| 冻结安装、Ruff lint/format、严格 mypy、compileall | 通过；93 个文件格式检查、78 个源文件类型检查 |
| 普通业务和基础设施回归 | 58 passed、2 skipped，61.20 秒；跳过的 Sentinel/HA 集成测试在下列独立入口执行 |
| Redis Sentinel 故障回归 | 7 passed，62.51 秒；两组切主、旧主恢复和原客户端恢复通过 |
| PostgreSQL/RabbitMQ 故障回归 | 1 个集成测试覆盖上述 7 个阶段，176.17 秒，通过 |
| PostgreSQL 主节点停止 | `pg-2` → `pg-1`；27.911 秒后完成连接池恢复和原幂等键订单核对；已提交订单保留、未提交库存修改回滚 |
| PostgreSQL 与 etcd 恢复 | 旧主重新作为副本加入；无同步副本时写入等待；etcd 多数派中断后业务访问停止，恢复后可写 |
| RabbitMQ leader 停止 | 停止 `rabbitmq-3`；原连接恢复，30 条已确认消息全部取回，未 ack 消息重新投递；完整恢复耗时 67.877 秒，包含失联消费者等待期 |
| RabbitMQ 多数派恢复 | 故障期间 outbox 保留未发送记录；恢复后支付准备完成，模拟付款只产生一条付款分录 |
| 切主后备份与恢复 | 使用实际备份/恢复脚本完成新库往返，付款记录核对通过 |
| 恢复后业务冒烟 | 226 次请求，9 单中 6 单付款确认、3 单按场景放弃；无失败或丢弃路径 |
| 库存与资金核对 | 库存差异 0，付款分录差异 0 |
| 监控与滚动恢复 | 八个抓取目标正常；告警到达 Alertmanager，剩余 API 查询成功，滚动重启通过且告警解除 |

上述时间来自这一轮本地演练，不作为生产恢复时间承诺或容量结果。
普通回归原始证据位于 `var/validation/commerce-test-20261025t051511z-5cc52c/`；
HA、负载和监控原始证据位于 `var/validation/commerce-acceptance-20261025t052923z-ba1cb2/`。
验收项目的容器、卷和网络已清理，报告与构建镜像保留。未执行服务器部署或远端 CI。

## 配置入口与已有数据

首次开发配置由 `scripts/configure_local.py` 生成。已有 `.env` 可执行：

```sh
uv run --no-sync python scripts/configure_local.py --ha-secrets
```

只追加缺失的 PostgreSQL 管理员、复制账号、Patroni API 密码和 RabbitMQ cookie，
不输出密码、不覆盖已有值。Redis 凭证仍使用 `--redis-secrets` 单独补齐。
应用账号和 PostgreSQL 超级用户分离；本地恢复工具使用的应用账号有建库权限。

手动启动时按顺序合并 `deploy/compose.yaml`、`deploy/compose.redis-sentinel.yaml`、
`deploy/compose.ha.yaml`；使用独立项目、端口与网段，应用和后台进程都在容器内运行。
如同时启用监控，再加入 `deploy/compose.monitoring.yaml`。Compose 需支持 `!override`
（2.24.4 或更新版本）。PostgreSQL 节点和 etcd 不映射宿主机端口。

HA 配置使用新的 `pg-*`、`etcd-*` 和 `rabbit-ha-*` 数据卷，**不会自动接管旧单节点数据**。
不能直接把旧 PostgreSQL 或 RabbitMQ 数据卷挂到新拓扑；业务数据迁移需要维护窗口、备份、
恢复核对及明确切换步骤。根目录的普通 Compose 和服务器 `deploy.sh` 仍保留原部署入口，
此次没有执行服务器迁移或发布。HA 项目的 API 滚动脚本需设置 `COMMERCE_HA=1`，并使用
该项目原有的 `COMMERCE_ENV_FILE`、`COMPOSE_PROJECT_NAME` 和镜像/网络配置。

本地后端/协调网络分别默认 `172.28.84.0/24` 和 `172.27.84.0/24`，可通过
`COMMERCE_BACKEND_PREFIX`、`COMMERCE_DCS_PREFIX` 更换前三段。节点使用固定内网地址和
`extra_hosts`，即使容器停止、Docker 删除其 DNS 记录，名称仍指向原节点，避免代理 fake-IP
以及 DNS 超时阻塞 Sentinel。自动验收会为本轮选择网段；不要在有数据的集群中随意更换地址。

备份与恢复脚本新增可重复的 `--compose-overlay` 和 `--database-service db-tools` 参数；
HA 项目必须传入同一套环境、项目名和覆盖文件。`db-tools` 使用 libpq 的多地址和
`PGTARGETSESSIONATTRS=read-write`，不依赖固定的 `pg-1` 为主。恢复入口仍只允许
新建 `commerce_restore_*` 数据库，拒绝覆盖已有库。HA 测试验证的是 PostgreSQL 往返；
模拟提供方 SQLite 和文件卷的完整一致备份仍需暂停业务并按原运维流程处理。

## 本地与生产的边界

- 应用后端使用桥接网络，保留管理界面的 loopback 端口映射与提供方出网；
  内部节点名称采用稳定映射。真实外部支付、邮件和 S3 的连接仍需凭证与独立验收。
- 三个副本仍在同一台 Docker 主机，共享宿主机、磁盘和电源；宿主机故障不在本轮验证内。
- PostgreSQL 严格同步与 quorum 队列保护的是各自已确认的数据边界，不能保证任何故障下
  零丢失或零中断。HA 不替代离机备份、时间点恢复和恢复目标演练。
- 本地 Patroni watchdog 关闭；进程冻结、网络分区、硬件 fencing 和多故障域需要独立验证。
  etcd 当前在隔离网络内使用 HTTP，跨主机部署需 TLS、身份验证及访问控制。
- 现有 Prometheus 配置仍抓取八个应用/基础设施目标；完整数据库、etcd 与各 broker 的
  集群指标、告警和生产容量预算尚未补齐。
- CI 配置已纳入 `--ha` 验收；本地结果不能代表远端 CI 已运行。
