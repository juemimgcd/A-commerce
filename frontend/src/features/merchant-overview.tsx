"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  ArrowUpRight,
  ArrowDownRight,
  ArrowsClockwise,
  CalendarBlank,
  ChartBar,
  ShoppingBag,
  Users,
  ArrowUUpLeft,
  Package,
  Truck,
  Sparkle,
  ClipboardText,
} from "@phosphor-icons/react";
import {
  Button,
  DataTable,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Money,
  Notice,
  Status,
} from "@/components/ui";
import { api, queryString } from "@/lib/api";
import { dateTime, money, shortId } from "@/lib/format";
import type { components } from "@/lib/api.generated";
import type { OrderPage } from "@/lib/types";

type Overview = components["schemas"]["MerchantOverview"];
type Totals = components["schemas"]["SalesTotals"];
type Day = components["schemas"]["SalesDay"];
type Range = { start: string; end: string };

function shiftDay(value: string, offset: number) {
  const day = new Date(`${value}T00:00:00Z`);
  day.setUTCDate(day.getUTCDate() + offset);
  return day.toISOString().slice(0, 10);
}

function recentRange(days: number): Range {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  return { start: shiftDay(today, 1 - days), end: shiftDay(today, 1) };
}

const metrics = [
  {
    key: "net_goods_fen",
    label: "商品净收入",
    icon: ShoppingBag,
    amount: true,
    note: "期间付款订单的商品金额，减截至当前已成功退款的商品金额，不含运费。",
  },
  {
    key: "paid_orders",
    label: "支付订单数",
    icon: ChartBar,
    amount: false,
    note: "期间付款的订单数；付款后退款不抹去支付记录。",
  },
  {
    key: "new_buyers",
    label: "新增购买客户",
    icon: Users,
    amount: false,
    note: "首次在本店付款发生于所选期间的去重客户数。",
  },
  {
    key: "refunds_fen",
    label: "退款金额",
    icon: ArrowUUpLeft,
    amount: true,
    note: "期间成功退款的金额，包含运费退款，可能来自以前的订单。",
  },
] as const;

export function MerchantOverview({
  shop,
  renderOrder,
}: {
  shop: components["schemas"]["ShopOut"];
  renderOrder: (id: string, onDone: () => void) => ReactNode;
}) {
  const [range, setRange] = useState<Range>(() => recentRange(7));
  const [preset, setPreset] = useState("7");
  const [rangeError, setRangeError] = useState("");
  const [selectedOrder, setSelectedOrder] = useState<string>();
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "overview", range],
    queryFn: () => api<Overview>(`merchant/shops/${shop.id}/overview${queryString(range)}`),
  });
  const orders = useQuery({
    queryKey: ["customer", "merchant", shop.id, "recent-orders"],
    queryFn: () => api<OrderPage>(`merchant/shops/${shop.id}/orders?limit=5`),
  });
  const href = (tab: string) =>
    `/merchant${queryString({ shop: shop.id, tab, ...(tab === "agent" ? range : {}) })}`;
  const refresh = () => {
    const next = preset === "custom" ? range : recentRange(Number(preset));
    if (next.start !== range.start || next.end !== range.end) {
      setRange(next);
    } else {
      void query.refetch();
    }
    void orders.refetch();
  };
  const data = query.data;
  return (
    <section className="sales-overview">
      <header className="sales-heading">
        <div>
          <p className="sales-eyebrow">店铺经营 / OVERVIEW</p>
          <h1>销售概览</h1>
        </div>
        <div className="sales-controls">
          <label className="sales-period">
            <CalendarBlank size={16} aria-hidden />
            <span className="sr-only">销售统计时间</span>
            <select
              value={preset}
              onChange={(event) => {
                setPreset(event.target.value);
                setRangeError("");
                if (event.target.value !== "custom")
                  setRange(recentRange(Number(event.target.value)));
              }}
            >
              <option value="1">今天</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
              <option value="custom">自定义时间</option>
            </select>
          </label>
          <Button
            variant="secondary"
            busy={query.isFetching || orders.isFetching}
            onClick={refresh}
            aria-label="刷新经营概览"
          >
            <ArrowsClockwise size={17} />
          </Button>
          <Link className="button primary sales-assistant" href={href("agent")}>
            <Sparkle size={17} />
            经营助手
          </Link>
        </div>
      </header>
      {preset === "custom" && (
        <form
          className="sales-custom-range"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const start = String(form.get("start"));
            const last = String(form.get("end"));
            const days = (Date.parse(last) - Date.parse(start)) / 86400000 + 1;
            if (!Number.isFinite(days) || days < 1 || days > 366) {
              setRangeError("请选择 1 至 366 天的有效日期范围。");
              return;
            }
            setRangeError("");
            setRange({ start, end: shiftDay(last, 1) });
          }}
        >
          <Field label="开始日期">
            <input name="start" type="date" required defaultValue={range.start} />
          </Field>
          <Field label="结束日期（含当天）">
            <input name="end" type="date" required defaultValue={shiftDay(range.end, -1)} />
          </Field>
          <Button type="submit">应用日期</Button>
          {rangeError && (
            <p role="alert" className="error-text">
              {rangeError}
            </p>
          )}
        </form>
      )}
      <ErrorNotice error={query.error} retry={refresh} />
      {query.isPending ? (
        <Loading rows={4} />
      ) : (
        data && (
          <>
            <div className="sales-meta">
              <span>
                {data.start} — {shiftDay(data.end, -1)} · 上海时间
              </span>
              <span>{query.isFetching ? "正在更新…" : `更新于 ${dateTime(data.as_of)}`}</span>
            </div>
            <div className="sales-metrics">
              {metrics.map(({ key, label, icon: Icon, amount, note }, index) => (
                <article className={`sales-metric sales-metric-${index}`} key={key}>
                  <div className="sales-metric-label">
                    <h2>{label}</h2>
                    <span className="sales-metric-icon">
                      <Icon size={19} />
                    </span>
                  </div>
                  <strong className="sales-value">
                    {data.current[key] === null
                      ? "暂不可算"
                      : amount
                        ? money(data.current[key])
                        : data.current[key].toLocaleString("zh-CN")}
                  </strong>
                  <Comparison
                    current={data.current[key]}
                    previous={data.previous[key]}
                    reverse={key === "refunds_fen"}
                  />
                  <details className="sales-definition">
                    <summary>统计口径</summary>
                    <p>{note}</p>
                    {key === "net_goods_fen" && data.current[key] === null && (
                      <p>历史退款分配缺失，无法计算完整商品净收入。</p>
                    )}
                  </details>
                </article>
              ))}
            </div>
            <div className="sales-middle">
              <SalesChart key={`${range.start}:${range.end}`} days={data.days} />
              <aside className="sales-panel sales-todos">
                <div className="sales-panel-heading">
                  <h2>经营待办</h2>
                  <span className="sales-live">当前</span>
                </div>
                <p className="sales-subtitle">待处理事项，不受日期筛选影响</p>
                <div className="sales-todo-list">
                  {[
                    {
                      label: "待发货订单",
                      count: data.todos.unshipped,
                      tab: "orders",
                      icon: Truck,
                    },
                    {
                      label: "待处理售后",
                      count: data.todos.returns,
                      tab: "returns",
                      icon: ArrowUUpLeft,
                    },
                    {
                      label: "低库存 SKU",
                      count: data.todos.low_stock,
                      tab: "inventory",
                      icon: Package,
                    },
                    {
                      label: "待确认补货",
                      count: data.todos.restock_drafts,
                      tab: "inventory",
                      icon: ClipboardText,
                    },
                  ].map(({ label, count, tab, icon: Icon }) => (
                    <Link className="sales-todo" href={href(tab)} key={label}>
                      <span className="sales-todo-icon">
                        <Icon size={18} />
                      </span>
                      <span>{label}</span>
                      <strong>{count.toLocaleString("zh-CN")}</strong>
                      <ArrowRight size={15} />
                    </Link>
                  ))}
                </div>
                <p className="sales-todo-note">
                  低库存按已设阈值统计，未设规则仅计缺货。
                  {data.todos.unconfigured_stock > 0 && (
                    <Link href={href("inventory")}>
                      {data.todos.unconfigured_stock} 个在售 SKU 待设置规则{" "}
                      <ArrowUpRight size={12} />
                    </Link>
                  )}
                </p>
              </aside>
            </div>
            <p className="sales-comparison-note">
              对比 {data.previous_start} — {shiftDay(data.start, -1)}
              （上一等长周期）；今天的数据仍在累计。
            </p>
          </>
        )
      )}
      <section className="sales-panel sales-orders">
        <div className="sales-panel-heading">
          <div>
            <h2>最近订单</h2>
            <p className="sales-subtitle">全店最新 5 笔订单</p>
          </div>
          <Link href={href("orders")} className="sales-text-link">
            查看全部订单 <ArrowUpRight size={15} />
          </Link>
        </div>
        <ErrorNotice error={orders.error} retry={() => void orders.refetch()} />
        {orders.isPending ? (
          <Loading />
        ) : orders.data?.items.length ? (
          <DataTable headers={["订单 / 时间", "商品", "数量", "实付金额", "支付状态", "履约状态"]}>
            {orders.data.items.map((order) => (
              <tr key={order.id}>
                <td>
                  <button
                    className="sales-order-link"
                    onClick={() => setSelectedOrder(order.id)}
                    aria-label={`查看订单 ${shortId(order.id)}`}
                  >
                    #{shortId(order.id)}
                  </button>
                  <small>{dateTime(order.created_at)}</small>
                </td>
                <td className="sales-order-product">
                  {order.items[0]?.title ?? "商品信息待更新"}
                  {order.items.length > 1 && <small>等 {order.items.length} 项商品</small>}
                </td>
                <td>{order.items.reduce((sum, item) => sum + item.quantity, 0)}</td>
                <td>
                  {["paid", "refunded"].includes(order.payment_status) ? (
                    <Money value={order.total_fen} />
                  ) : (
                    <>
                      {order.payment_status === "closed" ? "未付款" : "实付待确认"}
                      <small>
                        订单金额 <Money value={order.total_fen} />
                      </small>
                    </>
                  )}
                </td>
                <td>
                  <Status value={order.payment_status} />
                </td>
                <td>
                  <Status value={order.fulfillment_status} />
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          !orders.error && (
            <Empty title="还没有订单">新订单会显示在这里，成交后可直接查看与处理。</Empty>
          )
        )}
      </section>
      <footer className="sales-footer">
        <span>A-commerce · 卖家中心</span>
        <Link href={href("reports")}>
          查看完整经营报表 <ArrowRight size={14} />
        </Link>
      </footer>
      <Modal
        open={!!selectedOrder}
        wide
        title="订单履约"
        onClose={() => setSelectedOrder(undefined)}
      >
        {selectedOrder && renderOrder(selectedOrder, refresh)}
      </Modal>
    </section>
  );
}

function Comparison({
  current,
  previous,
  reverse,
}: {
  current: Totals["net_goods_fen"];
  previous: Totals["net_goods_fen"];
  reverse: boolean;
}) {
  if (current === null || previous === null || previous === 0)
    return <p className="sales-change neutral">暂无可比数据</p>;
  const delta = ((current - previous) / Math.abs(previous)) * 100;
  const improved = reverse ? delta < 0 : delta > 0;
  return (
    <p className={`sales-change ${delta === 0 ? "neutral" : improved ? "positive" : "negative"}`}>
      {delta !== 0 && (delta > 0 ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />)}
      {delta === 0
        ? "与上期持平"
        : `${Math.abs(delta).toFixed(1)}% 较上期${delta > 0 ? "增加" : "减少"}`}
    </p>
  );
}

function SalesChart({ days }: { days: Day[] }) {
  const [metric, setMetric] = useState<"net_goods_fen" | "paid_orders">("net_goods_fen");
  const [active, setActive] = useState<string>();
  const [table, setTable] = useState(false);
  const values = days.map((day) => day[metric]);
  const maximum = Math.max(1, ...values.map((value) => value ?? 0));
  const step = Math.pow(10, Math.floor(Math.log10(maximum)));
  const ceiling =
    metric === "paid_orders" ? Math.ceil(maximum / 4) * 4 : Math.ceil(maximum / step) * step;
  const selected = days.find((day) => day.day === active);
  const hasSales = days.some((day) => day.paid_orders > 0);
  return (
    <section className="sales-panel sales-chart">
      <div className="sales-panel-heading">
        <h2>销售趋势</h2>
        <div className="sales-chart-switch" aria-label="趋势指标">
          {(
            [
              ["net_goods_fen", "商品净收入"],
              ["paid_orders", "支付订单数"],
            ] as const
          ).map(([key, label]) => (
            <button key={key} aria-pressed={metric === key} onClick={() => setMetric(key)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="sales-chart-caption">
        <span>{metric === "net_goods_fen" ? "金额 / 元" : "订单 / 笔"}</span>
        <button onClick={() => setTable(!table)} aria-expanded={table}>
          {table ? "收起明细" : "查看每日明细"}
        </button>
      </div>
      {!hasSales ? (
        <Empty title="所选期间暂无成交">选择其他日期查看销售趋势。</Empty>
      ) : (
        <div
          className="sales-chart-canvas"
          onMouseLeave={() => setActive(undefined)}
          onBlur={() => setActive(undefined)}
          onKeyDown={(event) => {
            if (event.key === "Escape") setActive(undefined);
          }}
        >
          <div className="sales-chart-axis" aria-hidden>
            {[1, 0.75, 0.5, 0.25, 0].map((tick) => (
              <span key={tick}>
                {new Intl.NumberFormat("zh-CN", {
                  maximumFractionDigits: metric === "paid_orders" ? 1 : 2,
                  notation: "compact",
                }).format((ceiling * tick) / (metric === "net_goods_fen" ? 100 : 1))}
              </span>
            ))}
          </div>
          <div className="sales-chart-scroll" tabIndex={0} aria-label="每日销售柱状图，可横向滚动">
            <div
              className="sales-chart-plot"
              style={{ minWidth: `${Math.max(360, days.length * 38)}px` }}
            >
              <div className="sales-gridlines" aria-hidden>
                {[0, 1, 2, 3, 4].map((line) => (
                  <i key={line} />
                ))}
              </div>
              <div className="sales-bars">
                {days.map((day) => (
                  <button
                    key={day.day}
                    className={`sales-bar-column ${active === day.day ? "active" : ""}`}
                    aria-label={`${day.day}，商品净收入 ${day.net_goods_fen === null ? "未知" : money(day.net_goods_fen)}，支付订单 ${day.paid_orders} 笔`}
                    onMouseEnter={() => setActive(day.day)}
                    onFocus={() => setActive(day.day)}
                    onClick={() => setActive(day.day)}
                  >
                    <span className="sales-bar-track">
                      <span
                        className={`sales-bar ${day[metric] === null ? "unknown" : ""}`}
                        style={{
                          height: `${day[metric] === null ? 0 : (Math.max(0, day[metric]) / ceiling) * 100}%`,
                        }}
                      />
                      {day[metric] === null && <span className="sales-bar-unknown">—</span>}
                    </span>
                    <span className="sales-bar-date">{day.day.slice(5).replace("-", "/")}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
          {selected && (
            <div className="sales-chart-tooltip" role="status">
              <strong>{selected.day}</strong>
              <span>
                商品净收入{" "}
                <b>
                  {selected.net_goods_fen === null ? "暂不可算" : money(selected.net_goods_fen)}
                </b>
              </span>
              <span>
                支付订单 <b>{selected.paid_orders} 笔</b>
              </span>
            </div>
          )}
        </div>
      )}
      {values.some((value) => value === null) && (
        <Notice>部分日期退款分配缺失，净收入显示为未知。</Notice>
      )}
      {table && (
        <DataTable headers={["日期", "商品净收入", "支付订单数"]}>
          {days.map((day) => (
            <tr key={day.day}>
              <td>{day.day}</td>
              <td>{day.net_goods_fen === null ? "未知" : money(day.net_goods_fen)}</td>
              <td>{day.paid_orders}</td>
            </tr>
          ))}
        </DataTable>
      )}
    </section>
  );
}
