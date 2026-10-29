"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ArrowRight, ChatCircle, Plus, ThumbsDown, ThumbsUp } from "@phosphor-icons/react";
import { useVisible } from "@/components/providers";
import { AccountNavigation } from "@/components/shell";
import {
  AuthGate,
  Button,
  DataTable,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Notice,
  PageTitle,
} from "@/components/ui";
import { api, ApiError, queryString } from "@/lib/api";
import { dateTime } from "@/lib/format";
import type { components } from "@/lib/api.generated";

type Schemas = components["schemas"];
type Conversation = Schemas["ConversationOut"];
type Run = Schemas["SupportRunOut"];
type Memory = Schemas["MemoryOut"];
type MemoryProfile = Schemas["MemoryProfile"];
type Settings = Schemas["SupportSettingsIn"];
type Insights = Schemas["InsightsOut"];
type Audience = Schemas["AudiencePage"];
type PendingMessage = { request_id: string; text: string; conversation_id?: string };

const kinds: Record<Memory["kind"], string> = {
  category: "商品品类",
  brand: "品牌",
  budget: "预算",
  attribute: "规格与用途",
  service: "服务习惯",
};
const features: Record<string, string> = {
  product_search: "查找商品",
  product_details: "了解商品",
  recommendations: "商品推荐",
  orders: "查询订单",
  shipping: "查询物流",
  aftersales: "售后咨询",
  account: "账户资料",
  cart: "购物车",
};

export function SupportScreen() {
  return (
    <AuthGate>
      <SupportChat />
    </AuthGate>
  );
}

function SupportChat() {
  const client = useQueryClient();
  const visible = useVisible();
  const [selected, setSelected] = useState<string>();
  const [text, setText] = useState("");
  const [pending, setPending] = useState<PendingMessage>();
  const conversations = useQuery({
    queryKey: ["customer", "support", "conversations"],
    queryFn: () => api<Conversation[]>("customer/support/conversations"),
  });
  const active = selected ?? conversations.data?.[0]?.id;
  const history = useQuery({
    queryKey: ["customer", "support", "history", active],
    queryFn: () => api<Run[]>(`customer/support/conversations/${active}/messages`),
    enabled: !!active,
    refetchInterval: (query) =>
      visible && query.state.data?.some((run) => run.status === "running") ? 2000 : false,
  });
  const create = useMutation({
    mutationFn: () =>
      api<Conversation>("customer/support/conversations", { method: "POST", json: {} }),
    onSuccess: async (conversation) => {
      setSelected(conversation.id);
      setPending(undefined);
      setText("");
      await conversations.refetch();
    },
  });
  const send = useMutation({
    mutationFn: async (message: PendingMessage) => {
      let id = message.conversation_id ?? active;
      if (!id) {
        const conversation = await api<Conversation>("customer/support/conversations", {
          method: "POST",
          json: {},
        });
        id = conversation.id;
        setSelected(id);
      }
      setPending({ ...message, conversation_id: id });
      return api<Run>(`customer/support/conversations/${id}/messages`, {
        method: "POST",
        json: { request_id: message.request_id, text: message.text },
      });
    },
    onSuccess: async (run) => {
      setText("");
      setPending(undefined);
      setSelected(run.conversation_id);
      await client.invalidateQueries({ queryKey: ["customer", "support"] });
      await client.invalidateQueries({ queryKey: ["customer", "memory"] });
    },
    onError: async (error) => {
      if (
        error instanceof ApiError &&
        ((error.status >= 400 && error.status < 500 && error.status !== 408) ||
          error.code === "SUPPORT_NOT_CONFIGURED")
      ) {
        setPending(undefined);
      }
      await client.invalidateQueries({ queryKey: ["customer", "support"] });
    },
  });
  const recover = useMutation({
    mutationFn: async () => {
      const message = pending;
      if (!message?.conversation_id) throw new Error("尚未确认会话是否创建，请重试发送。");
      const id = message.conversation_id;
      const runs = await api<Run[]>(`customer/support/conversations/${id}/messages`);
      client.setQueryData(["customer", "support", "history", id], runs);
      return runs.find((run) => run.request_id === message.request_id);
    },
    onSuccess: async (run) => {
      if (run) setText("");
      setPending(undefined);
      send.reset();
      await client.invalidateQueries({ queryKey: ["customer", "support"] });
    },
  });
  const rate = useMutation({
    mutationFn: ({ id, feedback }: { id: string; feedback: "helpful" | "unhelpful" }) =>
      api(`customer/support/runs/${id}/feedback`, { method: "PUT", json: { feedback } }),
    onSuccess: () => history.refetch(),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api(`customer/support/runs/${id}/cancel`, { method: "POST" }),
    onSettled: () => history.refetch(),
  });
  const running = history.data?.some((run) => run.status === "running") ?? false;
  const busy = running || send.isPending || create.isPending || recover.isPending;
  return (
    <>
      <PageTitle
        eyebrow="购物与售后"
        title="有什么可以帮你？"
        description="查商品、比较选择、跟进订单与售后。涉及交易的操作由你核对并确认。"
        action={
          <Link className="button secondary" href="/account/memory">
            我的偏好 <ArrowRight />
          </Link>
        }
      />
      <div className="support-layout">
        <aside className="panel support-sidebar" aria-label="客服会话">
          <Button variant="secondary" disabled={busy || !!pending} onClick={() => create.mutate()}>
            <Plus /> 新的咨询
          </Button>
          <ErrorNotice
            error={conversations.error || create.error}
            retry={() => void conversations.refetch()}
          />
          {conversations.isPending ? (
            <Loading rows={2} />
          ) : (
            <nav className="support-conversations" aria-label="历史咨询">
              {conversations.data?.length ? (
                conversations.data.map((item) => (
                  <button
                    key={item.id}
                    aria-current={item.id === active ? "page" : undefined}
                    disabled={send.isPending || recover.isPending || !!pending}
                    onClick={() => {
                      setSelected(item.id);
                      setText("");
                      send.reset();
                    }}
                  >
                    <ChatCircle size={18} aria-hidden />
                    <span>{item.title}</span>
                  </button>
                ))
              ) : (
                <p className="muted">发送第一条消息后，会保留本次咨询。</p>
              )}
            </nav>
          )}
          <p className="muted">AI 回复可能存在遗漏。商品与订单以业务页面的最新信息为准。</p>
          <Link className="text-link" href="/help">
            购物帮助与联系商家 <ArrowRight />
          </Link>
        </aside>
        <section className="panel support-chat" aria-label="客服对话">
          <ErrorNotice error={history.error} retry={() => void history.refetch()} />
          {active && history.isPending ? (
            <Loading />
          ) : (
            <div className="support-messages" aria-live="polite" aria-relevant="additions text">
              {!history.data?.length && (
                <div className="support-welcome">
                  <ChatCircle size={32} weight="light" aria-hidden />
                  <h2>从你的问题开始</h2>
                  <p className="muted">告诉我预算、用途或需要处理的订单问题。</p>
                  <div className="support-suggestions">
                    {["帮我找 200 元以内的数据线", "查看我最近的订单", "如何申请退货？"].map(
                      (item) => (
                        <Button
                          key={item}
                          variant="secondary"
                          disabled={busy || !!pending}
                          onClick={() => setText(item)}
                        >
                          {item}
                        </Button>
                      ),
                    )}
                  </div>
                </div>
              )}
              {history.data?.map((run) => (
                <article className="support-turn" key={run.id}>
                  <div className="support-user">
                    <strong>你</strong>
                    <p className="preserve-lines">{run.user_text}</p>
                  </div>
                  <div className="support-answer">
                    <strong>商城客服</strong>
                    {run.status === "running" ? (
                      <>
                        <p className="muted">{runProgress(run.runtime)}</p>
                        <Loading rows={2} />
                        <div className="support-links">
                          <Button
                            variant="secondary"
                            busy={cancel.isPending}
                            onClick={() => cancel.mutate(run.id)}
                          >
                            停止回复
                          </Button>
                        </div>
                      </>
                    ) : run.status === "failed" ? (
                      <Notice>
                        {run.error_code === "SUPPORT_CANCELLED"
                          ? "已停止本次回复。你可以修改问题后重新发送。"
                          : run.error_code === "SUPPORT_BUDGET_EXHAUSTED"
                            ? "本次查询已达到处理上限。请缩小问题范围后重新发送。"
                            : run.error_code === "MEMORY_CHANGED"
                              ? "你的偏好设置已更新，请重新提问以使用最新设置。"
                              : "本次咨询未完成。可以重新发送，或通过购物帮助联系商家。"}
                      </Notice>
                    ) : (
                      <p className="preserve-lines">{run.assistant_text}</p>
                    )}
                    {run.status === "completed" && (
                      <>
                        {!!run.links.length && (
                          <nav className="support-links" aria-label="本次咨询相关操作">
                            {run.links.map((link) => (
                              <Link key={link.href} className="button secondary" href={link.href}>
                                {link.title} <ArrowRight />
                              </Link>
                            ))}
                          </nav>
                        )}
                        <div className="support-feedback">
                          <small className="muted">{dateTime(run.created_at)}</small>
                          <Button
                            variant="ghost"
                            aria-pressed={run.feedback === "helpful"}
                            busy={rate.isPending && rate.variables?.id === run.id}
                            onClick={() => rate.mutate({ id: run.id, feedback: "helpful" })}
                          >
                            <ThumbsUp /> 有帮助
                          </Button>
                          <Button
                            variant="ghost"
                            aria-pressed={run.feedback === "unhelpful"}
                            busy={rate.isPending && rate.variables?.id === run.id}
                            onClick={() => rate.mutate({ id: run.id, feedback: "unhelpful" })}
                          >
                            <ThumbsDown /> 未解决
                          </Button>
                        </div>
                      </>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
          <ErrorNotice error={rate.error || cancel.error} />
          <form
            className="support-composer"
            onSubmit={(event) => {
              event.preventDefault();
              const message = pending ?? { request_id: crypto.randomUUID(), text: text.trim() };
              if (!message.text || busy) return;
              recover.reset();
              setPending(message);
              send.mutate(message);
            }}
          >
            <Field label="你的问题" hint="请勿发送密码、支付验证码或银行卡信息。">
              <textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                maxLength={2000}
                rows={3}
                required
                disabled={busy || !!pending}
                placeholder="例如：我想买一根适合出差用的数据线，预算 100 元…"
              />
            </Field>
            <ErrorNotice error={recover.error || send.error} />
            {pending && !send.isPending && (
              <Notice>发送结果尚未确认。请先查询已有回复，或重试发送。</Notice>
            )}
            <div className="support-compose-actions">
              <small className="muted">{text.length} / 2000</small>
              {pending && !send.isPending && (
                <Button
                  variant="secondary"
                  busy={recover.isPending}
                  disabled={!pending.conversation_id}
                  onClick={() => recover.mutate()}
                >
                  查询已有回复
                </Button>
              )}
              <Button type="submit" busy={busy} disabled={!text.trim() && !pending}>
                {pending ? "重试发送" : "发送问题"}
                <ArrowRight />
              </Button>
            </div>
          </form>
        </section>
      </div>
    </>
  );
}

function runProgress(runtime: Run["runtime"]) {
  const events = runtime.events;
  const last: unknown = Array.isArray(events) ? events.at(-1) : undefined;
  const type = last && typeof last === "object" && "type" in last ? last.type : undefined;
  if (type === "compaction_start") return "正在整理较早的对话，保留你的需求…";
  if (type === "auto_retry_start") return "服务暂时繁忙，正在重试…";
  if (type === "tool_execution_start") return "正在查询商品或订单信息…";
  return "正在整理回复…";
}

export function MemoryScreen() {
  return (
    <AuthGate>
      <AccountNavigation />
      <MemoryManager />
    </AuthGate>
  );
}

function MemoryManager() {
  const query = useQuery({
    queryKey: ["customer", "memory"],
    queryFn: () => api<MemoryProfile>("customer/memory"),
  });
  const [editing, setEditing] = useState<Memory | "new">();
  const [clearOpen, setClearOpen] = useState(false);
  const [message, setMessage] = useState("");
  const saveSettings = useMutation({
    mutationFn: (settings: Settings) =>
      api("customer/memory/settings", { method: "PUT", json: settings }),
    onSuccess: async () => {
      setMessage("记忆与共享设置已保存。");
      await query.refetch();
    },
  });
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api(`customer/memory/items${editing && editing !== "new" ? `/${editing.id}` : ""}`, {
        method: editing === "new" ? "POST" : "PUT",
        json: { kind: data.get("kind"), value: String(data.get("value") ?? "").trim() },
      }),
    onSuccess: async () => {
      setEditing(undefined);
      setMessage("偏好已保存，后续推荐会使用更新后的内容。");
      await query.refetch();
    },
  });
  const decide = useMutation({
    mutationFn: ({ id, status }: { id: string; status: "confirmed" | "dismissed" }) =>
      api(`customer/memory/items/${id}/decision`, { method: "PUT", json: { status } }),
    onSuccess: async () => {
      setMessage("偏好状态已更新。");
      await query.refetch();
    },
  });
  const clear = useMutation({
    mutationFn: () => api("customer/memory", { method: "DELETE" }),
    onSuccess: async () => {
      setClearOpen(false);
      setMessage("已清除偏好及功能使用统计。");
      await query.refetch();
    },
  });
  return (
    <>
      <PageTitle
        title="我的偏好与记忆"
        description="由你决定记住什么、分享什么。未确认的总结不会用于长期推荐或商家画像。"
        action={
          <Link className="button secondary" href="/support">
            咨询客服 <ArrowRight />
          </Link>
        }
      />
      <ErrorNotice
        error={query.error || saveSettings.error || decide.error}
        retry={() => void query.refetch()}
      />
      {message && <Notice>{message}</Notice>}
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <div className="settings-layout">
            <div className="stack">
              <section className="panel stack">
                <div className="support-section-heading">
                  <h2>已确认的偏好</h2>
                  <Button
                    variant="secondary"
                    disabled={!query.data.settings.memory_enabled}
                    onClick={() => {
                      save.reset();
                      setEditing("new");
                    }}
                  >
                    <Plus /> 添加偏好
                  </Button>
                </div>
                {!query.data.memories.some((m) => m.status === "confirmed") && (
                  <Empty title="还没有确认的偏好">
                    开启记忆后，可以自己填写，也可以确认客服的总结。
                  </Empty>
                )}
                {query.data.memories
                  .filter((m) => m.status === "confirmed")
                  .map((memory) => (
                    <div className="memory-row" key={memory.id}>
                      <div>
                        <small className="muted">{kinds[memory.kind]}</small>
                        <h3>{memory.value}</h3>
                        <small className="muted">
                          {dateTime(memory.updated_at)} ·{" "}
                          {memory.source === "user" ? "你填写或纠正" : "对话总结，经你确认"}
                        </small>
                      </div>
                      <div className="support-links">
                        <Button
                          variant="ghost"
                          disabled={!query.data?.settings.memory_enabled}
                          onClick={() => {
                            save.reset();
                            setEditing(memory);
                          }}
                        >
                          纠正
                        </Button>
                        <Button
                          variant="ghost"
                          busy={decide.isPending}
                          onClick={() => decide.mutate({ id: memory.id, status: "dismissed" })}
                        >
                          停用
                        </Button>
                      </div>
                    </div>
                  ))}
              </section>
              <section className="panel stack">
                <h2>等待你确认</h2>
                {!query.data.memories.some((m) => m.status === "candidate") ? (
                  <p className="muted">客服尚未提出新的偏好总结。</p>
                ) : (
                  query.data.memories
                    .filter((m) => m.status === "candidate")
                    .map((memory) => (
                      <div className="memory-row" key={memory.id}>
                        <div>
                          <small className="muted">{kinds[memory.kind]} · 待确认</small>
                          <h3>{memory.value}</h3>
                          <p className="muted">依据：{memory.evidence}</p>
                        </div>
                        <div className="support-links">
                          <Button
                            variant="secondary"
                            disabled={!query.data?.settings.memory_enabled}
                            busy={decide.isPending}
                            onClick={() => decide.mutate({ id: memory.id, status: "confirmed" })}
                          >
                            确认偏好
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => {
                              save.reset();
                              setEditing(memory);
                            }}
                            disabled={!query.data?.settings.memory_enabled}
                          >
                            纠正
                          </Button>
                          <Button
                            variant="ghost"
                            busy={decide.isPending}
                            onClick={() => decide.mutate({ id: memory.id, status: "dismissed" })}
                          >
                            忽略
                          </Button>
                        </div>
                      </div>
                    ))
                )}
              </section>
              <section className="panel stack">
                <h2>常用客服功能</h2>
                <p className="muted">按成功的业务查询次数统计，不作为商品兴趣。</p>
                {query.data.usage.length ? (
                  <DataTable headers={["功能", "使用次数", "最近使用"]}>
                    {query.data.usage.map((item) => (
                      <tr key={item.feature}>
                        <td>{features[item.feature] ?? item.feature}</td>
                        <td>{item.uses}</td>
                        <td>{dateTime(item.last_used_at)}</td>
                      </tr>
                    ))}
                  </DataTable>
                ) : (
                  <p className="muted">启用记忆后，逐步积累功能使用记录。</p>
                )}
              </section>
            </div>
            <section className="panel stack support-settings">
              <h2>记忆与共享范围</h2>
              <MemorySettings
                key={JSON.stringify(query.data.settings)}
                initial={query.data.settings}
                busy={saveSettings.isPending}
                onSave={(settings) => saveSettings.mutate(settings)}
              />
              <p className="muted">
                关闭长期记忆后，客服不再读取或新增偏好。历史偏好保留供你管理，也可以全部清除。
              </p>
              <Button variant="danger" onClick={() => setClearOpen(true)}>
                清除全部记忆
              </Button>
              <Link className="text-link" href="/policies/privacy">
                查看隐私说明 <ArrowRight />
              </Link>
            </section>
          </div>
        )
      )}
      <Modal
        open={!!editing}
        title={editing === "new" ? "添加偏好" : "纠正偏好"}
        onClose={() => !save.isPending && setEditing(undefined)}
      >
        {editing && (
          <form
            className="stack"
            key={editing === "new" ? "new" : editing.id}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(new FormData(event.currentTarget));
            }}
          >
            <Field label="偏好类型">
              <select name="kind" defaultValue={editing === "new" ? "category" : editing.kind}>
                {Object.entries(kinds).map(([key, label]) => (
                  <option value={key} key={key}>
                    {label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="偏好内容"
              hint="例如：数据线、便携、预算 200 元以内。只记录与你的购物和服务有关的信息。"
            >
              <input
                name="value"
                defaultValue={editing === "new" ? "" : editing.value}
                required
                maxLength={120}
              />
            </Field>
            <ErrorNotice error={save.error} />
            <Button type="submit" busy={save.isPending}>
              保存并确认
            </Button>
          </form>
        )}
      </Modal>
      <Modal
        open={clearOpen}
        title="清除全部记忆"
        onClose={() => !clear.isPending && setClearOpen(false)}
      >
        <div className="stack">
          <p>
            删除所有已确认、待确认和已忽略的偏好，以及功能使用统计。订单与聊天记录保留；旧对话不会重新用于模型上下文。
          </p>
          <ErrorNotice error={clear.error} />
          <Button variant="danger" busy={clear.isPending} onClick={() => clear.mutate()}>
            确认清除
          </Button>
        </div>
      </Modal>
    </>
  );
}

function MemorySettings({
  initial,
  busy,
  onSave,
}: {
  initial: Settings;
  busy: boolean;
  onSave: (settings: Settings) => void;
}) {
  const [settings, setSettings] = useState(initial);
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(settings);
      }}
    >
      <label className="checkbox">
        <input
          type="checkbox"
          checked={settings.memory_enabled}
          onChange={(event) =>
            setSettings({
              memory_enabled: event.target.checked,
              share_with_merchant: false,
              marketing_enabled: false,
            })
          }
        />
        启用长期记忆
      </label>
      <p className="muted">记住已确认偏好，记录常用功能，改善后续服务。</p>
      <label className="checkbox">
        <input
          type="checkbox"
          disabled={!settings.memory_enabled}
          checked={settings.share_with_merchant}
          onChange={(event) =>
            setSettings({
              ...settings,
              share_with_merchant: event.target.checked,
              marketing_enabled: false,
            })
          }
        />
        向商家共享画像
      </label>
      <p className="muted">授权员工可以查看已确认偏好和功能统计，不包含原始聊天记录。</p>
      <label className="checkbox">
        <input
          type="checkbox"
          disabled={!settings.share_with_merchant}
          checked={settings.marketing_enabled}
          onChange={(event) =>
            setSettings({ ...settings, marketing_enabled: event.target.checked })
          }
        />
        允许基于画像的营销推荐
      </label>
      <p className="muted">用于商品与活动推荐。当前不会自动发送广告或邮件。</p>
      <Button type="submit" busy={busy}>
        保存设置
      </Button>
    </form>
  );
}

export function CustomerInsightsScreen() {
  return (
    <AuthGate scope="admin" permission="audit">
      <CustomerInsights />
    </AuthGate>
  );
}

function CustomerInsights() {
  const [cursor, setCursor] = useState<string>();
  const [marketing, setMarketing] = useState(false);
  const insights = useQuery({
    queryKey: ["admin", "customer-insights"],
    queryFn: () => api<Insights>("admin/customer-insights"),
  });
  const audience = useQuery({
    queryKey: ["admin", "customer-audience", cursor, marketing],
    queryFn: () =>
      api<Audience>(
        `admin/customer-audience${queryString({ cursor, marketing_only: marketing, limit: 20 })}`,
      ),
  });
  return (
    <>
      <PageTitle
        eyebrow="客服与个性化服务"
        title="用户画像与兴趣"
        description="仅统计主动允许共享且仍启用记忆的用户。偏好来自用户填写或确认，不把模型推测当作事实。"
      />
      <ErrorNotice
        error={insights.error || audience.error}
        retry={() => {
          void insights.refetch();
          void audience.refetch();
        }}
      />
      {insights.isPending ? (
        <Loading />
      ) : (
        insights.data && (
          <>
            <dl className="insight-metrics">
              <div>
                <dt>共享画像用户</dt>
                <dd>{insights.data.shared_users}</dd>
              </div>
              <div>
                <dt>允许营销推荐</dt>
                <dd>{insights.data.marketing_users}</dd>
              </div>
              <div>
                <dt>近 30 天有帮助</dt>
                <dd>{insights.data.helpful}</dd>
              </div>
              <div>
                <dt>近 30 天未解决</dt>
                <dd>{insights.data.unhelpful}</dd>
              </div>
            </dl>
            <div className="settings-layout">
              <section className="panel stack">
                <h2>已确认兴趣</h2>
                {insights.data.interests.length ? (
                  <DataTable headers={["类型", "偏好", "人数"]}>
                    {insights.data.interests.map((item) => (
                      <tr key={`${item.kind}:${item.value}`}>
                        <td>{kinds[item.kind as Memory["kind"]] ?? item.kind}</td>
                        <td>{item.value}</td>
                        <td>{item.users}</td>
                      </tr>
                    ))}
                  </DataTable>
                ) : (
                  <Empty title="暂无共享偏好">用户确认偏好并开启共享后，统计会出现在这里。</Empty>
                )}
              </section>
              <section className="panel stack">
                <h2>常用客服功能</h2>
                <p className="muted">人数按使用过该功能的共享用户去重。</p>
                {insights.data.features.length ? (
                  <DataTable headers={["功能", "人数"]}>
                    {insights.data.features.map((item) => (
                      <tr key={item.value}>
                        <td>{features[item.value] ?? item.value}</td>
                        <td>{item.users}</td>
                      </tr>
                    ))}
                  </DataTable>
                ) : (
                  <Empty title="暂无功能使用统计" />
                )}
              </section>
            </div>
          </>
        )
      )}
      <section className="panel stack support-audience">
        <div className="support-section-heading">
          <h2>用户画像明细</h2>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={marketing}
              onChange={(event) => {
                setMarketing(event.target.checked);
                setCursor(undefined);
              }}
            />
            仅看允许营销推荐的用户
          </label>
        </div>
        {audience.isPending ? (
          <Loading />
        ) : audience.data?.items.length ? (
          <DataTable headers={["用户", "已确认偏好", "常用功能", "营销授权"]}>
            {audience.data.items.map((user) => (
              <tr key={user.user_id}>
                <td>
                  <strong>{user.email}</strong>
                  <small className="support-user-id">{user.user_id}</small>
                </td>
                <td>
                  {user.preferences.length
                    ? user.preferences.map((memory) => (
                        <p key={memory.id}>
                          {kinds[memory.kind]}：{memory.value}
                        </p>
                      ))
                    : "尚无已确认偏好"}
                </td>
                <td>
                  {user.usage.length
                    ? user.usage.map((item) => (
                        <p key={item.feature}>
                          {features[item.feature] ?? item.feature} · {item.uses} 次
                        </p>
                      ))
                    : "暂无记录"}
                </td>
                <td>{user.marketing_enabled ? "已允许" : "未允许"}</td>
              </tr>
            ))}
          </DataTable>
        ) : (
          !audience.error && <Empty title="没有符合条件的用户" />
        )}
        <div className="support-links">
          {cursor && (
            <Button variant="secondary" onClick={() => setCursor(undefined)}>
              返回第一页
            </Button>
          )}
          {audience.data?.next_cursor && (
            <Button
              variant="secondary"
              onClick={() => setCursor(audience.data?.next_cursor ?? undefined)}
            >
              下一页
            </Button>
          )}
        </div>
      </section>
    </>
  );
}
