# 后端运行与故障处理

适用于当前单店本地部署，数据库迁移为 `0005_case_fence`。基础启动步骤见 [实施记录](backend-implementation.md)。金额使用整数分；所有资金状态以提供方认证查询和本地持久记录共同核实，不用直接 SQL 改成成功。

## 进程与连接预算

API、独立 provider、relay、critical/general/maintenance worker 分开启动。Compose 中 API/relay/三个 worker 的数据库池上限分别为 5/2/4/2/2，总计 15，另加迁移和管理连接；滚动发布有新旧实例重叠时按实际副本重新计算。maintenance 默认并发 1，与支付队列隔离。

健康接口 `/health/live` 表示 API 存活，`/health/ready` 只确认数据库可访问且版本匹配，不代表 broker、支付或所有 worker 健康。后台存活看 `/metrics` 的 `commerce_background_alive`：每 15 秒写心跳，45 秒内没有更新视为失联。

`/metrics` 和 RabbitMQ 管理端口应只允许内部监控访问；当前 Compose 端口仅绑定本机。`deploy/prometheus-alerts.yml` 可加入 Prometheus 的 `rule_files`。规则阈值是初始配置，尚未根据容量数据调优，也未配置通知接收人。

多进程 API 的 HTTP 计数/耗时使用 `PROMETHEUS_MULTIPROC_DIR`；数据库汇总指标只根据本次查询生成样本，不写共享 Gauge 文件。采集时过滤共享目录里旧版的五类后台指标，避免重复输出和残留状态误报；不会删除其他进程的共享文件。

## 任务失败与重放

1. 查 `GET /api/v1/admin/operations?status=manual_review` 和 `/message-failures`，记录 operation ID、业务 ID、last_error；日志用请求 ID 关联。
2. 检查 relay 和对应 worker 心跳、数据库连接、队列连接及提供方可用性。outbox 最老年龄升高但 worker 空闲时，先检查发布确认和队列路由。
3. 修复原因后调用 `POST /api/v1/admin/operations/{id}/replay`，提交 `{"reason":"具体核对结论"}`。需要 audit 权限和 15 分钟内重新登录。
4. 重放保留原支付/退款 ID；再次主动查询后才执行。重复消息不代表新业务请求，不能另建退款 ID 作为重试。

pending 首次消息立即投递；90 秒没有领取可补发。running 通过租约与 fence 恢复；重试次数耗尽进入 manual_review。不要手动清空 broker 来代替恢复，也不要将 unknown 当成资金失败。

## 资金对账

`COMMERCE_RECONCILIATION_INTERVAL_SECONDS` 默认 86400。调度计划和上一轮操作 ID 保存在数据库；未完成或待人工处理的周期任务不会无限另起一轮。维护进程停止后，恢复进程会继续原游标。

- `POST /api/v1/admin/reconciliations`：手动发起一轮。
- `GET /api/v1/admin/reconciliations`：查看 provider/local_payments/local_refunds/done 阶段及检查条数。
- `GET /api/v1/admin/reconciliations/{id}`：按创建返回的 ID 查询单轮进度。
- `GET /api/v1/admin/reconciliation-cases?status=open`：查看差异和提供方证据。
- `POST /api/v1/admin/reconciliation-cases/{id}/recheck`，提交 `{"note":"核对说明"}`：再次认证查单。只有商户、币种、意图 ID、业务 ID、金额和资金终态匹配，才补齐本地缺失的收款/退款；沿用原入账函数，保留审计。

金额/身份不匹配、提供方缺记录、本地缺记录等情况保持 open；修复适配器或数据来源后再核对。不得凭人工备注直接把差异变成 resolved。迟到收款也走原订单取消/补偿规则，不强行恢复履约。

差异单写回使用递增版本；人工复核另保存复核时间。旧轮次不能覆盖后发生的人工复核，查询期间版本已变化的复核请求返回 409 `RECONCILIATION_CHANGED`，且不会先补账。遇到该响应应重新读取差异单，再主动复核；不要沿用上次响应中的状态或直接修改版本。

当前账单来自独立模拟提供方。其旧数据没有历史付款时刻，迁移流水标记 `migration_observed`；新流水标记 `provider_observed`。本地报表按本地确认时间入账，均不冒充真实渠道结算日。生产通道需单独实现并验证相同的认证查询、稳定操作 ID、关闭和流水契约。

## 事件游标与快照恢复

在线事件默认 90 天；归档保留订单事件事实，订单、资金、审计不物理删除。待发邮件依赖的事件前缀会推迟归档，需处理邮件积压。

客户端正常使用 `GET /api/v1/customer/events?after=<sequence>`。收到 `410 CURSOR_EXPIRED` 时：

1. 调用 `POST /api/v1/customer/event-snapshots`，保存快照 ID、`through_sequence` 和有效期。
2. 调用 `GET /api/v1/customer/event-snapshots/{id}?limit=100`，用 `next_cursor` 取完所有页；所有页属于同一已固定快照。
3. 用快照替换本地订单状态，再从 `events?after=<through_sequence>` 读取后续变化，并按订单 version 忽略已处理版本。
4. 快照过期则重新创建；不要用未取完的快照跳过事件。单用户最多三个未过期快照，默认 15 分钟有效。

订单历史可从 customer/admin 的 `/orders/{id}/history` 查询，包含在线与归档记录；买家只能访问自己的订单。归档后旧站内通知随对应在线事件移除，订单历史继续保留。

## 媒体与周期清理

上传只接受至多 8 MiB 的可解码 PNG/JPEG/WebP，解码像素不超过 2000 万；重新编码为 JPEG，移除元数据。新上传先建立对象台账，成功关联商品才标记 ready。每小时维护任务清理超过一天、无媒体引用的 pending/deleting 对象；只处理服务端生成的对象键，不遍历删除任意文件。

本地/对象存储写入与数据库不是同一个事务，清理任务承担孤立文件恢复责任。S3 桶/端点改变前先处理旧存储台账，不能把旧键送到不相关的新桶。迁移前未登记的文件需要单独盘点，不由本任务猜测删除。

## 备份与恢复

`scripts/backup_local.py` 针对当前 Compose 数据库及宿主机 provider/文件目录。全容器 provider 使用命名卷，需另行复制卷中的 SQLite/文件后再备份；不要误认为此脚本会自动读取容器卷。

为得到业务库与提供方一致的停机快照，先停止本项目 API、relay、三个 worker 和 provider，再运行：

```sh
uv run --no-sync python scripts/backup_local.py
```

脚本将 PG custom dump、通过 SQLite backup API 读取的库及文件目录放入新的时间戳目录；不会覆盖旧备份。配置和真实密钥需另用受控渠道备份，脚本不把 `.env` 加入业务备份。

恢复演练只能使用全新隔离库名：

```sh
uv run --no-sync python scripts/restore_local.py var/backups/<时间戳>/commerce.dump commerce_restore_<标识>
```

脚本拒绝业务库名和已经存在的库，不使用 `--clean`。恢复后检查迁移版本、订单/退款数量、资金汇总、库存余额，再在该隔离库执行 `alembic upgrade head`。原业务库不变；演练库不会自动删除，以便核对。恢复 provider/文件时也应先使用独立副本，不能让新旧业务库同时写同一提供方数据。

本机已完成一次小数据量恢复。异地备份、加密、备份周期、恢复时间/数据丢失目标和大数据恢复尚未验收。

数据库迁移前先停止本项目的写入进程并备份。`0004_recovery` 降级时会独占锁定归档表：只要 `event_archive` 非空，就在删除任何表/列之前拒绝该迁移。归档记录已经从在线表移走，不能通过删表回滚；应保留当前版本，使用升级前备份在隔离库演练恢复。归档表为空时仍允许降级。

## CI 与发布边界

`.github/workflows/backend.yml` 固定 Python/PG 镜像和 Action 提交，执行冻结依赖、Ruff、mypy、compileall、空库迁移、漏洞审计及双架构镜像构建。main 上的新版本通过检查后自动创建 tag、GHCR 镜像和 GitHub Release。远端执行结果以 [Actions](https://github.com/juemimgcd/A-commerce/actions) 为准。没有自动业务测试或压测。

服务器部署工作流默认关闭；配置方法、发布版本、停机备份与失败处理见 [发布指南](releasing.md)。部署工作流只面向使用模拟提供方的 staging 环境；发布制品不代表生产验收完成。

发布前仍需按 [分阶段计划](mvp-plan.md) 完成真实外部集成、授权后的自动业务/并发/故障验证、1 万在线容量和高可用验收。当前提供的是可运行的后端实现与已记录的本机证据。
