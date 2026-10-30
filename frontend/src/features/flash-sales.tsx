"use client";

import Link from "next/link";
import { useState } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowClockwise, ArrowRight, Plus } from "@phosphor-icons/react";
import { useVisible } from "@/components/providers";
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
import { api, queryString } from "@/lib/api";
import { attributes, dateTime, formText, parseFen } from "@/lib/format";
import type { FlashSale, FlashSalePage, ProductPage } from "@/lib/types";
import { ProductImage } from "./catalog";
import "./flash-sales.css";

const statusNames: Record<FlashSale["status"], string> = {
  disabled: "已下架",
  scheduled: "即将开始",
  active: "进行中",
  ended: "已结束",
  sold_out: "已售罄",
  unavailable: "商品已下架",
};

export function FlashSalesScreen() {
  const visible = useVisible();
  const query = useInfiniteQuery({
    queryKey: ["flash-sales"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<FlashSalePage>(`customer/flash-sales${queryString({ cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: visible ? 15000 : false,
    staleTime: 0,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        eyebrow="限时 · 限量"
        title="限时秒杀"
        description="选择活动商品，核对秒杀价后直接结算。剩余数量以创建订单时为准。"
        action={
          <Button variant="secondary" busy={query.isFetching} onClick={() => void query.refetch()}>
            <ArrowClockwise />
            刷新活动
          </Button>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <div className="flash-sale-list">
          {items.map((sale) => (
            <article className="flash-sale-row" key={sale.id}>
              <Link
                href={`/products/${sale.product_id}?sku=${sale.sku_id}`}
                aria-label={`查看${sale.title}`}
              >
                <ProductImage mediaId={sale.cover_media_id} title={sale.title} />
              </Link>
              <div className="stack">
                <Status
                  value={sale.status === "active" ? "paid" : "neutral"}
                  label={statusNames[sale.status]}
                />
                <h2>
                  <Link href={`/products/${sale.product_id}?sku=${sale.sku_id}`}>{sale.title}</Link>
                </h2>
                <p className="muted">{attributes(sale.attributes) || "标准规格"}</p>
                <div className="flash-sale-prices">
                  <span>
                    秒杀价 <Money value={sale.price_fen} />
                  </span>
                  <small className="muted">
                    日常价 <Money value={sale.regular_price_fen} />
                  </small>
                </div>
                <p className="muted">
                  {dateTime(sale.starts_at)} 至 {dateTime(sale.ends_at)}（北京时间）
                </p>
              </div>
              <div className="flash-sale-action stack">
                <strong>剩余 {sale.available} 件</strong>
                <small className="muted">本次活动共 {sale.stock} 件</small>
                {sale.status === "active" ? (
                  <Link className="button primary" href={`/checkout?flash_sale=${sale.id}`}>
                    立即抢购 <ArrowRight />
                  </Link>
                ) : (
                  <Button disabled>{statusNames[sale.status]}</Button>
                )}
                <small className="muted">每次结算 1 件</small>
              </div>
            </article>
          ))}
        </div>
      ) : (
        !query.error && (
          <Empty
            title="暂无秒杀活动"
            action={
              <Link className="button secondary" href="/products">
                浏览全部商品
              </Link>
            }
          >
            活动上架后会在这里展示，稍后再来看看。
          </Empty>
        )
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多活动
        </Button>
      )}
    </>
  );
}

export function AdminFlashSalesScreen() {
  return (
    <AuthGate scope="admin" permission="catalog">
      <AdminFlashSales />
    </AuthGate>
  );
}

function AdminFlashSales() {
  const [editing, setEditing] = useState<FlashSale | "new">();
  const [message, setMessage] = useState("");
  const query = useInfiniteQuery({
    queryKey: ["admin", "flash-sales"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<FlashSalePage>(`admin/flash-sales${queryString({ cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        title="秒杀管理"
        description="按商品规格配置秒杀价、活动数量和时间，可同时上架多个商品。"
        action={
          <Button
            onClick={() => {
              setMessage("");
              setEditing("new");
            }}
          >
            <Plus />
            新增秒杀商品
          </Button>
        }
      />
      <Notice>
        活动数量是秒杀销售额度，与普通商品共用实物库存。取消订单后回补额度；已发货商品退货不回补秒杀额度。
      </Notice>
      {message && <Notice>{message}</Notice>}
      <div className="toolbar">
        <Button variant="secondary" busy={query.isFetching} onClick={() => void query.refetch()}>
          <ArrowClockwise />
          刷新列表
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <DataTable
          headers={[
            "商品 / 规格",
            "秒杀价",
            "活动数量",
            "已占用及成交",
            "当前可售",
            "活动时间",
            "状态",
            "操作",
          ]}
        >
          {items.map((sale) => (
            <tr key={sale.id}>
              <td>
                <Link href={`/admin/products/${sale.product_id}`}>{sale.title}</Link>
                <small>{attributes(sale.attributes) || "标准规格"}</small>
              </td>
              <td>
                <Money value={sale.price_fen} />
              </td>
              <td>{sale.stock}</td>
              <td>{sale.claimed}</td>
              <td>{sale.available}</td>
              <td>
                {dateTime(sale.starts_at)}
                <small>至 {dateTime(sale.ends_at)}</small>
              </td>
              <td>
                <Status
                  value={sale.status === "active" ? "paid" : "neutral"}
                  label={statusNames[sale.status]}
                />
              </td>
              <td>
                <Button variant="secondary" onClick={() => setEditing(sale)}>
                  编辑
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && (
          <Empty title="还没有秒杀商品">点击“新增秒杀商品”，选择已有商品规格并设置活动。</Empty>
        )
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多活动
        </Button>
      )}
      <Modal
        open={!!editing}
        title={editing === "new" ? "新增秒杀商品" : "编辑秒杀商品"}
        onClose={() => setEditing(undefined)}
        wide
      >
        {editing && (
          <SaleForm
            key={editing === "new" ? "new" : `${editing.id}:${editing.version}`}
            sale={editing === "new" ? undefined : editing}
            onSaved={() => {
              setEditing(undefined);
              setMessage("秒杀配置已保存。");
              void query.refetch();
            }}
          />
        )}
      </Modal>
    </>
  );
}

// 管理界面固定以北京时间编辑，与列表显示和服务端带时区契约保持一致。
function beijingInput(value: string) {
  return new Date(Date.parse(value) + 8 * 3600000).toISOString().slice(0, 16);
}

function SaleForm({ sale, onSaved }: { sale?: FlashSale; onSaved: () => void }) {
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [skuId, setSkuId] = useState(sale?.sku_id ?? "");
  const products = useInfiniteQuery({
    queryKey: ["admin", "flash-sale-products", search],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<ProductPage>(`admin/products${queryString({ q: search, cursor: pageParam, limit: 50 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: !sale,
  });
  const choices =
    products.data?.pages
      .flatMap((page) => page.items)
      .flatMap((product) => product.skus.map((sku) => ({ product, sku }))) ?? [];
  const save = useMutation({
    mutationFn: (data: FormData) => {
      if (!skuId) throw new Error("请先选择商品规格。");
      const start = formText(data, "starts_at");
      const end = formText(data, "ends_at");
      if (end <= start) throw new Error("结束时间必须晚于开始时间。");
      return api<FlashSale>(`admin/flash-sales${sale ? `/${sale.id}` : ""}`, {
        method: sale ? "PUT" : "POST",
        json: {
          sku_id: skuId,
          price_fen: parseFen(formText(data, "price")),
          stock: Number(formText(data, "stock")),
          starts_at: `${start}:00+08:00`,
          ends_at: `${end}:00+08:00`,
          active: data.get("active") === "on",
          ...(sale ? { expected_version: sale.version } : {}),
        },
      });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["flash-sales"] });
      void client.invalidateQueries({ queryKey: ["flash-sale"] });
      onSaved();
    },
  });
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(new FormData(event.currentTarget));
      }}
    >
      <fieldset disabled={save.isPending} className="stack">
        {sale ? (
          <p>
            <strong>{sale.title}</strong>
            <br />
            {attributes(sale.attributes) || "标准规格"}
          </p>
        ) : (
          <>
            <Field label="搜索已有商品">
              <input
                type="search"
                maxLength={80}
                value={search}
                onChange={(event) => {
                  setSearch(event.target.value);
                  setSkuId("");
                }}
                placeholder="按商品名称搜索"
              />
            </Field>
            <ErrorNotice error={products.error} retry={() => void products.refetch()} />
            {products.isPending ? (
              <Loading rows={1} />
            ) : (
              <Field label="商品规格" hint="商品与规格需先上架；一个活动对应一个规格。">
                <select required value={skuId} onChange={(event) => setSkuId(event.target.value)}>
                  <option value="">请选择商品规格</option>
                  {choices.map(({ product, sku }) => (
                    <option key={sku.id} value={sku.id} disabled={!product.active || !sku.active}>
                      {product.title} · {attributes(sku.attributes) || sku.code} · 可售{" "}
                      {sku.available} 件
                    </option>
                  ))}
                </select>
              </Field>
            )}
            {!products.isPending && !products.error && !choices.length && (
              <p className="muted">未找到商品，请换个关键词或先在商品管理中添加规格。</p>
            )}
            {products.hasNextPage && (
              <Button
                variant="ghost"
                busy={products.isFetchingNextPage}
                onClick={() => void products.fetchNextPage()}
              >
                加载更多商品规格
              </Button>
            )}
          </>
        )}
        <div className="two-fields">
          <Field label="秒杀价（元）">
            <input
              name="price"
              type="number"
              min="0.01"
              max="1000000"
              step="0.01"
              required
              defaultValue={sale ? (sale.price_fen / 100).toFixed(2) : undefined}
            />
          </Field>
          <Field
            label="活动总数量（件）"
            hint={
              sale
                ? `已占用及成交 ${sale.claimed} 件，总数量不可低于此值。`
                : "上架时剩余额度不能超过实物可售库存。"
            }
          >
            <input
              name="stock"
              type="number"
              min={sale?.claimed ?? 0}
              max={1000000}
              step={1}
              required
              defaultValue={sale?.stock}
            />
          </Field>
        </div>
        <div className="two-fields">
          <Field label="开始时间（北京时间）">
            <input
              name="starts_at"
              type="datetime-local"
              required
              defaultValue={sale ? beijingInput(sale.starts_at) : undefined}
            />
          </Field>
          <Field label="结束时间（北京时间）">
            <input
              name="ends_at"
              type="datetime-local"
              required
              defaultValue={sale ? beijingInput(sale.ends_at) : undefined}
            />
          </Field>
        </div>
        <label className="flash-sale-toggle">
          <input name="active" type="checkbox" defaultChecked={sale?.active ?? false} />
          上架活动（到开始时间后可购买）
        </label>
        <p className="muted">
          修改配置后，未下单的旧报价需要重新确认。已创建订单的成交价保持不变。
        </p>
        <ErrorNotice error={save.error} />
        <Button type="submit" busy={save.isPending}>
          保存秒杀配置
        </Button>
      </fieldset>
    </form>
  );
}
