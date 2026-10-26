"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { z } from "zod";
import { Plus, ArrowRight, UploadSimple } from "@phosphor-icons/react";
import { api, ApiError, queryString } from "@/lib/api";
import { attributes, dateTime, formText, parseFen } from "@/lib/format";
import { useStored } from "@/lib/storage";
import type {
  Category,
  Change,
  ImportResult,
  Inventory,
  InventoryPage,
  Media,
  Movement,
  Product,
  ProductPage,
  Sku,
} from "@/lib/types";
import { useAuth } from "@/components/providers";
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
import { ProductImage } from "./catalog";

export function AdminProductsScreen() {
  return (
    <AuthGate scope="admin" permission="catalog">
      <Products />
    </AuthGate>
  );
}
function Products() {
  const router = useRouter();
  const params = useSearchParams();
  const term = params.get("q") ?? "";
  const [category, setCategory] = useState(false);
  const [importing, setImporting] = useState(false);
  const query = useInfiniteQuery({
    queryKey: ["admin", "products", term],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<ProductPage>(`admin/products${queryString({ q: term, cursor: pageParam, limit: 20 })}`),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        eyebrow="商品与目录"
        title="商品管理"
        description="维护商品资料、规格、售价与上架状态。"
        action={
          <Link href="/admin/products/new" className="button primary">
            <Plus />
            新增商品
          </Link>
        }
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          router.push(
            `/admin/products?q=${encodeURIComponent(formText(new FormData(event.currentTarget), "q"))}`,
          );
        }}
      >
        <Field label="搜索商品">
          <input name="q" defaultValue={term} maxLength={80} placeholder="商品名称" />
        </Field>
        <Button type="submit" variant="secondary">
          搜索
        </Button>
        <Button type="button" variant="secondary" onClick={() => setCategory(true)}>
          管理分类
        </Button>
        <Button type="button" variant="secondary" onClick={() => setImporting(true)}>
          <UploadSimple />
          JSON 批量导入
        </Button>
      </form>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : !items.length ? (
        <Empty title="暂无符合条件的商品" />
      ) : (
        <DataTable headers={["商品", "品牌", "规格 / 可售数量", "状态", "版本", "操作"]}>
          {items.map((product) => (
            <tr key={product.id}>
              <td>
                <div className="table-product">
                  <ProductImage mediaId={product.cover_media_id} title={product.title} admin />
                  <strong>{product.title}</strong>
                </div>
              </td>
              <td>{product.brand || "—"}</td>
              <td>
                {product.skus.length} 个规格
                <small>{product.skus.reduce((sum, sku) => sum + sku.available, 0)} 件可售</small>
              </td>
              <td>
                <span className={`status ${product.active ? "success" : "neutral"}`}>
                  {product.active ? "已上架" : "未上架"}
                </span>
              </td>
              <td>v{product.version}</td>
              <td>
                <Link href={`/admin/products/${product.id}`}>
                  编辑商品 <ArrowRight />
                </Link>
              </td>
            </tr>
          ))}
        </DataTable>
      )}
      {query.hasNextPage && (
        <div className="load-more">
          <Button
            variant="secondary"
            busy={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            加载更多
          </Button>
        </div>
      )}
      <Modal open={category} title="商品分类" onClose={() => setCategory(false)}>
        <Categories />
      </Modal>
      <Modal open={importing} title="批量导入商品" onClose={() => setImporting(false)} wide>
        <CatalogImport onSaved={() => void query.refetch()} />
      </Modal>
    </>
  );
}
function Categories() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<Category[]>("customer/categories"),
  });
  const mutation = useMutation({
    mutationFn: (data: FormData) =>
      api<Category>("admin/categories", { method: "POST", json: { name: formText(data, "name") } }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["categories"] }),
  });
  return (
    <>
      <ErrorNotice error={query.error || mutation.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        <ul className="simple-list">
          {query.data?.map((item) => (
            <li key={item.id}>
              <strong>{item.name}</strong>
              <small className="break-all">{item.id}</small>
            </li>
          ))}
        </ul>
      )}
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault();
          const form = event.currentTarget;
          mutation.mutate(new FormData(form), { onSuccess: () => form.reset() });
        }}
      >
        <Field label="新增分类名称">
          <input name="name" required maxLength={100} />
        </Field>
        <Button type="submit" busy={mutation.isPending}>
          保存分类
        </Button>
      </form>
    </>
  );
}
function CatalogImport({ onSaved }: { onSaved: () => void }) {
  const mutation = useMutation({
    mutationFn: async (data: FormData) => {
      const text = formText(data, "json");
      if (new Blob([text]).size > 900000) throw new Error("导入内容需小于 900 KB。");
      const parsed = z
        .object({ rows: z.array(z.record(z.string(), z.unknown())).min(1).max(100) })
        .parse(JSON.parse(text));
      return api<ImportResult[]>("admin/catalog-imports", { method: "POST", json: parsed });
    },
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
      <p>
        一次最多 100 行。每行包含 product（category_id、title、brand、description、active）和
        skus（code、price_fen、attributes、active）。价格单位为分，库存导入后通过库存调整录入。
      </p>
      <Field label="商品 JSON" hint='顶层格式：{"rows": [{"product": {...}, "skus": [...]}]}'>
        <textarea
          className="code-input"
          name="json"
          required
          rows={12}
          placeholder='{"rows": []}'
        />
      </Field>
      <Notice>
        此操作创建新商品。提交结果未知时先按商品名称、规格编号查询，不要重复导入同一批次。
      </Notice>
      <ErrorNotice error={mutation.error} />
      <Button type="submit" busy={mutation.isPending}>
        确认导入商品
      </Button>
      {mutation.data && (
        <DataTable headers={["行号", "结果"]}>
          {mutation.data.map((result) => (
            <tr key={result.row}>
              <td>{result.row}</td>
              <td>
                {result.product_id ? (
                  <Link href={`/admin/products/${result.product_id}`}>导入成功，编辑商品</Link>
                ) : (
                  result.error
                )}
              </td>
            </tr>
          ))}
        </DataTable>
      )}
    </form>
  );
}

export function AdminProductScreen({ id }: { id: string }) {
  return (
    <AuthGate scope="admin" permission="catalog">
      <ProductEditor id={id} />
    </AuthGate>
  );
}
function ProductEditor({ id }: { id: string }) {
  const query = useQuery({
    queryKey: ["admin", "product", id],
    queryFn: () => api<Product>(`admin/products/${id}`),
    enabled: id !== "new",
  });
  if (id !== "new" && query.isPending) return <Loading />;
  if (id !== "new" && !query.data)
    return <ErrorNotice error={query.error} retry={() => void query.refetch()} />;
  return <ProductForm key={id} product={query.data} refresh={() => void query.refetch()} />;
}
function ProductForm({ product, refresh }: { product?: Product; refresh: () => void }) {
  const router = useRouter();
  const client = useQueryClient();
  const [message, setMessage] = useState("");
  const [sku, setSku] = useState<Sku | "new">();
  const [categoryId, setCategoryId] = useState(product?.category_id ?? "");
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<Category[]>("customer/categories"),
  });
  const media = useQuery({
    queryKey: ["admin", "media", product?.id],
    queryFn: () => api<Media[]>(`admin/products/${product?.id}/media`),
    enabled: !!product,
  });
  const changes = useInfiniteQuery({
    queryKey: ["admin", "changes", product?.id],
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api<Change[]>(`admin/products/${product?.id}/changes?after=${pageParam}&limit=50`),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].version : undefined),
    enabled: !!product,
  });
  const save = useMutation({
    mutationFn: (data: FormData) =>
      api<Product>(`admin/products${product ? `/${product.id}` : ""}`, {
        method: product ? "PUT" : "POST",
        json: {
          category_id: formText(data, "category_id"),
          title: formText(data, "title"),
          brand: formText(data, "brand"),
          description: formText(data, "description"),
          active: data.get("active") === "on",
        },
      }),
    onSuccess: (value) => {
      setMessage("商品资料已保存。");
      void client.invalidateQueries({ queryKey: ["admin", "products"] });
      void client.invalidateQueries({ queryKey: ["catalog"] });
      if (product) {
        refresh();
        void changes.refetch();
      } else router.replace(`/admin/products/${value.id}`);
    },
  });
  const upload = useMutation({
    mutationFn: async (data: FormData) => {
      const file = data.get("file");
      if (!(file instanceof File) || !file.size) throw new Error("请选择图片。");
      if (file.size > 8 * 1024 * 1024) throw new Error("图片不能超过 8 MiB。");
      return api<Media>(`admin/products/${product?.id}/media`, {
        method: "POST",
        body: file,
        headers: { "Content-Type": file.type },
      });
    },
    onSuccess: () => {
      void media.refetch();
      refresh();
      setMessage("图片已上传。");
    },
  });
  return (
    <>
      <nav className="breadcrumbs">
        <Link href="/admin/products">商品列表</Link>
        <span>/</span>
        <span>{product ? product.title : "新增商品"}</span>
      </nav>
      <PageTitle
        title={product ? "编辑商品" : "新增商品"}
        description="先保存商品资料，再维护具体规格、图片与库存。"
        action={
          product?.active && (
            <Link className="button secondary" href={`/products/${product.id}`}>
              查看商城页面 <ArrowRight />
            </Link>
          )
        }
      />
      {message && <Notice>{message}</Notice>}
      <div className="editor-layout">
        <form
          className="panel stack"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(new FormData(event.currentTarget));
          }}
        >
          <h2>基本资料</h2>
          <Field label="商品名称">
            <input name="title" defaultValue={product?.title} required maxLength={200} />
          </Field>
          <div className="two-fields">
            <Field label="分类">
              <select
                name="category_id"
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
                required
              >
                <option value="">选择分类</option>
                {categories.data?.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="品牌">
              <input name="brand" defaultValue={product?.brand} maxLength={100} />
            </Field>
          </div>
          <Field label="商品说明">
            <textarea
              name="description"
              defaultValue={product?.description}
              rows={7}
              maxLength={4000}
            />
          </Field>
          <label className="checkbox">
            <input type="checkbox" name="active" defaultChecked={product?.active} />
            在商城上架
          </label>
          <ErrorNotice error={save.error || categories.error} />
          <Button type="submit" busy={save.isPending}>
            保存商品资料
          </Button>
        </form>
        {product && (
          <section className="panel">
            <h2>商品图片</h2>
            <p className="muted">
              按上传顺序展示，首张作为封面。支持 JPEG、PNG、WebP，每张不超过 8 MiB，最多 20 张。
            </p>
            <ErrorNotice error={media.error || upload.error} />
            {media.isPending && <Loading rows={1} />}
            {!media.isPending && !media.error && !media.data?.length && (
              <p className="muted">尚未上传商品图片。</p>
            )}
            <div className="media-grid">
              {media.data?.map((image) => (
                <ProductImage key={image.id} mediaId={image.id} title={product.title} admin />
              ))}
            </div>
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                upload.mutate(new FormData(event.currentTarget));
              }}
            >
              <Field label="选择图片">
                <input name="file" type="file" accept="image/jpeg,image/png,image/webp" required />
              </Field>
              <Button
                type="submit"
                variant="secondary"
                busy={upload.isPending}
                disabled={(media.data?.length ?? 0) >= 20}
              >
                <UploadSimple />
                上传图片
              </Button>
            </form>
          </section>
        )}
      </div>
      {product && (
        <>
          <section className="panel spaced">
            <div className="section-title">
              <h2>可购买规格（SKU）</h2>
              <Button onClick={() => setSku("new")}>
                <Plus />
                新增规格
              </Button>
            </div>
            {product.skus.length ? (
              <DataTable headers={["编号", "属性", "价格", "可售", "状态", "操作"]}>
                {product.skus.map((item) => (
                  <tr key={item.id}>
                    <td>{item.code}</td>
                    <td>{attributes(item.attributes)}</td>
                    <td>
                      <Money value={item.price_fen} />
                    </td>
                    <td>{item.available}</td>
                    <td>{item.active ? "有效" : "停用"}</td>
                    <td>
                      <div className="actions">
                        <Button variant="ghost" onClick={() => setSku(item)}>
                          编辑
                        </Button>
                        <Link href={`/admin/inventory/${item.id}`}>管理库存</Link>
                      </div>
                    </td>
                  </tr>
                ))}
              </DataTable>
            ) : (
              <Empty title="尚未添加可购买规格" />
            )}
          </section>
          <details className="panel spaced">
            <summary>商品变更记录</summary>
            <ErrorNotice error={changes.error} />
            {changes.isPending && <Loading rows={1} />}
            {!changes.isPending && !changes.error && !changes.data?.pages.flat().length && (
              <Empty title="暂无商品变更记录" />
            )}
            {changes.data?.pages.flat().map((change) => (
              <details key={change.id} className="change-record">
                <summary>
                  版本 {change.version} · {dateTime(change.created_at)}
                </summary>
                <pre>{JSON.stringify(change.snapshot, null, 2)}</pre>
              </details>
            ))}
            {changes.hasNextPage && (
              <Button variant="secondary" onClick={() => void changes.fetchNextPage()}>
                更多记录
              </Button>
            )}
          </details>
          <Modal
            open={!!sku}
            title={sku === "new" ? "新增规格" : "编辑规格"}
            onClose={() => setSku(undefined)}
          >
            {sku && (
              <SkuForm
                key={sku === "new" ? "new" : sku.id}
                productId={product.id}
                sku={sku === "new" ? undefined : sku}
                onSaved={() => {
                  refresh();
                  void changes.refetch();
                  setSku(undefined);
                }}
              />
            )}
          </Modal>
        </>
      )}
    </>
  );
}
function SkuForm({
  productId,
  sku,
  onSaved,
}: {
  productId: string;
  sku?: Sku;
  onSaved: () => void;
}) {
  const mutation = useMutation({
    mutationFn: (data: FormData) => {
      const attrs = z
        .record(z.string().max(80), z.string().max(200))
        .parse(JSON.parse(formText(data, "attributes") || "{}"));
      return api(`admin/products/${productId}/skus${sku ? `/${sku.id}` : ""}`, {
        method: sku ? "PUT" : "POST",
        json: {
          code: formText(data, "code"),
          price_fen: parseFen(formText(data, "price")),
          attributes: attrs,
          active: data.get("active") === "on",
        },
      });
    },
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
      <Field label="规格编号">
        <input name="code" required maxLength={80} defaultValue={sku?.code} />
      </Field>
      <Field label="售价（元）">
        <input
          name="price"
          required
          inputMode="decimal"
          pattern="[0-9]+(\.[0-9]{1,2})?"
          defaultValue={sku ? String(sku.price_fen / 100) : ""}
        />
      </Field>
      <Field label="规格属性 JSON" hint='例如：{"接口":"USB-C","功率":"65W"}'>
        <textarea
          name="attributes"
          rows={6}
          defaultValue={JSON.stringify(sku?.attributes ?? {}, null, 2)}
        />
      </Field>
      <label className="checkbox">
        <input type="checkbox" name="active" defaultChecked={sku?.active ?? true} />
        规格有效
      </label>
      <ErrorNotice error={mutation.error} />
      <Button type="submit" busy={mutation.isPending}>
        保存规格
      </Button>
    </form>
  );
}

export function InventoryScreen({ id }: { id?: string }) {
  return (
    <AuthGate scope="admin" permission="catalog">
      {id ? <InventoryDetail id={id} /> : <InventoryList />}
    </AuthGate>
  );
}
function InventoryList() {
  const [filter, setFilter] = useState({ q: "", low: false });
  const query = useInfiniteQuery({
    queryKey: ["admin", "inventory", filter],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<InventoryPage>(
        `admin/inventory${queryString({ ...filter, cursor: pageParam, limit: 30 })}`,
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <PageTitle
        title="库存管理"
        description="可售 = 在库 − 待付款预留 − 已付款占用。调整会写入库存流水。"
      />
      <form
        className="toolbar"
        onSubmit={(event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          setFilter({ q: formText(data, "q"), low: data.get("low") === "on" });
        }}
      >
        <Field label="商品或规格编号">
          <input name="q" maxLength={80} />
        </Field>
        <label className="checkbox">
          <input name="low" type="checkbox" />
          仅看可售 ≤ 5 的规格
        </label>
        <Button variant="secondary" type="submit">
          查询库存
        </Button>
      </form>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading />
      ) : items.length ? (
        <DataTable headers={["商品 / 规格", "在库", "预留", "占用", "可售", "操作"]}>
          {items.map((item) => (
            <tr key={item.sku_id}>
              <td>
                <strong>{item.title}</strong>
                <small>
                  {item.code} · {attributes(item.attributes)}
                </small>
              </td>
              <td>{item.on_hand}</td>
              <td>{item.reserved}</td>
              <td>{item.allocated}</td>
              <td>
                <strong className={item.available <= 5 ? "error-text" : ""}>
                  {item.available}
                </strong>
              </td>
              <td>
                <Link href={`/admin/inventory/${item.sku_id}`}>库存与流水</Link>
              </td>
            </tr>
          ))}
        </DataTable>
      ) : (
        <Empty title="没有符合条件的库存" />
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
const stockRequest = z.object({ key: z.string(), delta: z.number().int(), reason: z.string() });
function InventoryDetail({ id }: { id: string }) {
  const auth = useAuth();
  const [pending, save] = useStored(`commerce:stock:${auth.admin?.id}:${id}`, stockRequest);
  const [delta, setDelta] = useState(0);
  const [confirm, setConfirm] = useState<{ delta: number; reason: string }>();
  const [message, setMessage] = useState("");
  const query = useQuery({
    queryKey: ["admin", "stock", id],
    queryFn: () => api<Inventory>(`admin/inventory/${id}`),
  });
  const ledger = useInfiniteQuery({
    queryKey: ["admin", "movements", id],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      api<Movement[]>(
        `admin/inventory/${id}/movements${queryString({ cursor: pageParam, limit: 50 })}`,
      ),
    getNextPageParam: (page) => (page.length === 50 ? page[page.length - 1].id : undefined),
  });
  const adjust = useMutation({
    mutationFn: async () => {
      if (!pending && !confirm) throw new Error("请先填写调整内容。");
      const request = pending ?? { key: crypto.randomUUID(), ...confirm! };
      save(request);
      try {
        return await api(`admin/inventory/${id}/adjust`, {
          method: "POST",
          key: request.key,
          json: { delta: request.delta, reason: request.reason },
        });
      } catch (error) {
        if (!pending && error instanceof ApiError && error.status >= 400 && error.status < 500)
          save();
        throw error;
      }
    },
    onSuccess: () => {
      save();
      setConfirm(undefined);
      setMessage("库存调整已完成。");
      void query.refetch();
      void ledger.refetch();
    },
  });
  if (query.isPending) return <Loading />;
  if (!query.data) return <ErrorNotice error={query.error} retry={() => void query.refetch()} />;
  const item = query.data;
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setConfirm({ delta: Number(data.get("delta")), reason: formText(data, "reason") });
  }
  return (
    <>
      <PageTitle
        title={item.title}
        description={`${item.code} · ${attributes(item.attributes)}`}
        action={
          <Link href={`/admin/products/${item.product_id}`} className="button secondary">
            编辑商品
          </Link>
        }
      />
      <ErrorNotice error={query.error} />
      {message && <Notice>{message}</Notice>}
      <div className="stat-strip">
        {[
          ["在库", item.on_hand],
          ["待付款预留", item.reserved],
          ["已付款占用", item.allocated],
          ["可售", item.available],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      {pending && (
        <Notice>
          <div>
            已有库存调整请求待确认：{pending.delta > 0 ? "+" : ""}
            {pending.delta} 件，原因：{pending.reason}
            <Button
              variant="secondary"
              onClick={() => setConfirm({ delta: pending.delta, reason: pending.reason })}
            >
              用原请求核对结果
            </Button>
          </div>
        </Notice>
      )}
      <form className="panel stack form-narrow spaced" onSubmit={submit}>
        <h2>调整在库数量</h2>
        <Field label="变化数量" hint="正数入库，负数扣减；不得少于已预留与占用总量。">
          <input
            name="delta"
            type="number"
            min={-1000000}
            max={1000000}
            step={1}
            required
            value={delta}
            onChange={(event) => setDelta(Number(event.target.value))}
            disabled={!!pending}
          />
        </Field>
        <p>
          调整前 {item.on_hand} → 调整后 {item.on_hand + delta}
        </p>
        <Field label="调整原因">
          <textarea name="reason" required maxLength={300} rows={3} disabled={!!pending} />
        </Field>
        <Button type="submit" disabled={!!pending || delta === 0}>
          核对调整内容
        </Button>
      </form>
      <section className="panel spaced">
        <h2>库存流水</h2>
        <ErrorNotice error={ledger.error} />
        {ledger.isPending ? (
          <Loading />
        ) : ledger.data?.pages.flat().length ? (
          <DataTable headers={["时间", "在库变化", "预留变化", "占用变化", "原因 / 关联"]}>
            {ledger.data.pages.flat().map((row) => (
              <tr key={row.id}>
                <td>{dateTime(row.created_at)}</td>
                <td>{row.on_hand_delta}</td>
                <td>{row.reserved_delta}</td>
                <td>{row.allocated_delta}</td>
                <td>
                  {row.reason}
                  <small className="break-all">{row.reference}</small>
                </td>
              </tr>
            ))}
          </DataTable>
        ) : (
          <Empty title="尚无库存流水" />
        )}
        {ledger.hasNextPage && (
          <Button variant="secondary" onClick={() => void ledger.fetchNextPage()}>
            加载更多流水
          </Button>
        )}
      </section>
      <Modal
        open={!!confirm}
        title="确认库存调整"
        onClose={() => !adjust.isPending && setConfirm(undefined)}
      >
        <p>
          {item.title} · {item.code}
        </p>
        <p>本次变化：{confirm?.delta} 件</p>
        <p>原因：{confirm?.reason}</p>
        <ErrorNotice error={adjust.error} />
        <Button busy={adjust.isPending} onClick={() => adjust.mutate()}>
          确认调整
        </Button>
      </Modal>
    </>
  );
}
