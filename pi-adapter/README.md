# 商城客服适配器

固定使用 pi-coding-agent@1.4.0 的原生循环、会话和压缩机制运行中文客服。浏览器通过 FastAPI 保存咨询请求，再轮询回复。业务库维护归属、咨询记录和偏好；商城专用目录保存 pi 会话检查点。

依赖审查：新增显式依赖 proper-lockfile@4.1.2 及其类型，均固定版本，安装使用 --ignore-scripts。SDK 间接依赖的 undici@8.10.2 存在已公布漏洞，适配器通过精确 override 使用 8.12.2。已核对官方 8.11.0、8.12.0、8.12.1 和 [8.12.2 发布说明](https://github.com/nodejs/undici/releases/tag/v8.12.2)：影响包括代理匹配、TLS/响应解码和重定向错误处理；SDK 的 Client/Pool/EnvHttpProxyAgent 用法保持兼容，需以真实模型 HTTP 联调验证。此 override 不修改 pi 源码，也不替代 Node 本身的安全更新。

## 五个核心模块

| 模块 | 实现 | 行为 |
|---|---|---|
| Loop | runtime.ts、model-requests.ts | createAgentSession + prompt，保留 SDK 的工具循环和 finishTurn，在每次普通及压缩请求边界执行鉴权和预算 |
| Tools | tools.ts、backend-client.ts | TypeBox 参数校验、后端权限复核、有界响应和固定错误代码 |
| Context | resources.ts、sessions.ts | 专用提示词、按需处理指引、完整 pi 会话；当前业务事实重新查询 |
| Compact | extensions.ts | 原生 compact 与压缩记录，补充中文摘要要求，保留需求、纠正、ID 和候选顺序 |
| Hooks | extensions.ts、events.ts | 运行开始、上下文、工具调用、压缩、消息完成钩子，以及脱敏事件订阅 |

commerce_get_guide 只读取代码内的选购、售后、偏好处理指引。当前政策、价格、库存与订单状态来自后端。资源加载禁用个人/项目扩展、Skills 自动发现、项目指令和文件工具。

commerce_respond 使用受校验的参数提交完整回复和最终商品 ID，并通过工具的原生 terminate 结果结束本轮。后端仅接受本轮成功查询过的商品 ID，使用后端生成的标题和入口；跨品牌和跨页候选会保留，未选商品不会成为最终入口。回复工具与指引工具一样计入原有工具预算，不覆盖 SDK 的 finishTurn。模型直接给出文本而未提交时，预算允许下最多补一次仅开放回复工具的格式提交，不重查业务数据。

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
   node --env-file=../.env src/server.ts
   ```

   默认读取 `~/.pi/agent/settings.json` 的 `defaultProvider` 和 `defaultModel`，并从同目录的 `auth.json`、`models.json` 读取模型凭证和配置。也可以设置 `COMMERCE_PI_AGENT_DIR`，或用 `COMMERCE_PI_PROVIDER`、`COMMERCE_PI_MODEL`、`COMMERCE_PI_API_KEY` 显式指定。Node 进程需要已导出的环境变量，不会自动加载仓库根目录的 `.env`。

3. 按 [前端说明](../frontend/README.md) 配置前端。买家入口为 `/support`、`/account/memory`；具有 `audit` 权限的员工可访问 `/admin/customer-insights`。

## 运行边界

- API 的 `COMMERCE_SUPPORT_ADAPTER_URL` 默认为 `http://127.0.0.1:58010`。适配器的 `COMMERCE_PI_HOST`、`COMMERCE_PI_PORT` 默认分别为 `127.0.0.1`、`58010`；`GET /health` 返回进程就绪状态。
- 未设置桥接密钥时，API 拒绝创建模型运行，返回 `SUPPORT_NOT_CONFIGURED`；商城原有交易接口继续工作。桥接密钥不进入浏览器。
- 工具仅查询当前买家的账户摘要、购物车、商品、订单、物流和售后资格，并可提出偏好候选。只注册 commerce 工具，不加载 shell、文件工具、项目指令或扩展。付款、取消订单和售后提交通过既有业务页面确认。
- 每次调用使用绑定买家登录会话的短期能力凭证，最多 12 次工具查询、8 轮模型回复；适配器最多同时处理 4 次咨询。API 默认每用户每小时 30 条消息，可通过 `COMMERCE_SUPPORT_HOURLY_MESSAGES` 调整。
- API 等待上限默认 90 秒，适配器默认 85 秒。正常会话接续保留完整工具历史与压缩记录；缺失本地检查点时只导入后端最近六轮同记忆版本问答。进程重启后的过期运行标记失败，不自动重放模型请求。
- 记忆、商家共享和营销用途分别需要用户启用。模型提出的偏好须由用户确认后才用于推荐和商家画像。用户可以纠正、停用或清除记忆；共享接口不返回原始对话依据。功能使用次数单独统计。
- 适配器是可选的独立 Node 进程，可通过 compose.support.yaml 部署；当前不自动发送营销消息。

## 配置

| 环境变量 | 默认值 | 含义 |
|---|---|---|
| COMMERCE_PI_STATE_DIR | var/commerce-pi | 专用持久化目录，相对适配器 cwd |
| COMMERCE_PI_MAX_TURNS | 8 | 本轮最大模型轮数 |
| COMMERCE_PI_MAX_TOOLS | 12 | 工具上限，后端另有 12 次限制 |
| COMMERCE_PI_MAX_OUTPUT_TOKENS | 1800 | 普通模型请求的输出上限 |
| COMMERCE_PI_TOKEN_BUDGET | 64000 | 本轮用量加下次请求估算的准入预算，含压缩用量 |
| COMMERCE_PI_CONTEXT_TOKENS | 32768 | 上下文软上限，不超过模型实际窗口 |
| COMMERCE_PI_RESERVE_TOKENS | 4096 | 上下文输出预留，同时作为每次压缩摘要的输出上限，必须覆盖普通输出上限 |
| COMMERCE_PI_KEEP_RECENT_TOKENS | 8000 | 压缩时保留的近期上下文预算 |
| COMMERCE_PI_TIMEOUT_MS | 85000 | 适配器总期限 |
| COMMERCE_PI_CONCURRENCY | 4 | 进程并发上限 |
| COMMERCE_PI_RETENTION_DAYS | 30 | 本地检查点保留期 |

压缩触发点为软上限减输出预留；近期保留量必须小于触发点。普通回复和原生 compact 内的每次摘要请求均重新鉴权、核验记忆版本、检查取消和预算；拆分轮次压缩的第一次摘要完成后立即累计用量，再决定是否允许第二次摘要。压缩未完成时，已完成摘要仍计入本轮内存用量，不重复累计最终合计。token 预算是 SDK 用量和请求估算的准入控制，不是账单精确上限。响应可能使已用量超过预算，下一次请求将被拒绝。模型只自动重试一次，提供方内部重试关闭；业务工具不在 HTTP 层自动重试。

## 会话与一致性

后端传入买家、会话、记忆版本、本轮 ID 和上一条后端确认完成的运行 ID。适配器通过本轮能力凭证向后端核验归属和记忆版本。目录按买家/会话/记忆版本隔离：sessions/ 保存原生追加式 JSONL，运行目录只保存结果索引。每轮索引记录共享日志文件名和已完成叶节点 ID，下一轮打开日志后通过原生 branch() 恢复到后端确认的叶节点；失败或未被后端接收的分支不会进入下一轮上下文。

完成后原子保存结果索引。重复桥接请求重新鉴权后返回已有结果；发现中断目录时拒绝自动重放。已有会话缺少本地检查点时，只导入已有问答，不补造工具结果和用量。每轮释放 SDK 对象，后续按需打开。升级遇到旧格式检查点时，只将该旧快照迁入共享目录一次，后续继续追加，不逐轮复制历史。运行索引过期时清理，仍被保留索引引用的日志不删除；无引用且过期的日志才清理。日志包含历史分支，随实际追加内容增长，仍需按长期会话规模配置卷容量。

偏好开关、清除、纠正与确认推进记忆版本。每次请求、工具执行和最终保存均核验版本，新版本使用新上下文；访问时删除该会话旧版本检查点。历史文件不会即时全部擦除：启动和每 12 小时清理过期运行，跳过正在执行的会话；活动会话接入时也清理过期分支。数据库聊天记录沿用原有保留规则。

会话文件含用户消息和工具结果，应按敏感业务数据保护、备份。进程 umask 为 077；消息钩子移除 assistant 的隐藏推理。模型凭证不进入消息、工具参数或运行日志，前端和商家共享接口不暴露原始 pi 文件。

## 状态、取消与中断

FastAPI BackgroundTasks 执行本轮调用，浏览器轮询。关闭浏览器不等于取消；“停止回复”先在后端撤销工具能力，再通知适配器调用原生 abort。

后端保存有界事件、工具名、调用 ID、耗时和错误标志，以及成功回复的模型、版本、token 和费用。事件每条上传最多等待 1 秒，回复收尾最多再等待 1 秒；收尾到期会取消在途上传并丢弃排队事件，释放会话和并发名额。事件上传失败不改变业务结果，取消或中断的用量可能不完整。completed 表示回复成功保存，不保证问题已解决；取消使用 failed + SUPPORT_CANCELLED。

适配器额外输出 support_run 结构化日志，记录业务工具的 HTTP 状态、执行结果和耗时。无 HTTP 响应时 status 为 null；结果区分网络错误、超时、取消、HTTP 错误、JSON 或结构校验失败、无效入口、结果过大和重复调用限制。日志不包含用户原文、工具参数、回复或原始异常。该日志的 completed 只表示适配器生成并缓存了回复，不能代替后端提交结果。指引、回复工具以及执行前被 SDK/扩展阻止的调用继续通过原生事件观察。

会话恢复不等于执行中任务自动恢复。进程崩溃后的过期运行标记 SUPPORT_INTERRUPTED，不重发模型或工具；结果未知的写入先核对后端状态。已完成本地结果可供同一有效桥接请求核对，后端已超时的运行不会自行转成成功。

## Compose 与回退

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.support.yaml up -d --build
```

配置桥接密钥和显式模型 provider/model/API key。服务只在内部网络监听，support 卷保存会话，容器使用 node 用户。GET /health 只证明初始化完成，不证明模型提供方或完整咨询可用。

一个卷只允许一个适配器拥有。proper-lockfile 使用固定 30 秒失效期与 5 秒心跳；崩溃后等待锁过期再启动，锁被破坏时进程退出。不要删除活动锁，也不要将共享卷多实例当作已支持能力。备份恢复需停服后整体复制卷。

升级顺序：备份数据库和会话卷 → 执行 0007_harness 迁移 → 配套更新 API 和适配器 → 更新前端 → 验证咨询。回退时配套恢复旧 API/适配器/前端；runtime 列可保留，不在运行中降级数据库。旧适配器不读取新检查点，保留卷供再次升级。

商品入口修复新增必填的桥接响应字段 product_ids，API 与适配器必须一起更新，无新增数据库迁移。既有已确认检查点仍可用于会话接续；缺少商品选择字段的旧缓存结果不能作为新协议回复重放，返回 SUPPORT_INTERRUPTED。

## 检查

```sh
npm run check
```

后端和前端分别使用仓库根目录的 `sh scripts/check.sh` 与 `frontend` 中的 `npm run check`。

人工验收覆盖真实模型多步查询、跨轮条件、重启接续、低阈值原生压缩、偏好失效、取消、重复请求和用户隔离；分别记录通过项与未覆盖项。静态检查不能代替真实模型行为或生产容量验收。不新增测试文件。

2026-10-30 商品入口、工具轨迹与重启接续复查见 [审核修复记录](../docs/support-review-2026-10-30.md)。

### 2026-10-29 本地验收记录

使用独立 PostgreSQL 数据库和 Redis DB，以及已配置的 DeepSeek 模型完成以下人工联调，未修改测试文件：

- 多步循环实际调用商品、分类与偏好工具，保存工具事件和用量。
- 跨轮保留预算、用途、排除条件；重启 API 与适配器后接续同一 pi 会话。
- 验证环境降低上下文阈值，实际记录两次 compaction_start/compaction_end；之后恢复默认配置。
- 清除偏好后新运行使用新记忆版本，模型明确没有旧预算/排除条件的上下文。
- 浏览器停止回复后保存 SUPPORT_CANCELLED，撤销本轮凭证。
- 重复消息请求返回原 completed 运行，其他账号读取会话返回 NOT_FOUND。
- 第二个适配器进程因存储锁被拒绝。
- 390/768/1440 像素浏览器布局检查，未出现横向溢出。
- 适配器、后端、前端检查通过；迁移执行及 alembic check 通过；Compose 配置检查、容器构建与容器内 SDK 加载通过；适配器 npm audit 为 0 个已知漏洞。

三项审查修复后的追加验收：

- 旧检查点成功迁移，后续多轮和重启继续使用同一共享日志，各轮索引保存不同的叶节点 ID。
- 低阈值下实际完成一次拆分轮次压缩和一次普通压缩，继续执行模型轮次；日志中普通回复与压缩的用量合计 15,703 token，与本轮记录一致，压缩未重复计数。之后恢复默认阈值。
- 取消一轮包含临时预算修改的请求，再重启适配器接续。取消消息仍在追加日志中，但不在新运行的确认分支上，模型保留取消前的预算与排除条件。
- 修复后的适配器类型检查、diff 空白检查和容器构建通过。事件上传的正常路径通过联调；未注入慢事件接口或穷举两次摘要请求之间撤权的时序。

未通过本轮联调建立的结论：生产容量、跨实例接管、硬崩溃的所有时序、真实提供方限流后的重试路径，以及自然语言事实准确率。会话文件保留期针对检查点，存续检查点可能包含更早的会话历史；不等同于逐条消息的自动删除期限。
