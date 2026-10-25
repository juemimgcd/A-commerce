#!/bin/sh
# 配置在数据卷中可写，Sentinel 的 CONFIG REWRITE 必须跨重启保留角色。
set -eu
umask 077
case "${REDIS_PASSWORD:-}" in ''|*[!a-f0-9]*) echo 'Redis password must be hex' >&2; exit 1;; esac
[ "${#REDIS_PASSWORD}" -ge 32 ]
if [ ! -f /data/redis.conf ]; then
  cat > /data/redis.conf <<EOF
bind 0.0.0.0
protected-mode yes
port 6379
dir /data
requirepass $REDIS_PASSWORD
masterauth $REDIS_PASSWORD
replica-announce-ip $REDIS_NODE
replica-announce-port 6379
maxmemory 128mb
maxmemory-policy $REDIS_POLICY
appendonly $REDIS_AOF
appendfsync everysec
min-replicas-to-write $REDIS_MIN_REPLICAS
min-replicas-max-lag 5
EOF
  if [ -n "${REDIS_SEED:-}" ]; then
    printf 'replicaof %s 6379\n' "$REDIS_SEED" >> /data/redis.conf
  fi
fi
# 不在每次启动时重写 replicaof：原主恢复后应服从 Sentinel 记录的拓扑。
exec /usr/local/bin/docker-entrypoint.sh redis-server /data/redis.conf
