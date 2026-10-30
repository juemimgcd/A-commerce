# 客服商品入口与工具轨迹修复

日期：2026-10-30。PR 基于主分支 `57b32ba`，在已合入的原生 pi harness 上适配本轮修复。原开发目录基于更早的 storefront 分支，其中的读写分离、容量和其他未提交改动未纳入本 PR。

## 行为

商品入口由本轮成功查询结果与最终选择共同确定。模型通过带参数校验的 `commerce_respond` 提交纯文本和最多 20 个商品 ID；Python 校验每个 ID 来自本轮成功查询，使用后端生成的标题和 URL，去掉未选商品。查询阶段保留整轮候选，跨页和多品牌比较不会因后续查询丢失入口；收窄筛选不再残留无关候选。

使用原生工具 terminate 结果结束回复，不覆盖 SDK 的 finishTurn。回复工具沿用原有鉴权、工具/轮数/token 预算、取消和总超时；模型直接输出文字时，预算允许下最多补一次只开放回复工具的格式提交。持久化会话、分支恢复和压缩保留。已有已确认检查点可继续接续，但缺少 `product_ids` 的旧结果不能作为新桥接协议回复重放。API 与适配器需要配套更新，无新增数据库迁移或前端接口变更。

业务工具轨迹在成功和失败路径均记录工具名、HTTP 状态、固定分类和耗时，无响应时状态为 null。分类区分网络错误、超时、取消、HTTP 错误、JSON 解析失败、结构/链接校验失败、结果过大和重复调用限制。结构化运行日志不记录参数、原始异常、用户原文或回复；原生事件仍提供执行前被拒绝的工具以及本地指引/回复工具的状态。日志中的 completed 只代表适配器产出并缓存结果，后端是否提交需查询运行状态。

提示约束同时明确标题子串搜索与查空复查方式、当前时间与北京时间展示、付款不等于可退货、退款处理中与到账的区别，以及记忆无需同时开启共享或营销。

## 验证

环境为 macOS / Apple Silicon、Node v26.7.0、pi-coding-agent 1.4.0 和本机已有配置的 `deepseek/deepseek-v4-pro`。使用独立 `commerce_support` 数据库、Redis 和 12 件演示商品；本次将该独立库从 `0006_support` 升级到已合入的 `0007_harness`，未访问生产数据。

| 场景 | 实际结果 |
|---|---|
| 200 元内 AHC、欧莱雅各选一件 | completed；保留 AHC 96.90 元与欧莱雅 60.72 元两个入口 |
| 200 元宽筛选收窄到 100 元内 AHC | completed；只保留 AHC 入口 |
| 第一页、第二页各选一件 | completed；两页选中商品入口均保留 |
| 1 元内 AHC，禁止放宽 | completed；无商品入口 |
| 重启适配器后接续同一会话 | 重新查询此前两个品牌并保留入口；会话 ID 复核见本地记录 |
| 实际 BackendClient 回连未监听端口 | status=null，outcome=network_error |
| 实际 BackendClient 使用无效运行凭证 | 后端返回 401，outcome=http_error |

原始记录位于本工作树忽略目录 `var/support-pr/`：`model-runs.jsonl`、`adapter.log`、`network.log`。这些文件含本地业务对话或运行信息，不随 PR 提交；账号与凭证单独保存在本地私有文件。

检查结果：

- `npm ci --ignore-scripts`、适配器 `npm run check` 通过，未修改依赖或锁文件。
- 全后端 Ruff、格式检查、mypy（91 个源文件）、compileall 和 `git diff --check` 通过。
- 独立库 `alembic upgrade head` 和 `alembic check` 通过。
- `uv run --no-sync python scripts/test_backend.py -q tests/test_transactions.py tests/test_identity_media.py tests/test_backup.py`：18 passed；一次性 PostgreSQL、Redis、RabbitMQ 回归环境已清理。

没有新增或修改测试文件、没有修改前端界面。超时、取消、畸形 JSON 与错误响应结构分支本轮只静态核对，未人为构造服务响应；没有重做原生压缩、生产部署或容量验收。此前旧分支的模型记录不作为当前主分支适配后的通过证据，指定场景通过也不代表全量模型成功率。
