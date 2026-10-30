# 商品目录逻辑过期

列表首页（无搜索、游标、品牌、属性和价格筛选）与商品详情使用逻辑过期缓存。搜索、筛选和游标分页使用参数完整的独立查询缓存，默认保留 15–20 秒，无额外旧值窗口；分类字典与私有查询不进入缓存。报价、下单和库存事务始终读取数据库事实。

公开列表和详情还可复用已校验的 JSON 响应，默认 1 秒、最大 5 秒，并保留入口限流与 Redis 版本确认。详细边界、配置和发压工具见 [商品浏览优化与压测工具](catalog-performance.md)。

配置读写分离后，搜索、筛选和分页缓存回源使用副本；未配置时使用主库。查询缓存与 HTTP 响应缓存均按主库/副本来源隔离，复制延迟仍可能叠加缓存时间，不能用于交易判断。配置和故障行为见 [商品查询读写分离](database-read-routing.md)。本机压测证据见 [第一轮](catalog-benchmark-2026-10-30.md) 与 [第二轮](catalog-benchmark-round2-2026-10-30.md)。

## 请求行为

缓存保存展示数据、`fresh_until` 和 `stale_until`，使用独立的 `logical-v1` 命名空间。

1. 新鲜期：直接返回，不更新任何到期时间。
2. 逻辑过期但未到 `stale_until`：立即返回旧值，尝试启动后台刷新。每进程同一 key 最多一个后台任务，任务总数不超过 `cache_origin_concurrency`。
3. 超过旧值期限、物理过期或被淘汰：只在取得回源预算后等待新结果。250ms 内拿不到预算或另一实例的刷新结果，返回 `CATALOG_BUSY`，不无限排队。
4. Redis 不可用：只允许有界数据库回源，不读取无法确认版本的本地旧副本。

数据库加载总预算为 15 秒；冷回源超时返回 `503 CATALOG_TIMEOUT`、`retryable=true` 和 `Retry-After: 1`，仍执行租约与回源名额清理。

默认新鲜期为 30 秒加 0–5 秒抖动，之后最多允许 120 秒旧值。两个时间从本次数据库读取开始计算；Redis 物理 TTL 为新鲜期加旧值窗口，应用仍检查绝对截止时间。各 API 主机需要时钟同步。访问不会续期；成功回源才产生新快照，因此热点数据也会定期刷新。

商品详情不存在或已下架时缓存空结果，并保持 API 返回 404。后台商品/SKU 更新仍通过 outbox 增加 `catalog:generation`，使新请求转到新版本 key；旧刷新只写旧版本，不会恢复已失效的缓存。该通知是异步的，不提供下架后立即不可见的强一致性保证。展示库存允许陈旧，不能用于承诺可购买数量。

## 刷新合并与生命周期

实例使用 `SET NX EX` 获取 20 秒刷新租约，数据库加载有 15 秒总超时。发布缓存和释放租约的 Lua 均核对随机所有权 token，过期的刷新者不能覆盖新持有者的数据或删除其租约。获取租约后再次读取缓存，避免上一刷新者刚完成时重复回源。

冷回源和后台刷新共享每进程回源信号量，默认上限 4。后台任务由资源生命周期持有，退出时取消并等待结束，然后关闭 Redis/数据库。后台刷新异常有日志与指标，失败不会延长旧数据截止时间。

后台刷新未获得租约或本地回源名额时，保留本地任务记录并冷却 1–2 秒；刷新或发布失败时冷却 2–5 秒，均带随机抖动。冷却不占数据库回源名额，但计入后台任务总数上限，因此不会为任意 key 建立无限增长的退避表。冷却结束移除记录，由后续访问触发新尝试；不会自动循环重试。刷新失败还保留原租约直至自然过期（从获取时起最多 20 秒），防止其他实例立即接着回源。冷却与租约都不延长旧值期限。

只有发布 Lua 返回 `1` 才计为后台 `refreshed`；返回 `0` 计为 `lease_lost`，Redis 发布异常计为 `publish_failed`，两者在后台路径均计为 `refresh_failed`。冷回源即使发布失败，仍可返回刚从数据库取得的结果。

这是减少重复查询的租约，不是订单一致性锁。Redis 主从切换或 `allkeys-lru` 淘汰租约时可能发生重复刷新；仍有每进程数据库预算保护。全局最坏回源并发按 API 进程数乘以每进程预算计算。Redis 官方说明了异步复制故障切换的锁互斥限制，以及随机 token 校验释放的必要性：[Distributed locks](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)。

## 配置与观测

- `COMMERCE_CACHE_SECONDS=30`：基础新鲜期，另加 0–5 秒抖动。
- `COMMERCE_CACHE_QUERY_SECONDS=15`：查询缓存新鲜期，另加 0–5 秒抖动，无额外旧值窗口。
- `COMMERCE_CACHE_RESPONSE_SECONDS=1`：公开 JSON 响应复用，最大 5 秒，0 关闭；不超过底层截止时间并预留提前刷新机会。
- `COMMERCE_REDIS_CONNECTIONS=50`：每进程每组 Redis 连接上限，池满最多等待 50ms。
- `COMMERCE_CACHE_STALE_SECONDS=120`：逻辑过期后的最大旧值窗口。
- `COMMERCE_CACHE_ORIGIN_CONCURRENCY=4`：每进程冷回源和后台刷新的共享并发预算。
- `commerce_catalog_cache_total{outcome=...}`：`hit`、`stale`、`miss`、`coalesced`、`refreshed`、`refresh_busy`、`refresh_failed`、`lease_lost`、`publish_failed`、`origin_timeout`、`invalid`、`unavailable`、`busy`。
- `commerce_catalog_origin_seconds`：数据库加载耗时；不包含等待回源名额的时间。

现有回归覆盖正常命中与 Redis 故障下的回源预算。新逻辑的过期、刷新失败、租约竞争、取消和故障切换需要专项回归；本次遵守项目约定，没有新增或修改测试。缓存改造本身不是 50,000 RPS 的容量验收。
