#!/bin/sh
# 宿主机执行；只设置本项目 vhost 的队列策略，不改其他 RabbitMQ 项目。
set -eu
docker compose --env-file .env -f deploy/compose.yaml exec -T rabbitmq \
  rabbitmqctl set_policy -p commerce commerce-safe-dlx '^commerce\.(payment|close|refund|mail|export|cache|reconcile|housekeeping)\.v1$' \
  '{"dead-letter-strategy":"at-least-once","dead-letter-exchange":"commerce.isolation.v1","overflow":"reject-publish","max-length":10000,"delivery-limit":20}' \
  --apply-to quorum_queues --priority 10
