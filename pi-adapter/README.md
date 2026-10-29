# 商城客服适配器

使用固定版本的 pi SDK 运行中文客服。浏览器通过 FastAPI 保存咨询请求，再轮询回复；适配器仅在内存中运行模型会话。会话归属、最近六轮历史、请求编号、截止时间、用户记忆和共享设置由业务数据库维护。

## 启动

需要 Node.js 22.19.0 或更新版本，以及已配置的 Python 后端、PostgreSQL 和 Redis。先按项目现有开发说明配置业务服务。

1. 在仓库根目录升级数据库，再启动 API：

   ```sh
   export PYTHONPATH=src
   uv run --no-sync alembic upgrade head
   # 在 API 与适配器的进程环境中分别设置同一个随机密钥，至少 32 个字符。
   export COMMERCE_SUPPORT_BRIDGE_SECRET='replace_with_the_same_random_secret_in_both_processes'
   uv run --no-sync uvicorn a_commerce.main:create_app --factory --host 127.0.0.1 --port 58000
   ```

2. 在另一个终端启动适配器，使用相同的桥接密钥：

   ```sh
   cd pi-adapter
   npm ci --ignore-scripts
   export COMMERCE_SUPPORT_BRIDGE_SECRET='replace_with_the_same_random_secret_in_both_processes'
   export COMMERCE_API_ORIGIN=http://127.0.0.1:58000
   npm start
   ```

   默认读取 `~/.pi/agent/settings.json` 的 `defaultProvider` 和 `defaultModel`，并从同目录的 `auth.json`、`models.json` 读取模型凭证和配置。也可以设置 `COMMERCE_PI_AGENT_DIR`，或用 `COMMERCE_PI_PROVIDER`、`COMMERCE_PI_MODEL`、`COMMERCE_PI_API_KEY` 显式指定。Node 进程需要已导出的环境变量，不会自动加载仓库根目录的 `.env`。

3. 按 [前端说明](../frontend/README.md) 配置前端。买家入口为 `/support`、`/account/memory`；具有 `audit` 权限的员工可访问 `/admin/customer-insights`。

## 运行边界

- API 的 `COMMERCE_SUPPORT_ADAPTER_URL` 默认为 `http://127.0.0.1:58010`。适配器的 `COMMERCE_PI_HOST`、`COMMERCE_PI_PORT` 默认分别为 `127.0.0.1`、`58010`；`GET /health` 返回进程就绪状态。
- 未设置桥接密钥时，API 拒绝创建模型运行，返回 `SUPPORT_NOT_CONFIGURED`；商城原有交易接口继续工作。桥接密钥不进入浏览器。
- 工具仅查询当前买家的账户摘要、购物车、商品、订单、物流和售后资格，并可提出偏好候选。只注册 commerce 工具，不加载 shell、文件工具、项目指令或扩展。付款、取消订单和售后提交通过既有业务页面确认。
- 每次调用使用绑定买家登录会话的短期能力凭证，最多 12 次工具查询、8 轮模型回复；适配器最多同时处理 4 次咨询。API 默认每用户每小时 30 条消息，可通过 `COMMERCE_SUPPORT_HOURLY_MESSAGES` 调整。
- API 的模型调用等待上限由 `COMMERCE_SUPPORT_TIMEOUT_SECONDS` 设置，默认 90 秒；适配器单次等待上限为 85 秒。历史按 UTF-8 JSON 实际字节裁剪到 64 KiB，优先保留最近的完整问答。进程重启后的过期运行由查询接口标记失败，不自动重放模型请求。
- 记忆、商家共享和营销用途分别需要用户启用。模型提出的偏好须由用户确认后才用于推荐和商家画像。用户可以纠正、停用或清除记忆；共享接口不返回原始对话依据。功能使用次数单独统计。
- 适配器是可选的独立 Node 进程，当前未加入 Compose 部署，也不自动发送营销消息。

## 检查

```sh
npm run check
```

后端和前端分别使用仓库根目录的 `sh scripts/check.sh` 与 `frontend` 中的 `npm run check`。
