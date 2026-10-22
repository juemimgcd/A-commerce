# GitHub、版本发布与服务器部署

仓库：[juemimgcd/A-commerce](https://github.com/juemimgcd/A-commerce)。应用按源码和 Docker 镜像分发，不发布 PyPI wheel。`.env`、`var/`、账号、数据库、导出文件和备份不进入 Git 或镜像。

## 发布过程

1. 分支 push、PR 和手动触发运行 `backend`：冻结依赖、Ruff lint/format、严格 mypy、compileall、真实空 PostgreSQL 迁移、模型一致性、依赖漏洞审计。
2. 检查成功后验证 Compose 配置并构建 `linux/amd64` 和 `linux/arm64` 镜像。当前没有自动业务测试或容量测试。
3. main 上 `pyproject.toml` 出现尚未发布的 `X.Y.Z` 版本时，推送 `ghcr.io/juemimgcd/a-commerce:vX.Y.Z` 和 `sha-<完整提交号>`，随后自动创建 Git tag 和 GitHub Release。`0.x.y` 标记为 prerelease。
4. Release 附带源码包、部署包和 `SHA256SUMS`；部署包的 `image.env` 固定镜像 SHA-256 摘要。镜像同时生成 SBOM 和 provenance 元数据。
5. 只有仓库变量 `DEPLOY_ENABLED=true` 才调用 staging 部署；尚未提供服务器时保持 `false`。

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

GHCR 第一次创建的包默认私有。仓库公开不自动保证镜像公开；需在 [Packages](https://github.com/juemimgcd?tab=packages) 将 `a-commerce` 的可见性设为 Public，或在服务器使用 `read:packages` 凭据执行 `docker login ghcr.io`。Actions 发包使用内置 `GITHUB_TOKEN`，无需把个人 token 存入仓库。[官方包发布说明](https://docs.github.com/en/packages/managing-github-packages-using-github-actions-workflows/publishing-and-installing-a-package-with-github-actions)。

## 启用服务器 CD

当前没有配置服务器。此流程适用于一台独立 Ubuntu/Linux 测试服务器：已安装 Docker Engine、Compose v2、bash、curl、GNU tar/coreutils 和 `flock`；部署用户可运行 Docker。需要空闲的 58000/58001、55432、56379、55672/55673 本机端口。项目使用固定 `a-commerce-staging` Compose 名称，不能与现有本地数据混用。

1. 将部署目录设为例如 `/srv/a-commerce`，部署用户拥有该目录，创建 `shared/`。从部署包执行 `python3 scripts/configure_local.py` 生成随机 `.env`，将它移动为 `/srv/a-commerce/shared/.env`，权限保持 0600。这个配置只用于模拟支付 staging，不可复制到生产交易环境。
2. 在仓库 **Settings → Environments → staging** 配置下面的变量和 Secrets。主机公钥必须通过可信渠道核对后保存，工作流不会自动信任 `ssh-keyscan` 返回的未知密钥。

| 类型 | 名称 | 含义 |
|---|---|---|
| Variable | `DEPLOY_HOST` | 服务器域名或 IPv4 地址 |
| Variable | `DEPLOY_USER` | SSH 部署用户 |
| Variable | `DEPLOY_PORT` | SSH 端口，默认 22 |
| Variable | `DEPLOY_ROOT` | 绝对目录，例如 `/srv/a-commerce`；不含空格 |
| Secret | `DEPLOY_SSH_KEY` | 专用部署私钥 |
| Secret | `DEPLOY_KNOWN_HOSTS` | 已核对的 OpenSSH known_hosts 行；非 22 端口用 `[host]:port` |

3. 将**仓库级**变量 `DEPLOY_ENABLED` 改成 `true`。之后每个成功发布的新版本都会调用 CD；也可在 `deploy-staging` 中手动填写已发布 tag，选择 main 运行。

SSH 凭据仅在部署任务中使用，`staging` 环境可自行设置审批人。应用凭据留在服务器 `shared/.env`，不会复制进 GitHub。工作流下载已发布制品、核对校验和，通过严格 SSH 主机校验传输；同一服务器使用 `flock` 串行部署。

## 部署行为与失败处理

`deploy/deploy.sh` 拉取固定摘要的镜像、等待 PostgreSQL/Redis/RabbitMQ、设置队列策略，然后停止 API/provider/relay/三个 worker。停写期间依次保存 PG dump、provider 卷和媒体卷，执行数据库升级，再启动应用并等待 `/health/ready` 通过。成功后更新 `current` 符号链接，旧版本与 `backups/` 保留。

这是有维护窗口的单机部署。就绪检查确认 API 数据库版本；Compose 同时确认其他进程运行，不等于支付、队列、全部 worker 和业务链路验收。首次启动的备份是空环境快照。备份或迁移失败后任务终止并保留现场，可能需要人工恢复停掉的服务；不要盲目重新启动旧代码。

迁移成功后不能仅切换旧镜像来保证回滚。应先判断旧程序与新 schema 是否兼容；不兼容时，按 [运维手册](backend-operations.md) 在隔离库演练恢复，核实备份和资金状态后再决定恢复方式。自动部署不执行数据库 downgrade，不删除数据卷，也不宣称零停机或高可用。

API/provider 及基础设施端口均只绑定服务器 loopback。对外访问需单独配置 TLS 网关、域名和访问控制；`/metrics` 与模拟提供方不应公开。真实支付、容量和生产部署仍需独立验收。
