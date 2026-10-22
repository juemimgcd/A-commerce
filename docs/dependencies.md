# 依赖基线与版本策略

检查日期：2026-10-22。已安装并锁定依赖；静态检查、数据库迁移和本机业务验证见 [实施记录](backend-implementation.md)。`pyproject.toml` 是直接依赖的唯一来源，`uv.lock` 保存传递依赖和文件哈希。

## 1. 运行环境

- Python 3.13：`requires-python = ">=3.13,<3.14"`，统一开发/CI/部署小版本系列。当前解析使用本机 CPython 3.13.14。
- PostgreSQL 18 系列：最终补丁版本与镜像 digest 在部署配置落地时固定；不使用 RC 版本，也不跨大版本自动升级。
- Redis 7.4 系列：只用于缓存与限流，精确镜像版本/digest 同部署验收确定。Python `redis` 客户端版本与服务端版本不是一回事。
- RabbitMQ 4.4 系列：通过 AMQP 0-9-1 提供消息队列，检查时最新稳定版为 4.4.0；部署时固定受支持补丁版本与镜像 digest。业务队列采用 quorum queue，开发单节点与部署高可用配置分别记录。
- aio-pika 10.1.1：原生 asyncio 发布/消费客户端；进程管理、业务重试和任务恢复由本项目与部署配置负责，PostgreSQL 保存业务状态。
- 包管理使用 uv；直接依赖精确版本，传递依赖锁定；项目显式使用 PyPI，避免机器本地默认镜像造成解析漂移。解析截止设置为 `2026-10-22T00:00:00Z`，升级时显式更新截止时间并审查差异。
- pi-adapter 延后到 M7，是独立 TypeScript 包。实现时锁定所用 pi 包版本/提交及其支持的 Node 版本；当前未创建其 npm manifest，也未锁定运行时兼容性。

服务镜像不属于 Python lock 的覆盖范围。开发 Compose 已固定镜像 digest，并运行 PostgreSQL、Redis、RabbitMQ。

## 2. 必需运行依赖

| 依赖 | 已锁版本 | 用途 |
|---|---|---|
| fastapi | 0.159.1 | API、依赖注入、OpenAPI |
| uvicorn[standard] | 0.54.0 | ASGI 服务运行 |
| sqlalchemy[asyncio] | 2.0.55 | ORM、事务与异步支持；本项目明确保持 2.0 系列 |
| alembic | 1.20.0 | 数据库结构迁移 |
| asyncpg | 0.32.0 | PostgreSQL 异步驱动 |
| redis | 6.4.0 | 异步缓存客户端与限流存储；不承担任务传递 |
| pydantic | 2.14.1 | 输入输出 schema 与运行时校验 |
| pydantic-settings | 2.16.1 | 类型化配置 |
| pwdlib[argon2] | 0.3.1 | 密码哈希与验证 |
| httpx | 0.28.1 | 支付/物流等 HTTP 适配器 |
| aio-pika | 10.1.1 | RabbitMQ 异步客户端、发布确认、手动消费确认与连接恢复 |
| structlog | 26.1.0 | 结构化 JSON 日志 |
| limits | 5.8.0 | 登录、授权与写操作等限流基础 |
| prometheus-client | 0.26.0 | 业务/运行指标输出 |
| email-validator | 2.3.0 | 邮箱字段校验 |

SQLAlchemy 保持 2.0 兼容性基线。aio-pika 10.1.1 的 PyPI 元数据要求 Python >=3.11,<4，覆盖本项目 Python 3.13；aiormq、pamqp、yarl 等传递依赖由 uv.lock 固定。元数据匹配不代替 RabbitMQ 运行兼容性验证。

未为 HTTP 重试引入统一重试装饰器：资金调用是否可以重试取决于业务状态与提供方语义。消息重试由 PostgreSQL 的 next_attempt_at 与扫描器驱动，不依赖另一套任务框架或延迟消息插件。

## 3. 可选部署能力

| 依赖组 | 已锁依赖 | 启用条件 |
|---|---|---|
| observability | opentelemetry-api / sdk / exporter-otlp-proto-http 均为 1.45.1 | 导出 trace 到 Collector；基础日志和指标不依赖它 |
| storage | boto3 1.43.117、pillow 13.0.0 | S3 兼容媒体存储与图像校验；CPU/同步操作不在 API 事件循环直接执行 |
| mail | aiosmtplib 5.1.3 | 验证邮件、密码重置和订单通知；开发接本地邮件捕获器 |

不默认引入 OpenTelemetry 的全部自动 instrumentation 包；先对请求、业务操作和外部调用做明确的埋点，避免无界采集请求体和凭证。

## 4. 开发依赖

| 依赖 | 已锁版本 | 计划用途 |
|---|---|---|
| ruff | 0.17.1 | 格式和 lint |
| mypy | 1.20.2 | 类型检查，启用 Pydantic 插件 |
| pytest | 9.1.1 | 未来业务与集成验证 |
| pytest-asyncio | 1.4.0 | 异步验证 |
| pytest-cov | 7.1.0 | 覆盖率记录，不代替场景正确性 |
| respx | 0.23.1 | 未来 HTTP 客户端错误路径验证 |
| pip-audit | 2.10.1 | 依赖漏洞审计 |

已运行静态检查和依赖审计；未创建自动化测试文件或执行自动化测试套件。开发依赖不进入生产镜像。

## 5. 明确暂不引入

- JWT / python-jose / PyJWT：服务端 opaque token 足够支撑撤销与权限，本项目不混用两套会话体系。
- LangChain / LangGraph / 模型 SDK：模型执行属于 pi，Python 后端不实现第二套 Agent。
- Elasticsearch / pgvector：先以关系筛选与关键词查询满足目录规模，再依据查询质量与数据量扩展。
- Kafka / Kubernetes：当前模块化单体使用 RabbitMQ + aio-pika 和明确的部署进程，按后续需求评估流式日志或编排平台。
- 额外任务框架 / MQTT 客户端：当前明确使用 RabbitMQ AMQP 与自有异步 worker，不混用两套任务消费/重试体系。
- APScheduler：已有数据库到期扫描，不再配置第二套定时调度源。
- 通用 Repository 基类、依赖注入框架、复杂工作流框架：先使用 FastAPI DI、显式 service 和事务。

## 6. 验证边界与后续命令

当前已完成冻结安装、静态检查、迁移与本机交易链路联调。Pillow 从 12.3.0 升级到 13.0.0，修复依赖审计报告的问题；依据 [官方发布说明](https://pillow.readthedocs.io/en/stable/releasenotes/13.0.0.html)，上传解码仅启用 PNG/JPEG/WebP。

采用源码部署，保留 `tool.uv.package = false`；本机设置 `PYTHONPATH=src`，容器已设置该路径。

```text
uv sync --frozen                         # 本地开发
uv sync --frozen --no-dev                # 核心运行环境
uv sync --frozen --no-dev --all-extras    # 完整部署能力
uv lock --check --default-index https://pypi.org/simple
                                        # 验证锁文件与清单一致
```

已执行 core + storage + mail 的冻结安装；observability 仅锁定依赖，尚未接入完整 trace。安装与单机联调不代表所有平台、故障恢复和生产容量均已验收。

## 7. 官方依据

- [uv 依赖与可选组](https://docs.astral.sh/uv/concepts/projects/dependencies/)
- [SQLAlchemy 异步 session 与事件循环限制](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)
- [aio-pika 10.1.1 与 Python 要求](https://pypi.org/project/aio-pika/10.1.1/)
- [aio-pika 消费、连接与资源生命周期](https://docs.aio-pika.com/quick-start.html)
- [RabbitMQ 发布确认与消费确认](https://www.rabbitmq.com/docs/confirms)
- [RabbitMQ quorum queue 与死信](https://www.rabbitmq.com/docs/quorum-queues)
- [RabbitMQ 版本与支持周期](https://www.rabbitmq.com/release-information)
- [FastAPI 的 pwdlib/Argon2 示例](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)

## 8. 容量相关的部署工具

1 万在线用户目标通过访问模式、资源预算与压测验证。RabbitMQ 服务端和以下部署/验收工具不写入 `pyproject.toml`；Python 侧只锁定客户端及应用依赖：

| 工具 | 计划用途 | 引入条件 |
|---|---|---|
| Nginx 或部署平台负载均衡器 | TLS、路由、keep-alive、请求上限和入口限流 | 多实例部署基线，实施时选定一个 |
| RabbitMQ | durable exchange/quorum queue、确认投递、隔离队列与流量控制 | M1 纳入开发部署，M3 实现可靠消费；镜像和策略独立版本化 |
| Prometheus + Grafana | API/DB/Redis/RabbitMQ/worker 指标、图表和告警 | M1 开始，M6 完整验收；所需 exporter/插件同期固定版本 |
| k6 | 用户路径、并发在线用户与固定到达率压测 | 脚本实施时固定版本；独立压测端 |
| PgBouncer | 多副本下的数据库连接管理 | 连接成为实测瓶颈，且 asyncpg/prepared statement/事务池兼容性通过后 |
| 对象存储 / CDN | 图片与导出文件分发 | 媒体部署需要；不让 API 承担大文件流量 |

当前已提供本地 Compose；网关、完整监控和压测工具尚未部署。镜像与工具版本在实施时精确固定，负载、池预算、指标与兼容性要求见 [capacity-plan.md](capacity-plan.md)。性能依赖正确的访问模式与资源配置，锁文件解析通过不构成容量证据。
