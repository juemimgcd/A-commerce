# A-commerce

[![backend](https://github.com/juemimgcd/A-commerce/actions/workflows/backend.yml/badge.svg)](https://github.com/juemimgcd/A-commerce/actions/workflows/backend.yml)
[发布版本](https://github.com/juemimgcd/A-commerce/releases) · [发布指南](docs/releasing.md) · [前端说明](frontend/README.md) · [Agent 适配器](pi-adapter/README.md)

基于 FastAPI、Next.js 和 pi 的电商系统，提供买家商城、商家工作台、平台管理及受限业务工具驱动的 AI 助手。覆盖商品浏览、按店下单、模拟支付、履约、部分退货退款、经营分析和客户偏好记忆；普通交易流程可独立于 Agent 运行。

当前采用单仓库存、CNY 和按店独立结算。支付、物流通过独立模拟提供方联调，真实支付沙箱、生产部署与完整容量目标仍需独立验收。

## 已实现能力

| 模块 | 能力 |
|---|---|
| 买家商城 | 商品搜索、筛选、比较、收藏、购物车、地址、报价确认、幂等下单、模拟支付、物流查询、取消及部分退货退款 |
| 商家工作台 | 一名用户经营多店、店铺授权、商品与成本管理、库存调整、订单履约、售后处理和经营报表 |
| 经营概览 | 按上海经营日查看收入、支付订单、新增客户与退款趋势，对比上一等长周期，聚合经营待办、低库存和最近订单 |
| 商家经营助手 | 诊断证据、费用与贡献、补货收货、客户快照及固定价活动预算；可生成内部草稿，活动发布和实际收货由店主确认 |
| 平台管理 | 商品与库存、店铺、员工权限、配送、资金对账、差异复核、审计及多商品秒杀活动 |
| 客服与导购 | pi 原生工具循环、会话接续与压缩，受限查询商品、订单、物流和售后资格；显式偏好候选由用户确认后保存 |
| 异步与运维 | outbox、消息重试、租约与 fence 恢复、定期对账、事件归档、媒体清理、后台心跳、指标与告警 |

商家固定价活动由 `COMMERCE_MERCHANT_PROMOTIONS_ENABLED` 控制，默认关闭。客服和商家 Agent 需要单独配置模型及桥接密钥；未配置时不影响普通交易。完整交易 Agent 的 grant/action 授权、独立确认和事件跟进仍属于后续范围。

目前不包含多商家分账、跨店合并支付、多仓拆单、通用复杂促销引擎或推荐模型。功能口径与限制见 [功能清单](docs/features.md)、[商家实施说明](docs/merchants.md)和[商家经营 Agent](docs/merchant-agent.md)。

## 架构

```text
浏览器 → Next.js 商城 / 商家工作台 / 平台管理
              ↓ 受控 API 代理
          HAProxy 网关
              ├── 业务 API × 2
              └── 独立回调 API
                       ↓
              routers → services → crud / models → PostgreSQL
                            ├── Redis：目录缓存与独立限流存储
                            └── outbox → relay → RabbitMQ → asyncio workers
                                                               ↓
                                                        支付 / 物流模拟提供方

FastAPI ← 受限 HTTP 业务工具 → pi / TypeScript 适配器 → 模型提供方
```

- Python 模块化单体按 API、relay 和不同 worker 进程部署。业务服务控制事务，CRUD 不单独提交；PostgreSQL 保存业务事实，Redis 不承担订单或资金账本。
- RabbitMQ / aio-pika 提供发布确认和手动 ack，结合数据库记录进行重试、去重与恢复。
- pi 复用同一套业务权限与服务，不直接访问数据库；交易提交仍由用户在业务页面确认。
- 目录支持查询缓存、短时 JSON 响应复用和可选只读副本路由。缓存仅服务展示，报价、订单、库存与授权仍核验数据库事实。
- 多实例、Redis Sentinel、PostgreSQL / RabbitMQ HA 均提供部署或演练配置；单机容器切换不能代表跨主机容灾完成。

事务与进程边界见 [架构设计](docs/architecture.md)，缓存和读路由见 [目录缓存](docs/catalog-cache.md)与[商品查询读写分离](docs/database-read-routing.md)。

## 技术栈

| 层 | 选择 |
|---|---|
| 后端 | Python 3.13、uv、FastAPI、Uvicorn、Pydantic |
| 数据库 | PostgreSQL、SQLAlchemy AsyncSession、asyncpg、Alembic |
| 缓存与消息 | Redis、RabbitMQ、aio-pika |
| 前端 | Next.js 16 App Router、React 19、TypeScript、TanStack Query、Zod |
| Agent | pi-coding-agent、独立 TypeScript 适配器 |
| 身份与集成 | 可撤销服务端 Token、角色与店铺权限、Argon2、HTTPX，可选对象存储和邮件适配器 |
| 部署与观测 | Docker Compose、HAProxy、structlog、Prometheus、Alertmanager，可选 OpenTelemetry |

精确依赖以 `pyproject.toml`、各子项目 `package.json` 和锁文件为准；用途见 [依赖说明](docs/dependencies.md)。

## 本地启动

需要 Python 3.13、uv、Docker Compose；运行前端建议使用 Node.js 24。以下命令在仓库根目录执行，适用于新的本地开发环境，默认使用直连 Redis。已有环境应沿用原配置、数据卷及模拟提供方数据；Sentinel 启动步骤见 [多实例部署](docs/traffic-deployment.md)。

### 1. 安装依赖并初始化配置

```sh
uv sync --frozen --no-build --extra storage --extra mail
# 仅首次运行；已有 .env 时跳过，脚本拒绝覆盖。
uv run --no-sync python scripts/configure_local.py
```

配置项说明见 [.env.example](.env.example)。生成的 `.env` 含本机随机凭证，不应提交；启动命令不会自动创建商品或演示账号。

### 2. 启动后端及异步进程

```sh
docker compose --env-file .env -f deploy/compose.yaml build api
docker compose --env-file .env -f deploy/compose.yaml up -d --wait db redis redis-rate rabbitmq
sh deploy/rabbitmq-policy.sh
docker compose --env-file .env -f deploy/compose.yaml up -d --no-build --wait \
  api api-secondary api-callbacks gateway provider relay worker worker-general worker-maintenance
curl --fail http://127.0.0.1:58000/health/ready
```

Compose 会通过一次性 `migrate` 服务执行 `alembic upgrade head`，迁移成功后才启动 API。默认 API 入口为 <http://127.0.0.1:58000>，接口文档为 <http://127.0.0.1:58000/docs>。

需要平台管理账号时，通过交互式输入设置密码：

```sh
docker compose --env-file .env -f deploy/compose.yaml exec api \
  python scripts/create_admin.py admin@example.com
```

该脚本不会覆盖已有管理员的密码或权限。买家可以在商城注册；店铺开设与店主分配见 [商家实施说明](docs/merchants.md)。

### 3. 启动前端

在另一个终端执行：

```sh
cd frontend
npm ci --ignore-scripts
npm run dev
```

默认前端连接 `http://127.0.0.1:58000`。自定义地址时参考 [前端环境变量](frontend/.env.example)创建 `frontend/.env.local`，并保持浏览器访问来源与 `COMMERCE_WEB_ORIGIN` 一致。

| 入口 | 地址 |
|---|---|
| 商城 | <http://127.0.0.1:3000> |
| 商家工作台 | <http://127.0.0.1:3000/merchant> |
| 平台管理 | <http://127.0.0.1:3000/admin> |
| 客服 / 偏好记忆 | <http://127.0.0.1:3000/support> / <http://127.0.0.1:3000/account/memory> |

客服功能需继续按 [适配器启动说明](pi-adapter/README.md#启动)配置桥接密钥和模型。容器部署可叠加 `deploy/compose.support.yaml`；前端容器可叠加 `deploy/compose.web.yaml`，默认网页入口为 `http://127.0.0.1:58080`。详细配置及已有环境切换要求见各子项目 README。

## 检查与验证

以下为现有检查入口，在仓库根目录执行：

```sh
# 后端依赖同步、Ruff、mypy 与编译检查
sh scripts/check.sh

# 独立 PostgreSQL / Redis / RabbitMQ 与 HTTP 业务回归，需要 Docker
uv run --no-sync python scripts/test_backend.py -q -W error::ResourceWarning

# 全容器部署冒烟；--ha 额外执行 PostgreSQL / RabbitMQ HA 演练
uv run --no-sync python scripts/validate_deployment.py --ha

# 前端静态检查与构建（先安装子项目依赖）
npm --prefix frontend run check
npm --prefix frontend run build

# Agent 适配器依赖安装及类型检查
npm --prefix pi-adapter ci --ignore-scripts
npm --prefix pi-adapter run check
```

回归入口创建隔离项目、凭证和数据卷，结果保存于忽略目录 `var/validation/`。全容器验收默认是小规模部署冒烟，不能作为容量验收。完整流程和证据口径见 [本地后端回归与验收](docs/backend-validation.md)。

**已记录的验证与未完成目标：**

- 2026-10-31 的商家经营概览记录包含本地静态检查、桌面 Chrome 联调和现有服务回归（58 项通过、2 项条件跳过）；这是历史记录，不代表每次修改后自动重验。详见 [经营概览](docs/merchant-overview.md)。
- 客服、导购、偏好确认、会话接续与压缩已有本地真实模型联调记录；自然语言准确率和生产容量未由该轮联调证明。详见 [客服与记忆验收](docs/support-acceptance-2026-10-30.md)。
- 目录浏览已有固定资源压测：历史 30 分钟测试约 9,970 成功 RPS，失败及未发出比例为 0.2953%，**未通过稳定 10k RPS 门槛**。详见 [性能说明](docs/catalog-performance.md)与[第二轮记录](docs/catalog-benchmark-round2-2026-10-30.md)。
- 10,000 名持续浏览与下单用户是完整业务容量目标，与单独目录 RPS 不等价；真实支付、生产环境和跨主机容灾仍需独立验收。详见 [容量计划](docs/capacity-plan.md)。

CI 配置包含静态检查、迁移、依赖审计和隔离回归，符合发布条件后构建镜像及 Release；服务器 CD 由部署开关控制。当前运行结果以工作流页面为准，发布步骤见 [CI/CD 与发布指南](docs/releasing.md)。

## 目录与文档导航

| 路径 | 职责 |
|---|---|
| `src/a_commerce/` | API、业务服务、模型、消息、worker 与集成 |
| `frontend/` | 商城、商家工作台、平台管理及受控 API 代理 |
| `pi-adapter/` | Agent 循环、工具、会话、压缩及运行预算 |
| `alembic/` | 数据库迁移 |
| `deploy/` | 容器、网关、监控、HA 与发布配置 |
| `scripts/` | 配置、管理员初始化、检查、回归、备份恢复及压测工具 |
| `tests/` | 现有自动化回归 |
| `docs/` | 架构、实施记录、验收证据与运维手册 |

- **业务与产品**：[功能清单](docs/features.md)、[商家角色纲要](docs/merchant-plan.md)、[商家实施](docs/merchants.md)、[经营概览](docs/merchant-overview.md)、[经营 Agent](docs/merchant-agent.md)、[秒杀活动](docs/flash-sales.md)。
- **实现与联调**：[后端实施](docs/backend-implementation.md)、[前端实施](docs/frontend-implementation.md)、[前端设计](docs/frontend-design-plan.md)、[客服验收](docs/support-acceptance-2026-10-30.md)。
- **性能与部署**：[目录性能](docs/catalog-performance.md)、[读写分离](docs/database-read-routing.md)、[多实例与限流](docs/traffic-deployment.md)、[Redis Sentinel](docs/redis-sentinel.md)、[PostgreSQL / RabbitMQ HA](docs/postgres-rabbitmq-ha.md)、[运维手册](docs/backend-operations.md)。
- **规划与背景**：[容量计划](docs/capacity-plan.md)、[分阶段计划](docs/mvp-plan.md)、[后续交易 Agent 协议](docs/pi-integration.md)、[参考项目取舍](docs/reference-review.md)。规划文档中的阶段目标应结合当前实现及对应验收记录阅读。
