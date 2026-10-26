"use client";

import Link from "next/link";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, ArrowRight, DownloadSimple, Plus } from "@phosphor-icons/react";
import { api, queryString } from "@/lib/api";
import { dateTime, formText, parseFen, shortId } from "@/lib/format";
import {
  permissions,
  type Audit,
  type DailyReport,
  type ExportJob,
  type Failure,
  type Operation,
  type Ranking,
  type Reconciliation,
  type ReconciliationCase,
  type Refund,
  type ShippingRule,
  type ShippingRulePage,
  type Staff,
} from "@/lib/types";
import { useAuth, useVisible } from "@/components/providers";
import { adminLinks } from "@/components/shell";
import {
  AuthGate,
  Button,
  DataTable,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Money,
  Notice,
  PageTitle,
  Status,
} from "@/components/ui";

export function AdminHome() {
  return (
    <AuthGate scope="admin">
      <AdminRedirect />
    </AuthGate>
  );
}
function AdminRedirect() {
  const auth = useAuth();
  const router = useRouter();
  const first =
    adminLinks.find((link) => link.permission && auth.admin?.permissions.includes(link.permission))
      ?.href ?? "/admin/account";
  useEffect(() => {
    router.replace(first);
  }, [first, router]);
  return <Loading />;
}

function useRecords<T extends { id: string }>(
  resource: string,
  filters: Record<string, string> = {},
) {
  return useInfiniteQuery({
    queryKey: ["admin", resource, filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<T[]>(`admin/${resource}${queryString({ ...filters, cursor: pageParam, limit: 50 })}`),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].id : undefined),
  });
}

export function RefundsScreen() {
  return (
    <AuthGate scope="admin" permission="aftersales">
      <Refunds />
    </AuthGate>
  );
}
function Refunds() {
  const [status, setStatus] = useState("");
  const auth = useAuth();
  const query = useRecords<Refund>("refunds", { status });
  const rows = query.data?.pages.flat() ?? [];
  return (
    <>
      <PageTitle
        title="退款记录"
        description="展示退款的资金处理结果；申请与验收记录见售后申请。"
      />
      <div className="toolbar">
        <Field label="退款状态">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            <option value="pending">处理中</option>
            <option value="unknown">核对中</option>
            <option value="succeeded">已成功</option>
            <option value="manual_review">人工处理</option>
          </select>
        </Field>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          <ArrowClockwise />
          刷新
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable headers={["退款编号", "订单", "金额", "状态", "创建 / 确认"]}>
          {rows.map((row) => (
            <tr key={row.id}>
              <td className="break-all">{row.id}</td>
              <td>
                {auth.admin?.permissions.includes("fulfillment") ? (
                  <Link href={`/admin/orders/${row.order_id}`}>{shortId(row.order_id)}</Link>
                ) : (
                  shortId(row.order_id)
                )}
              </td>
              <td>
                <Money value={row.amount_fen} />
              </td>
              <td>
                <Status value={row.status} />
              </td>
              <td>
                {dateTime(row.created_at)}
                <small>{dateTime(row.confirmed_at)}</small>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="暂无退款记录" />
      )}
      {query.hasNextPage && (
        <Button variant="secondary" onClick={() => void query.fetchNextPage()}>
          加载更多
        </Button>
      )}
    </>
  );
}

export function ReportsScreen() {
  return (
    <AuthGate scope="admin" permission="audit">
      <Reports />
    </AuthGate>
  );
}
function Reports() {
  const today = new Intl.DateTimeFormat("en-CA", {
    timeZone: "UTC",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
  const [range, setRange] = useState({ start: today, end: today });
  const [exportId, setExportId] = useState<string>();
  const [started, setStarted] = useState(0);
  const visible = useVisible();
  const report = useQuery({
    queryKey: ["admin", "reports", range],
    queryFn: () => api<DailyReport[]>(`admin/reports/daily${queryString(range)}`),
  });
  const ranking = useQuery({
    queryKey: ["admin", "ranking", range],
    queryFn: () => api<Ranking[]>(`admin/reports/ranking${queryString(range)}`),
  });
  const createExport = useMutation({
    mutationFn: () => api<ExportJob>("admin/exports", { method: "POST", json: range }),
    onSuccess: (job) => {
      setExportId(job.id);
      setStarted(Date.now());
    },
  });
  const job = useQuery({
    queryKey: ["admin", "export", exportId],
    queryFn: () => api<ExportJob>(`admin/exports/${exportId}`),
    enabled: !!exportId,
    refetchInterval: (query) =>
      visible &&
      Date.now() - started < 120000 &&
      !["succeeded", "done", "failed"].includes(query.state.data?.status ?? "")
        ? 5000
        : false,
  });
  return (
    <>
      <PageTitle
        title="经营报表"
        description="按 UTC 自然日统计已确认收款和退款，与导出文件一致；对应北京时间当日 08:00 至次日 08:00。净收款不等同于利润。"
        action={
          <Button
            variant="secondary"
            busy={createExport.isPending}
            disabled={!report.data}
            onClick={() => createExport.mutate()}
          >
            <DownloadSimple />
            导出资金日报 CSV
          </Button>
        }
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setRange({ start: formText(data, "start"), end: formText(data, "end") });
        }}
      >
        <Field label="开始日期">
          <input name="start" type="date" defaultValue={range.start} required />
        </Field>
        <Field label="结束日期">
          <input name="end" type="date" defaultValue={range.end} required />
        </Field>
        <Button type="submit" variant="secondary">
          查询报表
        </Button>
      </form>
      <ErrorNotice
        error={report.error || ranking.error || createExport.error || job.error}
        retry={() => {
          void report.refetch();
          void ranking.refetch();
        }}
      />
      {exportId && (
        <Notice>
          <div>
            导出任务 {shortId(exportId)}：{job.data ? <Status value={job.data.status} /> : "查询中"}
            {job.data?.status === "done" ? (
              <a
                className="button secondary"
                href={`/api/commerce/admin/exports/${exportId}/download`}
                download
              >
                <DownloadSimple />
                下载 CSV
              </a>
            ) : (
              <Button variant="ghost" onClick={() => void job.refetch()}>
                刷新导出结果
              </Button>
            )}
            <small>自动查询最多持续 2 分钟。</small>
          </div>
        </Notice>
      )}
      <section className="panel">
        <h2>每日资金结果</h2>
        {report.isPending ? (
          <Loading />
        ) : report.data?.length ? (
          <DataTable headers={["日期（UTC）", "确认收款", "确认退款", "净收款", "付款订单数"]}>
            {report.data.map((row) => (
              <tr key={row.day}>
                <td>{row.day}</td>
                <td>
                  <Money value={row.sales_fen} />
                </td>
                <td>
                  <Money value={row.refunds_fen} />
                </td>
                <td>
                  <Money value={row.net_receipts_fen} />
                </td>
                <td>{row.paid_orders}</td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="此日期范围没有资金记录" />
        )}
      </section>
      <section className="panel spaced">
        <h2>商品规格排行</h2>
        <p className="muted">按已支付商品数量统计，最多显示 20 个规格。</p>
        {ranking.isPending ? (
          <Loading />
        ) : ranking.data?.length ? (
          <DataTable headers={["规格编号（SKU ID）", "已支付数量", "商品金额"]}>
            {ranking.data.map((row) => (
              <tr key={row.sku_id}>
                <td className="break-all">{row.sku_id}</td>
                <td>{row.paid_quantity}</td>
                <td>
                  <Money value={row.sales_fen} />
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="此范围暂无商品排行" />
        )}
      </section>
    </>
  );
}

export function ReconciliationsScreen() {
  return (
    <AuthGate scope="admin" permission="audit">
      <Reconciliations />
    </AuthGate>
  );
}
function Reconciliations() {
  const [status, setStatus] = useState("open");
  const [selected, setSelected] = useState<ReconciliationCase>();
  const [confirm, setConfirm] = useState(false);
  const runs = useRecords<Reconciliation>("reconciliations");
  const cases = useRecords<ReconciliationCase>("reconciliation-cases", { status });
  const create = useMutation({
    mutationFn: () => api<Reconciliation>("admin/reconciliations", { method: "POST" }),
    onSuccess: () => {
      setConfirm(false);
      void runs.refetch();
    },
  });
  const recheck = useMutation({
    mutationFn: (data: FormData) =>
      api<ReconciliationCase>(`admin/reconciliation-cases/${selected?.id}/recheck`, {
        method: "POST",
        json: { note: formText(data, "note") },
      }),
    onSuccess: () => {
      setSelected(undefined);
      void cases.refetch();
    },
  });
  return (
    <>
      <PageTitle
        title="资金对账"
        description="核对提供方流水与本地资金记录，差异保留证据和复核说明。"
        action={<Button onClick={() => setConfirm(true)}>创建对账任务</Button>}
      />
      <div className="toolbar">
        <Field label="差异状态">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="open">待处理</option>
            <option value="resolved">已解决</option>
          </select>
        </Field>
        <Button
          variant="secondary"
          onClick={() => {
            void runs.refetch();
            void cases.refetch();
          }}
        >
          <ArrowClockwise />
          刷新
        </Button>
      </div>
      <ErrorNotice error={runs.error || cases.error} />
      <section className="panel">
        <h2>资金差异</h2>
        {cases.isPending ? (
          <Loading />
        ) : cases.data?.pages.flat().length ? (
          <DataTable headers={["差异类型", "关联订单", "原因", "状态", "操作"]}>
            {cases.data.pages.flat().map((row) => (
              <tr key={row.id}>
                <td>{row.kind}</td>
                <td>{row.order_id ? shortId(row.order_id) : "—"}</td>
                <td>{row.reason}</td>
                <td>
                  <Status value={row.status} />
                </td>
                <td>
                  <Button variant="ghost" onClick={() => setSelected(row)}>
                    证据与复核
                  </Button>
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="暂无此状态的资金差异" />
        )}
        {cases.hasNextPage && (
          <Button variant="secondary" onClick={() => void cases.fetchNextPage()}>
            更多差异
          </Button>
        )}
      </section>
      <section className="panel spaced">
        <h2>对账任务</h2>
        {runs.isPending ? (
          <Loading />
        ) : runs.data?.pages.flat().length ? (
          <DataTable headers={["任务", "状态", "已核对流水", "创建 / 完成"]}>
            {runs.data.pages.flat().map((row) => (
              <tr key={row.id}>
                <td>{shortId(row.id)}</td>
                <td>
                  <Status value={row.status} />
                </td>
                <td>{row.entries_checked}</td>
                <td>
                  {dateTime(row.created_at)}
                  <small>{dateTime(row.finished_at)}</small>
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="尚无对账任务" />
        )}
        {runs.hasNextPage && (
          <Button variant="secondary" onClick={() => void runs.fetchNextPage()}>
            更多任务
          </Button>
        )}
      </section>
      <Modal open={confirm} title="创建资金对账任务" onClose={() => setConfirm(false)}>
        <p>系统将读取提供方流水并与本地记录核对。已发现的差异会进入人工复核列表。</p>
        <ErrorNotice error={create.error} />
        <Button busy={create.isPending} onClick={() => create.mutate()}>
          确认创建
        </Button>
      </Modal>
      <Modal open={!!selected} title="核对差异证据" onClose={() => setSelected(undefined)} wide>
        {selected && (
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              recheck.mutate(new FormData(event.currentTarget));
            }}
          >
            <p>
              {selected.kind} · {selected.reason}
            </p>
            <pre>{JSON.stringify(selected.evidence, null, 2)}</pre>
            <p>最近核对：{dateTime(selected.checked_at)}</p>
            {selected.resolution_note && <p>处理说明：{selected.resolution_note}</p>}
            <Field label="本次复核说明">
              <textarea name="note" required maxLength={500} rows={3} />
            </Field>
            <ErrorNotice error={recheck.error} />
            <Button type="submit" busy={recheck.isPending}>
              提交复核并重新核对
            </Button>
          </form>
        )}
      </Modal>
    </>
  );
}

export function OperationsScreen() {
  return (
    <AuthGate scope="admin" permission="audit">
      <Operations />
    </AuthGate>
  );
}
function Operations() {
  const [status, setStatus] = useState("manual_review");
  const [selected, setSelected] = useState<Operation>();
  const query = useQuery({
    queryKey: ["admin", "operations", status],
    queryFn: () => api<Operation[]>(`admin/operations${queryString({ status, limit: 100 })}`),
  });
  const failures = useQuery({
    queryKey: ["admin", "message-failures"],
    queryFn: () => api<Failure[]>("admin/message-failures?limit=100"),
  });
  const replay = useMutation({
    mutationFn: (data: FormData) =>
      api(`admin/operations/${selected?.id}/replay`, {
        method: "POST",
        json: { reason: formText(data, "reason") },
      }),
    onSuccess: () => {
      setSelected(undefined);
      void query.refetch();
    },
  });
  return (
    <>
      <PageTitle
        title="异常任务"
        description="受控重放前先核对错误原因和关联资源。列表最多显示 100 条。"
      />
      <div className="toolbar">
        <Field label="任务状态">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="manual_review">需人工处理</option>
            <option value="pending">待处理</option>
            <option value="running">运行中</option>
            <option value="done">已完成</option>
          </select>
        </Field>
        <Button
          variant="secondary"
          onClick={() => {
            void query.refetch();
            void failures.refetch();
          }}
        >
          刷新
        </Button>
      </div>
      <ErrorNotice error={query.error || failures.error} />
      <section className="panel">
        <h2>业务任务</h2>
        {query.isPending ? (
          <Loading />
        ) : query.data?.length ? (
          <DataTable headers={["任务 / 资源", "状态", "尝试次数", "最后错误", "操作"]}>
            {query.data.map((row) => (
              <tr key={row.id}>
                <td>
                  {row.kind}
                  <small className="break-all">{row.resource_id}</small>
                </td>
                <td>
                  <Status value={row.status} />
                </td>
                <td>{row.attempts}</td>
                <td>
                  {row.last_error ?? "—"}
                  <small>下次尝试 {dateTime(row.next_attempt_at)}</small>
                </td>
                <td>
                  <Button
                    variant="secondary"
                    disabled={row.status !== "manual_review"}
                    onClick={() => setSelected(row)}
                  >
                    受控重放
                  </Button>
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="没有此状态的任务" />
        )}
      </section>
      <section className="panel spaced">
        <h2>隔离消息</h2>
        {failures.isPending ? (
          <Loading />
        ) : failures.data?.length ? (
          <DataTable headers={["时间", "消息编号", "隔离原因"]}>
            {failures.data.map((row) => (
              <tr key={row.id}>
                <td>{dateTime(row.created_at)}</td>
                <td className="break-all">{row.message_id ?? "未知"}</td>
                <td>{row.reason}</td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="暂无隔离消息" />
        )}
      </section>
      <Modal open={!!selected} title="确认受控重放" onClose={() => setSelected(undefined)}>
        {selected && (
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              replay.mutate(new FormData(event.currentTarget));
            }}
          >
            <p>
              {selected.kind} · {selected.id}
            </p>
            <p>最近错误：{selected.last_error}</p>
            <Field label="重放原因">
              <textarea name="reason" required maxLength={500} rows={4} />
            </Field>
            <Notice>重放会再次执行此任务，请先确认错误原因已处理。系统保留原业务幂等标识。</Notice>
            <ErrorNotice error={replay.error} />
            <Button type="submit" busy={replay.isPending}>
              确认重放
            </Button>
          </form>
        )}
      </Modal>
    </>
  );
}

export function AuditsScreen() {
  return (
    <AuthGate scope="admin" permission="audit">
      <Audits />
    </AuthGate>
  );
}
function Audits() {
  const query = useRecords<Audit>("audits");
  const rows = query.data?.pages.flat() ?? [];
  return (
    <>
      <PageTitle
        title="操作审计"
        description="查看操作者、关联资源与操作说明。"
        action={
          <Button variant="secondary" onClick={() => void query.refetch()}>
            <ArrowClockwise />
            刷新
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable headers={["时间", "操作", "操作者", "关联资源", "说明"]}>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{dateTime(row.created_at)}</td>
              <td>{row.action}</td>
              <td className="break-all">{row.actor_id ?? "系统"}</td>
              <td className="break-all">{row.resource_id}</td>
              <td>{row.detail}</td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="暂无审计记录" />
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多审计
        </Button>
      )}
    </>
  );
}

export function ShippingScreen() {
  return (
    <AuthGate scope="admin" permission="catalog">
      <Shipping />
    </AuthGate>
  );
}
function Shipping() {
  const [selected, setSelected] = useState<ShippingRule | "new">();
  const query = useInfiniteQuery({
    queryKey: ["admin", "shipping-rules"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<ShippingRulePage>(`admin/shipping-rules${queryString({ cursor: pageParam, limit: 50 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        title="配送与运费"
        description="地区名称与收货地址精确匹配；星号 * 表示默认规则。修改后只影响新报价。"
        action={
          <Button onClick={() => setSelected("new")}>
            <Plus />
            新增地区规则
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable headers={["配送地区", "运费", "免运费门槛", "版本", "操作"]}>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.region === "*" ? "默认地区（*）" : row.region}</td>
              <td>
                <Money value={row.fee_fen} />
              </td>
              <td>
                {row.free_above_fen !== null && row.free_above_fen !== undefined ? (
                  <Money value={row.free_above_fen} />
                ) : (
                  "不设门槛"
                )}
              </td>
              <td>{row.version}</td>
              <td>
                <Button variant="ghost" onClick={() => setSelected(row)}>
                  编辑
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="尚未设置专属配送规则">系统将使用后端默认运费配置。</Empty>
      )}
      {query.hasNextPage && (
        <Button variant="secondary" onClick={() => void query.fetchNextPage()}>
          加载更多
        </Button>
      )}
      <Modal open={!!selected} title="保存运费规则" onClose={() => setSelected(undefined)}>
        {selected && (
          <ShippingForm
            key={selected === "new" ? "new" : selected.id}
            rule={selected === "new" ? undefined : selected}
            onSaved={() => {
              setSelected(undefined);
              void query.refetch();
            }}
          />
        )}
      </Modal>
    </>
  );
}
function ShippingForm({ rule, onSaved }: { rule?: ShippingRule; onSaved: () => void }) {
  const mutation = useMutation({
    mutationFn: (data: FormData) =>
      api("admin/shipping-rules", {
        method: "PUT",
        json: {
          region: formText(data, "region"),
          fee_fen: parseFen(formText(data, "fee")),
          free_above_fen: formText(data, "free") ? parseFen(formText(data, "free")) : null,
        },
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate(new FormData(event.currentTarget));
      }}
    >
      <Field label="地区名称" hint="使用 * 配置默认规则。相同地区会更新已有规则。">
        <input
          name="region"
          defaultValue={rule?.region}
          required
          maxLength={120}
          readOnly={!!rule}
        />
      </Field>
      <Field label="运费（元）">
        <input
          name="fee"
          defaultValue={rule ? String(rule.fee_fen / 100) : ""}
          inputMode="decimal"
          required
        />
      </Field>
      <Field label="免运费商品金额门槛（元）" hint="留空表示不设置免运费门槛。">
        <input
          name="free"
          defaultValue={
            rule?.free_above_fen !== null && rule?.free_above_fen !== undefined
              ? String(rule.free_above_fen / 100)
              : ""
          }
          inputMode="decimal"
        />
      </Field>
      <ErrorNotice error={mutation.error} />
      <Button type="submit" busy={mutation.isPending}>
        确认保存规则
      </Button>
    </form>
  );
}

export function StaffScreen() {
  return (
    <AuthGate scope="admin" permission="identity">
      <StaffList />
    </AuthGate>
  );
}
function StaffList() {
  const [selected, setSelected] = useState<Staff | "new">();
  const query = useRecords<Staff>("staff");
  const client = useQueryClient();
  const rows = query.data?.pages.flat() ?? [];
  return (
    <>
      <PageTitle
        title="员工与权限"
        description="权限变更和停用均要求说明原因，并记录审计。"
        action={
          <Button onClick={() => setSelected("new")}>
            <Plus />
            新增员工
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable headers={["邮箱", "权限", "账号状态", "操作"]}>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.email}</td>
              <td>
                {row.permissions
                  .map((name) => permissions[name as keyof typeof permissions] ?? name)
                  .join("、")}
              </td>
              <td>{row.active ? "启用" : "停用"}</td>
              <td>
                <Button variant="ghost" onClick={() => setSelected(row)}>
                  调整权限
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="暂无员工" />
      )}
      {query.hasNextPage && (
        <Button variant="secondary" onClick={() => void query.fetchNextPage()}>
          加载更多员工
        </Button>
      )}
      <Modal
        open={!!selected}
        title={selected === "new" ? "创建员工账号" : "调整员工权限"}
        onClose={() => setSelected(undefined)}
      >
        {selected && (
          <StaffForm
            key={selected === "new" ? "new" : selected.id}
            staff={selected === "new" ? undefined : selected}
            onSaved={() => {
              setSelected(undefined);
              void query.refetch();
              void client.invalidateQueries({ queryKey: ["admin", "identity"] });
            }}
          />
        )}
      </Modal>
    </>
  );
}
function StaffForm({ staff, onSaved }: { staff?: Staff; onSaved: () => void }) {
  const mutation = useMutation({
    mutationFn: (data: FormData) =>
      api(`admin/staff${staff ? `/${staff.id}` : ""}`, {
        method: staff ? "PUT" : "POST",
        json: staff
          ? {
              active: data.get("active") === "on",
              permissions: data.getAll("permissions"),
              reason: formText(data, "reason"),
            }
          : {
              email: formText(data, "email"),
              password: String(data.get("password") ?? ""),
              permissions: data.getAll("permissions"),
            },
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        mutation.mutate(new FormData(event.currentTarget));
      }}
    >
      {staff ? (
        <>
          <strong>{staff.email}</strong>
          <label className="checkbox">
            <input name="active" type="checkbox" defaultChecked={staff.active} />
            启用此员工账号
          </label>
        </>
      ) : (
        <>
          <Field label="员工邮箱">
            <input name="email" type="email" required />
          </Field>
          <Field label="初始密码" hint="12 至 128 位，员工登录后可自行更改。">
            <input
              name="password"
              type="password"
              required
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
            />
          </Field>
        </>
      )}
      <fieldset className="permission-options">
        <legend>授予权限（至少一项）</legend>
        {Object.entries(permissions).map(([name, label]) => (
          <label className="checkbox" key={name}>
            <input
              name="permissions"
              value={name}
              type="checkbox"
              defaultChecked={staff?.permissions.includes(name)}
            />
            {label}
          </label>
        ))}
      </fieldset>
      {staff && (
        <Field label="变更原因">
          <textarea name="reason" required maxLength={500} rows={3} />
        </Field>
      )}
      <ErrorNotice error={mutation.error} />
      <Button type="submit" busy={mutation.isPending}>
        确认保存员工权限 <ArrowRight />
      </Button>
    </form>
  );
}
