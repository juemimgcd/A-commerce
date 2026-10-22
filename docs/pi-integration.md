# pi 接入、授权与 API 契约

状态：**延后到 M7 的参考契约，尚未实现，不作为后端 v1.0 发布前置条件。** 先完成后端业务、故障恢复和 1 万在线用户验收，再实现此文。前缀统一 `/api/v1`，Python 后端不实现模型循环。

普通买家确认报价后直接调用 customer/orders，不需要 agent_grant/action 或确认 CLI。M7 为 Agent 增加独立授权批准入口，并复用后端已有交易 service；本文中的 grant/action 接口与表均为未来增量。

## 1. 两边分别维护什么

| pi / TypeScript 适配器 | FastAPI 后端 |
|---|---|
| 自然语言需求、比较、追问、工具调用 | 商品、报价、库存、订单、支付、售后事实 |
| 会话、上下文、提示词、工具 schema | 用户/管理员/Agent grant、动作批准、业务状态 |
| 调用映射、稳定幂等键、事件消费游标 | 数据库事务、幂等结果、outbox、对账、审计 |
| 解释业务错误、查已有结果再决定下一步 | 校验价格/权限/状态、拒绝不合法操作 |

默认一个买家身份的购物会话。pi 不连接 PostgreSQL/Redis，不读取商家管理凭证，不自行标记付款或退款成功。

## 2. SDK 依据与适配位置

接入代码计划位于 `A-commerce/pi-adapter`，独立 TypeScript 包。之前已读本机：

- `/Users/jquery/python_files/pi/packages/coding-agent/examples/sdk/README.md`
- `/Users/jquery/python_files/pi/packages/coding-agent/examples/sdk/05-tools.ts`
- `/Users/jquery/python_files/pi/packages/coding-agent/examples/sdk/12-full-control.ts`

这些示例展示 `createAgentSession()`、工具名称 allowlist、自定义工具、资源加载和 session 事件。尚未运行 SDK；实现时核对真实导出、Node 兼容范围，固定依赖版本或 pi commit，并记录对 pi 的自主改动。

适配器分为 config、http-client、schemas、tools、invocation-store、events 和 session 接入。HTTP 使用运行时 fetch 与 AbortSignal；响应必须解析并校验，不把网络 200 等同于业务成功。TS runtime schema 使用 Zod，版本在适配器实施时确定；不把 Python schema 类型当成 TS 运行时校验。

commerce 会话只启用明确工具，关闭通用 shell、文件修改和自动发现的无关扩展。工具限制不替代 OS/容器隔离，human/admin 凭证必须在独立进程与密钥范围内。

## 3. 会话、grant 和用户确认

用户通过正常登录获得 human 会话；用户创建 `agent_grant`，选择 scopes、有效期和可选单笔订单总额上限，并取得单独的 Agent token。token 只进入适配器凭证存储/HTTP header，不进入模型消息、工具参数或日志。

后端从 token 解析用户，不接受工具自报 user_id。grant 不能包含管理员、动作批准、模拟付款或 provider 回调能力。用户可以查看和撤销 grant。

确认流程：

1. pi 创建报价，再创建 action；后端保存动作类型、商品/数量/价格/地址/政策快照和参数摘要。
2. 用户通过独立可信 CLI（后续可换 Web 页）从后端读取完整摘要，批准或拒绝。不是仅相信 Agent 自行生成的文本摘要。
3. 决策绑定 action 版本、用户、有效期；参数不可编辑，变更需要新 action。
4. pi 只查询批准状态并执行；执行时仍检查账号/grant、库存、价格和状态。
5. 创建订单返回 pending_payment；用户在可信支付入口完成支付。Agent 的操作授权不包含银行账户/支付密码授权。

无用户确认时，后端返回 CONFIRMATION_REQUIRED。用户在聊天中说“同意”只能提示用户走可信入口，不能由模型伪造 human 决策请求。以后若把确认按钮集成进聊天客户端，按钮事件仍由持有 human 会话的客户端发送。

## 4. 工具与接口

以下省略 `/api/v1`。customer 与 agent 路由调用相同业务 service，避免独立维护两套订单逻辑。

| 工具 | HTTP 接口 | 能力 |
|---|---|---|
| commerce_search_products | GET /customer/products | 条件、规格、价格筛选，受限分页 |
| commerce_get_product | GET /customer/products/{id} | 商品属性、SKU 与可展示库存 |
| commerce_list_addresses | GET /customer/addresses | 用户自己的地址 ID 与摘要 |
| commerce_get_cart | GET /customer/cart | 用户自己的购物车 |
| commerce_update_cart | PUT /customer/cart/items/{sku_id} | 设置数量；有独立 cart:write scope，不提交交易 |
| commerce_create_quote | POST /customer/quotes | 后端核算价格、运费、有效期 |
| commerce_prepare_action | POST /agent/actions | 下单、取消、退货等待确认动作 |
| commerce_get_action | GET /agent/actions/{id} | 批准状态、错误与结果引用 |
| commerce_execute_action | POST /agent/actions/{id}/execute | 执行批准动作，幂等返回结果 |
| commerce_list_orders | GET /customer/orders | 当前用户订单 |
| commerce_get_order | GET /customer/orders/{id} | 订单、支付、履约、退款摘要 |
| commerce_get_return_options | GET /customer/orders/{id}/return-options | 可退行/数量、政策、费用估计 |
| commerce_get_return | GET /customer/returns/{id} | 退货处理与退款状态 |
| commerce_list_events | GET /agent/events?after=... | 用户授权范围内的业务事件 |

action 参数是明确的判别联合：

- place_order：quote_id；结果是订单 ID 与待支付状态。
- cancel_order：order_id、expected_version、reason。
- request_return：order_id、expected_version、items[{order_item_id, quantity}]、reason。

退款金额由后端计算，不接受模型随意填写。工具不提供通用 URL 请求、任意 SQL、任意表字段修改或“将订单设为 paid”。

只供可信入口/管理使用：

- `/customer/auth/*`、`/customer/sessions/*`：登录、退出与会话管理。
- `/customer/agent-grants/*`：创建/撤销 grant，必须 human 会话。
- `/customer/actions/{id}/decision`：M7 批准/拒绝 Agent 动作，仅 human 会话可调用。
- `/customer/orders`：普通买家确认报价后直接下单，仅 human 会话可写；Agent 下单必须经过 execute_action，不能绕过批准。
- `/customer/orders/{id}/payment-session`：可信支付流程；创建支付入口本身不表示已扣款。
- `/admin/*`：商品、库存、发货、退货验收、对账与角色管理。
- `/callbacks/payments/{provider}`、`/callbacks/shipping/{provider}`：按提供方鉴权和验签，不使用 Agent token。

## 5. 幂等与崩溃恢复

适配器在发起写请求前，把 invocation_id、action_id、幂等键、请求摘要与状态保存到本地调用记录。持久化方式在适配器实现时选用单进程 SQLite 或原子文件，不能只放内存。

execute_action 的 key 由稳定 action_id 派生。后端额外约束同一 action 只有一个交易结果、同一报价只产生一个订单；有意再次购买需新报价和确认，不能换工具调用 ID 绕过约束。

服务端幂等记录按用户/动作类型分区，同键异参返回冲突。订单写入、action 结果、幂等结果、outbox 在同一事务中提交。模型重新开始一轮也先查询尚未完成的动作，不直接重新买一次。

网络超时后的处理顺序：查询 action → 查询已有订单/operation → 在确认未执行且仍合法时用原 key 重试。UNKNOWN 状态不能转换成“失败，请重试购买”。调用 AbortSignal 只终止本地等待，不意味着后端已回滚。

事件采用至少一次交付，按 event_id 去重。保存用户范围游标并在本地消费成功后推进；游标过期返回明确错误，拉取订单状态快照并建立新游标。不能把 Redis pub/sub 当作持久化事件流。

自动跟进：用户显式启用的订单订阅 + 唯一 trigger_event_id + 有界模型预算；适配器登记处理状态后再唤醒会话。失败或重启可查询/重试，不声称模型调用恰好一次。任何新的交易变更仍需确认。

## 6. 错误与可解释结果

```json
{
  "error": {
    "code": "QUOTE_STALE",
    "message": "报价中的商品价格已变化，请重新报价并确认。",
    "retryable": false,
    "details": { "quote_id": "q_example" }
  },
  "request_id": "req_example"
}
```

主要错误：QUOTE_EXPIRED、QUOTE_STALE、OUT_OF_STOCK、ORDER_STATE_CONFLICT、CONFIRMATION_REQUIRED、ACTION_EXPIRED、GRANT_REVOKED、IDEMPOTENCY_CONFLICT、RETURN_WINDOW_EXPIRED、PAYMENT_RESULT_UNKNOWN。

业务结果分别描述 order_created、pending_payment、paid、refund_pending、refunded；工具返回明确 next_action/可执行操作，帮助模型追问或等待。retryable 不绕过结果核对与额度限制。

API 响应携带 request_id；审计记录 action_id、order_id、operation_id、pi_session_id、tool_call_id。调用方提供的关联 ID 不参与授权。日志不存 token、完整地址、支付数据或隐藏推理。

## 7. 验收与贡献边界

复用 pi 的模型访问、会话/上下文和工具执行能力。自己实现业务 HTTP 工具、授权确认、事件恢复、错误处理与验证场景；Python 实现业务规则、数据一致性、异步集成和运营能力。

分别记录：API/真实 PostgreSQL、模拟外部服务、真实沙箱、pi/真实模型端到端、部署环境。任何一项的通过不能代替另一项。
