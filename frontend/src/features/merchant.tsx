"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, queryString } from "@/lib/api";
import { attributes, dateTime, formText, parseFen, shortId } from "@/lib/format";
import type { components } from "@/lib/api.generated";
import type { Category, Order, OrderPage, Product, Return, Sku } from "@/lib/types";
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
import { useVisible } from "@/components/providers";
import "./merchant.css";
import {
  MerchantCustomers,
  MerchantDiagnostics,
  MerchantInventory,
  MerchantPromotions,
  OrderExpenses,
} from "./merchant-ops";

type Schema = components["schemas"];
type Shop = Schema["ShopOut"];
type MerchantPage = Schema["MerchantProductPage"];
type MerchantProduct = Schema["MerchantProductOut"];
type Cost = Schema["CostOut"];
type Report = Schema["MerchantReport"];
type Conversation = Schema["ConversationOut"];
type Run = Schema["SupportRunOut"];
const shopStatus = { active: "营业中", suspended: "暂停经营", closed: "已关闭" };

export function MerchantScreen() {
  return (
    <AuthGate>
      <Merchant />
    </AuthGate>
  );
}

function Merchant() {
  const search = useSearchParams();
  const router = useRouter();
  const query = useQuery({
    queryKey: ["customer", "merchant", "shops"],
    queryFn: () => api<Shop[]>("merchant/shops"),
  });
  const selected = query.data?.find((shop) => shop.id === search.get("shop")) ?? query.data?.[0];
  return (
    <div className="merchant-page stack">
      <PageTitle
        eyebrow="经营店铺"
        title="商家工作台"
        description="管理当前店铺的商品、订单与经营数据。"
        action={
          <Link className="button secondary" href="/account">
            我的账户
          </Link>
        }
      />
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : selected ? (
        <>
          <div className="toolbar">
            <Field label="当前店铺">
              <select
                value={selected.id}
                onChange={(event) => router.replace(`/merchant?shop=${event.target.value}`)}
              >
                {query.data?.map((shop) => (
                  <option key={shop.id} value={shop.id}>
                    {shop.name} · {shopStatus[shop.status]}
                  </option>
                ))}
              </select>
            </Field>
            <Button
              variant="secondary"
              busy={query.isFetching}
              onClick={() => void query.refetch()}
            >
              刷新店铺
            </Button>
          </div>
          {selected.status !== "active" && (
            <Notice>
              {selected.status === "suspended"
                ? "店铺暂停新交易，已有订单与售后仍可处理。"
                : "店铺已关闭，可查看历史经营记录。"}
            </Notice>
          )}
          <MerchantWorkspace key={selected.id} shop={selected} />
        </>
      ) : (
        !query.error && (
          <Empty title="当前账号还没有店铺">请联系平台为此账号开通店铺。开通后刷新即可进入。</Empty>
        )
      )}
    </div>
  );
}

function MerchantWorkspace({ shop }: { shop: Shop }) {
  const search = useSearchParams();
  const router = useRouter();
  const tab = search.get("tab") ?? "products";
  return (
    <div className="stack">
      <nav className="merchant-tabs" aria-label="店铺功能">
        {[
          ["products", "商品与库存"],
          ["orders", "订单履约"],
          ["returns", "售后处理"],
          ["reports", "经营报表"],
          ["diagnostics", "经营诊断"],
          ["inventory", "库存与补货"],
          ["customers", "客户分群"],
          ["promotions", "活动方案"],
          ["agent", "经营助手"],
          ["settings", "店铺资料"],
        ].map(([key, name]) => (
          <Button
            key={key}
            variant={tab === key ? "primary" : "secondary"}
            aria-current={tab === key ? "page" : undefined}
            onClick={() => router.replace(`/merchant?shop=${shop.id}&tab=${key}`)}
          >
            {name}
          </Button>
        ))}
      </nav>
      {tab === "products" && <MerchantProducts shop={shop} />}
      {tab === "orders" && <MerchantOrders shop={shop} />}
      {tab === "returns" && <MerchantReturns shop={shop} />}
      {tab === "reports" && <MerchantReports shop={shop} />}
      {tab === "diagnostics" && <MerchantDiagnostics shop={shop} />}
      {tab === "inventory" && <MerchantInventory shop={shop} />}
      {tab === "customers" && <MerchantCustomers shop={shop} />}
      {tab === "promotions" && <MerchantPromotions shop={shop} />}
      {tab === "agent" && <MerchantAgent shop={shop} />}
      {tab === "settings" && <ShopForm shop={shop} />}
    </div>
  );
}

function MerchantProducts({ shop }: { shop: Shop }) {
  const [edit, setEdit] = useState<Product | "new">();
  const [skuProduct, setSkuProduct] = useState<MerchantProduct>();
  const query = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "products"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<MerchantPage>(
        `merchant/shops/${shop.id}/products${queryString({ cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const hide = useMutation({
    mutationFn: (product: Product) =>
      api(`merchant/shops/${shop.id}/products/${product.id}`, {
        method: "PUT",
        json: {
          title: product.title,
          brand: product.brand,
          description: product.description,
          category_id: product.category_id,
          active: false,
          expected_version: product.version,
        },
      }),
    onSuccess: () => query.refetch(),
  });
  return (
    <section className="stack">
      <div className="section-title">
        <h2>商品与库存</h2>
        <Button disabled={shop.status !== "active"} onClick={() => setEdit("new")}>
          新增商品
        </Button>
      </div>
      <ErrorNotice error={query.error || hide.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : rows.length ? (
        <DataTable headers={["商品", "上架状态", "规格 / 可售", "操作"]}>
          {rows.map((row) => (
            <tr key={row.product.id}>
              <td>
                {row.product.title}
                <small>{row.product.brand}</small>
              </td>
              <td>{row.product.active ? "已上架" : "已下架"}</td>
              <td>
                {row.product.skus.map((sku) => (
                  <p key={sku.id}>
                    {sku.code} · {sku.available} 件 · <Money value={sku.price_fen} />
                  </p>
                ))}
              </td>
              <td>
                <div className="actions">
                  <Button
                    variant="secondary"
                    disabled={shop.status === "closed"}
                    onClick={() => setEdit(row.product)}
                  >
                    编辑商品
                  </Button>
                  <Button variant="secondary" onClick={() => setSkuProduct(row)}>
                    规格与库存
                  </Button>
                  {row.product.active && (
                    <Button
                      variant="secondary"
                      busy={hide.isPending}
                      disabled={shop.status === "closed"}
                      onClick={() => hide.mutate(row.product)}
                    >
                      下架商品
                    </Button>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && (
          <Empty title="本店还没有商品">新增商品后，维护规格、成本与库存，再上架销售。</Empty>
        )
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多商品
        </Button>
      )}
      <Modal
        open={!!edit}
        title={edit === "new" ? "新增商品" : "编辑商品"}
        onClose={() => setEdit(undefined)}
      >
        {edit && (
          <ProductForm
            shop={shop}
            product={edit === "new" ? undefined : edit}
            onDone={() => {
              setEdit(undefined);
              void query.refetch();
            }}
          />
        )}
      </Modal>
      <Modal
        open={!!skuProduct}
        wide
        title="规格、成本与库存"
        onClose={() => setSkuProduct(undefined)}
      >
        {skuProduct && (
          <SkuManager
            shop={shop}
            row={skuProduct}
            onDone={() => {
              setSkuProduct(undefined);
              void query.refetch();
            }}
          />
        )}
      </Modal>
    </section>
  );
}

function ProductForm({
  shop,
  product,
  onDone,
}: {
  shop: Shop;
  product?: Product;
  onDone: () => void;
}) {
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<Category[]>("customer/categories"),
  });
  const save = useMutation({
    mutationFn: (form: FormData) =>
      api(`merchant/shops/${shop.id}/products${product ? `/${product.id}` : ""}`, {
        method: product ? "PUT" : "POST",
        json: {
          title: formText(form, "title"),
          brand: formText(form, "brand"),
          description: formText(form, "description"),
          category_id: formText(form, "category"),
          active: form.get("active") === "on",
          ...(product ? { expected_version: product.version } : {}),
        },
      }),
    onSuccess: onDone,
  });
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(new FormData(event.currentTarget));
      }}
    >
      <Field label="商品名称">
        <input
          name="title"
          defaultValue={product?.title}
          required
          maxLength={200}
          disabled={shop.status !== "active"}
        />
      </Field>
      <Field label="品牌">
        <input
          name="brand"
          defaultValue={product?.brand}
          maxLength={100}
          disabled={shop.status !== "active"}
        />
      </Field>
      <Field label="分类">
        <select
          name="category"
          defaultValue={product?.category_id}
          required
          disabled={shop.status !== "active"}
        >
          {categories.data?.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="商品说明">
        <textarea
          name="description"
          defaultValue={product?.description}
          maxLength={4000}
          rows={4}
          disabled={shop.status !== "active"}
        />
      </Field>
      <label className="actions">
        <input
          name="active"
          type="checkbox"
          defaultChecked={product?.active}
          disabled={shop.status !== "active"}
        />
        上架销售
      </label>
      <ErrorNotice error={save.error || categories.error} />
      <Button
        type="submit"
        busy={save.isPending}
        disabled={categories.isPending || !!categories.error || shop.status !== "active"}
      >
        保存商品
      </Button>
    </form>
  );
}

function SkuManager({
  shop,
  row,
  onDone,
}: {
  shop: Shop;
  row: MerchantProduct;
  onDone: () => void;
}) {
  const [selected, setSelected] = useState<Sku | "new">(row.product.skus[0] ?? "new");
  const sku = selected === "new" ? undefined : selected;
  const cost = row.costs.find((item) => item.sku_id === sku?.id);
  const save = useMutation({
    mutationFn: (form: FormData) => {
      const values: Record<string, string> = {};
      for (const line of formText(form, "attributes")
        .split("\n")
        .filter((line) => line.trim())) {
        const separator = line.search(/[:：]/);
        if (separator <= 0 || !line.slice(separator + 1).trim())
          throw new Error("每行规格请填写“名称：值”。");
        values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
      }
      return api(
        `merchant/shops/${shop.id}/products/${row.product.id}/skus${sku ? `/${sku.id}` : ""}`,
        {
          method: sku ? "PUT" : "POST",
          json: {
            code: formText(form, "code"),
            price_fen: parseFen(formText(form, "price")),
            attributes: values,
            active: form.get("active") === "on",
            ...(sku ? { expected_version: sku.version } : {}),
          },
        },
      );
    },
    onSuccess: onDone,
  });
  return (
    <div className="stack">
      <Field label="选择规格">
        <select
          value={sku?.id ?? "new"}
          onChange={(event) =>
            setSelected(row.product.skus.find((item) => item.id === event.target.value) ?? "new")
          }
        >
          <option value="new">新增规格</option>
          {row.product.skus.map((item) => (
            <option key={item.id} value={item.id}>
              {item.code} · {attributes(item.attributes)}
            </option>
          ))}
        </select>
      </Field>
      <form
        className="stack"
        key={sku?.id ?? "new"}
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(new FormData(event.currentTarget));
        }}
      >
        <div className="two-fields">
          <Field label="规格编码">
            <input name="code" required maxLength={80} defaultValue={sku?.code} />
          </Field>
          <Field label="售价（元）">
            <input
              name="price"
              type="number"
              min="0.01"
              max="1000000"
              step="0.01"
              required
              defaultValue={sku ? sku.price_fen / 100 : undefined}
            />
          </Field>
        </div>
        <Field label="规格属性" hint="每行填写一项，例如“颜色：白色”“容量：500ml”。">
          <textarea
            name="attributes"
            rows={3}
            defaultValue={Object.entries(sku?.attributes ?? {})
              .map(([name, value]) => `${name}：${value}`)
              .join("\n")}
          />
        </Field>
        <label className="actions">
          <input name="active" type="checkbox" defaultChecked={sku?.active ?? true} />
          规格可售
        </label>
        <ErrorNotice error={save.error} />
        <Button type="submit" busy={save.isPending} disabled={shop.status !== "active"}>
          保存规格
        </Button>
      </form>
      {sku && cost && <CostStockForm key={sku.id} shop={shop} cost={cost} onDone={onDone} />}
      <MediaUpload shop={shop} productId={row.product.id} />
    </div>
  );
}

function CostStockForm({ shop, cost, onDone }: { shop: Shop; cost: Cost; onDone: () => void }) {
  const [key, setKey] = useState(() => crypto.randomUUID());
  const saveCost = useMutation({
    mutationFn: (form: FormData) =>
      api(`merchant/shops/${shop.id}/skus/${cost.sku_id}/cost`, {
        method: "PUT",
        json: {
          cost_fen: formText(form, "cost") === "" ? null : parseFen(formText(form, "cost")),
          expected_version: cost.cost_version,
        },
      }),
    onSuccess: onDone,
  });
  const adjust = useMutation({
    mutationFn: (form: FormData) =>
      api(`merchant/shops/${shop.id}/inventory/${cost.sku_id}/adjust`, {
        method: "POST",
        key,
        json: { delta: Number(form.get("delta")), reason: formText(form, "reason") },
      }),
    onSuccess: () => {
      setKey(crypto.randomUUID());
      onDone();
    },
  });
  return (
    <>
      <form
        className="stack panel"
        onSubmit={(event) => {
          event.preventDefault();
          saveCost.mutate(new FormData(event.currentTarget));
        }}
      >
        <h3>单位商品成本</h3>
        <Field label="成本（元）" hint="留空表示未知；修改只影响后续创建的订单。">
          <input
            name="cost"
            type="number"
            min="0"
            max="1000000"
            step="0.01"
            defaultValue={cost.cost_fen === null ? "" : cost.cost_fen / 100}
          />
        </Field>
        <ErrorNotice error={saveCost.error} />
        <Button type="submit" busy={saveCost.isPending} disabled={shop.status !== "active"}>
          保存成本
        </Button>
      </form>
      <form
        className="stack panel"
        onSubmit={(event) => {
          event.preventDefault();
          adjust.mutate(new FormData(event.currentTarget));
        }}
      >
        <h3>库存调整</h3>
        <p>
          实物 {cost.on_hand} · 预留 {cost.reserved} · 已分配 {cost.allocated}
        </p>
        <Field label="调整数量" hint="入库填写正数，盘亏填写负数。">
          <input name="delta" type="number" min={-1000000} max={1000000} step={1} required />
        </Field>
        <Field label="调整原因">
          <input name="reason" maxLength={300} required />
        </Field>
        <ErrorNotice error={adjust.error} />
        <Button type="submit" busy={adjust.isPending} disabled={shop.status !== "active"}>
          确认调整库存
        </Button>
      </form>
    </>
  );
}

function MediaUpload({ shop, productId }: { shop: Shop; productId: string }) {
  const upload = useMutation({
    mutationFn: (file: File) =>
      api(`merchant/shops/${shop.id}/products/${productId}/media`, {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type },
      }),
  });
  return (
    <section className="stack">
      <h3>商品图片</h3>
      <Field label="上传图片" hint="最多 8 MiB，上传后作为商品图片保存。">
        <input
          type="file"
          accept="image/jpeg,image/png,image/webp"
          disabled={shop.status !== "active" || upload.isPending}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
          }}
        />
      </Field>
      <ErrorNotice error={upload.error} />
      {upload.isSuccess && <Notice>图片已保存。</Notice>}
    </section>
  );
}

function MerchantOrders({ shop }: { shop: Shop }) {
  const [selected, setSelected] = useState<Order>();
  const query = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "orders"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<OrderPage>(
        `merchant/shops/${shop.id}/orders${queryString({ cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => page.next_cursor,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section className="stack">
      <div className="section-title">
        <h2>本店订单</h2>
        <Button variant="secondary" onClick={() => void query.refetch()}>
          刷新订单
        </Button>
      </div>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <DataTable headers={["订单 / 时间", "商品", "金额", "状态", "操作"]}>
          {items.map((order) => (
            <tr key={order.id}>
              <td>
                {shortId(order.id)}
                <small>{dateTime(order.created_at)}</small>
              </td>
              <td>{order.items[0]?.title}</td>
              <td>
                <Money value={order.total_fen} />
              </td>
              <td>
                <Status value={order.status} />
                <Status value={order.fulfillment_status} />
              </td>
              <td>
                <Button variant="secondary" onClick={() => setSelected(order)}>
                  查看与处理
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && <Empty title="本店暂无订单">成交后可在这里处理发货。</Empty>
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          onClick={() => void query.fetchNextPage()}
          busy={query.isFetchingNextPage}
        >
          加载更多订单
        </Button>
      )}
      <Modal open={!!selected} wide title="订单履约" onClose={() => setSelected(undefined)}>
        {selected && (
          <MerchantOrder
            key={selected.id}
            shop={shop}
            orderId={selected.id}
            onDone={() => void query.refetch()}
          />
        )}
      </Modal>
    </section>
  );
}

function MerchantOrder({
  shop,
  orderId,
  onDone,
}: {
  shop: Shop;
  orderId: string;
  onDone: () => void;
}) {
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "order", orderId],
    queryFn: () => api<Order>(`merchant/shops/${shop.id}/orders/${orderId}`),
  });
  const order = query.data;
  const action =
    order?.fulfillment_status === "unfulfilled"
      ? "pick"
      : order?.fulfillment_status === "fulfilling"
        ? "ship"
        : "deliver";
  const save = useMutation({
    mutationFn: (form: FormData) =>
      api(`merchant/shops/${shop.id}/orders/${orderId}/${action}`, {
        method: "POST",
        json: {
          expected_version: order?.version,
          ...(action === "ship"
            ? { carrier: formText(form, "carrier"), tracking_number: formText(form, "tracking") }
            : {}),
        },
      }),
    onSuccess: () => {
      void query.refetch();
      onDone();
    },
    onError: () => void query.refetch(),
  });
  if (query.isPending) return <Loading />;
  if (!order) return <ErrorNotice error={query.error} />;
  return (
    <div className="stack">
      <ErrorNotice error={query.error} />
      <p>
        订单 {shortId(order.id)} · <Money value={order.total_fen} />
      </p>
      <div className="actions">
        <Status value={order.status} />
        <Status value={order.fulfillment_status} />
      </div>
      {order.items.map((item) => (
        <p key={item.id}>
          {item.title} · {attributes(item.attributes)} × {item.quantity}
        </p>
      ))}
      <address>{Object.values(order.address_snapshot).join(" · ")}</address>
      <p>
        已退款 <Money value={order.refunded_fen} /> · 处理中{" "}
        <Money value={order.refund_reserved_fen} />
      </p>
      <OrderExpenses shop={shop} orderId={order.id} />
      {order.status === "paid" && order.fulfillment_status !== "delivered" && (
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(new FormData(event.currentTarget));
          }}
        >
          {action === "ship" && (
            <>
              <Field label="物流公司">
                <input name="carrier" required maxLength={80} />
              </Field>
              <Field label="物流单号">
                <input name="tracking" required maxLength={100} />
              </Field>
            </>
          )}
          <ErrorNotice error={save.error} />
          <Button type="submit" busy={save.isPending} disabled={shop.status === "closed"}>
            {action === "pick" ? "开始拣货" : action === "ship" ? "确认发货" : "核实后确认签收"}
          </Button>
        </form>
      )}
    </div>
  );
}

function MerchantReturns({ shop }: { shop: Shop }) {
  const [selected, setSelected] = useState<Return>();
  const query = useInfiniteQuery({
    queryKey: ["customer", "merchant", shop.id, "returns"],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<Return[]>(
        `merchant/shops/${shop.id}/returns${queryString({ cursor: pageParam, limit: 20 })}`,
      ),
    getNextPageParam: (page) => (page.length === 20 ? page[page.length - 1].id : undefined),
  });
  const items = query.data?.pages.flat() ?? [];
  return (
    <section className="stack">
      <h2>售后申请</h2>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <DataTable headers={["商品", "原因", "状态", "退款", "操作"]}>
          {items.map((item) => (
            <tr key={item.id}>
              <td>{item.items[0]?.title}</td>
              <td>{item.reason}</td>
              <td>
                <Status value={item.status} />
              </td>
              <td>
                {item.refund ? (
                  <>
                    <Money value={item.refund.amount_fen} />
                    <Status value={item.refund.status} />
                  </>
                ) : (
                  "尚未退款"
                )}
              </td>
              <td>
                <Button variant="secondary" onClick={() => setSelected(item)}>
                  查看处理
                </Button>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && <Empty title="本店暂无售后申请" />
      )}
      {query.hasNextPage && (
        <Button
          variant="secondary"
          busy={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          加载更多售后
        </Button>
      )}
      <Modal open={!!selected} title="处理售后" onClose={() => setSelected(undefined)}>
        {selected && (
          <MerchantReturn
            key={selected.id}
            shop={shop}
            item={selected}
            onDone={() => {
              setSelected(undefined);
              void query.refetch();
            }}
          />
        )}
      </Modal>
    </section>
  );
}

function MerchantReturn({ shop, item, onDone }: { shop: Shop; item: Return; onDone: () => void }) {
  const [validation, setValidation] = useState("");
  const save = useMutation({
    mutationFn: ({ form, action }: { form: FormData; action: string }) =>
      api(
        `merchant/shops/${shop.id}/returns/${item.id}/${action}${action === "reject" ? queryString({ reason: formText(form, "note") }) : ""}`,
        {
          method: "POST",
          json:
            action === "inspection"
              ? { note: formText(form, "note"), resellable: form.get("resellable") === "true" }
              : undefined,
        },
      ),
    onSuccess: onDone,
  });
  return (
    <form className="stack" onSubmit={(event) => event.preventDefault()}>
      <p>{item.reason}</p>
      {item.items.map((line) => (
        <p key={line.order_item_id}>
          {line.title} × {line.quantity} · <Money value={line.amount_fen} />
        </p>
      ))}
      <Status value={item.status} />
      {item.carrier && (
        <p>
          寄回：{item.carrier} · {item.tracking_number}
        </p>
      )}
      <Field label="处理说明">
        <textarea name="note" maxLength={500} rows={3} onChange={() => setValidation("")} />
      </Field>
      {item.status === "received" && (
        <Field label="商品处理">
          <select name="resellable">
            <option value="false">报损，不入库</option>
            <option value="true">可再次销售，验收后入库</option>
          </select>
        </Field>
      )}
      <ErrorNotice error={save.error} />
      {validation && (
        <p className="error-text" role="alert">
          {validation}
        </p>
      )}
      <div className="actions">
        {(item.status === "requested"
          ? [
              ["approve", "同意退货"],
              ["reject", "拒绝申请"],
            ]
          : item.status === "shipped"
            ? [["receive", "确认收件"]]
            : item.status === "received"
              ? [["inspection", "确认验收并发起退款"]]
              : []
        ).map(([action, label]) => (
          <Button
            key={action}
            busy={save.isPending}
            disabled={shop.status === "closed"}
            onClick={(event) => {
              const form = event.currentTarget.form;
              if (!form) return;
              const data = new FormData(form);
              if (["reject", "inspection"].includes(action) && !formText(data, "note")) {
                setValidation("请填写拒绝原因或验收说明。");
                form.querySelector("textarea")?.focus();
                return;
              }
              save.mutate({ form: data, action });
            }}
          >
            {label}
          </Button>
        ))}
      </div>
      <p className="muted">验收与退款结果分别记录，退款处理中不代表资金已到账。</p>
    </form>
  );
}

function MerchantReports({ shop }: { shop: Shop }) {
  const [range, setRange] = useState(() => {
    const current = new Date();
    const today = current.toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" });
    current.setUTCDate(current.getUTCDate() + 1);
    return {
      start: today.slice(0, 8) + "01",
      end: current.toLocaleDateString("en-CA", { timeZone: "Asia/Shanghai" }),
    };
  });
  const query = useQuery({
    queryKey: ["customer", "merchant", shop.id, "reports", range],
    queryFn: () => api<Report>(`merchant/shops/${shop.id}/reports${queryString(range)}`),
    enabled: range.start < range.end,
  });
  const row = query.data;
  return (
    <section className="stack">
      <h2>本店经营报表</h2>
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          setRange({ start: formText(form, "start"), end: formText(form, "end") });
        }}
      >
        <Field label="开始日期">
          <input name="start" type="date" required defaultValue={range.start} />
        </Field>
        <Field label="结束日期（不包含）">
          <input name="end" type="date" required defaultValue={range.end} />
        </Field>
        <Button type="submit">查询经营数据</Button>
      </form>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {!query.isEnabled ? (
        <Notice>请选择晚于开始日期的结束日期。</Notice>
      ) : query.isPending ? (
        <Loading />
      ) : (
        row && (
          <>
            <p className="muted">上海时区 · 截至 {dateTime(row.as_of)}</p>
            <div className="stat-strip">
              <div>
                <span>期间收款</span>
                <strong>
                  <Money value={row.cash.receipts_fen} />
                </strong>
              </div>
              <div>
                <span>期间退款</span>
                <strong>
                  <Money value={row.cash.refunds_fen} />
                </strong>
              </div>
              <div>
                <span>期间净收款</span>
                <strong>
                  <Money value={row.cash.net_receipts_fen} />
                </strong>
              </div>
              <div>
                <span>期间购买人数</span>
                <strong>{row.customers.buyers}</strong>
              </div>
            </div>
            <section className="panel stack">
              <h3>所选期间成交订单截至当前的表现</h3>
              <p>
                支付销量 {row.performance.paid_quantity} 件 · 已验收退货{" "}
                {row.performance.returned_quantity} 件
              </p>
              <p>
                商品净收入{" "}
                {row.performance.net_goods_fen === null ? (
                  "未知"
                ) : (
                  <Money value={row.performance.net_goods_fen} />
                )}{" "}
                · 可计算部分毛利{" "}
                {row.performance.gross_profit_fen === null ? (
                  "未知"
                ) : (
                  <Money value={row.performance.gross_profit_fen} />
                )}
              </p>
              <p>
                成本金额覆盖率 {percent(row.performance.cost_coverage)} · 可计算毛利明细覆盖率{" "}
                {percent(row.performance.profit_coverage)}
              </p>
              <p>
                退款处理中 <Money value={row.refund_pending_fen} /> · 待处理退货{" "}
                {row.pending_returns} 份
              </p>
            </section>
            <DataTable headers={["商品", "支付数量", "商品净收入", "可计算部分毛利", "覆盖率"]}>
              {row.products.map((product, index) => (
                <tr key={product.product_id ?? index}>
                  <td>{product.title}</td>
                  <td>{product.paid_quantity}</td>
                  <td>
                    {product.net_goods_fen === null ? (
                      "未知"
                    ) : (
                      <Money value={product.net_goods_fen} />
                    )}
                  </td>
                  <td>
                    {product.gross_profit_fen === null ? (
                      "未知"
                    ) : (
                      <Money value={product.gross_profit_fen} />
                    )}
                  </td>
                  <td>{percent(product.profit_coverage)}</td>
                </tr>
              ))}
            </DataTable>
            <section className="panel stack">
              <h3>本店客户购买统计</h3>
              <p>
                新客 {row.customers.new_buyers} 人 · 老客 {row.customers.returning_buyers} 人 ·
                支付订单 {row.customers.paid_orders} 笔
              </p>
              {row.customers.spending_excluded_buyers > 0 && (
                <p className="muted">
                  有 {row.customers.spending_excluded_buyers}{" "}
                  位客户存在历史退款分配缺失，未计入消费额分布。
                </p>
              )}
              {[
                ["收货地区", row.customers.regions],
                ["成交品类", row.customers.categories],
                ["购买次数", row.customers.frequency],
                ["商品消费额", row.customers.spending],
              ].map(([title, values]) => (
                <div key={title as string}>
                  <h3>{title as string}</h3>
                  <p>
                    {(values as Report["customers"]["regions"])
                      .map((value) => `${value.name}：${value.count}`)
                      .join("；") || "暂无数据"}
                  </p>
                </div>
              ))}
            </section>
            {row.limitations.map((note) => (
              <p className="muted" key={note}>
                {note}
              </p>
            ))}
          </>
        )
      )}
    </section>
  );
}

function percent(value: number | null) {
  return value === null ? "不适用" : `${(value * 100).toFixed(1)}%`;
}

function MerchantAgent({ shop }: { shop: Shop }) {
  const visible = useVisible();
  const [conversation, setConversation] = useState<string>();
  const [text, setText] = useState("");
  const [requestId, setRequestId] = useState(() => crypto.randomUUID());
  const base = `merchant/shops/${shop.id}`;
  const conversations = useQuery({
    queryKey: ["customer", "merchant", shop.id, "conversations"],
    queryFn: () => api<Conversation[]>(`${base}/conversations`),
  });
  const active = conversation;
  const history = useQuery({
    queryKey: ["customer", "merchant", shop.id, "messages", active],
    queryFn: () => api<Run[]>(`${base}/conversations/${active}/messages`),
    enabled: !!active,
    refetchInterval: (query) =>
      visible && query.state.data?.some((row) => row.status === "running") ? 1500 : false,
  });
  const create = useMutation({
    mutationFn: () =>
      api<Conversation>(`${base}/conversations`, {
        method: "POST",
        json: { title: `${shop.name}经营咨询`.slice(0, 80) },
      }),
    onSuccess: (row) => {
      setConversation(row.id);
      void conversations.refetch();
    },
  });
  const send = useMutation({
    mutationFn: () =>
      api<Run>(`${base}/conversations/${active}/messages`, {
        method: "POST",
        json: { text, request_id: requestId },
      }),
    onSuccess: () => {
      setText("");
      setRequestId(crypto.randomUUID());
      void history.refetch();
    },
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api(`${base}/runs/${id}/cancel`, { method: "POST" }),
    onSuccess: () => history.refetch(),
  });
  const running = history.data?.find((row) => row.status === "running");
  return (
    <section className="stack">
      <h2>本店经营助手</h2>
      <p className="muted">分析本店经营数据并生成内部草稿。活动发布和实际入库需在业务页面确认。</p>
      <div className="toolbar">
        <Field label="经营会话">
          <select
            value={active ?? ""}
            onChange={(event) => {
              setConversation(event.target.value || undefined);
              setText("");
              setRequestId(crypto.randomUUID());
              send.reset();
            }}
          >
            <option value="">请选择或新建会话</option>
            {conversations.data?.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} · {dateTime(item.created_at)}
              </option>
            ))}
          </select>
        </Field>
        <Button variant="secondary" busy={create.isPending} onClick={() => create.mutate()}>
          新建经营会话
        </Button>
      </div>
      <ErrorNotice
        error={conversations.error || history.error || create.error || send.error || cancel.error}
      />
      {history.isEnabled && history.isPending ? (
        <Loading />
      ) : history.data?.length ? (
        <div className="stack" aria-live="polite">
          {history.data.map((row) => (
            <article className="panel stack" key={row.id}>
              <p>{row.user_text}</p>
              <p className="preserve-lines">
                {row.status === "completed"
                  ? row.assistant_text
                  : row.status === "running"
                    ? "正在查询本店数据…"
                    : `本轮未完成（${row.error_code}），可重新提问。`}
              </p>
              {row.links.length > 0 && (
                <div className="actions">
                  {row.links.map((link) => (
                    <Link className="button secondary" key={link.href} href={link.href}>
                      {link.title}
                    </Link>
                  ))}
                </div>
              )}
            </article>
          ))}
        </div>
      ) : (
        <Empty title="查询本店经营情况">可以询问指定期间的销量、成本覆盖、退款和新老客情况。</Empty>
      )}
      {running && (
        <Button
          variant="secondary"
          busy={cancel.isPending}
          onClick={() => cancel.mutate(running.id)}
        >
          停止本轮查询
        </Button>
      )}
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          send.mutate();
        }}
      >
        <Field label="经营问题">
          <textarea
            rows={3}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              if (send.isError) {
                setRequestId(crypto.randomUUID());
                send.reset();
              }
            }}
            maxLength={2000}
            required
            disabled={!active || !!running || send.isPending}
          />
        </Field>
        <Button type="submit" disabled={!active || !!running || !text.trim()} busy={send.isPending}>
          查询本店数据
        </Button>
      </form>
    </section>
  );
}

function ShopForm({
  shop,
  admin = false,
  onDone,
}: {
  shop?: Shop;
  admin?: boolean;
  onDone?: () => void;
}) {
  const client = useQueryClient();
  const save = useMutation({
    mutationFn: (form: FormData) =>
      api(`${admin ? "admin" : "merchant"}/shops${shop ? `/${shop.id}` : ""}`, {
        method: shop ? "PUT" : "POST",
        json: {
          name: formText(form, "name"),
          ...(shop ? { expected_version: shop.version } : { owner_email: formText(form, "owner") }),
          return_address: {
            recipient: formText(form, "recipient"),
            phone: formText(form, "phone"),
            region: formText(form, "region"),
            detail: formText(form, "detail"),
          },
        },
      }),
    onSuccess: () => {
      void client.invalidateQueries({
        queryKey: admin ? ["admin", "shops"] : ["customer", "merchant", "shops"],
      });
      onDone?.();
    },
  });
  return (
    <form
      className="panel stack"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(new FormData(event.currentTarget));
      }}
    >
      <h2>{shop ? "店铺资料" : "为已有账号创建店铺"}</h2>
      {!shop && (
        <Field label="店主注册邮箱">
          <input name="owner" type="email" required maxLength={254} />
        </Field>
      )}
      <Field label="店铺名称">
        <input name="name" defaultValue={shop?.name} required maxLength={100} />
      </Field>
      <h3>退货地址</h3>
      <div className="two-fields">
        <Field label="收件人">
          <input
            name="recipient"
            defaultValue={shop?.return_address?.recipient}
            required
            maxLength={80}
          />
        </Field>
        <Field label="联系电话">
          <input
            name="phone"
            type="tel"
            defaultValue={shop?.return_address?.phone}
            required
            minLength={5}
            maxLength={32}
          />
        </Field>
      </div>
      <Field label="地区">
        <input name="region" defaultValue={shop?.return_address?.region} required maxLength={120} />
      </Field>
      <Field label="详细地址">
        <input name="detail" defaultValue={shop?.return_address?.detail} required maxLength={300} />
      </Field>
      <ErrorNotice error={save.error} />
      {save.isSuccess && <Notice>店铺资料已保存。</Notice>}
      <Button type="submit" busy={save.isPending} disabled={shop?.status === "closed"}>
        {shop ? "保存店铺资料" : "创建待启用店铺"}
      </Button>
    </form>
  );
}

export function AdminShopsScreen() {
  return (
    <AuthGate scope="admin" permission="identity">
      <AdminShops />
    </AuthGate>
  );
}

function AdminShops() {
  const query = useQuery({
    queryKey: ["admin", "shops"],
    queryFn: () => api<Shop[]>("admin/shops"),
  });
  const [edit, setEdit] = useState<Shop | "new">();
  const [status, setStatus] = useState<Shop>();
  const save = useMutation({
    mutationFn: (form: FormData) =>
      api(`admin/shops/${status?.id}/status`, {
        method: "PUT",
        json: {
          expected_version: status?.version,
          status: formText(form, "status"),
          reason: formText(form, "reason"),
        },
      }),
    onSuccess: () => {
      setStatus(undefined);
      void query.refetch();
    },
  });
  return (
    <div className="stack">
      <PageTitle
        title="店铺管理"
        description="为已有用户开通店铺，并管理营业状态。"
        action={<Button onClick={() => setEdit("new")}>创建店铺</Button>}
      />
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.length ? (
        <DataTable headers={["店铺", "状态", "操作"]}>
          {query.data.map((shop) => (
            <tr key={shop.id}>
              <td>{shop.name}</td>
              <td>{shopStatus[shop.status]}</td>
              <td>
                <div className="actions">
                  <Button variant="secondary" onClick={() => setEdit(shop)}>
                    维护资料
                  </Button>
                  <Button
                    variant="secondary"
                    disabled={shop.status === "closed"}
                    onClick={() => {
                      save.reset();
                      setStatus(shop);
                    }}
                  >
                    调整营业状态
                  </Button>
                </div>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        !query.error && <Empty title="暂无店铺" />
      )}
      <Modal open={!!edit} title="店铺资料" onClose={() => setEdit(undefined)}>
        {edit && (
          <ShopForm
            admin
            shop={edit === "new" ? undefined : edit}
            onDone={() => setEdit(undefined)}
          />
        )}
      </Modal>
      <Modal open={!!status} title="调整店铺状态" onClose={() => setStatus(undefined)}>
        {status && (
          <form
            className="stack"
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(new FormData(event.currentTarget));
            }}
          >
            <p>{status.name}</p>
            <Field label="营业状态">
              <select name="status" defaultValue={status.status}>
                <option value="active">营业中</option>
                <option value="suspended">暂停经营</option>
                <option value="closed">关闭店铺</option>
              </select>
            </Field>
            <Field label="变更原因">
              <textarea name="reason" required maxLength={500} />
            </Field>
            <Notice>关闭前需完成订单、售后和异步任务。关闭后不能重新营业。</Notice>
            <ErrorNotice error={save.error} />
            <Button type="submit" busy={save.isPending}>
              确认调整状态
            </Button>
          </form>
        )}
      </Modal>
    </div>
  );
}
