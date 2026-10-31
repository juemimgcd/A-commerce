# 多实例部署与分层限流

本阶段保留 Python 模块化单体。HAProxy 负责负载均衡、就绪探测和入口粗限流，Python + 独立 Redis 负责身份、业务和热点额度。默认是单主机上的两个业务 API 和一个回调 API；部署已加入缓存/限流各一主两从及三个 Sentinel，见 [Redis 哨兵部署](redis-sentinel.md)。网关、数据库、RabbitMQ 仍是单节点，整体不是跨主机高可用。下文 2026-10-23 的运行记录保留当时的单节点事实。

## 1. 拓扑与启动

```text
客户端 → gateway:8080 → api / api-secondary（买家、管理员）
                     → api-callbacks（支付、物流回调）
三个 API → PostgreSQL（会话、业务事实）
         → Sentinel → commerce-cache（商品缓存，一主两从，allkeys-lru）
                    → commerce-rate（限流，一主两从，noeviction + AOF）
业务事务 → outbox → relay → RabbitMQ → 分组 worker → provider
provider → gateway → api-callbacks
```

API 不映射宿主机端口，统一入口为 `127.0.0.1:58000`。API 只信任网关固定地址的转发头；网关覆盖 `X-Forwarded-For`，删除 `Forwarded`、`X-Real-IP`。默认直接接收客户端 HTTP，不信任前置代理自报地址。接 TLS 负载均衡/CDN 时需要另行配置可信来源、真实地址与协议传递，不能开启任意来源信任。

在项目根目录启动：

```sh
# 首次创建配置；已有 .env 时跳过，该脚本拒绝覆盖。
uv run --no-sync python scripts/configure_local.py
# 已有 .env 只补缺失凭证：uv run --no-sync python scripts/configure_local.py --redis-secrets
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml build api
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml up -d --wait \
  db redis redis-rate redis-cache-replica-1 redis-cache-replica-2 \
  redis-rate-replica-1 redis-rate-replica-2 sentinel-1 sentinel-2 sentinel-3 rabbitmq
sh deploy/rabbitmq-policy.sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml up -d --no-build --wait \
  api api-secondary api-callbacks gateway provider relay worker worker-general worker-maintenance
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml exec -T api python scripts/check_redis.py
curl --fail http://127.0.0.1:58000/health/ready
```

API 依赖一次性迁移成功，迁移不在每个实例的 lifespan 中执行。网关检查各实例的 `/health/ready`，数据库版本正确且未摘流才接流量。readiness 不依赖 Redis，以便故障时查询和回调仍能降级运行。

原宿主机进程占用 58000/58001 时先处理冲突；原 provider SQLite 和本地文件需一致地迁入卷，不能把旧业务库与空 provider 状态混用。宿主机直连调试可单独使用裸 `compose.yaml`，缓存/限流端口为 56379/56380；这种模式没有哨兵。哨兵配置关闭 Redis 宿主机映射，应用应在容器网络内启动。

API 与 worker 共享 `files` 卷，单主机内媒体和导出文件可跨实例读取。跨主机部署需要共享/对象存储，并改造仍写本地文件的导出链路。

隔离运行使用独立的 `COMPOSE_PROJECT_NAME`、`COMMERCE_ENV_FILE`、`COMMERCE_*_PORT`、`COMMERCE_EDGE_SUBNET`、`COMMERCE_EDGE_RANGE` 与 `COMMERCE_GATEWAY_IP`。子网、动态地址池和固定 IP 一并调整；固定 IP 必须位于子网内、动态池外，避免 API 先启动时抢占网关地址。不要与 Docker/VPN 网段重叠，也不复用原业务凭证、卷和 provider 数据。

## 2. 限流范围与配置

以下均为初始保护预算，不是压测结论。Python 配置位于 `conf/traffic.py`；rate 为每秒恢复令牌数，burst 为桶容量，一次操作消耗一个令牌。

| 层次 | 主体 / 范围 | rate / burst |
|---|---|---|
| 网关入口 | 普通 IP / 回调 IP，独立本地表 | 300 / 600 次每 10 秒 |
| 共享入口 | 同一真实 IP，所有业务路由，鉴权查库前 | 30 / 60 |
| 敏感操作 | 同一 IP 的登录/注册/改密/重置共用 | 20÷60 / 20 |
| 用户读取 | 账号类型 + 服务端账号 ID | 10 / 20 |
| 用户写入 | 同账号普通写操作共用 | 2 / 5 |
| 用户报价 / 下单 | 同账号，分别独立，叠加普通写额度 | 1 / 3；0.5 / 2 |
| 全局报价 / 下单 | 所有业务 API 共用 | 40 / 60；20 / 30 |
| SKU 报价 / 下单 | 每个 SKU 各自的额度 | 10 / 15；5 / 8 |
| 支付 / 物流回调 | 验签和字段校验后，分别独立 | 20 / 40；10 / 20 |

增加 API 实例不会增加 Python 共享额度。同账号的新会话仍共用额度。热点保护对所有涉及的 SKU 分别建桶，不做自动 Top-K 热点识别。多 SKU 请求的全局桶与 SKU 桶由同一 Lua 脚本判断，全部足够才扣减，额度不足则整组不扣；使用 Redis 时间，桶空闲至完全恢复后过期。当前使用单独 Redis；所有桶有相同 hash tag，不能靠 Redis 分片扩容该脚本。

下单从属于当前账号的不可变报价读取 SKU，释放读会话后执行流控，再进入原事务复核身份、报价、幂等与库存。Redis 等待不持有商品/库存锁。已创建订单重放跳过新建全局/SKU 额度，但仍受入口/用户限频。限流不替代数据库正确性。

环境变量可以覆盖单项参数，两个业务实例必须保持一致：

```dotenv
COMMERCE_TRAFFIC__ORDER_GLOBAL__RATE=20
COMMERCE_TRAFFIC__ORDER_GLOBAL__BURST=30
COMMERCE_TRAFFIC__ORDER_SKU__RATE=5
COMMERCE_TRAFFIC__ORDER_SKU__BURST=8
```

配置随启动加载，没有动态规则管理界面；修改共享额度需协调重启，不能在滚动期间混用不同规则。网关本地额度在 `deploy/haproxy.cfg` 修改。

## 3. 故障与过载语义

- 额度不足：`429 RATE_LIMITED` 和整数秒 `Retry-After`。时间表示当前桶最早恢复时间，不保证下次一定成功；客户端退避并加抖动，写请求保持原幂等键。
- 限流 Redis 不可用：新写入/敏感操作返回 `503 RATE_LIMIT_UNAVAILABLE`、`Retry-After: 1`；GET/HEAD/OPTIONS 和验签通过的回调继续。每次 Redis 调用总等待不超过 350ms。
- 查询仍受网关入口/后端连接、Uvicorn 并发和数据库池保护；缓存失败继续有界回源。回调继续执行签名、去重和数据库约束，不伪装成功。额度耗尽仍返回 429，依赖提供方重试与后台核对恢复。
- 限流 Redis 是独立进程，换 DB 编号不能隔离内存淘汰。`noeviction` 内存满时失败关闭；不能自动清空限流库。Lua 运行错误不回滚已扣额度，错误时保守拒绝。AOF 每秒刷盘仍可能丢失最近额度，不是账务意义的强一致配额。
- 网关后端排队最多 1 秒，写请求不自动重放。超时/断连不能证明订单未提交，应查询原请求结果。

## 4. 资源预算

| 进程 | PostgreSQL 连接上限 |
|---|---|
| api / api-secondary | 2 × 5 = 10 |
| api-callbacks | 2 |
| relay | 2 |
| critical / general / maintenance worker | 4 + 2 + 2 = 8 |
| 常驻合计 | 22 |

滚动替换按额外 5 条连接留余量，迁移、备份、监控和人工查询另外预算。网关每个业务实例的后端连接/排队上限为 80/40，回调为 16/16。Uvicorn 业务并发 100，回调 24，退出等待 30 秒，Compose 停止宽限 40 秒。

每业务 API 回源槽位默认 4，两个共 8，不是共享信号量。每进程两个 Redis 数据池分别最多 10 连接；哨兵模式还为每个组的三个 Sentinel 分别建立发现连接池，每池同样最多 10 连接。回调使用独立数据库池但仍共享 PostgreSQL 服务，不能声称数据库层完全隔离。

## 5. 滚动替换与观测

无需迁移、兼容当前 schema 和限流规则的 API 版本，可先准备镜像再运行：

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml build api
bash deploy/roll-api.sh
```

脚本按实例依次创建 `/tmp/commerce-draining`，等待网关确认 DOWN，再重建；容器健康且网关确认 UP 后才处理下一个。Uvicorn 收到 SIGTERM 后有界等待在途请求。失败即停止，保留另一个实例。目录锁为 `var/api-roll.lock`，异常退出留下锁时先检查现场再清理。

摘流文件不进入共享卷，重建容器自然清除。若摘流后未重建，需要恢复原实例，可确认业务状态后删除该实例的 `/tmp/commerce-draining`，等待网关重新检查。脚本不更新单实例回调、worker、网关，也不执行迁移；完整发布仍用 `deploy/deploy.sh` 停写备份。

公开网关的 `/metrics` 返回 404。Prometheus 应分别抓取 `api:8000/metrics`、`api-secondary:8000/metrics`、`api-callbacks:8000/metrics`；每容器单 Python 进程，不跨容器共享 `PROMETHEUS_MULTIPROC_DIR`。数据库派生的后台指标不能跨实例简单求和。

新增 `commerce_rate_limit_total{policy,outcome}`，结果为 allowed、rejected、unavailable_open、unavailable_closed；不使用用户/IP/SKU 作为指标标签。网关日志记录实际后端实例；`gateway:8081/stats;csv` 为容器内只读状态，不映射宿主机，不开放管理操作。

## 6. 验证记录

2026-10-23，在 macOS Apple Silicon 的 Docker 隔离项目 `a-commerce-traffic-20261023` 完成以下手工运行检查。使用独立凭证、端口、网络、PostgreSQL/Redis/RabbitMQ/provider/文件卷，没有连接原业务数据。应用镜像本轮只在本机 ARM64 构建运行，没有发布新镜像或 GitHub Release。

| 检查 | 实际观察 |
|---|---|
| 静态与部署配置 | Ruff lint/format、严格 mypy、compileall；Compose 与 bash 语法、HAProxy 配置检查通过 |
| 数据库与全容器启动 | 空库升级至 `0005_case_fence`；`alembic check` 无差异；网关、双 API、回调 API 及后台进程启动成功 |
| 跨实例会话 | 同一登录 token 交替访问两实例，8 次 profile 查询均 200，网关日志确认两个后端分别处理 |
| 共享用户额度 | 32 次并发 profile 请求合计 20 次 200、12 次 429；两实例各记录 6 次拒绝，额度未翻倍 |
| 代理头与登录保护 | 28 次错误登录每次带不同伪造 X-Forwarded-For；20 次 401、8 次 429；Redis 仅一个 auth_ip 桶，429 带 Retry-After |
| 网关入口 | 350 次短时健康接口请求中 300 次 200、50 次 429；后续响应为 ENTRY_RATE_LIMITED，Retry-After=10 |
| 入口隔离 | 普通入口限流期间，回调仍到达专用 API，由签名校验返回 401，而不是被普通入口额度拒绝 |
| 单实例停止 | 停止 api，网关确认 DOWN 后同一会话的 8 次请求均由剩余实例成功处理；重启后重新 UP |
| 滚动替换 | 脚本依次确认两实例 DOWN → 重建 → UP；观察窗口的 100 次目录请求均 200。不能据此保证所有故障场景零失败 |
| 限流存储中断 | 停止 redis-rate：商品查询 200，新报价 503 RATE_LIMIT_UNAVAILABLE、Retry-After=1 |
| 合法支付回调降级 | 限流 Redis 停止期间，独立模拟提供方发出真实签名回调；专用 API 返回 204，订单确认 paid/5400 分，指标记录 unavailable_open |
| 热点与组合扣减 | 仅在隔离环境临时将 SKU 容量设为 1、补充 0.01/s：SKU A 下单成功；随后 A+B 请求返回 429、Retry-After=97。全局桶 tokens/updated 前后相同，B 桶未创建，未生成第三张订单 |
| 幂等重放 | 重放已成功的 SKU A 订单仍返回原订单 ID；数据库订单数量保持 2 |
| 回调防伪与运维边界 | 无效签名 401 且不创建支付回调额度；公开 `/metrics` 和编码后的 `/%6detrics` 均 404 |
| 存储策略 | 实际查询缓存 Redis 为 allkeys-lru；限流 Redis 为 noeviction、AOF=yes、appendfsync=everysec |

首次启动发现动态地址分配可能占用网关固定 IP，已通过独立动态地址池修复并重新启动成功。滚动脚本中的中文标点紧邻 shell 变量问题也在实际执行时修复。热点检查后已恢复默认限流配置。

临时服务已停止，原 `a-commerce` 项目的三个基础容器保持原来的停止状态。日志、响应计数和隔离库快照保存在忽略目录 `var/traffic-validation/20261023/`；该目录中的隔离配置权限为 0600，包含临时凭证，不得提交或对外分享。未改写原 `.env` 或原业务卷。

本轮没有创建/修改测试文件、测试夹具或自动化断言；以上是实际容器与 HTTP 操作观察，不是自动化回归套件。未验证万级用户容量、长稳、跨主机切换、限流 Redis 高可用/掉电恢复、真实支付或 TLS 公网部署。单实例突发故障在网关探测窗口内仍可能产生短暂 5xx，写请求必须保留幂等恢复策略。

依据：[HAProxy 3.2 配置手册](https://docs.haproxy.org/3.2/configuration.html)、[Redis Lua 执行语义](https://redis.io/docs/latest/develop/programmability/eval-intro/)、[Redis TIME](https://redis.io/docs/latest/commands/time/)。

滚动与维护发布脚本从现有 API 容器的 Compose 标签读取已部署的覆盖文件，
在当前 Release 中继续加载 web、support、monitoring 和 HA 配置。缺少或不认识的
覆盖文件会在摘流之前报错，避免重建 API 时静默丢失配置。
