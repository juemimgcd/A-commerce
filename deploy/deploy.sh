#!/usr/bin/env bash
# 从已校验的 Release 目录执行；只操作固定的 staging Compose 项目。
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."
: "${DEPLOY_ROOT:?Set the absolute deployment root}"
export COMMERCE_ENV_FILE="$DEPLOY_ROOT/shared/.env"
export COMPOSE_PROJECT_NAME=a-commerce-staging
if [[ ! -f "$COMMERCE_ENV_FILE" ]]; then
  echo "Missing $COMMERCE_ENV_FILE; configure the staging environment first." >&2
  exit 1
fi
# image.env 来自发布工作流，只允许当前仓库的不可变镜像摘要。
COMMERCE_IMAGE=$(sed -n 's/^COMMERCE_IMAGE=//p' image.env)
if [[ ! "$COMMERCE_IMAGE" =~ ^ghcr\.io/juemimgcd/a-commerce@sha256:[a-f0-9]{64}$ ]]; then
  echo "Invalid release image digest." >&2
  exit 1
fi
export COMMERCE_IMAGE
compose=(docker compose --env-file "$COMMERCE_ENV_FILE" -f deploy/compose.yaml -f deploy/compose.redis-sentinel.yaml)
"${compose[@]}" config --quiet
# 镜像下载失败时不停止正在运行的应用。
"${compose[@]}" pull
"${compose[@]}" up -d --no-recreate --wait db redis redis-rate \
  redis-cache-replica-1 redis-cache-replica-2 redis-rate-replica-1 redis-rate-replica-2 \
  sentinel-1 sentinel-2 sentinel-3 rabbitmq
# PING 健康不代表发现/复制已经完成；在停写业务之前检查两个组。
"${compose[@]}" run --rm --no-deps -T api python scripts/check_redis.py
"${compose[@]}" exec -T rabbitmq rabbitmqctl set_policy -p commerce commerce-safe-dlx \
  '^commerce\.(payment|close|refund|mail|export|cache|reconcile|housekeeping)\.v1$' \
  '{"dead-letter-strategy":"at-least-once","dead-letter-exchange":"commerce.isolation.v1","overflow":"reject-publish","max-length":10000,"delivery-limit":20}' \
  --apply-to quorum_queues --priority 10

# 先停所有写入方，随后备份业务库、provider 卷和媒体卷，最后执行迁移。
"${compose[@]}" stop gateway api api-secondary api-callbacks relay worker worker-general worker-maintenance provider
backup="$DEPLOY_ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$backup"
trap 'echo "Deployment failed. Services may be stopped; inspect logs and backup: $backup. No automatic database downgrade was attempted." >&2' ERR
"${compose[@]}" exec -T db pg_dump -U commerce -d commerce -Fc > "$backup/commerce.dump"
"${compose[@]}" run --rm --no-deps -T --entrypoint tar provider -C /app/var -czf - . > "$backup/provider.tar.gz"
"${compose[@]}" run --rm --no-deps -T --entrypoint tar api -C /app/var/files -czf - . > "$backup/files.tar.gz"
if [[ -f "$DEPLOY_ROOT/current/image.env" ]]; then
  cp "$DEPLOY_ROOT/current/image.env" "$backup/previous-image.env"
fi
"${compose[@]}" run --rm --no-deps -T migrate
"${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 180 \
  api api-secondary api-callbacks provider relay worker worker-general worker-maintenance
"${compose[@]}" up -d --no-build --no-deps --wait --wait-timeout 90 gateway
curl --fail --silent --show-error --retry 10 --retry-all-errors --retry-delay 2 \
  --max-time 5 "http://127.0.0.1:${COMMERCE_GATEWAY_PORT:-58000}/health/ready"
# 仅成功后移动 current；旧 Release 和备份均保留。
ln -sfn "$PWD" "$DEPLOY_ROOT/current.next"
mv -Tf "$DEPLOY_ROOT/current.next" "$DEPLOY_ROOT/current"
echo "Deployed $COMMERCE_IMAGE; backup: $backup"
