"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { z } from "zod";
import { ArrowRight, ArrowClockwise, Check, Trash, Plus, LockKey } from "@phosphor-icons/react";
import { api, ApiError, orderSchema, quoteSchema, queryString } from "@/lib/api";
import { attributes, dateTime, eventLabel, formText, shortId } from "@/lib/format";
import { useStored } from "@/lib/storage";
import type {
  Address,
  CartItem,
  FlashSale,
  History,
  Order,
  OrderPage,
  Quote,
  Refund,
  Return,
  ReturnOption,
  Shipment,
} from "@/lib/types";
import { useAuth, useStore, useVisible } from "@/components/providers";
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
import { ProductImage } from "./catalog";

export function AddressForm({
  address,
  onSaved,
  onCancel,
}: {
  address?: Address;
  onSaved: (address: Address) => void;
  onCancel?: () => void;
}) {
  const save = useMutation({
    mutationFn: async (data: FormData) =>
      api<Address>(`customer/addresses${address ? `/${address.id}` : ""}`, {
        method: address ? "PUT" : "POST",
        json: {
          recipient: formText(data, "recipient"),
          phone: formText(data, "phone"),
          region: formText(data, "region"),
          detail: formText(data, "detail"),
        },
      }),
    onSuccess: onSaved,
  });
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(new FormData(event.currentTarget));
      }}
    >
      <div className="two-fields">
        <Field label="收货人">
          <input
            name="recipient"
            defaultValue={address?.recipient}
            autoComplete="name"
            required
            maxLength={80}
          />
        </Field>
        <Field label="联系电话">
          <input
            name="phone"
            defaultValue={address?.phone}
            type="tel"
            autoComplete="tel"
            required
            pattern="[+0-9 \-]{5,32}"
            maxLength={32}
          />
        </Field>
      </div>
      <Field
        label="配送地区"
        hint="填写完整地区名称，例如“上海”。运费按此地区的规则计算，无专属规则时使用默认运费。"
      >
        <input
          name="region"
          defaultValue={address?.region}
          autoComplete="address-level1"
          required
          maxLength={120}
        />
      </Field>
      <Field label="详细地址">
        <textarea
          name="detail"
          defaultValue={address?.detail}
          autoComplete="street-address"
          required
          maxLength={300}
          rows={3}
        />
      </Field>
      <ErrorNotice error={save.error} />
      <div className="actions">
        <Button busy={save.isPending} type="submit">
          保存收货地址
        </Button>
        {onCancel && (
          <Button type="button" variant="secondary" onClick={onCancel}>
            取消
          </Button>
        )}
      </div>
    </form>
  );
}

export function AddressesScreen() {
  return (
    <AuthGate>
      <AddressBook />
    </AuthGate>
  );
}
function AddressBook() {
  const query = useQuery({
    queryKey: ["customer", "addresses"],
    queryFn: () => api<Address[]>("customer/addresses"),
  });
  const [edit, setEdit] = useState<Address | "new" | undefined>();
  return (
    <>
      <PageTitle
        title="收货地址"
        description="新增或更新用于报价与配送的地址。"
        action={
          <Button onClick={() => setEdit("new")}>
            <Plus />
            新增地址
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.length ? (
        <div className="address-grid">
          {query.data.map((item) => (
            <article key={item.id} className="panel">
              <h2>
                {item.recipient} <small>{item.phone}</small>
              </h2>
              <p>
                {item.region} {item.detail}
              </p>
              <Button variant="secondary" onClick={() => setEdit(item)}>
                编辑地址
              </Button>
            </article>
          ))}
        </div>
      ) : (
        !query.error && <Empty title="还没有收货地址">新增地址后即可生成结算报价。</Empty>
      )}
      <Modal
        open={!!edit}
        title={edit === "new" ? "新增地址" : "编辑地址"}
        onClose={() => setEdit(undefined)}
      >
        {edit && (
          <AddressForm
            key={edit === "new" ? "new" : edit.id}
            address={edit === "new" ? undefined : edit}
            onSaved={() => {
              setEdit(undefined);
              void query.refetch();
            }}
            onCancel={() => setEdit(undefined)}
          />
        )}
      </Modal>
    </>
  );
}

export function CartScreen() {
  return (
    <AuthGate>
      <Cart />
    </AuthGate>
  );
}
function Cart() {
  const query = useQuery({
    queryKey: ["customer", "cart"],
    queryFn: () => api<CartItem[]>("customer/cart"),
  });
  const [excluded, setExcluded] = useState<string[]>([]);
  const update = useMutation({
    mutationFn: ({ id, quantity }: { id: string; quantity: number }) =>
      api(`customer/cart/items/${id}`, { method: "PUT", json: { quantity } }),
    onSuccess: () => query.refetch(),
  });
  const selected =
    query.data?.filter(
      (item) => item.active && item.quantity <= item.available && !excluded.includes(item.sku_id),
    ) ?? [];
  const total = selected.reduce((sum, item) => sum + item.quantity * item.price_fen, 0);
  return (
    <>
      <PageTitle
        eyebrow="购物袋"
        title="你的购物车"
        description="购物车不预留库存。价格和可售数量将在结算时再次核对。"
        action={
          <Link className="text-link" href="/products">
            继续选购 <ArrowRight />
          </Link>
        }
      />
      <ErrorNotice error={query.error || update.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.length ? (
        <div className="checkout-layout">
          <div className="cart-lines">
            {query.data.map((item) => {
              const valid = item.active && item.quantity <= item.available;
              return (
                <article className="cart-line" key={item.sku_id}>
                  <input
                    type="checkbox"
                    aria-label={`选择${item.title}`}
                    checked={valid && !excluded.includes(item.sku_id)}
                    disabled={!valid || update.isPending}
                    onChange={(event) =>
                      setExcluded((previous) =>
                        event.target.checked
                          ? previous.filter((id) => id !== item.sku_id)
                          : [...previous, item.sku_id],
                      )
                    }
                  />
                  <Link href={`/products/${item.product_id}?sku=${item.sku_id}`}>
                    <ProductImage mediaId={item.cover_media_id} title={item.title} />
                  </Link>
                  <div className="cart-description">
                    <h2>
                      <Link href={`/products/${item.product_id}?sku=${item.sku_id}`}>
                        {item.title}
                      </Link>
                    </h2>
                    <p className="muted">{attributes(item.attributes)}</p>
                    <Money value={item.price_fen} />
                    {!valid && (
                      <p className="error-text">
                        {!item.active
                          ? "当前规格已不可售"
                          : `库存不足，当前可售 ${item.available} 件`}
                      </p>
                    )}
                    <div className="cart-line-controls">
                      <label>
                        数量{" "}
                        <input
                          key={`${item.sku_id}:${item.quantity}`}
                          aria-label={`${item.title}的数量`}
                          className="quantity"
                          type="number"
                          min={1}
                          max={99}
                          defaultValue={item.quantity}
                          disabled={update.isPending}
                          onBlur={(event) => {
                            const quantity = Number(event.target.value);
                            if (
                              Number.isInteger(quantity) &&
                              quantity >= 1 &&
                              quantity <= 99 &&
                              quantity !== item.quantity
                            )
                              update.mutate({ id: item.sku_id, quantity });
                            else event.target.value = String(item.quantity);
                          }}
                        />
                      </label>
                      <Button
                        variant="ghost"
                        disabled={update.isPending}
                        onClick={() => update.mutate({ id: item.sku_id, quantity: 0 })}
                      >
                        <Trash size={18} />
                        移除
                      </Button>
                    </div>
                  </div>
                  <Money value={item.quantity * item.price_fen} className="line-total" />
                </article>
              );
            })}
          </div>
          <aside className="summary-panel">
            <h2>已选商品</h2>
            <dl className="amounts">
              <div>
                <dt>商品数量</dt>
                <dd>{selected.reduce((sum, item) => sum + item.quantity, 0)} 件</dd>
              </div>
              <div>
                <dt>商品参考小计</dt>
                <dd>
                  <Money value={total} />
                </dd>
              </div>
              <div>
                <dt>运费</dt>
                <dd>结算时计算</dd>
              </div>
            </dl>
            <p className="muted">每次最多结算 50 个规格，其余商品可留在购物车。</p>
            {selected.length > 50 && (
              <Notice>已选 {selected.length} 个规格，请取消部分勾选。</Notice>
            )}
            {selected.length > 0 && selected.length <= 50 && !update.isPending ? (
              <Link
                className="button primary full"
                href={`/checkout?${selected.map((item) => `sku=${item.sku_id}`).join("&")}`}
              >
                前往结算 <ArrowRight />
              </Link>
            ) : (
              <Button className="full" disabled>
                请选择可结算商品
              </Button>
            )}
            <small>
              <LockKey size={16} />
              下一步核对收货地址与最终报价
            </small>
          </aside>
        </div>
      ) : (
        !query.error && (
          <Empty
            title="购物车还是空的"
            action={
              <Link className="button primary" href="/products">
                浏览全部商品 <ArrowRight />
              </Link>
            }
          >
            先选一个适合你设备的配件。
          </Empty>
        )
      )}
    </>
  );
}

const pendingOrder = z.object({
  key: z.string(),
  quote_id: z.string().uuid(),
  started_at: z.number(),
});
function uncertain(error: unknown) {
  return (
    !(error instanceof ApiError) ||
    error.status === 0 ||
    error.status >= 500 ||
    error.code === "INVALID_RESPONSE"
  );
}
export function CheckoutScreen({ flashSaleId }: { flashSaleId?: string }) {
  return (
    <AuthGate>
      <Checkout flashSaleId={flashSaleId} />
    </AuthGate>
  );
}
function Checkout({ flashSaleId }: { flashSaleId?: string }) {
  const auth = useAuth();
  const visible = useVisible();
  const search = useSearchParams();
  const router = useRouter();
  const client = useQueryClient();
  const [pending, savePending] = useStored(`commerce:order:${auth.customer?.id}`, pendingOrder);
  const [quote, setQuote] = useState<Quote>();
  const [previousTotal, setPreviousTotal] = useState<number>();
  const [addressId, setAddressId] = useState("");
  const [newAddress, setNewAddress] = useState(false);
  const [message, setMessage] = useState("");
  const addresses = useQuery({
    queryKey: ["customer", "addresses"],
    queryFn: () => api<Address[]>("customer/addresses"),
  });
  const cart = useQuery({
    queryKey: ["customer", "cart"],
    queryFn: () => api<CartItem[]>("customer/cart"),
    enabled: !flashSaleId,
  });
  const sale = useQuery({
    queryKey: ["flash-sale", flashSaleId],
    queryFn: () => api<FlashSale>(`customer/flash-sales/${flashSaleId}`),
    enabled: !!flashSaleId,
    staleTime: 0,
    refetchInterval: visible ? 15000 : false,
  });
  const restoredQuote = useQuery({
    queryKey: ["customer", "quote", pending?.quote_id],
    queryFn: () =>
      api<Quote>(`customer/quotes/${pending?.quote_id}`, { validate: quoteSchema.parse }),
    enabled: !!pending,
  });
  const chosen = search.getAll("sku");
  const items = flashSaleId
    ? sale.data
      ? [{ ...sale.data, quantity: 1 }]
      : []
    : (cart.data?.filter((item) => !chosen.length || chosen.includes(item.sku_id)) ?? []);
  const selectedAddress = addressId || addresses.data?.[0]?.id || "";
  const displayedQuote = pending
    ? (restoredQuote.data ?? (quote?.id === pending.quote_id ? quote : undefined))
    : quote;
  const createQuote = useMutation({
    mutationFn: () =>
      api<Quote>("customer/quotes", {
        method: "POST",
        json: {
          address_id: selectedAddress,
          items: items.map((item) => ({
            sku_id: item.sku_id,
            quantity: item.quantity,
            flash_sale_id: flashSaleId,
          })),
        },
        validate: quoteSchema.parse,
      }),
    onSuccess: (value) => {
      setPreviousTotal(quote?.total_fen);
      setQuote(value);
      setMessage("");
      submit.reset();
      recover.reset();
    },
  });
  function completed(order: Order) {
    savePending();
    client.setQueryData(["customer", "order", order.id], order);
    router.replace(`/orders/${order.id}/payment`);
  }
  const submit = useMutation({
    mutationFn: async () => {
      const request =
        pending ??
        (quote
          ? { key: crypto.randomUUID(), quote_id: quote.id, started_at: Date.now() }
          : undefined);
      if (!request) throw new Error("请先获取报价。");
      savePending(request);
      try {
        return await api<Order>("customer/orders", {
          method: "POST",
          key: request.key,
          json: { quote_id: request.quote_id },
          validate: orderSchema.parse,
        });
      } catch (error) {
        if (
          (!pending && !uncertain(error)) ||
          (error instanceof ApiError &&
            [
              "QUOTE_EXPIRED",
              "QUOTE_STALE",
              "FLASH_SALE_UNAVAILABLE",
              "FLASH_SALE_INACTIVE",
              "FLASH_SALE_SOLD_OUT",
            ].includes(error.code))
        )
          savePending();
        throw error;
      }
    },
    onSuccess: completed,
  });
  const recover = useMutation({
    mutationFn: async () => {
      if (!pending) throw new Error("没有待核对请求。");
      try {
        return await api<Order>(`customer/order-requests/${pending.key}`, {
          validate: orderSchema.parse,
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404)
          throw new Error("暂未查询到订单。原请求仍可能完成，请稍后继续查询，或使用原请求重试。");
        throw error;
      }
    },
    onSuccess: completed,
  });
  return (
    <>
      <PageTitle
        eyebrow="结算 · 核对后下单"
        title={pending ? "确认订单提交结果" : "确认这次购买"}
        description={
          flashSaleId
            ? "秒杀每次购买 1 件，报价不占库存。活动结束或售罄后无法创建新订单。"
            : "所有应付金额与配送规则，均以服务端报价为准。"
        }
      />
      <ErrorNotice
        error={
          addresses.error ||
          cart.error ||
          sale.error ||
          createQuote.error ||
          submit.error ||
          recover.error ||
          restoredQuote.error
        }
      />
      {message && <Notice>{message}</Notice>}
      {flashSaleId && !pending && sale.data && sale.data.status !== "active" && (
        <Notice>
          当前活动尚未开始、已结束或已售罄。
          <Link href="/flash-sales">返回秒杀列表选择其他商品</Link>
        </Notice>
      )}
      {pending && (
        <Notice>
          <div>
            <strong>已有一笔下单请求待核对。</strong>
            <p>刷新页面后保留原请求，不会创建第二笔订单。请求编号：{pending.key}</p>
            <div className="actions">
              <Button variant="secondary" busy={recover.isPending} onClick={() => recover.mutate()}>
                <ArrowClockwise />
                查询已有结果
              </Button>
              <Button variant="secondary" busy={submit.isPending} onClick={() => submit.mutate()}>
                用原请求重试
              </Button>
            </div>
          </div>
        </Notice>
      )}
      {!pending && (addresses.isPending || (flashSaleId ? sale.isPending : cart.isPending)) ? (
        <Loading />
      ) : (
        <div className="checkout-layout">
          <div className="stack spaced">
            <section className="panel">
              <div className="section-title">
                <h2>01 收货地址</h2>
                {!pending && (
                  <Button variant="ghost" onClick={() => setNewAddress(true)}>
                    <Plus />
                    新增
                  </Button>
                )}
              </div>
              {displayedQuote ? (
                <AddressSnapshot value={displayedQuote.address_snapshot} />
              ) : addresses.data?.length ? (
                <div className="address-options">
                  {addresses.data.map((address) => (
                    <label
                      key={address.id}
                      className={selectedAddress === address.id ? "selected" : ""}
                    >
                      <input
                        type="radio"
                        name="address"
                        value={address.id}
                        checked={selectedAddress === address.id}
                        onChange={() => {
                          setAddressId(address.id);
                          setQuote(undefined);
                        }}
                      />
                      <div>
                        <strong>
                          {address.recipient} · {address.phone}
                        </strong>
                        <p>
                          {address.region} {address.detail}
                        </p>
                      </div>
                    </label>
                  ))}
                </div>
              ) : (
                <Empty title="请先添加收货地址" />
              )}
            </section>
            <section className="panel">
              <h2>02 商品与规格</h2>
              {displayedQuote ? (
                <LineItems items={displayedQuote.items} />
              ) : items.length ? (
                <LineItems items={items} />
              ) : (
                <Empty title="没有待结算商品" action={<Link href="/cart">返回购物车</Link>} />
              )}
            </section>
            {displayedQuote && (
              <section className="panel">
                <h2>03 配送与售后</h2>
                <Policy value={displayedQuote.policy} />
                <p className="muted">报价不预留库存，创建订单时会再次检查。</p>
              </section>
            )}
          </div>
          <aside className="summary-panel">
            <h2>{displayedQuote ? "确认报价" : "获取本次报价"}</h2>
            {displayedQuote ? (
              <>
                <Amounts total={displayedQuote.total_fen} shipping={displayedQuote.shipping_fen} />
                <p>
                  报价有效至
                  <br />
                  <strong>{dateTime(displayedQuote.expires_at)}</strong>
                </p>
                {previousTotal !== undefined && previousTotal !== displayedQuote.total_fen && (
                  <Notice>
                    报价已变化：原总价 <Money value={previousTotal} />
                    ，请重新确认当前总价。
                  </Notice>
                )}
                {!pending && (
                  <>
                    <Button
                      className="full"
                      busy={submit.isPending}
                      disabled={
                        submit.error instanceof ApiError &&
                        [
                          "QUOTE_STALE",
                          "QUOTE_EXPIRED",
                          "OUT_OF_STOCK",
                          "PRODUCT_UNAVAILABLE",
                          "FLASH_SALE_UNAVAILABLE",
                          "FLASH_SALE_INACTIVE",
                          "FLASH_SALE_SOLD_OUT",
                        ].includes(submit.error.code)
                      }
                      onClick={() => submit.mutate()}
                    >
                      确认并创建订单 <ArrowRight />
                    </Button>
                    <Button
                      className="full"
                      variant="ghost"
                      onClick={() => {
                        setQuote(undefined);
                        setMessage("请重新选择地址并获取报价。");
                      }}
                    >
                      修改购买内容
                    </Button>
                    <Button
                      className="full"
                      variant="ghost"
                      busy={createQuote.isPending}
                      onClick={() => createQuote.mutate()}
                    >
                      刷新报价
                    </Button>
                  </>
                )}
              </>
            ) : (
              !pending && (
                <>
                  <p className="muted">选择地址后，计算商品金额与运费。</p>
                  <Button
                    className="full"
                    busy={createQuote.isPending}
                    disabled={
                      !selectedAddress ||
                      !items.length ||
                      items.length > 50 ||
                      (!!flashSaleId && sale.data?.status !== "active")
                    }
                    onClick={() => createQuote.mutate()}
                  >
                    获取报价 <ArrowRight />
                  </Button>
                </>
              )
            )}
            <p className="muted">
              {flashSaleId
                ? "秒杀直接结算，不影响购物车中的商品。"
                : "下单成功后购物车保留原商品，便于继续购买。可返回购物车手动移除已成交行。"}
            </p>
          </aside>
        </div>
      )}
      <Modal open={newAddress} title="新增收货地址" onClose={() => setNewAddress(false)}>
        <AddressForm
          onSaved={(address) => {
            void addresses.refetch();
            setAddressId(address.id);
            setQuote(undefined);
            setNewAddress(false);
          }}
        />
      </Modal>
    </>
  );
}

export function AddressSnapshot({ value }: { value: Record<string, string> }) {
  return (
    <address>
      <strong>
        {value.recipient} · {value.phone}
      </strong>
      <p>
        {value.region} {value.detail}
      </p>
    </address>
  );
}
export function LineItems({
  items,
}: {
  items: {
    title: string;
    quantity: number;
    price_fen: number;
    attributes?: Record<string, string>;
    flash_sale_id?: string | null;
  }[];
}) {
  return (
    <div className="order-lines">
      {items.map((item, i) => (
        <div className="order-line" key={i}>
          <div>
            <strong>{item.title}</strong>
            {item.flash_sale_id && <span className="status neutral">秒杀</span>}
            <p className="muted">{attributes(item.attributes) || "标准规格"}</p>
            <small>
              <Money value={item.price_fen} /> × {item.quantity}
            </small>
          </div>
          <Money value={item.price_fen * item.quantity} />
        </div>
      ))}
    </div>
  );
}
export function Amounts({
  total,
  shipping,
  refunded = 0,
  reserved = 0,
}: {
  total: number;
  shipping: number;
  refunded?: number;
  reserved?: number;
}) {
  return (
    <dl className="amounts">
      <div>
        <dt>商品金额</dt>
        <dd>
          <Money value={total - shipping} />
        </dd>
      </div>
      <div>
        <dt>运费</dt>
        <dd>
          <Money value={shipping} />
        </dd>
      </div>
      <div className="amount-total">
        <dt>订单总额</dt>
        <dd>
          <Money value={total} />
        </dd>
      </div>
      {refunded > 0 && (
        <div>
          <dt>已退款</dt>
          <dd>
            <Money value={refunded} />
          </dd>
        </div>
      )}
      {reserved > 0 && (
        <div>
          <dt>退款处理中</dt>
          <dd>
            <Money value={reserved} />
          </dd>
        </div>
      )}
    </dl>
  );
}
function Policy({ value }: { value: Record<string, string | number> }) {
  return (
    <dl className="spec-table">
      {value.return_days !== undefined && (
        <div>
          <dt>退货申请</dt>
          <dd>签收后 {value.return_days} 天内可申请，以订单可退数量为准。</dd>
        </div>
      )}
      {value.return_shipping === "non_refundable" && (
        <div>
          <dt>原配送运费</dt>
          <dd>退货不退原运费。</dd>
        </div>
      )}
    </dl>
  );
}

export function OrdersScreen({ admin = false }: { admin?: boolean }) {
  return (
    <AuthGate scope={admin ? "admin" : "customer"} permission={admin ? "fulfillment" : undefined}>
      <Orders admin={admin} />
    </AuthGate>
  );
}
function Orders({ admin }: { admin: boolean }) {
  const scope = admin ? "admin" : "customer";
  const router = useRouter();
  const params = useSearchParams();
  const filter = new URLSearchParams(params.toString());
  filter.delete("cursor");
  const filters = filter.toString();
  const query = useInfiniteQuery({
    queryKey: [scope, "orders", filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<OrderPage>(
        `${scope}/orders?${filters}&limit=20${pageParam ? `&cursor=${encodeURIComponent(pageParam)}` : ""}`,
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        eyebrow={admin ? "履约工作台" : "我的购买"}
        title={admin ? "订单履约" : "我的订单"}
        description="分别查看订单、付款与配送进度。"
        action={
          <Link className="button secondary" href={admin ? "/admin/returns" : "/returns"}>
            查看售后申请
          </Link>
        }
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const id = formText(data, "id");
          if (id) {
            router.push(`${admin ? "/admin" : ""}/orders/${encodeURIComponent(id)}`);
            return;
          }
          const next = new URLSearchParams();
          for (const key of ["status", "fulfillment_status"])
            if (formText(data, key)) next.set(key, formText(data, key));
          router.push(`${admin ? "/admin" : ""}/orders?${next}`);
        }}
      >
        <Field label="订单状态">
          <select name="status" defaultValue={params.get("status") ?? ""}>
            <option value="">全部状态</option>
            <option value="pending_payment">待付款</option>
            <option value="paid">已付款</option>
            <option value="closing">关闭核对中</option>
            <option value="cancelled">已取消</option>
          </select>
        </Field>
        <Field label="配送状态">
          <select name="fulfillment_status" defaultValue={params.get("fulfillment_status") ?? ""}>
            <option value="">全部配送</option>
            <option value="unfulfilled">待拣货</option>
            <option value="fulfilling">拣货中</option>
            <option value="shipped">已发货</option>
            <option value="delivered">已签收</option>
          </select>
        </Field>
        <Field label="直接定位订单">
          <input name="id" placeholder="完整订单编号" />
        </Field>
        <Button type="submit" variant="secondary">
          查询订单
        </Button>
      </form>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : !items.length ? (
        <Empty title="暂无符合条件的订单" />
      ) : admin ? (
        <DataTable headers={["订单 / 创建时间", "商品", "总额", "订单", "支付", "履约", "操作"]}>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                <strong>{shortId(item.id)}</strong>
                <small>{dateTime(item.created_at)}</small>
              </td>
              <td>
                {item.items[0]?.title}
                <small>{item.items.length} 个规格</small>
              </td>
              <td>
                <Money value={item.total_fen} />
              </td>
              <td>
                <Status
                  value={item.status}
                  label={item.status === "shipped" ? "已寄回" : undefined}
                />
              </td>
              <td>
                <Status value={item.payment_status} />
              </td>
              <td>
                <Status value={item.fulfillment_status} />
              </td>
              <td>
                <Link href={`/admin/orders/${item.id}`}>处理订单</Link>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <div className="order-list">
          {items.map((item) => (
            <article className="panel" key={item.id}>
              <div className="section-title">
                <div>
                  <strong>订单 {shortId(item.id)}</strong>
                  <small className="muted"> {dateTime(item.created_at)}</small>
                </div>
                <Status
                  value={item.status}
                  label={item.status === "shipped" ? "已寄回" : undefined}
                />
              </div>
              <LineItems items={item.items} />
              <div className="order-card-footer">
                <div className="status-row">
                  <Status value={item.payment_status} />
                  <Status value={item.fulfillment_status} />
                </div>
                <strong>
                  <Money value={item.total_fen} />
                </strong>
                <Link href={`/orders/${item.id}`} className="button secondary">
                  查看订单 <ArrowRight />
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}
      {query.hasNextPage && (
        <div className="load-more">
          <Button
            variant="secondary"
            busy={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            加载更多订单
          </Button>
        </div>
      )}
    </>
  );
}

export function OrderScreen({
  id,
  admin = false,
  payment = false,
}: {
  id: string;
  admin?: boolean;
  payment?: boolean;
}) {
  return (
    <AuthGate scope={admin ? "admin" : "customer"} permission={admin ? "fulfillment" : undefined}>
      <OrderDetail key={id} id={id} admin={admin} payment={payment} />
    </AuthGate>
  );
}
function OrderDetail({ id, admin, payment }: { id: string; admin: boolean; payment: boolean }) {
  const scope = admin ? "admin" : "customer";
  const auth = useAuth();
  const visible = useVisible();
  const store = useStore();
  const [started, setStarted] = useState(() => Date.now());
  const [action, setAction] = useState("");
  const [notice, setNotice] = useState("");
  const query = useQuery({
    queryKey: [scope, "order", id],
    queryFn: () => api<Order>(`${scope}/orders/${id}`, { validate: orderSchema.parse }),
    refetchInterval: (state) =>
      visible &&
      Date.now() - started < 120000 &&
      state.state.data &&
      ["pending_payment", "closing"].includes(state.state.data.status)
        ? Math.min(2000 * (1 + state.state.dataUpdateCount), 15000)
        : false,
  });
  const order = query.data;
  const history = useInfiniteQuery({
    queryKey: [scope, "history", id],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<History[]>(`${scope}/orders/${id}/history?after=${pageParam}&limit=50`),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].sequence : undefined),
    enabled: !!order && (!admin || !!auth.admin?.permissions.includes("audit")),
  });
  const shipment = useQuery({
    queryKey: [scope, "shipment", id],
    queryFn: () => api<Shipment>(`${scope}/orders/${id}/shipment`),
    enabled: !!order && ["shipped", "delivered"].includes(order.fulfillment_status),
  });
  const refunds = useQuery({
    queryKey: [scope, "order-refunds", id, order?.refunded_fen, order?.refund_reserved_fen],
    queryFn: () => api<Refund[]>(`customer/orders/${id}/refunds`),
    enabled: !admin && !!order && (order.refunded_fen > 0 || order.refund_reserved_fen > 0),
  });
  const paymentSession = useQuery({
    queryKey: ["customer", "payment-session", id, order?.payment_status],
    queryFn: () =>
      api<{ status: string; payment_path: string }>(`customer/orders/${id}/payment-session`),
    enabled: !admin && order?.status === "pending_payment" && order.payment_status === "pending",
  });
  const mutate = useMutation({
    mutationFn: async (data: FormData) => {
      if (!order) return;
      const json =
        action === "simulate-payment"
          ? undefined
          : action === "ship"
            ? {
                expected_version: order.version,
                carrier: formText(data, "carrier"),
                tracking_number: formText(data, "tracking_number"),
              }
            : action === "cancel"
              ? { expected_version: order.version, reason: formText(data, "reason") }
              : { expected_version: order.version };
      await api(`${scope}/orders/${id}/${action}`, { method: "POST", json });
    },
    onSuccess: () => {
      setAction("");
      setNotice("操作已提交，以下结果以服务端最新状态为准。");
      setStarted(Date.now());
      void query.refetch();
      void history.refetch();
    },
    onError: () => {
      void query.refetch();
    },
  });
  if (query.isPending) return <Loading />;
  if (!order) return <ErrorNotice error={query.error} retry={() => void query.refetch()} />;
  const actions: ReactNode = (
    <div className="actions">
      <Button
        variant="secondary"
        busy={query.isFetching}
        onClick={() => {
          setStarted(Date.now());
          void query.refetch();
          void history.refetch();
          if (shipment.isEnabled) void shipment.refetch();
          if (refunds.isEnabled) void refunds.refetch();
        }}
      >
        <ArrowClockwise />
        刷新状态
      </Button>
      {!admin &&
        order.status === "pending_payment" &&
        order.payment_status === "pending" &&
        store.data?.payment_mode === "simulator" &&
        paymentSession.data?.status === "pending" && (
          <Button onClick={() => setAction("simulate-payment")}>模拟付款</Button>
        )}
      {!admin &&
        ["pending_payment", "paid"].includes(order.status) &&
        order.fulfillment_status === "unfulfilled" && (
          <Button variant="secondary" onClick={() => setAction("cancel")}>
            取消订单
          </Button>
        )}
      {!admin && order.fulfillment_status === "delivered" && order.payment_status === "paid" && (
        <Link href={`/orders/${id}/return`} className="button secondary">
          申请退货
        </Link>
      )}
      {admin && order.status === "paid" && order.fulfillment_status === "unfulfilled" && (
        <Button onClick={() => setAction("pick")}>开始拣货</Button>
      )}
      {admin && order.status === "paid" && order.fulfillment_status === "fulfilling" && (
        <Button onClick={() => setAction("ship")}>填写物流并发货</Button>
      )}
      {admin && order.fulfillment_status === "shipped" && (
        <Button onClick={() => setAction("deliver")}>确认已签收</Button>
      )}
    </div>
  );
  return (
    <>
      <nav className="breadcrumbs">
        <Link href={admin ? "/admin/orders" : "/orders"}>订单列表</Link>
        <span>/</span>
        <span>{shortId(id)}</span>
      </nav>
      <PageTitle
        title={payment ? "付款与订单状态" : `订单 ${shortId(id)}`}
        description={`创建于 ${dateTime(order.created_at)}`}
        action={actions}
      />
      <ErrorNotice error={query.error || paymentSession.error} />
      {notice && (
        <Notice>
          <Check />
          {notice}
        </Notice>
      )}
      <div className="status-banner">
        <div>
          <span>订单</span>
          <Status value={order.status} />
        </div>
        <div>
          <span>付款</span>
          <Status value={order.payment_status} />
        </div>
        <div>
          <span>配送</span>
          <Status value={order.fulfillment_status} />
        </div>
      </div>
      {["closing", "pending_payment"].includes(order.status) && (
        <Notice>
          <div>
            <strong>
              {order.status === "closing"
                ? "正在核对订单关闭与支付结果"
                : order.payment_status === "unknown"
                  ? "支付结果仍在核对中，请勿重复付款"
                  : "等待服务端确认付款结果"}
            </strong>
            <p>
              付款截止：{dateTime(order.expires_at)}。页面最多自动查询 2
              分钟，之后可手动刷新；等待结束不代表付款失败。
            </p>
            {store.data?.payment_mode === "simulator" && <p>当前为模拟支付，不会产生真实扣款。</p>}
          </div>
        </Notice>
      )}
      <div className="checkout-layout order-layout">
        <div className="stack spaced">
          <section className="panel">
            <h2>购买的商品</h2>
            <LineItems items={order.items} />
          </section>
          <section className="panel">
            <h2>收货信息</h2>
            <AddressSnapshot value={order.address_snapshot} />
          </section>
          {shipment.isEnabled && (
            <section className="panel">
              <h2>物流进度</h2>
              <ErrorNotice error={shipment.error} />
              {shipment.isPending ? (
                <Loading rows={1} />
              ) : (
                shipment.data && (
                  <>
                    <p>
                      {shipment.data.carrier} · {shipment.data.tracking_number}
                    </p>
                    <ol className="timeline">
                      {shipment.data.events.map((event) => (
                        <li key={event.id}>
                          <Status value={event.state} />
                          <small>{dateTime(event.occurred_at)}</small>
                        </li>
                      ))}
                    </ol>
                  </>
                )
              )}
            </section>
          )}
          {history.isEnabled && (
            <section className="panel">
              <h2>订单记录</h2>
              <ErrorNotice error={history.error} />
              <ol className="timeline">
                {history.data?.pages.flat().map((item) => (
                  <li key={item.sequence}>
                    <strong>{eventLabel(item.kind)}</strong>
                    <p>{dateTime(item.occurred_at)}</p>
                  </li>
                ))}
              </ol>
              {history.hasNextPage && (
                <Button
                  variant="ghost"
                  busy={history.isFetchingNextPage}
                  onClick={() => void history.fetchNextPage()}
                >
                  加载更多记录
                </Button>
              )}
            </section>
          )}
        </div>
        <aside className="summary-panel">
          <h2>金额明细</h2>
          <Amounts
            total={order.total_fen}
            shipping={order.shipping_fen}
            refunded={order.refunded_fen}
            reserved={order.refund_reserved_fen}
          />
          <p className="muted break-all">订单编号：{id}</p>
          <Policy value={order.policy} />
          {order.return_deadline && <p>退货申请截止：{dateTime(order.return_deadline)}</p>}
          {refunds.data?.map((refund) => (
            <div className="refund-line" key={refund.id}>
              <Money value={refund.amount_fen} />
              <Status value={refund.status} />
              <small>{dateTime(refund.confirmed_at ?? refund.created_at)}</small>
            </div>
          ))}
          <ErrorNotice error={refunds.error} />
        </aside>
      </div>
      <Modal
        open={!!action}
        title={
          action === "simulate-payment"
            ? "确认模拟付款"
            : action === "cancel"
              ? "取消这笔订单"
              : action === "ship"
                ? "确认发货"
                : action === "deliver"
                  ? "确认订单已签收"
                  : "开始拣货"
        }
        onClose={() => !mutate.isPending && setAction("")}
      >
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            mutate.mutate(new FormData(event.currentTarget));
          }}
        >
          <p>
            订单 {shortId(id)} · <Money value={order.total_fen} />
          </p>
          {action === "simulate-payment" && (
            <Notice>仅向本地支付模拟器提交付款，请在提交后查询订单结果。</Notice>
          )}
          {action === "cancel" && (
            <Field label="取消原因">
              <textarea name="reason" required maxLength={300} rows={3} />
            </Field>
          )}
          {action === "ship" && (
            <>
              <Field label="物流公司">
                <input name="carrier" required maxLength={80} />
              </Field>
              <Field label="物流单号">
                <input name="tracking_number" required maxLength={100} />
              </Field>
              <AddressSnapshot value={order.address_snapshot} />
            </>
          )}
          {action === "deliver" && (
            <p>仅在核实收件人已收到包裹后确认，此操作会开始计算退货申请期限。</p>
          )}
          <ErrorNotice error={mutate.error} />
          <Button type="submit" busy={mutate.isPending}>
            确认提交
          </Button>
        </form>
      </Modal>
    </>
  );
}

const returnDraft = z.object({
  key: z.string(),
  body: z.object({
    expected_version: z.number(),
    reason: z.string(),
    items: z.array(z.object({ order_item_id: z.string(), quantity: z.number() })),
  }),
});
export function ReturnRequestScreen({ id }: { id: string }) {
  return (
    <AuthGate>
      <ReturnRequest id={id} />
    </AuthGate>
  );
}
function ReturnRequest({ id }: { id: string }) {
  const auth = useAuth();
  const router = useRouter();
  const [pending, save] = useStored(`commerce:return:${auth.customer?.id}:${id}`, returnDraft);
  const order = useQuery({
    queryKey: ["customer", "order", id],
    queryFn: () => api<Order>(`customer/orders/${id}`, { validate: orderSchema.parse }),
  });
  const options = useQuery({
    queryKey: ["customer", "return-options", id],
    queryFn: () => api<ReturnOption[]>(`customer/orders/${id}/return-options`),
  });
  const mutation = useMutation({
    mutationFn: async (data: FormData) => {
      const request = pending ?? {
        key: crypto.randomUUID(),
        body: {
          expected_version: order.data?.version ?? 0,
          reason: formText(data, "reason"),
          items: (options.data ?? [])
            .map((item) => ({
              order_item_id: item.order_item_id,
              quantity: Number(data.get(item.order_item_id) ?? 0),
            }))
            .filter((item) => item.quantity > 0),
        },
      };
      if (!request.body.items.length) throw new Error("请至少选择一件要退回的商品。");
      save(request);
      try {
        return await api<Return>(`customer/orders/${id}/returns`, {
          method: "POST",
          key: request.key,
          json: request.body,
        });
      } catch (error) {
        if (
          (!pending && !uncertain(error)) ||
          (error instanceof ApiError &&
            ["ORDER_STATE_CONFLICT", "RETURN_WINDOW_EXPIRED"].includes(error.code))
        )
          save();
        throw error;
      }
    },
    onSuccess: (result) => {
      save();
      router.replace(`/returns/${result.id}`);
    },
    onError: () => {
      void order.refetch();
      void options.refetch();
    },
  });
  return (
    <>
      <PageTitle
        title="申请退货"
        description="按成交单价申请退回部分或全部商品，最终退款以验收与资金结果为准。"
      />
      <ErrorNotice error={order.error || options.error} />
      {pending && (
        <Notice>
          上次提交结果待确认。再次提交会复用原请求、原商品数量与原因，不会新增另一份申请。
        </Notice>
      )}
      {order.isPending || options.isPending ? (
        <Loading />
      ) : (
        order.data && (
          <form
            className="panel stack"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate(new FormData(event.currentTarget));
            }}
          >
            {options.data?.map((option) => {
              const item = order.data.items.find((line) => line.id === option.order_item_id);
              return (
                <div className="return-choice" key={option.order_item_id}>
                  <div>
                    <strong>{item?.title}</strong>
                    <p className="muted">{attributes(item?.attributes)}</p>
                    <p>
                      每件退款 <Money value={option.unit_refund_fen} /> · 可退{" "}
                      {option.quantity_available} 件
                    </p>
                  </div>
                  <Field label="退货数量">
                    <input
                      name={option.order_item_id}
                      type="number"
                      min={0}
                      max={option.quantity_available}
                      defaultValue={
                        pending?.body.items.find(
                          (line) => line.order_item_id === option.order_item_id,
                        )?.quantity ?? 0
                      }
                      disabled={!!pending}
                    />
                  </Field>
                </div>
              );
            })}
            <Field label="申请原因">
              <textarea
                name="reason"
                required
                maxLength={500}
                rows={4}
                defaultValue={pending?.body.reason}
                disabled={!!pending}
              />
            </Field>
            <ErrorNotice error={mutation.error} />
            <Button
              type="submit"
              busy={mutation.isPending}
              disabled={!pending && !options.data?.some((item) => item.quantity_available > 0)}
            >
              {pending ? "使用原请求确认结果" : "提交退货申请"}
            </Button>
          </form>
        )
      )}
    </>
  );
}

export function ReturnsScreen({ admin = false }: { admin?: boolean }) {
  return (
    <AuthGate scope={admin ? "admin" : "customer"} permission={admin ? "aftersales" : undefined}>
      <Returns admin={admin} />
    </AuthGate>
  );
}
function Returns({ admin }: { admin: boolean }) {
  const scope = admin ? "admin" : "customer";
  const [status, setStatus] = useState("");
  const query = useInfiniteQuery({
    queryKey: [scope, "returns", status],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<Return[]>(`${scope}/returns${queryString({ status, cursor: pageParam, limit: 50 })}`),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].id : undefined),
  });
  const items = query.data?.pages.flat() ?? [];
  return (
    <>
      <PageTitle
        title={admin ? "售后申请" : "我的售后"}
        description="审核、寄回、验收与退款进度分别记录。"
      />
      <div className="toolbar">
        <Field label="申请状态">
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            {["requested", "approved", "shipped", "received", "inspected", "rejected"].map(
              (value) => (
                <option key={value} value={value}>
                  {
                    {
                      requested: "待审核",
                      approved: "待寄回",
                      shipped: "已寄回",
                      received: "待验收",
                      inspected: "已验收",
                      rejected: "未通过",
                    }[value]
                  }
                </option>
              ),
            )}
          </select>
        </Field>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          刷新
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : !items.length ? (
        <Empty title="暂无售后申请" />
      ) : (
        <DataTable headers={["申请 / 时间", "商品", "处理状态", "退款状态", "操作"]}>
          {items.map((item) => (
            <tr key={item.id}>
              <td>
                {shortId(item.id)}
                <small>{dateTime(item.created_at)}</small>
              </td>
              <td>
                {item.items[0]?.title}
                <small>{item.items.reduce((sum, line) => sum + line.quantity, 0)} 件</small>
              </td>
              <td>
                <Status
                  value={item.status}
                  label={item.status === "shipped" ? "已寄回" : undefined}
                />
              </td>
              <td>
                {item.refund ? (
                  <>
                    <Money value={item.refund.amount_fen} />
                    <Status value={item.refund.status} />
                  </>
                ) : (
                  "尚未生成退款"
                )}
              </td>
              <td>
                <Link href={`${admin ? "/admin" : ""}/returns/${item.id}`}>查看详情</Link>
              </td>
            </tr>
          ))}
        </DataTable>
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

export function ReturnScreen({ id, admin = false }: { id: string; admin?: boolean }) {
  return (
    <AuthGate scope={admin ? "admin" : "customer"} permission={admin ? "aftersales" : undefined}>
      <ReturnDetail id={id} admin={admin} />
    </AuthGate>
  );
}
function ReturnDetail({ id, admin }: { id: string; admin: boolean }) {
  const scope = admin ? "admin" : "customer";
  const [action, setAction] = useState("");
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: [scope, "return", id],
    queryFn: () => api<Return>(`${scope}/returns/${id}`),
  });
  const mutate = useMutation({
    mutationFn: (data: FormData) =>
      api<Return>(`${scope}/returns/${id}/${action}`, {
        method: "POST",
        json:
          action === "ship"
            ? {
                carrier: formText(data, "carrier"),
                tracking_number: formText(data, "tracking_number"),
              }
            : action === "reject"
              ? { reason: formText(data, "note") }
              : action === "inspect"
                ? { resellable: data.get("resellable") === "true", note: formText(data, "note") }
                : undefined,
      }),
    onSuccess: (result) => {
      setAction("");
      setMessage("处理已提交，请继续核对退款与库存结果。");
      query.refetch();
      return result;
    },
    onError: () => {
      void query.refetch();
    },
  });
  const item = query.data;
  if (query.isPending) return <Loading />;
  if (!item) return <ErrorNotice error={query.error} retry={() => void query.refetch()} />;
  return (
    <>
      <PageTitle
        title={`售后申请 ${shortId(id)}`}
        description={`申请于 ${dateTime(item.created_at)}`}
        action={
          <Button variant="secondary" busy={query.isFetching} onClick={() => void query.refetch()}>
            <ArrowClockwise />
            刷新结果
          </Button>
        }
      />
      <ErrorNotice error={query.error} />
      {message && <Notice>{message}</Notice>}
      <div className="status-banner">
        <div>
          <span>实物处理</span>
          <Status value={item.status} label={item.status === "shipped" ? "已寄回" : undefined} />
        </div>
        <div>
          <span>资金结果</span>
          {item.refund ? <Status value={item.refund.status} /> : "尚未生成退款"}
        </div>
      </div>
      <div className="checkout-layout">
        <div className="stack spaced">
          <section className="panel">
            <h2>申请退回的商品</h2>
            {item.items.map((line, index) => (
              <div key={index} className="order-line">
                <div>
                  <strong>{line.title}</strong>
                  <p>{attributes(line.attributes)}</p>
                  <small>申请数量 {line.quantity} 件</small>
                </div>
                <Money value={line.amount_fen} />
              </div>
            ))}
            <h3>申请原因</h3>
            <p className="preserve-lines">{item.reason}</p>
            {item.inspection_note && (
              <>
                <h3>商家处理说明</h3>
                <p>{item.inspection_note}</p>
              </>
            )}
          </section>
          <section className="panel">
            <h2>寄回与物流</h2>
            {item.return_address ? (
              <AddressSnapshot value={item.return_address} />
            ) : (
              <Notice>商家尚未配置退货地址，请联系商家确认后再寄回。</Notice>
            )}
            {item.carrier && (
              <p>
                寄回物流：{item.carrier} · {item.tracking_number}
              </p>
            )}
            {!admin && item.status === "approved" && item.return_address && (
              <Button onClick={() => setAction("ship")}>填写寄回单号</Button>
            )}
          </section>
        </div>
        <aside className="summary-panel">
          <h2>处理进度</h2>
          <p>验收完成表示实物处理完成；退款需以资金状态“已成功”为准。</p>
          {item.refund && (
            <div className="refund-line">
              <Money value={item.refund.amount_fen} />
              <Status value={item.refund.status} />
              <p>{dateTime(item.refund.confirmed_at)}</p>
            </div>
          )}
          {!admin && (
            <Link className="text-link" href={`/orders/${item.order_id}`}>
              查看原订单 <ArrowRight />
            </Link>
          )}
          {admin && (
            <div className="stack">
              {item.status === "requested" && (
                <>
                  <Button onClick={() => setAction("approve")}>通过退货申请</Button>
                  <Button variant="secondary" onClick={() => setAction("reject")}>
                    拒绝并说明原因
                  </Button>
                </>
              )}
              {item.status === "shipped" && (
                <Button onClick={() => setAction("receive")}>确认已收到退货</Button>
              )}
              {item.status === "received" && (
                <Button onClick={() => setAction("inspect")}>验收并发起退款</Button>
              )}
            </div>
          )}
        </aside>
      </div>
      <Modal
        open={!!action}
        title="确认售后处理"
        onClose={() => !mutate.isPending && setAction("")}
      >
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            mutate.mutate(new FormData(event.currentTarget));
          }}
        >
          {action === "ship" && (
            <>
              <Field label="物流公司">
                <input name="carrier" required maxLength={80} />
              </Field>
              <Field label="物流单号">
                <input name="tracking_number" required maxLength={100} />
              </Field>
            </>
          )}
          {action === "inspect" && (
            <Field label="整份申请的商品是否可重新销售">
              <select name="resellable">
                <option value="false">不可重新销售（不回补可售库存）</option>
                <option value="true">可重新销售（回补可售库存）</option>
              </select>
            </Field>
          )}
          {["reject", "inspect"].includes(action) && (
            <Field label={action === "reject" ? "拒绝原因" : "验收说明"}>
              <textarea name="note" required rows={3} maxLength={500} />
            </Field>
          )}
          <p>
            申请 {shortId(item.id)}，共 {item.items.reduce((sum, line) => sum + line.quantity, 0)}{" "}
            件。
          </p>
          <ErrorNotice error={mutate.error} />
          <Button type="submit" busy={mutate.isPending}>
            确认提交
          </Button>
        </form>
      </Modal>
    </>
  );
}
