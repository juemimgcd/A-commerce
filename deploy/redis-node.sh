#!/bin/sh
# 配置在数据卷中可写，Sentinel 的 CONFIG REWRITE 必须跨重启保留角色。
set -eu
umask 077
case "${REDIS_PASSWORD:-}" in ''|*[!a-f0-9]*) echo 'Redis password must be hex' >&2; exit 1;; esac
[ "${#REDIS_PASSWORD}" -ge 32 ]
REDIS_MAXMEMORY=${REDIS_MAXMEMORY:-128mb}
REDIS_SLOWLOG_MICROSECONDS=${REDIS_SLOWLOG_MICROSECONDS:-10000}
REDIS_SLOWLOG_MAX_LEN=${REDIS_SLOWLOG_MAX_LEN:-128}
printf '%s\n' "$REDIS_MAXMEMORY" | grep -Eq '^[1-9][0-9]*(b|k|kb|m|mb|g|gb)?$' || {
  echo 'REDIS_MAXMEMORY must be a positive Redis memory size' >&2; exit 1;
}
for number in "$REDIS_SLOWLOG_MICROSECONDS" "$REDIS_SLOWLOG_MAX_LEN"; do
  printf '%s\n' "$number" | grep -Eq '^[1-9][0-9]*$' || {
    echo 'Redis slowlog settings must be positive integers' >&2; exit 1;
  }
done
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
# 仅更新受环境变量管理的参数，保留 Sentinel CONFIG REWRITE 保存的角色和凭证。
# 临时文件与配置处于同一卷，替换完成后才启动 Redis。
temporary=$(mktemp /data/redis.conf.XXXXXX)
trap 'rm -f "$temporary"' EXIT HUP INT TERM
awk '$1 != "maxmemory" && $1 != "slowlog-log-slower-than" && $1 != "slowlog-max-len"' \
  /data/redis.conf > "$temporary"
printf '\nmaxmemory %s\nslowlog-log-slower-than %s\nslowlog-max-len %s\n' \
  "$REDIS_MAXMEMORY" "$REDIS_SLOWLOG_MICROSECONDS" "$REDIS_SLOWLOG_MAX_LEN" >> "$temporary"
mv "$temporary" /data/redis.conf
trap - EXIT HUP INT TERM
# 不在每次启动时重写 replicaof：原主恢复后应服从 Sentinel 记录的拓扑。
exec /usr/local/bin/docker-entrypoint.sh redis-server /data/redis.conf
