"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { api, ApiError, queryString } from "@/lib/api";
import { dateTime, formText, parseFen, shortId } from "@/lib/format";
import type { components } from "@/lib/api.generated";
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
} from "@/components/ui";

type S = components["schemas"];
type Shop = S["ShopOut"];
type Task = S["RestockOut"];
type Risk = S["RiskOut"];
type Promotion = S["PromotionOut"];
const taskNames: Record<string, string> = {
  draft: "草稿",
  confirmed: "已确认",
  in_progress: "处理中",
  partially_received: "部分收货",
  completed: "已完成",
  cancelled: "已取消",
};
const promotionNames: Record<string, string> = {
  draft: "待确认草稿",
  published: "已发布",
  stopped: "已停用",
  scheduled: "待开始",
  active: "生效中",
  ended: "已结束",
  unavailable: "暂停新优惠",
};
const riskNames: Record<Risk["risk"], string> = {
  low: "库存偏低",
  slow: "销量偏低",
  unconfigured: "待配置规则",
  normal: "正常",
  insufficient: "销量依据不足",
};
const base = (shopId: string) => `merchant/shops/${shopId}`;
function NullableMoney({ value }: { value: number | null | undefined }) {
  return value == null ? <>未知</> : <Money value={value} />;
}
function form(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  return new FormData(event.currentTarget);
}
function More({ next, busy, onClick }: { next: boolean; busy: boolean; onClick: () => void }) {
  return next ? (
    <Button variant="secondary" busy={busy} onClick={onClick}>
      加载更多
    </Button>
  ) : null;
}
function useRows<T extends { id: string }>(shopId: string, path: string) {
  return useInfiniteQuery({
    queryKey: ["customer", "merchant", shopId, path],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<T[]>(`${base(shopId)}/${path}${queryString({ cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (page) => (page.length === 20 ? page[page.length - 1].id : undefined),
  });
}

export function MerchantDiagnostics({ shop }: { shop: Shop }) {
  const search = useSearchParams();
  const [id, setId] = useState(search.get("report") ?? "");
  const [offset, setOffset] = useState(0);
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "diagnostic", id, offset],
    enabled: !!id,
    queryFn: () =>
      api<S["DiagnosticsOut"]>(
        `${base(shop.id)}/diagnostics/${id}${queryString({ offset, limit: 20 })}`,
      ),
  });
  const saved = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "saved-diagnostics"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<S["ReportIndex"][]>(
        `${base(shop.id)}/diagnostics${queryString({ cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => (page.length === 20 ? page[page.length - 1].report_id : undefined),
  });
  const create = useMutation({
    mutationFn: (data: FormData) =>
      api<S["DiagnosticsOut"]>(`${base(shop.id)}/diagnostics`, {
        method: "POST",
        key: requestKey,
        json: {
          start: formText(data, "start"),
          end: formText(data, "end"),
          minimum_samples: Number(data.get("samples")),
          minimum_change_fen: parseFen(formText(data, "amount")),
          change_percent: Number(data.get("percent")),
          ranking: formText(data, "ranking"),
        },
      }),
    onSuccess: (row) => {
      setId(row.report_id);
      setOffset(0);
      setRequestKey(crypto.randomUUID());
      void saved.refetch();
    },
  });
  const row = query.data;
  return (
    <section className="stack">
      <h2>经营诊断</h2>
      <p className="muted">对比等长周期，保存当次结果和明细证据。结束日期不包含当天。</p>
      <form className="panel stack" onSubmit={(event) => create.mutate(form(event))}>
        <div className="two-fields">
          <Field label="SKU 排序指标">
            <select name="ranking">
              <option value="gross_profit_fen">已覆盖毛利</option>
              <option value="paid_quantity">支付件数</option>
              <option value="net_goods_fen">商品净收入</option>
              <option value="contribution_fen">实际费用部分贡献</option>
            </select>
          </Field>
          <Field label="开始日期">
            <input name="start" type="date" required defaultValue={`${today.slice(0, 8)}01`} />
          </Field>
          <Field label="结束日期（不包含）">
            <input name="end" type="date" required defaultValue={today} />
          </Field>
          <Field label="最低明细样本数">
            <input name="samples" type="number" min={1} max={10000} defaultValue={5} required />
          </Field>
          <Field label="异常变化金额（元）">
            <input name="amount" type="number" min={0} step="0.01" defaultValue={10} required />
          </Field>
          <Field label="异常变化幅度（%）">
            <input name="percent" type="number" min={1} max={1000} defaultValue={10} required />
          </Field>
        </div>
        <Button type="submit" busy={create.isPending} disabled={shop.status === "closed"}>
          生成并保存诊断
        </Button>
      </form>
      <ErrorNotice
        error={create.error || query.error || saved.error}
        retry={() => void query.refetch()}
      />
      <Field label="已保存报告">
        <select
          value={id}
          onChange={(e) => {
            setId(e.target.value);
            setOffset(0);
          }}
        >
          <option value="">选择报告</option>
          {id && !saved.data?.pages.flat().some((r) => r.report_id === id) && (
            <option value={id}>当前报告 {shortId(id)}</option>
          )}
          {saved.data?.pages.flat().map((r) => (
            <option key={r.report_id} value={r.report_id}>
              {r.start} 至 {r.end} · {dateTime(r.as_of)}
            </option>
          ))}
        </select>
      </Field>
      <More
        next={saved.hasNextPage}
        busy={saved.isFetchingNextPage}
        onClick={() => void saved.fetchNextPage()}
      />
      {query.isFetching ? (
        <Loading />
      ) : row ? (
        <>
          <p className="muted">
            截至 {dateTime(row.as_of)} · {row.currency} · {row.timezone} · {row.basis_version}
          </p>
          <div className="stat-strip">
            {[
              ["可计算部分毛利", "gross_profit_fen"],
              ["已确认费用部分贡献", "contribution_fen"],
              ["上期部分毛利", "previous_gross_profit_fen"],
            ].map(([title, key]) => (
              <div key={key}>
                <span>{title}</span>
                <strong>
                  <NullableMoney value={row.summary[key]} />
                </strong>
              </div>
            ))}
            <div>
              <span>毛利明细覆盖率</span>
              <strong>
                {row.summary.current_profit_coverage == null
                  ? "不适用"
                  : `${(row.summary.current_profit_coverage * 100).toFixed(1)}%`}
              </strong>
            </div>
          </div>
          {row.anomalies.map((x) => (
            <Notice key={x}>{x}</Notice>
          ))}
          {row.current.length ? (
            <DataTable
              headers={[
                "SKU",
                "支付件数",
                "商品净收入",
                "部分毛利",
                "实际费用部分贡献",
                "含估算费用的贡献",
                "毛利明细覆盖",
              ]}
            >
              {row.current.map((x) => (
                <tr key={x.sku_id}>
                  <td>
                    {x.title}
                    <small>{shortId(x.sku_id)}</small>
                  </td>
                  <td>{x.paid_quantity}</td>
                  <td>
                    <NullableMoney value={x.net_goods_fen} />
                  </td>
                  <td>
                    <NullableMoney value={x.gross_profit_fen} />
                  </td>
                  <td>
                    <NullableMoney value={x.contribution_fen} />
                  </td>
                  <td>
                    <NullableMoney value={x.estimated_contribution_fen} />
                  </td>
                  <td>
                    {x.profit_line_count}/{x.line_count}
                  </td>
                </tr>
              ))}
            </DataTable>
          ) : (
            <Empty title="当前页没有支付商品明细" />
          )}
          <div className="actions">
            <Button
              variant="secondary"
              disabled={!offset}
              onClick={() => setOffset(Math.max(0, offset - 20))}
            >
              上一页
            </Button>
            <Button
              variant="secondary"
              disabled={row.next_offset === null}
              onClick={() => setOffset(row.next_offset ?? offset)}
            >
              下一页
            </Button>
            <Button variant="secondary" onClick={() => setEvidenceOpen(true)}>
              查看计算证据
            </Button>
          </div>
          {row.limitations.map((x) => (
            <p className="muted" key={x}>
              {x}
            </p>
          ))}
          {!!row.decomposition?.length && (
            <>
              <h3>变化分解</h3>
              <DataTable headers={["维度", "对象 / 指标", "本期", "上期", "差额"]}>
                {row.decomposition?.map((x, index) => (
                  <tr key={index}>
                    <td>{x.dimension}</td>
                    <td>
                      {x.name}
                      <small>{x.metric}</small>
                    </td>
                    <td>
                      <NullableMoney value={x.current_fen} />
                    </td>
                    <td>
                      <NullableMoney value={x.previous_fen} />
                    </td>
                    <td>
                      <NullableMoney value={x.delta_fen} />
                    </td>
                  </tr>
                ))}
              </DataTable>
            </>
          )}
        </>
      ) : (
        !query.error && (
          <Empty title="先生成一份经营诊断">报告保留原查询快照，重新计算会生成新报告。</Empty>
        )
      )}
      <Modal open={evidenceOpen} wide title="报告计算证据" onClose={() => setEvidenceOpen(false)}>
        {evidenceOpen && <Evidence shopId={shop.id} reportId={id} />}
      </Modal>
    </section>
  );
}

function Evidence({ shopId, reportId }: { shopId: string; reportId: string }) {
  const query = useInfiniteQuery({
    queryKey: ["customer", "merchant", shopId, "evidence", reportId],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<S["EvidenceOut"]>(
        `${base(shopId)}/diagnostics/${reportId}/evidence${queryString({ cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
  const rows = query.data?.pages.flatMap((x) => x.items) ?? [];
  return (
    <div className="stack">
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable
          headers={["期间 / 订单", "SKU", "成交数量", "退款（分）", "成本冲回数量", "费用依据"]}
        >
          {rows.map((x, index) => (
            <tr key={index}>
              <td>
                {String(x.period)}
                <small>{shortId(String(x.order_id))}</small>
              </td>
              <td>{String(x.title)}</td>
              <td>{String(x.paid_quantity)}</td>
              <td>{String(x.refund_fen)}</td>
              <td>{String(x.cost_reversed_quantity)}</td>
              <td>{String(x.expense_quality)}</td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="没有计算明细" />
      )}
      <More
        next={query.hasNextPage}
        busy={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
    </div>
  );
}

export function MerchantInventory({ shop }: { shop: Shop }) {
  const search = useSearchParams();
  const [selected, setSelected] = useState<Risk>();
  const [taskId, setTaskId] = useState(search.get("task") ?? "");
  const query = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "risks"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<S["MerchantInventoryOut"]>(
        `${base(shop.id)}/inventory-risks${queryString({ offset: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => page.next_offset,
  });
  const tasks = useRows<Task>(shop.id, "restock-tasks");
  const rows = query.data?.pages.flatMap((x) => x.items) ?? [];
  return (
    <section className="stack">
      <div className="section-title">
        <h2>库存风险与补货</h2>
        <Button
          variant="secondary"
          busy={query.isFetching}
          onClick={() => {
            void query.refetch();
            void tasks.refetch();
          }}
        >
          刷新库存
        </Button>
      </div>
      <Notice>可售库存等于实物减预留减已分配。补货草稿和确认任务都不会自动增加库存。</Notice>
      <ErrorNotice error={query.error || tasks.error} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable
          headers={[
            "商品规格",
            "可售 / 实物",
            "调整后销量",
            "覆盖天数",
            "建议补货",
            "风险",
            "操作",
          ]}
        >
          {rows.map((x) => (
            <tr key={x.sku_id}>
              <td>
                {x.title}
                <small>{x.code}</small>
              </td>
              <td>
                {x.available} / {x.on_hand}
                <small>
                  预留 {x.reserved} · 分配 {x.allocated}
                </small>
              </td>
              <td>{x.adjusted_sales}</td>
              <td>{x.coverage_days?.toFixed(1) ?? "依据不足"}</td>
              <td>{x.suggested_quantity ?? "待配置"}</td>
              <td>{riskNames[x.risk]}</td>
              <td>
                <Button variant="secondary" onClick={() => setSelected(x)}>
                  规则与补货
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && <Empty title="本店暂无库存商品" />
      )}
      <More
        next={query.hasNextPage}
        busy={query.isFetchingNextPage}
        onClick={() => void query.fetchNextPage()}
      />
      <h3>补货任务</h3>
      {tasks.isPending ? (
        <Loading />
      ) : tasks.data?.pages.flat().length ? (
        <DataTable headers={["任务", "数量 / 已收", "预计到货", "状态", "操作"]}>
          {tasks.data.pages.flat().map((x) => (
            <tr key={x.id}>
              <td>
                {x.note}
                <small>{shortId(x.id)}</small>
              </td>
              <td>
                {x.quantity} / {x.received}
              </td>
              <td>{x.expected_on ?? "未确认"}</td>
              <td>{taskNames[x.status]}</td>
              <td>
                <Button variant="secondary" onClick={() => setTaskId(x.id)}>
                  核对与处理
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !tasks.error && <Empty title="暂无补货任务">从风险商品创建内部草稿，确认后跟踪收货。</Empty>
      )}
      <More
        next={tasks.hasNextPage}
        busy={tasks.isFetchingNextPage}
        onClick={() => void tasks.fetchNextPage()}
      />
      <Modal open={!!selected} title="补货规则与草稿" wide onClose={() => setSelected(undefined)}>
        {selected && (
          <RestockForm
            key={selected.sku_id}
            shop={shop}
            risk={selected}
            onDone={() => {
              setSelected(undefined);
              void query.refetch();
              void tasks.refetch();
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!taskId}
        title="补货任务"
        onClose={() => {
          setTaskId("");
          void query.refetch();
          void tasks.refetch();
        }}
      >
        {taskId && <RestockDetail shop={shop} id={taskId} />}
      </Modal>
    </section>
  );
}

function RestockForm({ shop, risk, onDone }: { shop: Shop; risk: Risk; onDone: () => void }) {
  const [requestKey] = useState(() => crypto.randomUUID());
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api(`${base(shop.id)}/restock-rules/${risk.sku_id}`, {
        method: "PUT",
        json: Object.fromEntries(
          ["window_days", "lead_days", "review_days", "safety_stock", "low_stock", "slow_sales"]
            .map((key) => [key, Number(data.get(key))])
            .concat([["expected_version", risk.rule?.expected_version ?? 0]]),
        ),
      }),
    onSuccess: onDone,
  });
  const draft = useMutation({
    mutationFn: (data: FormData) =>
      api(`${base(shop.id)}/restock-tasks`, {
        method: "POST",
        key: requestKey,
        json: {
          sku_id: risk.sku_id,
          quantity: Number(data.get("quantity")),
          expected_on: formText(data, "expected_on") || null,
          note: formText(data, "note"),
        },
      }),
    onSuccess: onDone,
  });
  return (
    <div className="stack">
      <p>
        {risk.title} · {risk.code}
      </p>
      {risk.limitations.map((x) => (
        <p className="muted" key={x}>
          {x}
        </p>
      ))}
      <form className="panel stack" onSubmit={(event) => save.mutate(form(event))}>
        <h3>设置规则</h3>
        <div className="two-fields">
          {[
            ["window_days", "观察天数", 30],
            ["lead_days", "交货天数", undefined],
            ["review_days", "复查周期（天）", undefined],
            ["safety_stock", "安全库存", undefined],
            ["low_stock", "低库存阈值", undefined],
            ["slow_sales", "滞销销量阈值", 2],
          ].map(([key, label, fallback]) => (
            <Field key={String(key)} label={String(label)}>
              <input
                name={String(key)}
                type="number"
                min={key === "window_days" ? 7 : key === "review_days" ? 1 : 0}
                max={
                  key === "window_days"
                    ? 366
                    : key === "lead_days"
                      ? 365
                      : key === "review_days"
                        ? 90
                        : 1000000
                }
                required
                defaultValue={
                  risk.rule?.[key as keyof S["RuleIn"]] ?? (fallback as number | undefined)
                }
              />
            </Field>
          ))}
        </div>
        <ErrorNotice error={save.error} />
        <Button type="submit" busy={save.isPending} disabled={shop.status !== "active"}>
          保存补货规则
        </Button>
      </form>
      <form className="panel stack" onSubmit={(event) => draft.mutate(form(event))}>
        <h3>创建补货草稿</h3>
        <Field label="建议数量">
          <input
            name="quantity"
            type="number"
            min={1}
            max={1000000}
            required
            defaultValue={risk.suggested_quantity || undefined}
          />
        </Field>
        <Field label="预计到货日期">
          <input name="expected_on" type="date" />
        </Field>
        <Field label="补货说明">
          <input name="note" required maxLength={300} defaultValue={`${risk.title}补货`} />
        </Field>
        <p>
          预计采购金额 <NullableMoney value={risk.estimated_cost_fen} />
          ，以实际确认数量和采购成本为准。
        </p>
        <ErrorNotice error={draft.error} />
        <Button type="submit" busy={draft.isPending} disabled={shop.status === "closed"}>
          保存草稿
        </Button>
      </form>
    </div>
  );
}

function RestockDetail({ shop, id }: { shop: Shop; id: string }) {
  type Request = {
    kind: string;
    key: string;
    json: {
      expected_version: number;
      quantity?: number;
      expected_on?: string;
      note?: string;
    };
  };
  const [uncertain, setUncertain] = useState<Request>();
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "task", id],
    queryFn: () => api<Task>(`${base(shop.id)}/restock-tasks/${id}`),
  });
  const row = query.data;
  const action = useMutation({
    mutationFn: ({ kind, key, json }: Request) =>
      api(`${base(shop.id)}/restock-tasks/${id}/${kind}`, {
        method: "POST",
        key,
        json,
      }),
    onError: (error, request) => {
      if (!(error instanceof ApiError) || error.status === 0 || error.status >= 500) {
        setUncertain(request);
      } else if (
        ["VERSION_CONFLICT", "RESTOCK_RANGE", "RESTOCK_QUANTITY", "RESTOCK_STATE"].includes(
          error.code,
        )
      ) {
        setUncertain(undefined);
      }
    },
    onSuccess: () => {
      setUncertain(undefined);
      return query.refetch();
    },
  });
  const submit = (kind: string, data?: FormData) => {
    if (!row || uncertain || action.isPending) return;
    action.mutate({
      kind,
      key: crypto.randomUUID(),
      json: {
        expected_version: row.version,
        ...(kind === "confirm"
          ? {
              quantity: Number(data?.get("quantity")),
              expected_on: data ? formText(data, "date") : "",
              note: data ? formText(data, "note") : "",
            }
          : kind === "receive"
            ? { quantity: Number(data?.get("quantity")) }
            : {}),
      },
    });
  };
  return (
    <div className="stack">
      <ErrorNotice error={query.error || action.error} retry={() => void query.refetch()} />
      {uncertain && (
        <Notice>
          上次操作结果尚未确认。请先重试原请求；刷新任务不会再次登记收货。
          <Button busy={action.isPending} onClick={() => action.mutate(uncertain)}>
            重试原请求并核对结果
          </Button>
        </Notice>
      )}
      {query.isPending ? (
        <Loading />
      ) : (
        row && (
          <>
            <p>
              {row.note} · {taskNames[row.status]}
            </p>
            <p>
              确认 {row.quantity} 件 · 实收 {row.received} 件 · 版本 {row.version}
            </p>
            {!["completed", "cancelled"].includes(row.status) && (
              <>
                <form
                  className="stack"
                  key={`confirm:${row.version}`}
                  onSubmit={(event) => submit("confirm", form(event))}
                >
                  <Field label="确认数量">
                    <input
                      name="quantity"
                      type="number"
                      min={Math.max(1, row.received)}
                      max={1000000}
                      defaultValue={row.quantity}
                      required
                    />
                  </Field>
                  <Field label="预计到货">
                    <input name="date" type="date" required defaultValue={row.expected_on ?? ""} />
                  </Field>
                  <Field label="确认说明">
                    <input name="note" required maxLength={300} defaultValue={row.note} />
                  </Field>
                  <Button
                    type="submit"
                    busy={action.isPending}
                    disabled={shop.status !== "active" || !!uncertain}
                  >
                    确认此数量与日期
                  </Button>
                </form>
                {row.status !== "draft" && (
                  <form
                    className="stack panel"
                    onSubmit={(event) => submit("receive", form(event))}
                  >
                    <h3>实际收货后登记入库</h3>
                    <Field label="本次实收数量">
                      <input
                        name="quantity"
                        type="number"
                        min={1}
                        max={row.quantity - row.received}
                        required
                      />
                    </Field>
                    <Button
                      type="submit"
                      busy={action.isPending}
                      disabled={shop.status !== "active" || !!uncertain}
                    >
                      确认实收并入库
                    </Button>
                  </form>
                )}
                <Button
                  variant="danger"
                  busy={action.isPending}
                  disabled={shop.status === "closed" || !!uncertain}
                  onClick={() => submit("cancel")}
                >
                  取消尚未收到的余量
                </Button>
              </>
            )}
          </>
        )
      )}
    </div>
  );
}

export function MerchantCustomers({ shop }: { shop: Shop }) {
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const list = useRows<S["SegmentOut"]>(shop.id, "segments");
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<S["CategoryOut"][]>("customer/categories"),
  });
  const makeRules = (data: FormData): S["SegmentIn"] => ({
    name: formText(data, "name"),
    window_days: Number(data.get("window")),
    min_recency_days: Number(data.get("recency")),
    min_orders: Number(data.get("orders")),
    min_spending_fen: formText(data, "spending") ? parseFen(formText(data, "spending")) : null,
    category_id: formText(data, "category") || null,
  });
  const preview = useMutation({
    mutationFn: (data: FormData) =>
      api<S["SegmentPreview"]>(`${base(shop.id)}/segments/preview`, {
        method: "POST",
        json: makeRules(data),
      }),
  });
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api<S["SegmentOut"]>(`${base(shop.id)}/segments`, {
        method: "POST",
        key: requestKey,
        json: makeRules(data),
      }),
    onSuccess: () => {
      setRequestKey(crypto.randomUUID());
      void list.refetch();
    },
  });
  const deactivate = useMutation({
    mutationFn: (id: string) =>
      api(`${base(shop.id)}/segments/${id}/deactivate`, { method: "POST" }),
    onSuccess: () => list.refetch(),
  });
  const repeat = useMutation({
    mutationFn: (data: FormData) =>
      api<S["RepeatOut"]>(`${base(shop.id)}/repeat-purchase`, {
        method: "POST",
        json: { start: formText(data, "start"), end: formText(data, "end") },
      }),
  });
  return (
    <section className="stack">
      <h2>本店客户分群</h2>
      <Notice>按本店支付行为统计，退款不抹去支付事实。保存快照固定成员，不发送营销信息。</Notice>
      <form
        className="panel stack"
        onChange={() => {
          preview.reset();
          save.reset();
        }}
        onSubmit={(event) => preview.mutate(form(event))}
      >
        <div className="two-fields">
          <Field label="分群名称">
            <input name="name" required maxLength={100} defaultValue="复购运营人群" />
          </Field>
          <Field label="统计窗口（天）">
            <input name="window" type="number" min={1} max={366} defaultValue={90} required />
          </Field>
          <Field label="距最近支付至少（天）">
            <input name="recency" type="number" min={0} max={3650} defaultValue={30} required />
          </Field>
          <Field label="窗口内至少支付次数">
            <input name="orders" type="number" min={1} max={100000} defaultValue={1} required />
          </Field>
          <Field label="窗口商品净消费至少（元，可留空）">
            <input name="spending" type="number" min={0} step="0.01" />
          </Field>
          <Field label="曾购买品类">
            <select name="category">
              <option value="">不限品类</option>
              {categories.data?.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="actions">
          <Button type="submit" busy={preview.isPending}>
            预览人群
          </Button>
          <Button
            variant="secondary"
            busy={save.isPending}
            disabled={shop.status === "closed" || !preview.data}
            onClick={(event) => {
              const f = event.currentTarget.form;
              if (f?.reportValidity()) save.mutate(new FormData(f));
            }}
          >
            保存固定快照
          </Button>
        </div>
        {preview.data && (
          <Notice>
            符合 {preview.data.members} 人 · 未知消费排除 {preview.data.excluded_unknown_spending}{" "}
            人 · 最近支付间隔中位数 {preview.data.median_recency_days ?? "不适用"} 天
          </Notice>
        )}
        {save.isSuccess && <Notice>快照已保存，可在活动草稿中选择。</Notice>}
      </form>
      <ErrorNotice
        error={
          preview.error ||
          save.error ||
          list.error ||
          deactivate.error ||
          categories.error ||
          repeat.error
        }
      />
      {list.isPending ? (
        <Loading />
      ) : list.data?.pages.flat().length ? (
        <DataTable headers={["快照", "人数", "生成时间", "资格状态", "操作"]}>
          {list.data.pages.flat().map((x) => (
            <tr key={x.id}>
              <td>{x.name}</td>
              <td>{x.member_count}</td>
              <td>{dateTime(x.as_of)}</td>
              <td>{x.active ? "可使用" : "已失效"}</td>
              <td>
                <Button
                  variant="secondary"
                  busy={deactivate.isPending}
                  disabled={!x.active || shop.status === "closed"}
                  onClick={() => deactivate.mutate(x.id)}
                >
                  停止新优惠资格
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !list.error && <Empty title="尚未保存客户快照" />
      )}
      <More
        next={list.hasNextPage}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
      <form className="panel stack" onSubmit={(event) => repeat.mutate(form(event))}>
        <h3>首购后 30 天支付复购</h3>
        <div className="two-fields">
          <Field label="首购开始日期">
            <input name="start" type="date" required />
          </Field>
          <Field label="首购结束日期（不包含）">
            <input name="end" type="date" required />
          </Field>
        </div>
        <Button type="submit" busy={repeat.isPending}>
          查询支付复购
        </Button>
        {repeat.data && (
          <Notice>
            成熟客户 {repeat.data.mature_buyers} 人 · 复购 {repeat.data.repeat_buyers} 人 ·
            观察期未满 {repeat.data.immature_buyers} 人 · 复购率{" "}
            {repeat.data.repeat_rate == null
              ? "不适用"
              : `${(repeat.data.repeat_rate * 100).toFixed(1)}%`}
            。{repeat.data.note}
          </Notice>
        )}
      </form>
    </section>
  );
}

export function MerchantPromotions({ shop }: { shop: Shop }) {
  const search = useSearchParams();
  const [id, setId] = useState(search.get("promotion") ?? "");
  const [create, setCreate] = useState(false);
  const list = useRows<Promotion>(shop.id, "promotions");
  return (
    <section className="stack">
      <div className="section-title">
        <h2>活动草稿与复盘</h2>
        <Button disabled={shop.status !== "active"} onClick={() => setCreate(true)}>
          制定活动方案
        </Button>
      </div>
      <Notice>
        一个活动对应一个 SKU 和固定活动价。批准前不会生效；正常支付后退款不恢复活动预算。
      </Notice>
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.isPending ? (
        <Loading />
      ) : list.data?.pages.flat().length ? (
        <DataTable headers={["方案", "活动价", "预算 / 预占 / 消耗", "时间", "状态", "操作"]}>
          {list.data.pages.flat().map((x) => (
            <tr key={x.id}>
              <td>
                {x.note}
                <small>
                  SKU {shortId(x.sku_id)} · 版本 {x.version}
                </small>
              </td>
              <td>
                <Money value={x.price_fen} />
              </td>
              <td>
                <Money value={x.budget_fen} />
                <small>
                  <Money value={x.reserved_fen} /> / <Money value={x.consumed_fen} />
                </small>
              </td>
              <td>
                {dateTime(x.starts_at)}
                <small>至 {dateTime(x.ends_at)}</small>
              </td>
              <td>{promotionNames[x.phase]}</td>
              <td>
                <Button variant="secondary" onClick={() => setId(x.id)}>
                  核对与复盘
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !list.error && <Empty title="暂无活动方案">选择商品、人群和预算，先生成测算草稿。</Empty>
      )}
      <More
        next={list.hasNextPage}
        busy={list.isFetchingNextPage}
        onClick={() => void list.fetchNextPage()}
      />
      <Modal open={create} wide title="单 SKU 活动方案" onClose={() => setCreate(false)}>
        {create && (
          <PromotionForm
            shop={shop}
            onDone={(value) => {
              setCreate(false);
              setId(value);
              void list.refetch();
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!id}
        wide
        title="活动版本、确认与复盘"
        onClose={() => {
          setId("");
          void list.refetch();
        }}
      >
        {id && <PromotionDetail shop={shop} id={id} />}
      </Modal>
    </section>
  );
}

function PromotionForm({ shop, onDone }: { shop: Shop; onDone: (id: string) => void }) {
  const [requestKey] = useState(() => crypto.randomUUID());
  const [search, setSearch] = useState("");
  const [skuId, setSkuId] = useState("");
  const products = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "activity-products", search],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<S["MerchantProductPage"]>(
        `${base(shop.id)}/products${queryString({ q: search, cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
  const segments = useRows<S["SegmentOut"]>(shop.id, "segments");
  const input = (data: FormData): S["PromotionIn"] => ({
    sku_id: skuId,
    segment_id: formText(data, "segment") || null,
    price_fen: parseFen(formText(data, "price")),
    budget_fen: parseFen(formText(data, "budget")),
    quantity: Number(data.get("quantity")),
    starts_at: `${formText(data, "start")}:00+08:00`,
    ends_at: `${formText(data, "end")}:00+08:00`,
    unit_expense_fen: formText(data, "expense") ? parseFen(formText(data, "expense")) : null,
    minimum_margin_fen: parseFen(formText(data, "margin")),
    note: formText(data, "note"),
  });
  const simulation = useMutation({
    mutationFn: (data: FormData) =>
      api<S["SimulationOut"]>(`${base(shop.id)}/promotions/simulate`, {
        method: "POST",
        json: input(data),
      }),
  });
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api<Promotion>(`${base(shop.id)}/promotions`, {
        method: "POST",
        key: requestKey,
        json: input(data),
      }),
    onSuccess: (row) => onDone(row.id),
  });
  return (
    <form
      className="stack"
      onChange={() => simulation.reset()}
      onSubmit={(event) => simulation.mutate(form(event))}
    >
      <Field label="搜索商品">
        <input
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setSkuId("");
          }}
          maxLength={80}
        />
      </Field>
      {products.isPending ? (
        <Loading rows={1} />
      ) : (
        <Field label="本店商品规格">
          <select required value={skuId} onChange={(e) => setSkuId(e.target.value)}>
            <option value="">选择 SKU</option>
            {products.data?.pages
              .flatMap((x) => x.items)
              .flatMap((x) =>
                x.product.skus.map((sku) => (
                  <option key={sku.id} value={sku.id} disabled={!x.product.active || !sku.active}>
                    {x.product.title} · {sku.code} · {sku.price_fen / 100} 元
                  </option>
                )),
              )}
          </select>
        </Field>
      )}
      <More
        next={products.hasNextPage}
        busy={products.isFetchingNextPage}
        onClick={() => void products.fetchNextPage()}
      />
      <div className="two-fields">
        <Field label="活动说明">
          <input name="note" required maxLength={300} defaultValue="库存运营活动" />
        </Field>
        <Field label="目标人群">
          <select name="segment">
            <option value="">公开活动</option>
            {segments.data?.pages
              .flat()
              .filter((x) => x.active)
              .map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name} · {x.member_count} 人 · {dateTime(x.as_of)}
                </option>
              ))}
          </select>
        </Field>
        <Field label="活动价（元）">
          <input name="price" type="number" min="0.01" step="0.01" required />
        </Field>
        <Field label="让利预算（元）">
          <input name="budget" type="number" min="0.01" step="0.01" required />
        </Field>
        <Field label="活动限量（件）">
          <input name="quantity" type="number" min={1} max={1000000} required />
        </Field>
        <Field label="最低单件毛利或估算贡献（元）">
          <input name="margin" type="number" min={0} step="0.01" defaultValue={0} required />
        </Field>
        <Field label="单位费用假设（元，可留空）" hint="留空按商品毛利约束；填写后按估算贡献约束。">
          <input name="expense" type="number" min={0} step="0.01" />
        </Field>
        <Field label="开始时间（北京时间）">
          <input name="start" type="datetime-local" required />
        </Field>
        <Field label="结束时间（北京时间）">
          <input name="end" type="datetime-local" required />
        </Field>
      </div>
      <More
        next={segments.hasNextPage}
        busy={segments.isFetchingNextPage}
        onClick={() => void segments.fetchNextPage()}
      />
      <ErrorNotice error={products.error || segments.error || simulation.error || save.error} />
      {simulation.data && (
        <div className="panel stack">
          <p>
            {simulation.data.margin_basis} / 件：
            <NullableMoney value={simulation.data.unit_margin_fen} />
            ；预算与限量最多支持 {simulation.data.max_units} 件。
          </p>
          {simulation.data.blockers.map((x) => (
            <Notice key={x}>{x}</Notice>
          ))}
          <DataTable headers={["假设销量", "让利金额", "假设毛利或贡献"]}>
            {simulation.data.scenarios.map((x, index) => (
              <tr key={index}>
                <td>{x.quantity}</td>
                <td>
                  <NullableMoney value={x.discount_fen} />
                </td>
                <td>
                  <NullableMoney value={x.margin_fen} />
                </td>
              </tr>
            ))}
          </DataTable>
          <p className="muted">销量为情景假设，实际成本与费用可能变化。</p>
        </div>
      )}
      <div className="actions">
        <Button type="submit" busy={simulation.isPending} disabled={!skuId}>
          测算方案
        </Button>
        <Button
          variant="secondary"
          busy={save.isPending}
          disabled={!simulation.data || !!simulation.data.blockers.length}
          onClick={(event) => {
            const f = event.currentTarget.form;
            if (f?.reportValidity()) save.mutate(new FormData(f));
          }}
        >
          保存待确认草稿
        </Button>
      </div>
    </form>
  );
}

function PromotionDetail({ shop, id }: { shop: Shop; id: string }) {
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "promotion-report", id],
    queryFn: () => api<S["PromotionReportOut"]>(`${base(shop.id)}/promotions/${id}/report`),
  });
  const capabilities = useQuery({
    queryKey: ["customer", "merchant", shop.id, "capabilities"],
    queryFn: () => api<{ promotions_enabled: boolean }>(`${base(shop.id)}/operations/capabilities`),
  });
  const validate = useQuery({
    queryKey: ["customer", "merchant", shop.id, "promotion-validation", id],
    queryFn: () => api<S["SimulationOut"]>(`${base(shop.id)}/promotions/${id}/validation`),
  });
  const row = query.data?.promotion;
  const action = useMutation({
    mutationFn: (kind: string) =>
      api(`${base(shop.id)}/promotions/${id}/${kind}`, {
        method: "POST",
        key: `${id}:${kind}:${row?.version}`,
        json: { expected_version: row?.version },
      }),
    onSuccess: () => {
      void query.refetch();
      void validate.refetch();
    },
  });
  return (
    <div className="stack">
      <ErrorNotice
        error={query.error || capabilities.error || validate.error || action.error}
        retry={() => void query.refetch()}
      />
      {query.isPending ? (
        <Loading />
      ) : (
        row && (
          <>
            <h3>
              {row.note} · {promotionNames[row.phase]}
            </h3>
            <p>
              SKU {shortId(row.sku_id)} · 固定版本 {row.version}
            </p>
            <DataTable headers={["字段", "确认内容"]}>
              {[
                ["普通参考价", <Money key="reference" value={row.reference_price_fen} />],
                ["固定活动价", <Money key="price" value={row.price_fen} />],
                ["成本依据", <NullableMoney key="cost" value={row.cost_fen} />],
                [
                  "单位费用假设",
                  row.unit_expense_fen == null ? (
                    "未填写，按毛利约束"
                  ) : (
                    <Money key="expense" value={row.unit_expense_fen} />
                  ),
                ],
                ["最低单件约束", <Money key="margin" value={row.minimum_margin_fen} />],
                ["总让利预算", <Money key="budget" value={row.budget_fen} />],
                ["总件数上限", `${row.quantity} 件`],
                ["人群快照", row.segment_id ?? "公开活动"],
                ["活动时间", `${dateTime(row.starts_at)} 至 ${dateTime(row.ends_at)}`],
              ].map(([label, value]) => (
                <tr key={String(label)}>
                  <th scope="row">{label}</th>
                  <td>{value as ReactNode}</td>
                </tr>
              ))}
            </DataTable>
            {validate.data?.blockers.map((x) => (
              <Notice key={x}>{x}</Notice>
            ))}
            {!capabilities.data?.promotions_enabled && (
              <Notice>平台尚未启用商家活动发布。草稿可保存，启用后需重新核验。</Notice>
            )}
            {row.status === "draft" && (
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault();
                  action.mutate("publish");
                }}
              >
                <label className="actions">
                  <input type="checkbox" required />
                  我已核对以上版本、预算、商品、人群和费用假设，确认发布。
                </label>
                <Button
                  type="submit"
                  busy={action.isPending}
                  disabled={
                    shop.status !== "active" ||
                    !capabilities.data?.promotions_enabled ||
                    !validate.data ||
                    !!validate.data.blockers.length
                  }
                >
                  确认此版本并发布
                </Button>
              </form>
            )}
            {row.status === "published" && (
              <form
                className="stack"
                onSubmit={(event) => {
                  event.preventDefault();
                  action.mutate("stop");
                }}
              >
                <label className="actions">
                  <input type="checkbox" required />
                  确认停止新订单优惠，已有订单继续原约定。
                </label>
                <Button
                  variant="danger"
                  type="submit"
                  busy={action.isPending}
                  disabled={shop.status === "closed"}
                >
                  停用未来优惠
                </Button>
              </form>
            )}
            {row.approved_at && (
              <p className="muted">
                批准时间 {dateTime(row.approved_at)} · 批准人 {shortId(row.approved_by ?? "")} ·
                批准摘要 {row.approval_digest}
              </p>
            )}
            <h3>执行与复盘</h3>
            <p>
              预占 <Money value={row.reserved_fen} /> · 累计消耗 <Money value={row.consumed_fen} />{" "}
              · 商品退款 <Money value={query.data?.refunded_goods_fen ?? 0} /> · 净让利{" "}
              <Money value={query.data?.net_discount_fen ?? 0} />
            </p>
            <p>
              预占 {query.data?.reserved_quantity} 件 · 正常消耗 {query.data?.consumed_quantity} 件
              · 已释放 {query.data?.released_quantity} 件
            </p>
            {query.data?.performance.map((x) => (
              <p key={x.sku_id}>
                商品净收入 <NullableMoney value={x.net_goods_fen} /> · 可计算部分毛利{" "}
                <NullableMoney value={x.gross_profit_fen} /> · 实际费用部分贡献{" "}
                <NullableMoney value={x.contribution_fen} /> · 毛利覆盖 {x.profit_line_count}/
                {x.line_count}
              </p>
            ))}
            {query.data?.limitations.map((x) => (
              <p className="muted" key={x}>
                {x}
              </p>
            ))}
            <h3>批准与执行记录</h3>
            {query.data?.executions.map((x) => (
              <p className="muted" key={x.id}>
                {dateTime(x.created_at)} ·{" "}
                {x.action === "promotion.published"
                  ? "已批准发布"
                  : x.action === "promotion.publish_failed"
                    ? "发布未完成"
                    : x.action === "promotion.stopped"
                      ? "已停用"
                      : "草稿已保存"}
                <br />
                {x.detail}
              </p>
            ))}
          </>
        )
      )}
    </div>
  );
}

export function OrderExpenses({ shop, orderId }: { shop: Shop; orderId: string }) {
  const [category, setCategory] = useState<S["ExpenseIn"]["category"]>("delivery");
  const [quality, setQuality] = useState<S["ExpenseIn"]["quality"]>("actual");
  const [requestKey, setRequestKey] = useState(() => crypto.randomUUID());
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "expenses", orderId],
    queryFn: () => api<S["ExpenseOut"][]>(`${base(shop.id)}/orders/${orderId}/expenses`),
  });
  const prior = query.data?.find((x) => x.category === category);
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api(`${base(shop.id)}/orders/${orderId}/expenses`, {
        method: "POST",
        key: requestKey,
        json: {
          category,
          quality,
          amount_fen: quality === "unknown" ? null : parseFen(formText(data, "amount")),
          complete: quality !== "unknown" && data.get("complete") === "on",
          expected_revision: prior?.revision ?? 0,
          note: formText(data, "note"),
        },
      }),
    onSuccess: () => {
      setRequestKey(crypto.randomUUID());
      void query.refetch();
    },
  });
  return (
    <form
      className="panel stack"
      key={`${category}:${prior?.revision ?? 0}`}
      onSubmit={(event) => save.mutate(form(event))}
    >
      <h3>订单变动费用</h3>
      <p className="muted">
        配送、包装、支付费用均明确登记完整后才参与贡献统计。修订追加留痕，不改写旧记录。
      </p>
      <Field label="费用类别">
        <select
          value={category}
          onChange={(e) => setCategory(e.target.value as S["ExpenseIn"]["category"])}
        >
          <option value="delivery">发货配送费</option>
          <option value="packaging">包装费</option>
          <option value="payment">支付手续费</option>
        </select>
      </Field>
      <Field label="数据依据">
        <select
          value={quality}
          onChange={(e) => setQuality(e.target.value as S["ExpenseIn"]["quality"])}
        >
          <option value="actual">已确认</option>
          <option value="estimated">估算</option>
          <option value="unknown">未知</option>
        </select>
      </Field>
      {quality !== "unknown" && (
        <Field label="该类费用合计（元，零费用填 0）">
          <input
            name="amount"
            type="number"
            min={0}
            max={1000000}
            step="0.01"
            required
            defaultValue={prior?.amount_fen == null ? "" : prior.amount_fen / 100}
          />
        </Field>
      )}
      <Field label="登记或修订说明">
        <input name="note" required maxLength={300} />
      </Field>
      <label className="actions">
        <input name="complete" type="checkbox" disabled={quality === "unknown"} />
        已完整登记这一类费用
      </label>
      <ErrorNotice error={query.error || save.error} />
      {save.isSuccess && <Notice>费用修订已记录。重新生成诊断后查看最新贡献。</Notice>}
      <Button
        type="submit"
        busy={save.isPending}
        disabled={query.isPending || !!query.error || shop.status === "closed"}
      >
        保存费用修订
      </Button>
    </form>
  );
}
