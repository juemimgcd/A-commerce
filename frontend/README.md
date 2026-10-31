# A-commerce Web

中文数码配件商城与商家工作台。React 19、TypeScript 5.9、Next.js 16 App Router；交易规则由现有 FastAPI 服务执行。实际实现、联调证据和发布边界见 [前端实施记录](../docs/frontend-implementation.md)。

## 本机开发

需要 Node.js 24 或更新的受支持版本、npm，以及已经运行的 A-commerce 后端。后端的 PostgreSQL、两组 Redis、RabbitMQ、模拟提供方、relay 和 worker 启动方法见 [后端实施记录](../docs/backend-implementation.md#2-本机启动)。宿主机联调时基础服务还需包含 `redis-rate`。

```sh
cd /Users/jquery/python_files/A-commerce/frontend
npm ci --ignore-scripts
npm run dev
```

默认商城为 <http://127.0.0.1:3000>，后台为 <http://127.0.0.1:3000/admin>，后端为 <http://127.0.0.1:58000>。首次配置可将 `.env.example` 复制为 `.env.local`；已有配置时直接编辑，不要覆盖。

已有本机联调账号位于仓库忽略文件 `var/local-accounts.json`。不要把密码、Token 或该文件写入前端代码、公开文档或 `NEXT_PUBLIC_*` 环境变量。也可在商城注册自己的本机买家账号。

| 配置                     | 默认值 / 用途                                                                    |
| ------------------------ | -------------------------------------------------------------------------------- |
| `COMMERCE_API_ORIGIN`    | `http://127.0.0.1:58000`，仅服务端访问的 API 根地址                              |
| `COMMERCE_WEB_ORIGIN`    | `http://127.0.0.1:3000`，用于 canonical 和写请求来源核对；须与实际浏览器地址一致 |
| `COMMERCE_TRUST_GATEWAY` | 默认 `false`；仅在下述私有网关部署中启用                                         |
| `COMMERCE_INDEXABLE`     | 默认 `false`；正式商家资料与真实环境完成后才开放搜索索引                         |

`localhost` 与 `127.0.0.1` 是不同来源。若使用 `http://localhost:3000`，需同步修改 `COMMERCE_WEB_ORIGIN` 并重新启动。

## 检查与构建

```sh
npm run check
npm run build
npm run start
```

`check` 包含格式、零警告 lint 和严格 TypeScript 检查。`format` 统一源码格式。工程没有新增自动化测试或测试脚手架；现有后端测试通过仓库已有的隔离运行器执行。

后端契约变化时，从仓库根目录重新导出，再生成前端类型：

```sh
PYTHONPATH=src uv run --no-sync python scripts/export_openapi.py
cd frontend
npm run generate:api
npm run check
```

不要手改 `src/lib/api.generated.ts`。核心报价与订单响应另有运行时校验。

## 容器部署

`Dockerfile` 构建 standalone Node 服务，并以非 root 用户运行。`deploy/compose.web.yaml` 在原有后端 Compose 之上加入 Web 服务和网页入口；Next 服务不映射公共端口。网关写入客户端 IP，Web 转发后由网关校验私有来源，再交给 FastAPI 的限流依赖。

以下命令用于新的独立部署环境。在项目根目录执行：

```sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.web.yaml build api web
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.web.yaml up -d --wait db redis redis-rate rabbitmq
sh deploy/rabbitmq-policy.sh
docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.web.yaml up -d --wait
```

网页入口默认 <http://127.0.0.1:58080>。可在根 `.env` 中配置 `COMMERCE_WEB_PORT`、`COMMERCE_WEB_ORIGIN`、`COMMERCE_WEB_IP`；默认 Web 固定 IP 为 `172.29.83.3`，需与网关和边缘子网一致。TLS 在外层入口终止，并将 `COMMERCE_WEB_ORIGIN` 设置为真实 HTTPS 来源，以启用 Secure Cookie。

如果复用已有宿主机联调数据库，先按后端文档迁移同一份模拟提供方 SQLite 与媒体文件，并停止旧进程、处理端口占用，再切换到完整容器运行。旧订单不能接入一个空的提供方库。本次完成了镜像构建、网关配置和真实转发读取验证，未以该结果替代完整容器交易或生产容量验收。

## 商家资料

API 服务通过 `COMMERCE_SHOP_CONFIG` 读取 JSON 商家资料。字段包括 `name`、`support_email`、`support_phone`、`privacy_text`、`terms_text` 与 `return_address`。退货地址包含 `recipient`、`phone`、`region`、`detail`。

未配置正式政策时，页面明确显示尚未提供。未配置退货地址时，买家不能在界面中填写寄回单号。邮件未启用时，找回密码的邮件发送入口不可提交。当前支付能力为模拟支付，页面持续标注演示环境。

## 目录

| 路径                        | 职责                                                 |
| --------------------------- | ---------------------------------------------------- |
| `src/app/[[...route]]`      | 页面分发、公开商品服务端内容、元信息                 |
| `src/app/api/commerce`      | 方法与路径白名单、Cookie 会话、来源校验、受控转发    |
| `src/proxy.ts`              | 流式页面输出前识别未知路径，返回真实 404 状态        |
| `src/components`            | 查询与身份上下文、导航、表单、弹窗和异步状态         |
| `src/features/catalog.tsx`  | 首页、目录、商品详情与规格选择                       |
| `src/features/commerce.tsx` | 地址、购物车、报价、订单、履约与售后                 |
| `src/features/account.tsx`  | 收藏、比较、通知、会话与帮助政策                     |
| `src/features/admin-*.tsx`  | 商品库存、报表、对账、审计、配送、员工               |
| `src/lib`                   | 生成契约、关键响应校验、金额与时间格式、未决请求标识 |

普通购买独立运行；pi 助手仍按 M7 单独实施。

密码重置邮件使用后端及邮件 worker 的 `COMMERCE_WEB_ORIGIN` 生成网页链接，
部署时应将其设为用户实际访问的 HTTP(S) 来源（不包含路径）。
