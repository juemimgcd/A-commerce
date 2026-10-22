# fastapi_newsApp 架构参考与取舍

检查日期：2026-10-22。参考仓库：`juemimgcd/fastapi_newsApp`，分支 `master`，提交 `ef103925b3f277a0ea612df46a8a15f03d299abe`。

这是源码和配置阅读结果，没有启动新闻项目，也不是对整个仓库的安全审计。本项目只参考组织方式与已有经验，没有复制其业务代码。

## 1. 实际读到的架构

```text
main.py
  → routers/users.py
    → Depends(get_current_user / get_database)
    → crud/users.py
      → models/users.py
      → PostgreSQL / AsyncSession
    → schemas/users.py
    → utils/response.py

routers/news.py → crud/news.py → cache/news_cache.py → Redis
                             → models/news.py → PostgreSQL
```

配置通过 Pydantic Settings 读取；数据库使用 SQLAlchemy AsyncSession + asyncpg；Alembic 使用异步 engine 适配迁移；Docker Compose 编排应用、PostgreSQL 和 Redis。

用户与管理员采用数据库持久化 Token。新闻项目的 `routers → crud → models` 很适合查询和单实体修改，但交易业务需要显式业务事务。

## 2. 沿用和调整

| 参考实现 | A-commerce 决定 | 原因 |
|---|---|---|
| routers / crud / models / schemas 分层 | 保留命名，增加 services 层 | 方便延续已有习惯，集中管理下单、取消、退款等跨表用例 |
| `crud/users.py` 多个函数各自 commit；依赖退出时也 commit | service 统一 begin/commit；CRUD 只查询、add、flush | 下单中任一步失败都不能留下部分库存或订单变更 |
| `conf/db_conf.py` 提供 AsyncSession | 保留，区分鉴权读会话与业务事务会话 | 避免鉴权查询的 autobegin 与显式业务 begin 冲突 |
| 服务端 Token，用户/管理员分别鉴权 | 保留可撤销服务端会话；数据库存摘要；M7 再增加 Agent 授权记录 | 先完成设备会话、撤销和用户/管理员权限，后续扩展 Agent |
| bcrypt 密码哈希 | 新项目使用 pwdlib + Argon2 | 无旧密码迁移负担；选择当前 FastAPI 文档推荐的实现 |
| 热点读 Redis 缓存 | 保留，移到查询 service/cache 层 | CRUD 专注数据库；商品展示允许短暂陈旧，交易校验始终读数据库 |
| `.venv/.env` | 根目录 `.env` 用于开发，部署用环境/密钥注入 | 配置与虚拟环境生命周期分开 |
| FastAPI lifespan 关闭数据库 | 扩展为管理 DB、Redis、HTTP client 的创建与关闭 | 控制连接资源、超时与退出行为 |
| Docker 启动命令每次运行迁移 | 独立的一次性 migrate job，成功后启动 API/worker | 多副本启动时不争抢迁移 |
| `requirements.txt` 固定直接依赖 | `pyproject.toml` 固定直接依赖 + `uv.lock` 固定传递依赖 | 保留版本可控性，同时可复现安装 |
| 成功统一 JSONResponse | 类型化响应 schema + 正确 HTTP 状态码 + 统一错误协议 | OpenAPI 可消费；Agent 可区分参数错误、冲突、异步受理 |

这不是要求修改新闻项目；取舍仅用于 A-commerce。

## 3. 参考依赖快照

| 包 | 新闻项目锁定版本 |
|---|---|
| fastapi | 0.115.14 |
| uvicorn[standard] | 0.35.0 |
| sqlalchemy | 2.0.48 |
| alembic | 1.18.4 |
| asyncpg | 0.31.0 |
| redis | 6.4.0 |
| pydantic | 2.12.5 |
| pydantic-settings | 2.13.1 |
| bcrypt | 5.0.0 |
| httpx | 0.28.1 |

A-commerce 的版本以自己的依赖文件为准，不把参考项目固定版本当成最新或已验证兼容版本。

## 4. 阅读范围与来源

完整阅读了 README、requirements、入口、数据库/Redis/Settings、用户 router/CRUD/model/schema、新闻 router/CRUD/cache、管理 router/CRUD、鉴权与密码工具、响应工具、模型基类、Alembic env、Dockerfile 与 Compose；通过 Git tree 确认目录。

- [固定提交的 README](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/README.md)
- [用户请求入口](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/routers/users.py)
- [CRUD 与事务提交](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/crud/users.py)
- [数据库会话](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/conf/db_conf.py)
- [服务端 Token 模型](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/models/users.py)
- [依赖文件](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/requirements.txt)
- [容器启动方式](https://github.com/juemimgcd/fastapi_newsApp/blob/ef103925b3f277a0ea612df46a8a15f03d299abe/Dockerfile)
