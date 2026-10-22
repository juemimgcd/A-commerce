# 后端首版实施与运行记录

日期：2026-10-22。后端覆盖 M1–M5 的主要业务，并补入对账/恢复/运营维护及部分 M6 工具；已进行本机真实服务联调。这不是 v1.0 发布验收，也没有 1 万在线用户性能结论。前端与 M7 pi 适配器未实施。功能补齐见第 7 节，审查修复见第 8 节，边界验证见第 9 节，最新 RabbitMQ 实际投递与中断恢复验证见第 10 节。

## 1. 实际代码

| 位置 | 职责 |
|---|---|
| `src/a_commerce/main.py` | 应用工厂、资源生命周期、错误处理、健康检查 |
| `routers/customer.py`、`admin.py`、`callbacks.py` | 买家、管理员与签名回调 API |
| `services/identity.py` | Argon2、可撤销会话、权限复核；买家与员工身份分离 |
| `services/catalog.py`、`cart.py`、`checkout.py` | 商品、购物车、报价快照与幂等下单 |
| `services/inventory.py`、`orders.py` | 库存锁与预留、支付前后取消 |
| `services/payments.py`、`refunds.py` | 资金确认与退款占额；金额使用整数分 |
| `services/fulfillment.py`、`aftersales.py` | 拣货/发货/签收、退货审批/寄回/收货/质检 |
| `services/operations.py`、`dispatch/main.py` | 稳定操作 ID、租约、重试、超时扫描、outbox 转发 |
| `workers/`、`messaging/` | 关键/普通任务隔离、手动确认、隔离队列、重复投递处理 |
| `services/reporting.py`、`exports.py`、`notifications.py` | 资金日报/商品排名、异步 CSV、站内通知 |
| `services/administration.py`、`catalog_import.py` | 员工权限、审计、异常查询、按行提交商品导入 |
| `integrations/` | 独立 HTTP 模拟支付、图片校验、可选 SMTP/S3 |
| `services/reconciliation.py`、`event_recovery.py`、`scheduling.py` | 周期对账、差异复核、快照/归档与持久调度 |
| `services/management.py`、`observability/background.py` | 运营查询、员工维护与跨进程任务指标 |
| `alembic/versions/` | 五版明确迁移，当前 head 为 `0005_case_fence` |
| `deploy/`、`scripts/` | 固定镜像、本地配置、管理员初始化和静态检查 |

业务规则、事务/锁边界、资金状态不确定时的处理、重试与恢复路径都使用中文注释。接口请求/响应以 `/docs` 和 `schemas/api.py` 为准。

以“取消待支付订单”为例：订单先进入 closing，worker 在数据库事务之外请求提供方关闭；确认支付会话终态关闭后才释放库存。仅查到“尚未付款”不会直接释放。付款与关闭发生竞争时，按提供方确认结果处理退款，避免发出一个无法追踪的新付款操作。

## 2. 本机启动

在项目根目录执行。需要 Python 3.13、uv、Docker；已有 `.env` 时跳过配置生成，该脚本拒绝覆盖。

```sh
cd /Users/jquery/python_files/A-commerce
uv sync --frozen --no-build --extra storage --extra mail
uv run --no-sync python scripts/configure_local.py
docker compose --env-file .env -f deploy/compose.yaml up -d --wait db redis rabbitmq
sh deploy/rabbitmq-policy.sh
PYTHONPATH=src uv run --no-sync alembic upgrade head
PYTHONPATH=src uv run --no-sync python scripts/create_admin.py operator@example.com
```

分别在六个终端启动以下进程，均以项目根目录作为工作目录：

```sh
PYTHONPATH=src uv run --no-sync uvicorn a_commerce.main:create_app --factory --host 127.0.0.1 --port 58000 --no-access-log
PYTHONPATH=src uv run --no-sync uvicorn a_commerce.integrations.payments.simulator:create_app --factory --env-file .env --host 127.0.0.1 --port 58001 --no-access-log
PYTHONPATH=src uv run --no-sync python -m a_commerce.dispatch.main
PYTHONPATH=src uv run --no-sync python -m a_commerce.workers.main
COMMERCE_WORKER_GROUP=general PYTHONPATH=src uv run --no-sync python -m a_commerce.workers.main
COMMERCE_WORKER_GROUP=maintenance COMMERCE_WORKER_CONCURRENCY=1 COMMERCE_DB_POOL_SIZE=2 PYTHONPATH=src uv run --no-sync python -m a_commerce.workers.main
```

API 文档：<http://127.0.0.1:58000/docs>。配置由 `.env` 读取；买家/管理员登录后使用 `Authorization: Bearer <token>`。需要幂等键的接口使用 `Idempotency-Key`，具体见 OpenAPI。模拟支付服务的操作接口见其 `/docs`，仅限开发使用。

本次创建的联调账号保存在 `var/local-accounts.json`，文件权限为 0600；`.env` 与 `var/` 已忽略。不要将文件内容贴到日志、文档或仓库。初始化管理员命令再次执行不会覆盖已有密码。

本次基础服务使用容器，API/provider/relay/三组 worker 使用宿主机进程。数据库、broker 数据保存在 Compose 卷；模拟提供方数据在 `var/provider.sqlite3`，导出文件在 `var/files`。这些是本机联调数据。

## 3. 容器运行方案

`deploy/compose.yaml` 提供全部服务，镜像以版本和 digest 固定；应用使用非 root 用户。全容器方式的命令如下：

```sh
docker compose --env-file .env -f deploy/compose.yaml build api
docker compose --env-file .env -f deploy/compose.yaml up -d --wait db redis rabbitmq
sh deploy/rabbitmq-policy.sh
docker compose --env-file .env -f deploy/compose.yaml up -d api provider relay worker worker-general worker-maintenance
```

切换前先停止占用 58000/58001 的本项目宿主机进程；如要保留联调状态，必须将 provider SQLite 和文件目录迁入对应卷，再启动应用。不要只连接旧业务数据库却使用空的 provider 数据库。当前已验证镜像构建和 Compose 配置，尚未验证全容器业务链路。

本地单节点 quorum queue 不提供高可用。死信采用 at-least-once 策略并配置 reject-publish；队列恢复不能替代数据库的操作重试。相关限制见 [RabbitMQ 官方说明](https://www.rabbitmq.com/docs/quorum-queues)。

## 4. 已执行的验证

以下为首版在真实 PostgreSQL、Redis、RabbitMQ、独立 HTTP 模拟提供方上的手工 API 联调，没有生成自动化测试文件或测试脚手架。表中金额、文件数和版本是当次记录；后续结果见第 7、8 节。

| 验证 | 观察结果 |
|---|---|
| 静态检查 | Ruff lint/format、严格 mypy、compileall 通过，覆盖 52 个 Python 文件 |
| 数据库 | 空库迁移及后续迁移成功；`alembic check` 无模型差异 |
| 幂等下单 | 同报价/同幂等键重放返回同一订单 |
| 最后一件库存 | 两个买家同时下单，一个 201，一个 409 OUT_OF_STOCK |
| 身份隔离 | 跨买家查订单 404；买家访问管理员订单 401 |
| 正常购买 | 创建订单→异步支付→拣货/发货→签收 |
| 待支付取消 | 提供方关闭确认后订单取消、库存释放 |
| 已支付未发货取消 | 2500 分实际确认退款，库存恢复 |
| 分次退货 | 同订单两个数量分次退货，累计退 5800 分，原运费 600 分保留 |
| 完整退货流程 | 申请→批准→寄回→收货→质检→退款；重复质检返回同一退款 |
| 数据一致性 | 两种 SKU 最终 reserved/allocated 均为 0，在库数量恢复为 10 和 1 |
| 汇总与任务 | 收款 8900 分、退款 8300 分；当次检查的 9 个后台操作均完成 |
| 运营导出 | 普通 worker 生成最新 CSV，下载 200，实收/实退/净额为 8900/8300/600 分 |
| 图片边界 | 无效图片上传返回 422 INVALID_MEDIA |
| 服务就绪 | 当次 API `/health/ready` 返回 200，迁移版本 `0003_workflows` |
| 依赖审计 | 安装 core + storage + mail + dev 的本机环境审计未发现已知漏洞 |

可重新执行的检查命令：

```sh
sh scripts/check.sh
PYTHONPATH=src uv run --no-sync alembic check
uv run --no-sync pip-audit --local --format json --output var/dependency-audit.json
docker compose --env-file .env -f deploy/compose.yaml config --quiet
```

首次审计发现 Pillow 12.3.0 存在已知漏洞，已升级到 13.0.0 并更新锁文件；图片识别同时限制为 PNG/JPEG/WebP，解码后去元数据并重新编码。修复依据见 [Pillow 发布说明](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)。依赖审计通过只代表当时数据库未报告已知漏洞，不代表应用安全审计完成。

## 5. 与发布目标的差距

- 未进行 1 万在线混合负载、热点、长稳、多副本故障及高可用验收；已做一次回调中断恢复和小数据量备份恢复，不能宣称容量或生产恢复目标达标。
- 未创建或执行自动化业务测试套件；手工并发与成功路径不能覆盖所有竞争、崩溃和迟到回调窗口。
- 支付使用独立模拟提供方，未接真实商户沙箱/生产；已有固定流水快照的双向对账和差异复核，尚不包含真实渠道的清算日、手续费、结算到账等账单语义。
- SMTP、S3 适配器已写入，默认邮件关闭、本地文件存储；未使用真实邮件账号或 S3 桶联调。密码重置需先配置邮件。
- 已有请求日志、请求 ID、HTTP/持久任务/后台心跳指标、告警规则和 CI 检查配置；CI 未在 GitHub 运行，告警未接收通知。完整 SQL/池等待、broker 监控面板、OTel 链路和发布流水线仍未完成。
- 通知与统计采用同事务投影；任务扫描依赖数据库行锁与租约。需要实际多副本故障验证后再确定生产配置。
- CSV 目前是资金日报导出，导入是 JSON 商品批次按行提交；不等于任意业务表导入导出。新媒体上传已有对象台账与定期孤立对象清理；迁移前未登记的未知文件不自动删除。
- 事件归档删除、过期游标 410 和快照恢复已在隔离恢复库运行验证，见第 9 节；孤立媒体删除分支尚未运行验证。订单、资金、审计及对账证据保留，不做自动物理删除。生产运维面板、前端、Agent grant/action 和 pi 接入未实施。

实施阶段没有初始化 Git 或提交代码，也没有修改 pi 源码。

## 6. 首轮审查修复

本轮修复了四项源码审查问题，未改变数据库表结构：

- 普通改密在同一事务中作废该买家的全部密码重置令牌并撤销会话；重置令牌签发也先锁定账号，与改密/重置确认串行。
- `next_attempt_at` 统一表示扫描器最早可以重试或补发的时间。首次投递、人工重放的 outbox 立即可发送，未领取任务在 90 秒后可补发；扫描在 `LIMIT 50` 前排除未到期任务，不重复增加等待。
- 撤销会话按账号排他锁 → 身份复核 → 目标会话排他锁执行，避免并发退出和交叉撤销时升级共享锁。
- 购物车最多保存 100 种 SKU；计数与新增在同一账号锁下完成，超限新增返回 409 `CART_LIMIT_REACHED`。满额时仍可修改已有数量和删除条目。

验证：Ruff lint/format、严格 mypy、compileall 和 Alembic 结构检查通过。本机查询没有发现超过 100 项的旧购物车。更新后的 API 手工并发退出同一会话，分别返回 204 和 401 `UNAUTHENTICATED`，未出现数据库错误；服务就绪接口返回 200。API、relay 和两组 worker 已重启加载新代码。

新建导出任务从 outbox 创建到发布确认耗时约 1.012 秒，随后状态变为 `done`，确认 90 秒补发窗口没有延迟首次投递。修复后的 `a-commerce:local` 镜像已重新构建成功；全容器业务链路仍未验收。

未新增自动化测试文件或脚手架；旧重置令牌、购物车第 101 项竞争、扫描批次积压的完整运行回归尚未执行，不能以静态检查或一次并发请求代替这些验证。

## 7. 后端补齐：对账、恢复和运营维护

新增迁移 `0004_recovery`；旧事件序号补入保留水位 0，已关联媒体登记为 ready，不删除原交易数据。实现细节与处置步骤见 [运维手册](backend-operations.md)。

- **对账**：独立提供方增加持久资金流水。每轮固定上界、每条任务处理至多 100 条，游标和任务状态共同提交；先逐条核对提供方资金，再反查本地已确认资金。每天自动运行，管理员可手动发起。差异保留证据；复核主动查提供方，仅身份/金额完全匹配时补齐缺失确认，不能通过接口任意改金额。
- **事件恢复**：默认保留 90 天在线事件，过期前缀移到历史表；未完成邮件阻止其所属前缀归档。旧游标返回 410。恢复快照在同一用户事件水位下物化订单状态，分页读取后从 `through_sequence` 继续拉取事件。快照默认 15 分钟有效、每用户最多三个有效快照。
- **中间状态事件**：支付等待、资金结果未知和到期关闭中也推进订单版本并写事件，避免快照之后的状态变化缺少增量通知；已确认的终态不会被失败处理降为未知。
- **对象清理**：外部图片写入前登记 pending，对象与商品关联后转 ready。小时任务只清理超过一天且没有媒体引用的 pending/deleting 对象；删除不持有数据库事务，异常保留任务与台账供重试。
- **运营**：退货/退款列表、库存流水、商品变更、含归档事件的订单历史；员工权限/禁用、管理员改密与会话撤销，保留最后一位身份管理员；商品品牌/属性筛选，订单状态/时间/用户筛选，成功与失败登录审计。
- **运维**：维护任务使用独立消费组。API 从数据库导出 outbox 最老年龄、任务状态/超期、对账异常及各后台角色存活数；附告警规则。新增本地备份/隔离恢复脚本与冻结安装、静态检查、空库迁移、依赖审计的 CI 配置。GitHub checkout 动作固定到 [官方 v7.0.1 提交](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1)。

本轮实际执行：

| 验证 | 观察结果 |
|---|---|
| 静态检查 | 63 个 Python 文件的 Ruff、格式、严格 mypy、compileall 通过 |
| 迁移 | 现有库升级、另一个空库完整升级、恢复库升级均成功；`alembic check` 无差异 |
| 自动周期 | relay 创建对账/清理计划，maintenance worker 执行后均 done |
| 对账恢复 | 停止 API 与关键 worker；提供方收款 3500 分且明确返回回调未送达；重启 API 后对账生成 local_confirmation_missing；管理员主动复核使订单变 paid、差异变 resolved |
| 恢复后的资金/库存 | 两个联调订单均按正常取消路径完成 3500 分退款；最终累计实收 15900、实退 15300、净额 600 分；两种 SKU 的 reserved/allocated 均为 0 |
| 再次对账 | 最新一轮 done，提供方与本地共检查 18 项，未解决差异为 0 |
| 快照 | 创建 201、按每页一单分页 200、水位一致、另一个买家读取 404；从快照水位拉取增量 200 |
| 中间状态事件 | 新订单产生 order.created v1、payment.pending v2；停止提供方后变 unknown 并产生 payment.unknown v3；恢复后按正常取消流程关闭 |
| 运营/权限 | 退货、退款、库存流水、商品历史、订单历史均 200；移除最后管理员返回 409 LAST_ADMIN |
| 筛选/输入 | 实际 SKU 属性筛选命中一商品；不匹配规格为空；反向价格区间和无时区时间返回 422；超限 JSON 返回 413 BODY_TOO_LARGE |
| 登录审计 | 成功登录 200 与错误密码 401 分别写入审计；无密码/token/邮箱回显 |
| 进程指标 | relay、critical、general、maintenance 四个角色均报告一个有效心跳 |
| 本地恢复 | `var/backups/20261022T115850455917Z` 保存 PG dump、SQLite backup 及文件；恢复到新库 commerce_restore_20261022，随后升级到当前迁移；原库未覆盖 |
| 配置 | Compose 配置通过；CI 与 Prometheus 规则 YAML 可解析，未执行远端 CI 或真实告警投递 |
| 镜像与运行 | 最终 a-commerce:local 构建成功，非 root 用户 commerce；宿主机 API、relay、三个 worker 和 provider 已加载最新代码，ready 返回 0004_recovery |

仍未新增自动化测试、断言、fixtures 或压测脚本。以上是手工请求和实际基础服务的观察记录，不代表所有并发、归档、存储或外部通道都已验收。

## 8. 对账、事件归档和监控审查修复

本轮修复四项问题，并补充说明锁、版本和数据保留原因的中文注释：

- `0004_recovery` 的降级在任何删表/删列之前锁定归档表并检查；有历史记录时明确失败，避免删除在线表之外的唯一事件副本。空归档表仍可正常降级。
- 订单写入统一使用 `FOR NO KEY UPDATE`，包括到期扫描。订单写操作之间仍互斥，同时允许事件归档的外键 `KEY SHARE` 检查，消除订单锁与用户事件序号锁的反向等待。
- 新迁移 `0005_case_fence` 给差异单增加 `revision` 和 `reviewed_at`。后台在比较前读取版本，写回时复核；复核前启动的旧轮次也不能覆盖人工结论。人工查单前后检查版本，冲突在补账之前返回 409。
- 数据库汇总指标改用临时 `GaugeMetricFamily`，不再创建持久多进程 Gauge；多进程采集同时过滤旧版遗留的同名后台指标。

本轮已执行的检查：

| 验证 | 观察结果 |
|---|---|
| 静态检查 | `scripts/check.sh` 通过：64 个 Python 文件的 Ruff、格式、严格 mypy、compileall |
| 迁移 | 现有库升级到 `0005_case_fence`；隔离空库升级→降级到 0003→重新升级成功；两库 `alembic check` 无差异 |
| 订单锁 | 在已有恢复库中，事务 A 持有订单 NO KEY UPDATE 时，事务 B 的 KEY SHARE NOWAIT 成功，另一个 NO KEY UPDATE NOWAIT 被拒绝；事务均回滚，无数据改动 |
| 并发复核 | 对现有差异单同时发起两次真实 HTTP 复核，一次 200 resolved，一次 409 RECONCILIATION_CHANGED |
| 后台对账 | 新一轮完成，提供方与本地共检查 18 项，未解决差异为 0；实收/实退仍为 15900/15300 分，库存预留和分配仍为 0 |
| 多进程监控 | 两个 Uvicorn API worker 连续八次抓取均 200；五类后台 HELP 各一次，无后台 pid 样本，无残留 manual_review；共享目录只有 HTTP counter/histogram 文件，没有 Gauge 文件 |
| 运行 | API/provider/relay/三组 worker 已加载新代码；ready 返回 `0005_case_fence`。迁移前新增备份位于 `var/backups/20261022T140039507608Z` |
| 镜像 | `a-commerce:local` 重新构建成功，用户仍为 commerce；镜像内 Alembic head 为 `0005_case_fence` |

再次检查了订单写锁调用点、人工与后台差异单写回顺序、迁移拒绝条件和指标导出路径。未新增测试文件、fixtures 或断言。本轮结束时，非空归档拒绝降级、完整归档并发、后台与人工复核的精确交错、旧 mmap 文件过滤仍只有源码检查结论；随后已补做运行验证，见第 9 节。

## 9. 隔离恢复库中的边界运行验证

使用第 8 节的备份恢复出独立 PostgreSQL 库 `commerce_restore_boundaries_20261022`，升级到 `0005_case_fence`；模拟提供方也使用单独的 SQLite 副本。临时 API/provider 使用 58010/58011，双进程指标服务使用 58012，Redis 使用独立的 DB 15。原业务服务继续运行，未连接这些副本。

下单、付款、取消和人工复核使用实际 HTTP 接口；后台对账由实际 `workers.handlers.handle` 读取隔离库中的操作/outbox 并执行。通过短暂持有 PostgreSQL 锁控制交错，并在 `pg_stat_activity` 中确认两边确实到达预定等待位置，没有替换业务函数或伪造提供方响应。为触发保留期，只把隔离库中选定买家的现有事件时间调整到 91 天前。

| 验证 | 实际交错与观察结果 |
|---|---|
| 归档与订单写入 | 归档持有用户事件序号锁后，暂时阻塞在邮件表读取；买家取消订单取得订单锁，随后等待事件序号。解除阻塞后两边都成功，取消接口返回 200 closing，无死锁或锁超时 |
| 事件连续性 | 35 条原事件的序号、类型、订单版本与归档后逐条一致；保留水位为 35，新产生的取消事件在线序号为 36。旧游标返回 410 CURSOR_EXPIRED，新快照水位为 36，从该水位拉取增量返回 200 |
| 非空归档降级 | 实际执行从 head 降级到 0003，进程返回 1 且命中归档保护。失败后版本仍为 0005，35 条历史、保留水位、revision/reviewed_at 字段都保留；随后 `alembic check` 无结构差异 |
| 收款：人工先提交 | 提供方收款成功但回调未送达，本地尚未确认。人工与后台先后等待同一差异单锁；人工补账后，后台拿到锁时丢弃旧比较结果。差异保持 resolved、版本为 2；只有一笔 3500 分收款流水 |
| 退款：人工先提交 | 提供方已退 3500 分，本地仍占用退款余额。按相同交错复核后，差异保持 resolved；退款流水一笔 3500 分，已退款 3500、占用额 0，没有重复释放或重复入账 |
| 旧轮次读取新版本 | 对收款、退款分别调用实际写回函数：旧轮次使用人工复核后的当前 revision，尝试写回旧账单差异。reviewed_at 保护使状态和版本保持不变；随后新建的一轮对账可正常把版本推进到 3 |
| 后台先提交 | 人工主动查单后暂时等待订单锁，后台先把差异版本从 1 更新为 2。人工继续时返回 409 RECONCILIATION_CHANGED，本地仍未确认且资金流水为 0；重新复核返回 200，才新增一笔收款 |
| 旧指标文件残留 | 运行备份中的真实旧版采集代码，生成含 manual_review 和未解决差异的 Gauge 文件，然后恢复数据库状态。新版双 worker 共 20 次抓取均返回 200，五类后台指标各输出一次、无 pid 或旧 manual_review 样本，未解决数为 0；两个 worker 均留下正常 HTTP counter/histogram 文件，旧 Gauge 文件内容未被改写 |
| 原业务库保持一致 | 实收仍为 15900 分、实退 15300 分；两个 SKU 在库为 1 和 10，预留/分配均为 0；归档数和未解决差异数仍为 0 |

这些场景未发现新问题，因此本轮没有修改业务代码或迁移，仅更新验证记录。临时验证服务已停止，临时执行脚本已移除；隔离数据库和对应提供方副本保留，副本与摘要位于 `var/boundary-validation/20261022/`。未新增仓库测试文件、fixtures 或断言。

这是选定交错的实际运行证据，不等于所有并发窗口或生产容量验收。本轮直接调用真实消息处理入口，没有另启 RabbitMQ 投递链路；归档过程中崩溃恢复、未送达邮件长期阻塞、孤立媒体删除、真实支付/SMTP/S3 和压测仍不在本次验证范围内。

## 10. RabbitMQ 实际投递、重复消费与 worker 恢复

另建数据库 `commerce_restore_rabbitmq_20261022`、RabbitMQ vhost `commerce_rabbitmq_20261022` 和模拟提供方 SQLite 副本；Redis 使用 DB 14，临时 API/provider 端口为 58020/58021。队列使用实际声明代码创建的持久 quorum queue，并对独立 vhost 设置相同死信策略。未重启原 RabbitMQ 容器或修改原 vhost。

本轮启动实际 relay、关键 worker 和维护 worker 进程，所有业务操作经过 outbox → publisher confirm → RabbitMQ → worker → 数据库提交 → consumer ack。额外重复消息沿用数据库中同一 envelope/message_id，并通过 AMQP 实际发布，发布确认均为 Ack。没有直接调用 worker handler 来代替消息投递，也没有替换支付响应。隔离环境将操作租约设为 30 秒以缩短故障演练；应用默认 60 秒未变。

| 验证 | 触发方式与观察结果 |
|---|---|
| 重复支付消息 | 启动两个关键 worker，同一支付消息累计发布、投递、确认各 6 次，其中包含处理中和完成后的重复消息。订单只新增一笔 3500 分收款，任务 fence 保持 1，订单版本保持 2 |
| 退款外部成功后中断 | 持有订单锁，把 worker 阻塞在本地写回之前；提供方已完成 3500 分退款，本地仍为 refunded=0、reserved=3500。此时 SIGKILL worker，RabbitMQ 记录一次 redelivery |
| 退款重启恢复 | 重启后旧投递因租约仍有效而不重复执行；租约到期，真实 relay 扫描并发布 dispatch_version=2，worker fence 增至 2 后完成。提供方访问记录显示先 GET 404 → PUT 200，恢复时 GET 200；没有再次发出退款 PUT。两侧均只有一笔退款，本地占用额归零 |
| 恢复后的重复退款 | 再发布两次完成后的消息，资金流水、余额、订单版本和 fence 均保持不变 |
| 对账页提交前中断 | 持有轮次行锁，让维护 worker 停在实际 UPDATE reconciliation_runs；SIGKILL 后未提交的证据和游标全部回滚：provider_entries=0、entries_checked=0、after_sequence=0 |
| 对账重启恢复 | RabbitMQ 记录一次 redelivery，租约到期后真实扫描器补发，随后各阶段继续通过队列推进。最终 done，11 条提供方证据、22 项双向检查；完成后再次重复投递两次，计数不变 |
| 正常终止 | 提供方暂停期间向正在处理支付的 worker 发送 SIGTERM，再恢复提供方；worker 完成一次收款并确认消息后以退出码 0 结束。重启并重复投递后仍只记账一次，随后正常取消与退款完成 |
| 最终一致性 | 隔离库 28 个操作均 done，未发送 outbox、未解决对账差异、message_failures 均为 0；九个队列的 ready/unacknowledged 均为 0。隔离库新增的两笔收款及对应退款各 3500 分，库存预留/分配归零 |
| 原业务库 | 实收仍为 15900、实退 15300 分；在库数量仍为 1 和 10，预留/分配为 0，未解决差异为 0 |

这轮未发现新的实现问题，没有修改业务代码、依赖或迁移。验证摘要、逐步观察、worker 日志、隔离 PG dump 和 SQLite backup 保存在 `var/rabbitmq-validation/20261022/`；提供方快照通过 SQLite quick_check。全部临时服务已停止，执行脚本与临时凭证配置已移除；隔离库和空 vhost 保留，九个队列均无消费者。未新增仓库测试文件、fixtures 或断言。

这次补齐了实际 broker 投递中的重复处理、强制终止、租约扫描恢复和正常终止证据。仍未验证 broker 节点故障、集群选主、网络分区、长时间积压或生产吞吐；不能把单节点开发环境的恢复结果当作高可用或容量结论。
