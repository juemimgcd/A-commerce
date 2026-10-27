#!/bin/sh
set -eu
umask 077
for credential in "$REDIS_CACHE_PASSWORD" "$REDIS_RATE_PASSWORD" "$REDIS_SENTINEL_PASSWORD"; do
  case "$credential" in ''|*[!a-f0-9]*) echo 'Redis password must be hex' >&2; exit 1;; esac
  [ "${#credential}" -ge 32 ]
done
if [ ! -f /data/sentinel.conf ]; then
  cat > /data/sentinel.conf <<EOF
bind 0.0.0.0
protected-mode yes
port 26379
dir /data
requirepass $REDIS_SENTINEL_PASSWORD
sentinel sentinel-user default
sentinel sentinel-pass $REDIS_SENTINEL_PASSWORD
sentinel resolve-hostnames yes
sentinel announce-hostnames yes
sentinel announce-ip $SENTINEL_NODE
sentinel announce-port 26379
sentinel monitor commerce-cache redis 6379 2
sentinel auth-pass commerce-cache $REDIS_CACHE_PASSWORD
sentinel down-after-milliseconds commerce-cache 5000
sentinel failover-timeout commerce-cache 60000
sentinel parallel-syncs commerce-cache 1
sentinel monitor commerce-rate redis-rate 6379 2
sentinel auth-pass commerce-rate $REDIS_RATE_PASSWORD
sentinel down-after-milliseconds commerce-rate 5000
sentinel failover-timeout commerce-rate 60000
sentinel parallel-syncs commerce-rate 1
EOF
fi
# 显式保留 Redis 默认保护，不改动 Sentinel 保存的选主与发现状态。
temporary=$(mktemp /data/sentinel.conf.XXXXXX)
trap 'rm -f "$temporary"' EXIT HUP INT TERM
awk '!($1 == "sentinel" && $2 == "deny-scripts-reconfig")' /data/sentinel.conf > "$temporary"
printf '\nsentinel deny-scripts-reconfig yes\n' >> "$temporary"
mv "$temporary" /data/sentinel.conf
trap - EXIT HUP INT TERM
exec /usr/local/bin/docker-entrypoint.sh redis-server /data/sentinel.conf --sentinel
