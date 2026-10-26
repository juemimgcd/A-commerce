"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  ArrowRight,
  Bell,
  Heart,
  MapPin,
  ShieldCheck,
  Receipt,
  Plus,
  X,
} from "@phosphor-icons/react";
import { api, queryString } from "@/lib/api";
import { attributes, dateTime, eventLabel, formText } from "@/lib/format";
import type { Notification, Preferences, Product, ProductPage, Scope, Session } from "@/lib/types";
import { useAuth, useStore } from "@/components/providers";
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
  Money,
  Notice,
  PageTitle,
} from "@/components/ui";
import { AddressesScreen } from "./commerce";
import { ProductImage, ProductTile } from "./catalog";

export function AccountScreen({
  section = "",
  admin = false,
}: {
  section?: string;
  admin?: boolean;
}) {
  return (
    <AuthGate scope={admin ? "admin" : "customer"}>
      <AccountNavigation admin={admin} />
      {admin || section === "security" ? (
        <Security scope={admin ? "admin" : "customer"} />
      ) : section === "addresses" ? (
        <AddressesScreen />
      ) : section === "favorites" ? (
        <Favorites />
      ) : section === "notifications" ? (
        <Notifications />
      ) : (
        <AccountOverview />
      )}
    </AuthGate>
  );
}
function AccountOverview() {
  const auth = useAuth();
  return (
    <>
      <PageTitle eyebrow="我的账户" title="一处管理每次购买" description={auth.customer?.email} />
      <div className="account-links">
        {[
          { href: "/orders", icon: Receipt, title: "我的订单", text: "查看付款、配送与售后进度" },
          {
            href: "/account/addresses",
            icon: MapPin,
            title: "收货地址",
            text: "保存与更新常用收货信息",
          },
          {
            href: "/account/favorites",
            icon: Heart,
            title: "我的收藏",
            text: "继续比较之前关注的配件",
          },
          {
            href: "/account/notifications",
            icon: Bell,
            title: "订单通知",
            text: "阅读订单更新，管理通知偏好",
          },
          {
            href: "/account/security",
            icon: ShieldCheck,
            title: "登录与安全",
            text: "更改密码，撤销登录会话",
          },
        ].map(({ href, icon: Icon, title, text }) => (
          <Link href={href} key={href}>
            <Icon size={28} weight="light" />
            <div>
              <h2>{title}</h2>
              <p>{text}</p>
            </div>
            <ArrowRight />
          </Link>
        ))}
      </div>
    </>
  );
}
function Security({ scope }: { scope: Scope }) {
  const router = useRouter();
  const auth = useAuth();
  const [message, setMessage] = useState("");
  const [revokeId, setRevokeId] = useState<string>();
  const sessions = useQuery({
    queryKey: [scope, "sessions"],
    queryFn: () => api<Session[]>(`${scope}/sessions`),
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api(`${scope}/sessions/${id}`, { method: "DELETE" }),
    onSuccess: (_, id) => {
      setRevokeId(undefined);
      if (sessions.data?.some((session) => session.id === id && session.current)) {
        auth.forget(scope);
        router.replace(scope === "admin" ? "/admin/login" : "/login");
      } else {
        setMessage("会话已撤销。");
        void sessions.refetch();
      }
    },
  });
  const change = useMutation({
    mutationFn: (data: FormData) =>
      api(`${scope}/auth/password`, {
        method: "POST",
        json: {
          current_password: String(data.get("current_password") ?? ""),
          new_password: String(data.get("new_password") ?? ""),
        },
      }),
    onSuccess: () => {
      auth.forget(scope);
      router.replace(scope === "admin" ? "/admin/login" : "/login");
      router.refresh();
    },
  });
  return (
    <>
      <PageTitle
        title="登录与安全"
        description="会话撤销后立即失效。更改密码会使全部已有会话失效。"
      />
      {message && <Notice>{message}</Notice>}
      <div className="settings-layout">
        <section className="panel">
          <h2>登录会话</h2>
          <ErrorNotice error={sessions.error} retry={() => void sessions.refetch()} />
          {sessions.isPending ? (
            <Loading />
          ) : (
            <div className="session-list">
              {sessions.data?.map((session) => (
                <div className="session-row" key={session.id}>
                  <div>
                    <strong>
                      {session.current ? "当前会话" : `会话 ${session.id.slice(0, 8)}`}
                    </strong>
                    <p>创建：{dateTime(session.created_at)}</p>
                    <small>到期：{dateTime(session.expires_at)}</small>
                  </div>
                  {session.revoked ? (
                    <span className="muted">已撤销</span>
                  ) : (
                    <Button variant="secondary" onClick={() => setRevokeId(session.id)}>
                      撤销会话
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
        <form
          className="panel stack"
          onSubmit={(event) => {
            event.preventDefault();
            change.mutate(new FormData(event.currentTarget));
          }}
        >
          <h2>更改密码</h2>
          <Field label="当前密码">
            <input
              name="current_password"
              type="password"
              required
              maxLength={128}
              autoComplete="current-password"
            />
          </Field>
          <Field label="新密码" hint="12 至 128 位">
            <input
              name="new_password"
              type="password"
              required
              minLength={12}
              maxLength={128}
              autoComplete="new-password"
            />
          </Field>
          <ErrorNotice error={change.error} />
          <Button type="submit" busy={change.isPending}>
            更新密码并重新登录
          </Button>
        </form>
      </div>
      <Modal open={!!revokeId} title="确认撤销登录会话" onClose={() => setRevokeId(undefined)}>
        <p>该会话将立即无法访问账户。撤销当前会话后，需要重新登录。</p>
        <ErrorNotice error={revoke.error} />
        <Button
          variant="danger"
          busy={revoke.isPending}
          onClick={() => revokeId && revoke.mutate(revokeId)}
        >
          确认撤销
        </Button>
      </Modal>
    </>
  );
}
function Favorites() {
  const query = useInfiniteQuery({
    queryKey: ["customer", "favorites"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<ProductPage>(`customer/favorites${queryString({ cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`customer/favorites/${id}`, { method: "DELETE" }),
    onSuccess: () => query.refetch(),
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle title="我的收藏" description="收藏的商品不预留价格或库存。" />
      <ErrorNotice error={query.error || remove.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <div className="product-grid">
          {items.map((product) => (
            <div key={product.id}>
              <ProductTile product={product} />
              <Button
                variant="ghost"
                busy={remove.isPending && remove.variables === product.id}
                onClick={() => remove.mutate(product.id)}
              >
                <Heart weight="fill" />
                取消收藏
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="还没有收藏商品"
          action={
            <Link className="button primary" href="/products">
              去逛逛
            </Link>
          }
        />
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多
        </Button>
      )}
    </>
  );
}
function Notifications() {
  const store = useStore();
  const [saved, setSaved] = useState(false);
  const query = useInfiniteQuery({
    queryKey: ["customer", "notifications"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<Notification[]>(`customer/notifications?after=${pageParam}&limit=50`),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].sequence : undefined),
  });
  const preference = useQuery({
    queryKey: ["customer", "notification-preferences"],
    queryFn: () => api<Preferences>("customer/notification-preferences"),
  });
  const read = useMutation({
    mutationFn: (id: string) => api(`customer/notifications/${id}/read`, { method: "POST" }),
    onSuccess: () => query.refetch(),
  });
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api<Preferences>("customer/notification-preferences", {
        method: "PUT",
        json: { in_app: data.get("in_app") === "on", email: data.get("email") === "on" },
      }),
    onSuccess: () => {
      setSaved(true);
      void preference.refetch();
    },
  });
  const items = query.data?.pages.flat() ?? [];
  return (
    <>
      <PageTitle title="订单通知" description="按更新顺序查看订单消息。" />
      <div className="settings-layout">
        <section className="panel">
          <h2>通知列表</h2>
          <ErrorNotice error={query.error || read.error} retry={() => void query.refetch()} />
          {query.isPending ? (
            <Loading />
          ) : items.length ? (
            <div className="notification-list">
              {items.map((item) => (
                <article key={item.id}>
                  <div>
                    <h3>
                      <Link href={`/orders/${item.order_id}`}>{eventLabel(item.kind)}</Link>
                    </h3>
                    <p>{dateTime(item.created_at)}</p>
                  </div>
                  {item.read_at ? (
                    <span className="muted">已读</span>
                  ) : (
                    <Button
                      variant="secondary"
                      busy={read.isPending}
                      onClick={() => read.mutate(item.id)}
                    >
                      标为已读
                    </Button>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <Empty title="暂无订单通知" />
          )}
          {query.hasNextPage && (
            <Button
              variant="secondary"
              busy={query.isFetchingNextPage}
              onClick={() => void query.fetchNextPage()}
            >
              加载更多通知
            </Button>
          )}
        </section>
        <section className="panel">
          <h2>通知偏好</h2>
          <ErrorNotice error={preference.error || save.error} />
          {preference.data && (
            <form
              key={JSON.stringify(preference.data)}
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                save.mutate(new FormData(event.currentTarget));
              }}
            >
              <label className="checkbox">
                <input name="in_app" type="checkbox" defaultChecked={preference.data.in_app} />
                站内订单通知
              </label>
              <label className="checkbox">
                <input
                  name="email"
                  type="checkbox"
                  defaultChecked={preference.data.email}
                  disabled={!store.data?.mail_enabled}
                />
                邮件通知
              </label>
              {!store.data?.mail_enabled && <p className="muted">当前环境尚未启用邮件服务。</p>}
              <Button type="submit" busy={save.isPending}>
                保存偏好
              </Button>
              {saved && <Notice>通知偏好已保存。</Notice>}
            </form>
          )}
        </section>
      </div>
    </>
  );
}

export function CompareScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(false);
  const ids = [...new Set((params.get("ids") ?? "").split(",").filter(Boolean))].slice(0, 3);
  const queries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ["product", id],
      queryFn: () => api<Product>(`customer/products/${id}`),
    })),
  });
  const searchQuery = useQuery({
    queryKey: ["catalog", "compare-search", search],
    queryFn: () => api<ProductPage>(`customer/products${queryString({ q: search, limit: 20 })}`),
    enabled: open,
  });
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const records = queries.flatMap((query) => (query.data ? [query.data] : []));
  const selected = records.map(
    (product) =>
      product.skus.find((sku) => sku.id === chosen[product.id]) ??
      product.skus.find((sku) => sku.active),
  );
  const names = [...new Set(selected.flatMap((sku) => Object.keys(sku?.attributes ?? {})))];
  function update(next: string[]) {
    router.replace(`/compare?ids=${next.join(",")}`);
  }
  return (
    <>
      <PageTitle
        eyebrow="按实际参数对照"
        title="找到更适合的规格"
        description="最多比较 3 件商品，参数和库存取自当前目录。"
        action={
          <Button disabled={ids.length >= 3} onClick={() => setOpen(true)}>
            <Plus />
            添加商品
          </Button>
        }
      />
      {queries.some((query) => query.isPending) && <Loading />}
      {queries.map((query, index) =>
        query.error ? (
          <div key={ids[index]}>
            <ErrorNotice error={query.error} />
            <Button variant="ghost" onClick={() => update(ids.filter((id) => id !== ids[index]))}>
              移除此商品
            </Button>
          </div>
        ) : null,
      )}
      {records.length ? (
        <DataTable headers={["比较项", ...records.map((product) => product.title)]}>
          <tr>
            <th scope="row">商品</th>
            {records.map((product) => (
              <td key={product.id}>
                <div className="compare-image">
                  <ProductImage mediaId={product.cover_media_id} title={product.title} />
                </div>
                <Button
                  variant="ghost"
                  onClick={() => update(ids.filter((id) => id !== product.id))}
                >
                  <X />
                  移除
                </Button>
              </td>
            ))}
          </tr>
          <tr>
            <th scope="row">具体规格</th>
            {records.map((product) => (
              <td key={product.id}>
                <select
                  aria-label={`${product.title}的比较规格`}
                  value={chosen[product.id] ?? product.skus.find((sku) => sku.active)?.id ?? ""}
                  onChange={(event) =>
                    setChosen((previous) => ({ ...previous, [product.id]: event.target.value }))
                  }
                >
                  {product.skus
                    .filter((sku) => sku.active)
                    .map((sku) => (
                      <option key={sku.id} value={sku.id}>
                        {attributes(sku.attributes) || sku.code}
                      </option>
                    ))}
                </select>
              </td>
            ))}
          </tr>
          <tr>
            <th scope="row">价格</th>
            {selected.map((sku, i) => (
              <td key={i}>{sku ? <Money value={sku.price_fen} /> : "当前不可售"}</td>
            ))}
          </tr>
          <tr>
            <th scope="row">品牌</th>
            {records.map((product) => (
              <td key={product.id}>{product.brand || "未提供"}</td>
            ))}
          </tr>
          {names.map((name) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              {selected.map((sku, i) => (
                <td key={i}>{sku?.attributes?.[name] ?? "未提供"}</td>
              ))}
            </tr>
          ))}
          <tr>
            <th scope="row">可售数量</th>
            {selected.map((sku, i) => (
              <td key={i}>{sku?.available ?? 0}</td>
            ))}
          </tr>
          <tr>
            <th scope="row">查看商品</th>
            {records.map((product, i) => (
              <td key={product.id}>
                <Link
                  className="button primary"
                  href={`/products/${product.id}?sku=${selected[i]?.id ?? ""}`}
                >
                  选择此规格 <ArrowRight />
                </Link>
              </td>
            ))}
          </tr>
        </DataTable>
      ) : (
        !queries.some((query) => query.isPending) && (
          <Empty
            title="先选择要比较的商品"
            action={<Button onClick={() => setOpen(true)}>添加第一件商品</Button>}
          >
            选择同类商品，更容易看出接口、功率和价格的区别。
          </Empty>
        )
      )}
      <Modal open={open} title="添加比较商品" onClose={() => setOpen(false)} wide>
        <form
          className="toolbar"
          onSubmit={(event) => {
            event.preventDefault();
            setSearch(formText(new FormData(event.currentTarget), "q"));
          }}
        >
          <Field label="搜索商品">
            <input name="q" defaultValue={search} maxLength={80} />
          </Field>
          <Button type="submit" variant="secondary">
            搜索
          </Button>
        </form>
        <ErrorNotice error={searchQuery.error} />
        {searchQuery.isPending ? (
          <Loading />
        ) : (
          <div className="compare-options">
            {searchQuery.data?.items.map((product) => (
              <div key={product.id}>
                <strong>{product.title}</strong>
                <Button
                  variant="secondary"
                  disabled={ids.includes(product.id) || ids.length >= 3}
                  onClick={() => {
                    update([...ids, product.id]);
                    setOpen(false);
                  }}
                >
                  加入比较
                </Button>
              </div>
            ))}
          </div>
        )}
      </Modal>
    </>
  );
}

export function HelpScreen({ policy }: { policy?: "privacy" | "terms" }) {
  const store = useStore();
  const content = policy === "privacy" ? store.data?.privacy_text : store.data?.terms_text;
  return (
    <div className="reading-page">
      <PageTitle
        eyebrow="购物帮助"
        title={
          policy === "privacy" ? "隐私说明" : policy === "terms" ? "使用条款" : "购买前，先了解这些"
        }
        description={
          policy ? "以商家公布并配置的正式内容为准。" : "规格、报价、付款与售后的一些常见问题。"
        }
      />
      <ErrorNotice error={store.error} retry={() => void store.refetch()} />
      {policy ? (
        content ? (
          <article className="panel preserve-lines">{content}</article>
        ) : store.isPending ? (
          <Loading />
        ) : (
          <Notice>
            商家尚未提供正式{policy === "privacy" ? "隐私说明" : "使用条款"}
            。此环境用于功能演示，正式发布前需由商家补齐内容。
          </Notice>
        )
      ) : (
        <div className="faq-list">
          {[
            [
              "如何确认配件适合我的设备？",
              "查看商品详情，核对接口、功率、尺寸等参数。可以把最多 3 件商品放入比较页。未提供的兼容信息，请先向商家确认。",
            ],
            [
              "为什么购物车金额和结算金额可能不同？",
              "购物车展示当前商品参考价格；结算使用收货地址生成报价，加入适用运费。报价有有效期，价格或库存变化时需重新报价并确认。",
            ],
            [
              "下单或付款后页面一直等待，应该怎么办？",
              "先查询已有订单结果，不要重新创建另一笔订单。结算页会保存原下单请求编号。支付页会核对服务端状态，自动查询停止后可以手动刷新。",
            ],
            [
              "当前使用真实付款吗？",
              store.data?.payment_mode === "simulator"
                ? "当前是模拟支付环境，模拟付款不会发生真实扣款。"
                : "请以结算与付款页明确展示的支付能力为准。",
            ],
            [
              "如何取消订单或申请退货？",
              "进入订单详情，按当前状态显示的操作办理。已签收订单的可退数量与申请期限由后端核对；审核通过后按商家提供的地址寄回。验收完成和退款成功会分别展示。",
            ],
            [
              "商品缺货或没有图片怎么办？",
              "缺货规格无法加入购物车。无图片的商品会明确显示占位说明；可先核对规格，并向商家咨询。",
            ],
          ].map(([question, answer]) => (
            <details key={question}>
              <summary>{question}</summary>
              <p>{answer}</p>
            </details>
          ))}
        </div>
      )}
      <section className="help-contact">
        <h2>联系商家</h2>
        {store.data?.support_email && (
          <p>
            <a href={`mailto:${store.data.support_email}`}>{store.data.support_email}</a>
          </p>
        )}
        {store.data?.support_phone && (
          <p>
            <a href={`tel:${store.data.support_phone}`}>{store.data.support_phone}</a>
          </p>
        )}
        {!store.data?.support_email && !store.data?.support_phone && (
          <p className="muted">商家尚未配置公开联系方式。</p>
        )}
      </section>
    </div>
  );
}
