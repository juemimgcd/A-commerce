# GitHub、版本发布与服务器部署

仓库：[juemimgcd/A-commerce](https://github.com/juemimgcd/A-commerce)。应用按源码和 Docker 镜像分发，不发布 PyPI wheel。`.env`、`var/`、账号、数据库、导出文件和备份不进入 Git 或镜像。

## 发布过程

1. 分支 push、PR 和手动触发运行 `backend`：冻结依赖、Ruff lint/format、严格 mypy、compileall、真实空 PostgreSQL 迁移、模型一致性、依赖漏洞审计。
2. 隔离业务回归与全容器验收（含 Sentinel 自动切主）通过后，验证 Compose 并构建后端及 Next 前端的 `linux/amd64`、`linux/arm64` 镜像；前端镜像构建包含格式、lint、类型检查和 `/shop` 构建。容量测试仍单独验收。
3. main 上 `pyproject.toml` 出现尚未发布的 `X.Y.Z` 版本时，推送 `ghcr.io/juemimgcd/a-commerce:vX.Y.Z` 和 `sha-<完整提交号>`，随后自动创建 Git tag 和 GitHub Release。`0.x.y` 标记为 prerelease。
4. Release 附带源码包、部署包和 `SHA256SUMS`；部署包的 `image.env` 固定镜像 SHA-256 摘要。镜像同时生成 SBOM 和 provenance 元数据。
5. 每次 main CI 成功都会发布前后端 `sha-<提交号>` 镜像和固定摘要的部署 Artifact，不要求先提升版本号。`deploy-cloud` 在 `backend` 成功后触发，只有 `DEPLOY_ENABLED=true` 才部署；版本化 Release 仍按上述规则独立保留。

Git tag、镜像和 Release 在同一流水线中完成，不依赖机器人创建 tag 后再次触发工作流。[GitHub 官方说明](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)指出，普通 `GITHUB_TOKEN` 产生的 push 事件不会再触发另一次工作流。

相同版本的后续提交仍检查和构建，但不覆盖已经发布的版本；低于已有 tag 的版本会拒绝发布。初次创建版本由 `pyproject.toml` 的 `0.1.0` 决定。版本号由维护者决定，tag 和发包由流水线生成。

## 发布下一版

修复用 patch、新功能用 minor、不兼容变化用 major。在开发分支执行：

```sh
uv version --bump patch
sh scripts/check.sh
git add pyproject.toml uv.lock
git commit -m "chore(release): prepare next patch version"
git push
```

审核后合入 main；不必手动 `git tag`。`uv version` 同步更新项目元数据和锁文件，API 文档版本也读取同一份元数据。若版本发布途中失败，在 Actions 重跑失败任务；已发布的 Release 不会被重跑覆盖。若发布后发现问题，使用新版本修复，不移动旧 tag。

查看镜像与下载部署包：

```sh
docker pull ghcr.io/juemimgcd/a-commerce:v0.1.0
gh release download v0.1.0 --repo juemimgcd/A-commerce --dir release
cd release
sha256sum -c SHA256SUMS
tar -xzf a-commerce-v0.1.0-deploy.tar.gz
```

当前 [a-commerce 镜像包](https://github.com/juemimgcd/A-commerce/pkgs/container/a-commerce) 已确认是 Public。复用到其他仓库时仍应单独检查镜像可见性；私有镜像需在服务器使用 `read:packages` 凭据执行 `docker login ghcr.io`。Actions 发包使用内置 `GITHUB_TOKEN`，无需把个人 token 存入仓库。[官方包发布说明](https://docs.github.com/en/packages/managing-github-packages-using-github-actions-workflows/publishing-and-installing-a-package-with-github-actions)。

## 云服务器 CD

商城入口：`https://www.mneme.com.cn/shop`。SSH 管理别名为 `tencent`；作品集继续使用域名根路径，商城 Nginx 配置只接管 `/shop` 和 `/shop/`。复用现有证书，不创建新 DNS 记录。

服务器运行 Ubuntu、Docker Engine / Compose、Nginx。专用部署账号为 `commerce-deploy`，目录 `/srv/a-commerce`，Compose 项目名 `a-commerce-staging`：

- `shared/.env`：0600 的运行配置，独立生成随机凭据，不复制本机 `.env`。
- `releases/<commit>-<deploy-run>-<attempt>/`：不可变镜像清单、部署脚本和制品。
- `current`：最近一次通过本机健康检查的版本目录。
- `backups/`：每次迁移前的 PostgreSQL、模拟提供方及媒体卷备份。

GitHub `staging` Environment 的配置：

| 类型 | 名称 | 含义 |
|---|---|---|
| Variable | `DEPLOY_HOST` | 云服务器 IPv4 |
| Variable | `DEPLOY_USER` | `commerce-deploy` |
| Variable | `DEPLOY_PORT` | `22` |
| Variable | `DEPLOY_ROOT` | `/srv/a-commerce` |
| Variable | `DEPLOY_URL` | `https://www.mneme.com.cn/shop` |
| Secret | `DEPLOY_SSH_KEY` | 专用部署私钥，不使用本机 root 私钥 |
| Secret | `DEPLOY_KNOWN_HOSTS` | 经既有可信 SSH 连接核对的主机公钥 |

仓库级 `DEPLOY_ENABLED=true` 开启自动部署。部署作业先通过 GitHub API 验证来源确为本仓库 main 上成功的 `backend` 工作流（push 或手动运行），再下载 `deployment-<run_id>-<attempt>`、校验 SHA256SUMS 和提交号。PR/fork 的制品不能进入这个有部署凭据的流程。GitHub Artifact 默认保留 30 天，服务器上的旧版本和备份不自动删除。

Actions 使用当次短期 `GITHUB_TOKEN` 认证 GHCR，允许拉取新建的私有镜像包；服务器临时 Docker 登录目录在部署退出时删除，不保存个人 GitHub token。镜像来源和 SHA-256 摘要在部署入口验证。

手动重部署：在 Actions 的 `deploy-cloud` 工作流选择 main，填写某次成功的 main `backend` run ID。也可执行：

```sh
gh workflow run deploy.yml --ref main -f run_id=<successful-backend-run-id>
```

### `/shop` 路径与宿主机 Nginx

前端的 `NEXT_PUBLIC_COMMERCE_BASE_PATH=/shop` 在构建时固化，服务器配置 `COMMERCE_WEB_BASE_PATH=/shop`。`COMMERCE_WEB_ORIGIN` 保持纯 origin `https://www.mneme.com.cn`；Cookie 限定在 `/shop`，密码重置邮件、图片、表单和下载地址都包含路径前缀。`next/link` 和 router 自行处理前缀，不重复拼接。

把 `deploy/nginx-proxy.conf` 安装为 `/etc/nginx/snippets/a-commerce-proxy.conf`，把 `deploy/nginx-shop.conf` 安装为 `/etc/nginx/snippets/a-commerce-shop.conf`，在现有域名的 HTTPS server 中加入后者的 include。改动前备份现有配置，`nginx -t` 通过后 reload。不要替换原有作品集的 root、location 或统计/访客便笺接口。

宿主机只代理到 `127.0.0.1:58080`，并覆盖 `X-Commerce-Client-IP`。服务器 `.env` 设置 `COMMERCE_EDGE_PROXY_IP=172.29.83.1`，对应现有 edge Docker 网桥的宿主机地址。HAProxy 仅信任这个来源携带的客户端 IP；直接访问本机开发端口时默认仍使用连接源地址。更改 Docker edge 网段时必须一起更新这个可信地址。

云服务器约 4 GB 内存，两组 Redis 的各节点 `maxmemory` 设为 64mb。多个容器在同一主机上运行，不能视为跨主机高可用。当前支付仍为项目模拟提供方；上线网页不代表已接通真实支付通道。

## 部署行为与失败处理

`deploy/deploy.sh` 拉取固定摘要的镜像，默认叠加 `compose.redis-sentinel.yaml`，等待 PostgreSQL、两组各一主两从 Redis、三个 Sentinel、RabbitMQ；通过发现/认证/quorum/复制门禁，设置队列策略，然后停止应用写入方。停写期间依次保存 PG dump、provider 卷和媒体卷，执行数据库升级，再启动应用与网关并等待健康检查。成功后更新 `current` 符号链接，旧版本与 `backups/` 保留。已有单节点环境的首次迁移与凭证补齐按 [Redis Sentinel 手册](redis-sentinel.md) 执行，不能依赖 `--no-recreate` 自动转换。

部署同时停止和更新 Next 前端；API 与前端健康检查通过后才更新 current，工作流还从外网检查 HTTPS 页面和 `/shop/api/health`。这是有维护窗口的单机部署。已有 PostgreSQL/Redis/RabbitMQ 容器不自动重建，基础设施版本升级需单独安排。就绪检查确认 API 数据库版本；Compose 同时确认其他进程运行，不等于支付、队列、全部 worker 和业务链路验收。首次启动的备份是空环境快照。备份或迁移失败后任务终止并保留现场，可能需要人工恢复停掉的服务；不要盲目重新启动旧代码。

迁移成功后不能仅切换旧镜像来保证回滚。应先判断旧程序与新 schema 是否兼容；不兼容时，按 [运维手册](backend-operations.md) 在隔离库演练恢复，核实备份和资金状态后再决定恢复方式。自动部署不执行数据库 downgrade，不删除数据卷，也不宣称零停机或高可用。

API 只在容器网络可达，网关/provider 和基础设施端口绑定 loopback。对外访问需配置 TLS、域名和可信代理；公开网关屏蔽 `/metrics`，模拟提供方不应公开。无需迁移的业务 API 可单独滚动替换，见 [多实例部署与分层限流](traffic-deployment.md)；完整 CD 仍保留维护窗口。真实支付、容量和生产部署需独立验收。
