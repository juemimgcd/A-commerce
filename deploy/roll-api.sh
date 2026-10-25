#!/usr/bin/env bash
# 只滚动替换两个业务 API；不执行迁移，不更新 worker/回调/provider/网关。
# 新旧代码必须兼容当前 schema 和同一限流配置；含迁移的发布仍走 deploy.sh 维护窗口。
set -euo pipefail
cd "$(dirname "$0")/.."
env_file="${COMMERCE_ENV_FILE:-.env}"
compose=(docker compose --env-file "$env_file" -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml)
if [[ "${COMMERCE_HA:-0}" == 1 ]]; then
  compose+=(-f deploy/compose.ha.yaml)
fi

"${compose[@]}" config --quiet
# 防止同一工作目录中的两次滚动操作同时摘掉两个实例；锁仅由本次持有者清理。
mkdir -p var
if ! mkdir var/api-roll.lock; then
  echo "已有滚动操作或上次操作异常退出留下锁；请检查后再运行。" >&2
  exit 1
fi
trap 'rmdir var/api-roll.lock' EXIT
for service in gateway api api-secondary; do
  container=$("${compose[@]}" ps -q "$service")
  if [[ -z "$container" || "$(docker inspect --format '{{.State.Running}}' "$container")" != true ]]; then
    echo "需要已运行的 gateway、api 和 api-secondary；未开始摘流。" >&2
    exit 1
  fi
done

# 拒绝把直连开发部署半途滚动成哨兵模式；拓扑迁移先按维护流程完成。
"${compose[@]}" exec -T api python scripts/check_redis.py
if [[ "${COMMERCE_HA:-0}" == 1 ]]; then
  "${compose[@]}" exec -T api python scripts/check_ha.py
fi

for service in api api-secondary; do
  # 每次动一个实例前，再确认另一个实例可接流量。失败即停止，不继续摘掉最后一个。
  other=api
  [[ "$service" != api ]] || other=api-secondary
  other_container=$("${compose[@]}" ps -q "$other")
  if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$other_container")" != healthy ]]; then
    echo "$other 未就绪，停止滚动替换。" >&2
    exit 1
  fi
  echo "摘流 ${service}；等待网关探测后再停止进程。"
  "${compose[@]}" exec -T "$service" python -c "from pathlib import Path; Path('/tmp/commerce-draining').touch()"
  "${compose[@]}" exec -T "$other" python scripts/wait_gateway.py "$service" DOWN
  if ! "${compose[@]}" up -d --no-deps --no-build --force-recreate --wait --wait-timeout 120 "$service"; then
    echo "$service 替换失败，另一实例仍保留；请检查日志，不自动回滚数据库或镜像。" >&2
    exit 1
  fi
  "${compose[@]}" exec -T "$service" python scripts/wait_gateway.py "$service" UP
done
echo "两个业务 API 已依次替换；回调 API 和其他进程未重启。"
