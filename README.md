# A-commerce

[![backend](https://github.com/juemimgcd/A-commerce/actions/workflows/backend.yml/badge.svg)](https://github.com/juemimgcd/A-commerce/actions/workflows/backend.yml)
[发布版本](https://github.com/juemimgcd/A-commerce/releases) · [CI/CD 与发布指南](docs/releasing.md)

基于 FastAPI 的电商系统，包含 React / TypeScript 商城、商家工作台和平台管理，覆盖交易、履约、售后和运营能力，以 1 万并发在线用户为容量验收目标。

2026-10-30 商家扩展：本地已实现用户多店经营、按店独立结算、成本快照、店铺授权、履约售后、经营统计和只读商家 Agent 接入。实施依据、迁移与验证边界见 [商家角色实施与发布](docs/merchants.md)。测试夹具已适配店铺外键，现有自动回归 58 项通过、2 项按环境条件跳过；正式库迁移与生产发布尚未执行。

商家经营增强：在上述基础上增加诊断证据、费用与贡献、补货收货、客户快照和固定价活动预算。Agent 可生成内部草稿，活动发布与实收仍由店主确认。迁移为 `0010_merchant_ops`，新活动默认关闭；功能、API、启用顺序和验证证据见 [商家经营 Agent 四模块](docs/merchant-agent.md)。

2026-10-30：已接入基于 pi 的客服、导购和显式偏好记忆，并完成一轮本地真实模型联调。当前支持受限查询和偏好候选，交易提交仍由用户在业务页面确认；M7 的 grant/action 与事件跟进仍待实现。环境、验证场景、修复和证据见 [客服与记忆验收记录](docs/support-acceptance-2026-10-30.md)。下文早期“pi 延后”的描述针对完整交易 Agent。

前端已完成 F0–F4 的本机实现与核心流程联调：真实目录、幂等购买、模拟支付、履约、部分退货及运营管理。入口与启动命令见 [前端 README](frontend/README.md)，实际证据和发布边界见 [前端实施记录](docs/frontend-implementation.md)。正式商家资料、真实支付和生产容量仍需独立验收。

**当前状态：后端已完成交易链路，并补入对账、事件恢复和运营维护能力。** 已有定期资金核对/差异复核、订单快照/事件归档、媒体对象清理、员工管理、筛选和登录审计；新增维护 worker、持久心跳、告警规则及 CI 配置。启动方式、实际验证结果与发布缺口见 [后端实施记录](docs/backend-implementation.md)，故障处理见 [运维手册](docs/backend-operations.md)。尚未完成 1 万在线用户压测、真实支付沙箱与生产部署验收，pi 延后到 M7。

多实例与流量治理已加入部署配置：HAProxy、两个业务 API、独立回调 API、独立限流 Redis，以及入口/身份/交易/SKU 分层额度。启动、故障策略、滚动替换与本轮验证见 [多实例部署与分层限流](docs/traffic-deployment.md)。这些配置不代表跨主机高可用或容量验收完成。

部署脚本默认启用 [Redis Sentinel](docs/redis-sentinel.md)：缓存与限流各一主两从，三个哨兵监控两组，客户端自动发现当前主节点；裸 Compose 保留直连开发模式。单机容器切主验证与跨主机容灾分开验收。

另提供 [PostgreSQL 与 RabbitMQ 高可用演练](docs/postgres-rabbitmq-ha.md)：PostgreSQL + Patroni + etcd 三节点、RabbitMQ 三节点 quorum queues，使用 `validate_deployment.py --ha` 在隔离 Docker 项目中运行故障切换、节点恢复与备份恢复测试。该覆盖配置使用独立数据卷，现有单节点业务库需要另行迁移。

2026-10-25 补入隔离自动化业务回归、真实服务故障恢复验证、SQL/连接池/缓存指标、Prometheus + Alertmanager、本地负载与数据生成工具。运行方法和证据边界见 [本地后端回归与验收](docs/backend-validation.md)。服务器、真实外部集成与 1 万在线容量仍待独立验收。

## 目标

买家浏览/筛选商品、维护购物车、确认报价并下单；后端处理库存、支付、履约、取消、退货退款，管理员完成商品、库存、发货和异常处理。普通购买流程独立运行。

成熟度通过业务正确性、故障恢复、权限、观测、可部署性与验证证据衡量。当前商家扩展按店独立结算，沿用单仓库存和 CNY；以独立模拟支付/物流提供方验证异步边界，真实沙箱单独验收。

容量口径是 10,000 名持续浏览与下单的用户，包含思考时间和完整业务路径。QPS、成功下单 TPS、后台积压与延迟另外记录；当前没有实测成绩。负载假设、连接预算和验收目标见 [容量计划](docs/capacity-plan.md)。

## 架构决定

沿用 [fastapi_newsApp](https://github.com/juemimgcd/fastapi_newsApp) 的分层习惯，补齐业务层：

```text
网关 → 多个无状态 API 实例
          └── routers → services → crud → models / PostgreSQL
                           ├── policies / integrations
                           ├── cache → Redis cache
                           └── outbox → relay → RabbitMQ → aio-pika 异步 worker

客服已接入：pi / TypeScript → 受限 HTTP 工具 → 同一套 services
M7 后续：grant / action → 独立确认 → 交易执行与恢复
```

一份 Python 代码，按 API、worker、relay/scheduler 进程部署。services 控制事务，CRUD 不单独 commit；pi 不直接访问数据库。具体目录和事务边界见架构文档。

RabbitMQ 通过 AMQP 0-9-1 传递持久化消息；aio-pika 负责异步发布和消费。发布确认、手动 ack、消费并发、数据库驱动的重试与恢复都有明确边界，业务是否完成以 PostgreSQL 记录为准。Redis 只承担缓存与限流。

## 技术基线

| 层 | 选择 |
|---|---|
| 运行时 | Python 3.13、uv |
| HTTP | FastAPI、Uvicorn、Pydantic、Pydantic Settings |
| 持久化 | PostgreSQL、SQLAlchemy AsyncSession、asyncpg、Alembic |
| 缓存 | Redis：缓存与限流 |
| 消息与后台任务 | RabbitMQ、aio-pika、独立 asyncio worker |
| 身份 | 可撤销服务端 Token、分角色权限、pwdlib + Argon2 |
| 集成 | HTTPX，媒体/邮件通过可选适配器 |
| 观测 | structlog、Prometheus client、可选 OpenTelemetry |
| 容量工具 | 网关、Prometheus/Grafana、k6；部署版本在实施时固定 |
| Agent | pi + 独立 TypeScript 客服适配器已接入；grant/action 待 M7 实施 |

直接依赖精确版本与用途见 [依赖说明](docs/dependencies.md)，传递依赖见 `uv.lock`。当前安装、静态检查及本机业务验证范围见实施记录，不能据此推断生产容量。

## 功能范围

账号与权限、商品/SKU/媒体、搜索与比较、购物车/收藏、价格与运费、库存预留、订单、支付/对账、履约、部分退货退款、通知、运营统计、审计与运维。Agent 授权确认属于后续阶段。

核心验收覆盖正常购买、最后一件库存竞争、重复下单、确认后改价、支付超时/迟到、取消与发货并发、部分退货和 worker 重启；容量验收覆盖混合读写、热点、缓存失效和持续运行。

暂不承担多商家分账、跨店合并支付、多仓拆单、复杂促销、推荐模型或第二套 Python Agent。商家扩展的具体范围见最终纲要；秒杀已通过独立改动接入。

## 阅读顺序

1. [整体架构与事务设计](docs/architecture.md)
2. [功能清单与成熟度门槛](docs/features.md)
3. [1 万在线用户容量计划](docs/capacity-plan.md)
4. [依赖、版本与验证边界](docs/dependencies.md)
5. [分阶段实施计划](docs/mvp-plan.md)
6. [后续 pi 接入、授权与工具协议](docs/pi-integration.md)
7. [新闻项目参考与取舍](docs/reference-review.md)
8. [前端设计与实施计划](docs/frontend-design-plan.md)
9. [前端运行与联调记录](docs/frontend-implementation.md)
10. [商家角色最终纲要](docs/merchant-plan.md)与[实施及发布说明](docs/merchants.md)

## 落地顺序

M1 工程/身份/资源预算 → M2 商品/购物车/报价/读路径 → M3 订单/库存/异步支付 → M4 履约/售后/退款 → M5 运营/通知/可恢复任务 → M6 1 万在线用户与发布验收 → M7 pi 接入。

M1–M3 达到 v0.1 内部联调目标；M1–M6 及 v1.0 功能、正确性和容量门槛完成后发布后端 v1.0。M7 独立验收 Agent，不阻塞后端交付。

## 当前文件边界

已新增 `src/a_commerce`、`frontend`、`alembic`、`scripts`、`deploy`、`tests`、`pi-adapter`。业务规则、事务边界、重试与恢复均有中文注释；接口定义可通过 `/docs` 查看。代码托管于 [juemimgcd/A-commerce](https://github.com/juemimgcd/A-commerce)，CI 通过后为新版本自动创建 tag、GHCR 镜像和 Release 部署包。自动化业务回归已接入 CI 配置，未修改 pi 仓库；客服适配器已实现，完整交易 Agent 待 M7 实施。服务器 CD 默认关闭，启用条件见发布指南。

当前多实例启动以 [部署与限流说明](docs/traffic-deployment.md) 为准；早期单实例联调过程见 [启动与验证记录](docs/backend-implementation.md)。原本地联调凭证保存在忽略目录 `var/local-accounts.json`，不要提交到版本库。
