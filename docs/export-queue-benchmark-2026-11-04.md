# RabbitMQ 报表导出突发任务验证（2026-11-04）

## 结论

两次完整本地运行各分档提交 1,000 个报表导出请求，均全部受理、完成，下载后的 CSV 内容逐行核对通过。每次另有默认限流探针受理的 5 个任务，同样完成并验证。

100 次/秒提交 600 个任务时，两次运行的受理接口 P95 分别为 44.925 ms、32.805 ms；最后一个提交响应结束后，剩余任务分别在约 13.896 秒、13.374 秒完成。两次运行 Outbox 未投递记录的采样峰值均为 400，RabbitMQ ready 消息的采样峰值均为 22，unacked 消息采样峰值均为 1。

结果支持“报表导出异步解耦、任务缓冲和受控消费”。主要积压在 Outbox，不能把 400 条全部说成 RabbitMQ 积压；也不能据此声称下单削峰、数据库写入峰值降低或某个性能提升比例。没有同步导出对照组，也没有生产流量或高可用故障验证。

## 真实业务链路

`POST /api/v1/admin/exports` → 校验管理员权限 → 同一事务保存 ExportJob、Operation、Outbox → 返回 202 和任务 ID → relay 发布到 `commerce.export.v1` → general worker 生成 CSV → 更新任务完成状态并 ACK → 下载接口校验归属后返回文件。

消费者全程运行，没有暂停消费者来制造积压，也没有给导出或文件写入增加人工延迟。单个 worker 配置的全局并发为默认 4，但每个队列 prefetch 为 1，因此本次单导出队列最多一个未确认任务；采样到的 running 和 unacked 峰值也均为 1。

relay 按当前实现每轮最多发布 50 条，轮间等待 1 秒，且每条发布包含数据库事务与 broker confirm 等待。任务在进入 RabbitMQ 前已可能在 Outbox 排队，这也是必须分别观测两处积压的原因。

## 环境、数据及口径

- macOS Apple Silicon；Python HTTPX 定速提交脚本，真实本机 TCP HTTP 请求，不是 JMeter。API、relay、worker 各 1 个本机进程；发压器与服务共用宿主资源。
- 一次性 Docker PostgreSQL 18.6（2 CPU / 1 GiB）、RabbitMQ 4.4.0（2 CPU / 1 GiB）、Redis 7.4.9（1 CPU / 256 MiB）。RabbitMQ 为单节点 quorum 队列，未验证多节点副本容错。
- 独立 `commerce_export_test` 数据库/vhost、临时凭证、随机 loopback 端口，迁移到当前 head。没有使用业务配置或业务数据。
- 合成 366 天资金日报，每份导出覆盖全部 366 行及表头，逐行校验日期、实收、实退和净收款，非空文件检查不能代替内容校验。每个任务内容相同，用不同任务 ID 验证独立导出完成及下载归属。
- 默认限流探针：同一管理员并发提交 20 次，5 次返回 202，15 次返回 429。探针单独统计。
- 正式实验仅在隔离 API 中将 entry_ip、user_write、user_read 的 rate/burst 调为 200/200，避免先测到入口配额。业务默认配置未修改。
- 三档为 100 个任务 @ 10 RPS、300 个任务 @ 50 RPS、600 个任务 @ 100 RPS，共 1,000 个。每档完全处理完后开始下一档，不重试失败 HTTP 请求、不剔除慢请求。
- HTTP 延迟从实际开始调用 POST 到读取响应完成，包含客户端连接等待、网络、鉴权和数据库事务；不含计划发送调度迟延，后者另存。100 RPS 档两轮调度迟延 P95 均低于 1 ms，最大值约 24–29 ms。
- “追加完成时间”从最后一个 POST 响应完成到观测到所有任务 done、Outbox 无待投递且无 running 的时刻；数据库采样约 100 ms。这里不把 API 的 202 当成文件生成完成。
- RabbitMQ 管理接口约 1 秒采样，broker 统计间隔也设为 1 秒；峰值是采样观测值，可能漏掉更短暂的峰值，不能当成严格上界。结束时检查 ready=0、unacked=0，以及 publish=ack=成功受理数。
- 三档均没有丢弃预热样本；第一档包含较多冷启动影响，不能把不同档位的延迟差解释成提高流量反而加速。

## 复测结果

主报告采用修正观测脚本后的完整复测：`commerce-export-20261104t050328z-8d56`。

| 提交目标 | 任务数 | HTTP 202 | HTTP P95 | Outbox 未投递峰值 | RabbitMQ ready 峰值 | 追加完成时间 | 从首个计划提交至全部完成 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 RPS | 100 | 100 | 80.112 ms | 10 | 0 | 0.646 s | 10.605 s |
| 50 RPS | 300 | 300 | 33.232 ms | 123 | 8 | 3.957 s | 9.958 s |
| 100 RPS | 600 | 600 | 32.805 ms | 400 | 22 | 13.374 s | 19.384 s |

正式 1,000 次提交无 429、5xx 或传输错误；各档采样到的 retry_wait/unknown/manual_review 任务数均为 0。计入探针受理的 5 次，共 1,005 个任务、1,005 次发布与 ACK，所有 1,005 次文件下载返回 200 且内容正确；最终 Outbox、running、RabbitMQ ready/unacked 均为 0。未注入失败，本次不能替代消息重复投递或故障恢复演练。

约 600 / 19.384 = 31 个任务/秒是该档从开始提交到全部完成的平均完成速度，不是消费者持续吞吐上限，也不是 RabbitMQ 自身容量。

## 初次运行与观测修正

首个初始化尝试 `commerce-export-20261104t050036z-f861` 因测试 seed 缺少 ScheduledTask 必填字段失败，尚未提交导出请求；修正为 ORM insert 后重新运行，未改应用业务实现。

第一次完整运行 `commerce-export-20261104t050117z-0731` 的三档全部成功，结果如下：

| 提交目标 | HTTP P95 | Outbox 未投递峰值 | 管理接口 ready 峰值 | 追加完成时间 |
| --- | ---: | ---: | ---: | ---: |
| 10 RPS | 84.019 ms | 11 | 1 | 0.892 s |
| 50 RPS | 34.853 ms | 131 | 13 | 4.055 s |
| 100 RPS | 44.925 ms | 400 | 22 | 13.896 s |

该次运行 AMQP passive declare 返回的计数持续为 0，与独立管理 API 及 rabbitmqctl 的实测不一致。因此该次原始 `summary.json` 的 `peak_queue_ready=0` 和 `snapshots.jsonl` 中的队列计数字段不采用；上表依据同时保存的 `broker-stats.json` 按各档起止时刻重新计算。原始文件保留，没有覆盖失败或错误观测。已移除脚本中该计数来源，随后完整复测确认。

[官方队列文档](https://www.rabbitmq.com/docs/queues)说明了 ready 与 unacknowledged 状态及多种观测途径；本次选择与 CLI 相互印证的管理 API。启动阶段尚未生成 broker 统计的空字段不视作零。

## 复现与证据

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmarks/export_queue.py
```

脚本自动创建隔离服务、迁移、构造数据、执行探针和三档负载、校验文件、保存证据并清理它创建的进程与 Docker 项目。需要已有项目依赖、Docker 和对应镜像，不修改生产配置。临时 Compose 凭证文件在结束后删除。

- [可复用脚本](../scripts/benchmarks/export_queue.py)
- 本地证据目录：`var/validation/commerce-export-20261104t050328z-8d56/`（gitignored）。
- `summary.json`：分档结果、限流配置、最终完成和消息计数。
- `requests-*rps.json`：每次提交的状态、响应、延迟、调度迟延。
- `snapshots.jsonl`：数据库任务与 Outbox 时间序列。
- `broker-stats.json`：RabbitMQ ready/unacked、消费者数和消息计数。
- `downloads.json` 与 `files/exports/`：逐份下载核验及实际 CSV 文件。
- `api-*.log`、`worker.log`、`relay.log`、`services.log`：进程和服务日志。

`sh scripts/check.sh` 已通过（Ruff、格式、Mypy、编译）；测试环境已清理。简历本次未修改。
