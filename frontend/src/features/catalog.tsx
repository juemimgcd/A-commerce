"use client";

import Image from "next/image";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  Heart,
  SlidersHorizontal,
  ShoppingBag,
  Check,
  Scales,
  Package,
  MagnifyingGlass,
  PlugsConnected,
  CursorClick,
  Receipt,
} from "@phosphor-icons/react";
import { api, mediaUrl } from "@/lib/api";
import { attributes, formText, money, parseFen } from "@/lib/format";
import type { CartItem, Category, Media, Product, ProductPage } from "@/lib/types";
import { useAuth } from "@/components/providers";
import {
  Button,
  Empty,
  ErrorNotice,
  Field,
  Loading,
  Modal,
  Money,
  Notice,
  PageTitle,
} from "@/components/ui";

export function ProductImage({
  mediaId,
  title,
  admin = false,
  className = "",
  priority = false,
}: {
  mediaId?: string | null;
  title: string;
  admin?: boolean;
  className?: string;
  priority?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const src = mediaUrl(mediaId, admin);
  return (
    <div className={`product-image ${className}`}>
      {src && !failed ? (
        <Image
          src={src}
          alt={title}
          fill
          sizes="(max-width: 767px) 50vw, (max-width: 1023px) 33vw, 25vw"
          unoptimized
          priority={priority}
          onError={() => setFailed(true)}
        />
      ) : (
        <div className="image-missing">
          <Package weight="thin" size={48} />
          <span>暂无商品图片</span>
        </div>
      )}
    </div>
  );
}

export function ProductTile({ product, filter }: { product: Product; filter?: URLSearchParams }) {
  const eligible = product.skus.filter(
    (sku) =>
      sku.active &&
      (!filter ||
        ((!filter.get("min_price") || sku.price_fen >= Number(filter.get("min_price"))) &&
          (!filter.get("max_price") || sku.price_fen <= Number(filter.get("max_price"))) &&
          filter.getAll("attribute").every((value) => {
            const separator = value.indexOf(":");
            return sku.attributes?.[value.slice(0, separator)] === value.slice(separator + 1);
          }))),
  );
  const available = eligible.some((sku) => sku.available > 0);
  const price = eligible.length ? Math.min(...eligible.map((sku) => sku.price_fen)) : undefined;
  return (
    <article className="product-tile">
      <Link href={`/products/${product.id}`} className="product-visual">
        <ProductImage mediaId={product.cover_media_id} title={product.title} />
        {!available && <span className="product-availability">暂时缺货</span>}
        <span className="product-open" aria-hidden>
          <ArrowUpRight size={20} />
        </span>
      </Link>
      <div className="product-tile-info">
        <p className="eyebrow">{product.brand || "数码配件"}</p>
        <h2>
          <Link href={`/products/${product.id}`}>{product.title}</Link>
        </h2>
        <p className="product-spec muted">
          {attributes(eligible[0]?.attributes) || "查看商品规格"}
        </p>
        <div className="product-price">
          {price !== undefined ? (
            <>
              <Money value={price} />
              {new Set(eligible.map((sku) => sku.price_fen)).size > 1 && (
                <small className="muted">起</small>
              )}
            </>
          ) : (
            <span className="muted">当前无可选规格</span>
          )}
        </div>
      </div>
    </article>
  );
}

export function HomeScreen({ initial }: { initial?: ProductPage }) {
  const products = useQuery({
    queryKey: ["catalog", "home"],
    queryFn: ({ signal }) => api<ProductPage>("customer/products?limit=8", { signal }),
    initialData: initial,
  });
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<Category[]>("customer/categories"),
  });
  return (
    <>
      <section className="shop-hero">
        <div className="hero-copy">
          <p className="eyebrow">
            <span className="tiny-line" /> 为你的设备，选对配件
          </p>
          <h1>
            规格清楚。
            <br />
            <span>选择简单。</span>
          </h1>
          <p className="hero-description">
            从接口、功率和预算开始，
            <br className="desktop-only" />
            找到适合日常的数码配件。
          </p>
          <Link className="button primary" href="/products">
            浏览全部商品 <ArrowUpRight size={20} />
          </Link>
          <p className="hero-note">确认规格、运费与总价后再下单。</p>
        </div>
        <div className="hero-guide" aria-label="选购指南">
          <div className="guide-top">
            <span>THE RIGHT CONNECTION</span>
            <span>01 / 03</span>
          </div>
          <PlugsConnected className="hero-plug" weight="thin" aria-hidden />
          <div className="guide-bottom">
            <div>
              <strong>先看接口，再选规格。</strong>
              <p>让每一次连接，都刚刚好。</p>
            </div>
            <ArrowUpRight size={28} />
          </div>
        </div>
      </section>
      <section className="catalog-section">
        <div className="section-title">
          <div>
            <span className="eyebrow">浏览目录</span>
            <h2>从这里找到所需</h2>
          </div>
          <Link className="text-link" href="/products">
            查看全部 <ArrowRight />
          </Link>
        </div>
        {categories.data && (
          <nav className="category-chips" aria-label="商品分类">
            <Link href="/products">全部配件</Link>
            {categories.data.map((category) => (
              <Link key={category.id} href={`/products?category=${category.id}`}>
                {category.name}
                <ArrowUpRight size={14} />
              </Link>
            ))}
          </nav>
        )}
        <ErrorNotice error={categories.error} retry={() => void categories.refetch()} />
        <ErrorNotice error={products.error} retry={() => void products.refetch()} />
        {products.isPending ? (
          <Loading rows={4} />
        ) : products.data?.items.length ? (
          <div className="product-grid">
            {products.data.items.map((product) => (
              <ProductTile key={product.id} product={product} />
            ))}
          </div>
        ) : (
          !products.error && (
            <Empty title="商品正在准备中">商品上架后会显示在这里。你可以稍后回来查看。</Empty>
          )
        )}
      </section>
      <section className="buying-guide" aria-label="购买步骤">
        <div>
          <PlugsConnected size={28} weight="light" />
          <h3>选对规格</h3>
          <p>核对接口与参数，选择适合设备的规格。</p>
        </div>
        <div>
          <CursorClick size={28} weight="light" />
          <h3>确认总价</h3>
          <p>收货地址、商品与运费，在下单前逐项确认。</p>
        </div>
        <div>
          <Receipt size={28} weight="light" />
          <h3>追踪进度</h3>
          <p>付款、发货与售后处理，随时查看当前结果。</p>
        </div>
      </section>
    </>
  );
}

export function CatalogScreen({ initial }: { initial?: ProductPage }) {
  const router = useRouter();
  const search = useSearchParams();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<unknown>();
  const categories = useQuery({
    queryKey: ["categories"],
    queryFn: () => api<Category[]>("customer/categories"),
  });
  const filter = new URLSearchParams(search.toString());
  filter.delete("cursor");
  filter.delete("limit");
  const filterKey = filter.toString();
  const products = useInfiniteQuery({
    queryKey: ["catalog", filterKey],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api<ProductPage>(
        `customer/products?${filterKey}${filterKey ? "&" : ""}limit=20${pageParam ? `&cursor=${pageParam}` : ""}`,
        { signal },
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    initialData: initial ? { pages: [initial], pageParams: [null] } : undefined,
  });
  const items = products.data?.pages.flatMap((page) => page.items) ?? [];
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    const data = new FormData(event.currentTarget);
    try {
      const params = new URLSearchParams();
      for (const key of ["q", "category", "brand"])
        if (formText(data, key)) params.set(key, formText(data, key));
      const min = formText(data, "min");
      const max = formText(data, "max");
      if (min) params.set("min_price", String(parseFen(min)));
      if (max) params.set("max_price", String(parseFen(max)));
      if (min && max && parseFen(min) > parseFen(max))
        throw new Error("最高价格不能低于最低价格。");
      const name = formText(data, "attribute_name");
      const value = formText(data, "attribute_value");
      if (Boolean(name) !== Boolean(value)) throw new Error("请同时填写规格名称与规格要求。");
      if (name && value) params.set("attribute", `${name}:${value}`);
      router.push(`/products${params.size ? `?${params}` : ""}`);
      setOpen(false);
    } catch (e) {
      setError(e);
    }
  }
  const attribute = search.get("attribute")?.split(":") ?? [];
  const filters = (
    <form key={filterKey} className="stack filter-form" onSubmit={submit}>
      <Field label="搜索商品">
        <input
          name="q"
          defaultValue={search.get("q") ?? ""}
          maxLength={80}
          placeholder="商品名称"
        />
      </Field>
      <Field label="分类">
        <select
          key={`${filterKey}:${categories.data?.length ?? 0}`}
          name="category"
          defaultValue={search.get("category") ?? ""}
        >
          <option value="">全部分类</option>
          {categories.data?.map((category) => (
            <option key={category.id} value={category.id}>
              {category.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="品牌">
        <input
          name="brand"
          defaultValue={search.get("brand") ?? ""}
          maxLength={100}
          placeholder="输入完整品牌名称"
        />
      </Field>
      <div>
        <span className="field-label">价格范围（元）</span>
        <div className="two-fields">
          <input
            name="min"
            aria-label="最低价格"
            inputMode="decimal"
            defaultValue={
              search.has("min_price") ? String(Number(search.get("min_price")) / 100) : ""
            }
            placeholder="最低"
          />
          <input
            name="max"
            aria-label="最高价格"
            inputMode="decimal"
            defaultValue={
              search.has("max_price") ? String(Number(search.get("max_price")) / 100) : ""
            }
            placeholder="最高"
          />
        </div>
      </div>
      <Field label="规格名称">
        <input
          name="attribute_name"
          defaultValue={attribute[0] ?? ""}
          maxLength={80}
          placeholder="如：接口"
        />
      </Field>
      <Field label="规格要求">
        <input
          name="attribute_value"
          defaultValue={attribute.slice(1).join(":")}
          maxLength={200}
          placeholder="如：USB-C"
        />
      </Field>
      <ErrorNotice error={error} />
      <Button type="submit">应用筛选</Button>
      <Link href="/products" className="text-link" onClick={() => setOpen(false)}>
        清除全部条件
      </Link>
    </form>
  );
  return (
    <>
      <PageTitle
        eyebrow="配件目录"
        title={search.get("q") ? `搜索“${search.get("q")}”` : "找到合适的那一件"}
        description="按规格与预算筛选，查看具体商品了解兼容信息。"
      />
      <div className="catalog-layout">
        <aside className="catalog-filters">
          <h2>
            <SlidersHorizontal size={20} />
            筛选商品
          </h2>
          {filters}
          <ErrorNotice error={categories.error} retry={() => void categories.refetch()} />
        </aside>
        <section className="catalog-results">
          <div className="results-toolbar">
            <span className="muted">
              {items.length ? `已显示 ${items.length} 件商品` : "商品目录"}
            </span>
            <Button className="mobile-only" variant="secondary" onClick={() => setOpen(true)}>
              <SlidersHorizontal />
              筛选
            </Button>
            <span className="desktop-only muted">默认顺序</span>
          </div>
          <ErrorNotice error={products.error} retry={() => void products.refetch()} />
          {products.isPending ? (
            <Loading rows={4} />
          ) : items.length ? (
            <div className="product-grid">
              {items.map((product) => (
                <ProductTile key={product.id} product={product} filter={filter} />
              ))}
            </div>
          ) : (
            !products.error && (
              <Empty
                title="没有找到符合条件的商品"
                action={
                  <Link className="button secondary" href="/products">
                    清除筛选
                  </Link>
                }
              >
                可以减少规格限制，或换一个关键词。
              </Empty>
            )
          )}
          {products.hasNextPage && (
            <div className="load-more">
              <Button
                variant="secondary"
                busy={products.isFetchingNextPage}
                onClick={() => void products.fetchNextPage()}
              >
                加载更多商品
              </Button>
            </div>
          )}
        </section>
      </div>
      <Modal open={open} title="筛选商品" onClose={() => setOpen(false)}>
        <ErrorNotice error={categories.error} retry={() => void categories.refetch()} />
        {filters}
      </Modal>
    </>
  );
}

export function ProductScreen({ id, initial }: { id: string; initial?: Product }) {
  const product = useQuery({
    queryKey: ["product", id],
    queryFn: () => api<Product>(`customer/products/${id}`),
    initialData: initial,
  });
  if (product.isPending) return <Loading />;
  if (product.error)
    return <ErrorNotice error={product.error} retry={() => void product.refetch()} />;
  return product.data ? <ProductDetail key={id} product={product.data} /> : null;
}

function ProductDetail({ product }: { product: Product }) {
  const auth = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const client = useQueryClient();
  const skus = product.skus.filter((sku) => sku.active);
  const [skuId, setSkuId] = useState(() =>
    skus.some((sku) => sku.id === params.get("sku"))
      ? (params.get("sku") ?? "")
      : skus.length === 1
        ? skus[0].id
        : "",
  );
  const [quantity, setQuantity] = useState(1);
  const [selectedMedia, setSelectedMedia] = useState<string | undefined>();
  const [zoom, setZoom] = useState(false);
  const [saved, setSaved] = useState(false);
  const [message, setMessage] = useState("");
  const media = useQuery({
    queryKey: ["media", product.id],
    queryFn: () => api<Media[]>(`customer/products/${product.id}/media`),
  });
  const sku = skus.find((item) => item.id === skuId);
  const cover = selectedMedia ?? product.cover_media_id ?? media.data?.[0]?.id;
  const add = useMutation({
    mutationFn: async () => {
      if (!auth.customer) {
        router.push(`/login?next=${encodeURIComponent(`/products/${product.id}?sku=${skuId}`)}`);
        return false;
      }
      const cart = await api<CartItem[]>("customer/cart");
      const current = cart.find((item) => item.sku_id === skuId)?.quantity ?? 0;
      if (current + quantity > 99) throw new Error("同一规格在购物车中最多保留 99 件。");
      await api(`customer/cart/items/${skuId}`, {
        method: "PUT",
        json: { quantity: current + quantity },
      });
      return true;
    },
    onSuccess: (added) => {
      if (added) {
        setMessage("已加入购物车，购物车中的商品尚未预留库存。");
        void client.invalidateQueries({ queryKey: ["customer", "cart"] });
      }
    },
  });
  const favorite = useMutation({
    mutationFn: async () => {
      if (!auth.customer) {
        router.push(`/login?next=${encodeURIComponent(`/products/${product.id}`)}`);
        return;
      }
      await api(`customer/favorites/${product.id}`, { method: saved ? "DELETE" : "PUT" });
      setSaved(!saved);
      void client.invalidateQueries({ queryKey: ["customer", "favorites"] });
    },
  });
  return (
    <>
      <nav className="breadcrumbs" aria-label="面包屑">
        <Link href="/products">全部商品</Link>
        <span>/</span>
        <span>{product.title}</span>
      </nav>
      <div className="product-detail">
        <section className="product-gallery">
          <button
            className="gallery-main"
            onClick={(event) => {
              event.currentTarget.focus({ preventScroll: true });
              setZoom(true);
            }}
            aria-label="查看商品大图"
          >
            <ProductImage key={cover} mediaId={cover} title={product.title} priority />
            <span className="gallery-expand">
              <MagnifyingGlass size={20} />
            </span>
          </button>
          {media.data && media.data.length > 1 && (
            <div className="thumbnails">
              {media.data.map((image, index) => (
                <button
                  key={image.id}
                  aria-label={`查看第 ${index + 1} 张图片`}
                  aria-pressed={cover === image.id}
                  onClick={() => setSelectedMedia(image.id)}
                >
                  <ProductImage mediaId={image.id} title={`${product.title} 图片 ${index + 1}`} />
                </button>
              ))}
            </div>
          )}
          <ErrorNotice error={media.error} retry={() => void media.refetch()} />
        </section>
        <section className="product-buy">
          <p className="eyebrow">{product.brand || "数码配件"}</p>
          <h1>{product.title}</h1>
          <div className="detail-price">
            {sku ? (
              <Money value={sku.price_fen} />
            ) : skus.length ? (
              <>
                <Money value={Math.min(...skus.map((item) => item.price_fen))} />
                <small>起</small>
              </>
            ) : (
              <span>暂无可选规格</span>
            )}
          </div>
          <p className="muted">运费将在选择收货地址后计算。</p>
          <fieldset className="sku-options">
            <legend>选择规格</legend>
            {skus.map((item) => (
              <label key={item.id} className={item.id === skuId ? "selected" : ""}>
                <input
                  type="radio"
                  name="sku"
                  value={item.id}
                  checked={item.id === skuId}
                  onChange={() => {
                    setSkuId(item.id);
                    setMessage("");
                  }}
                  disabled={item.available <= 0}
                />
                <span>{attributes(item.attributes) || item.code}</span>
                <small>{item.available > 0 ? money(item.price_fen) : "暂时缺货"}</small>
              </label>
            ))}
          </fieldset>
          <Field label="购买数量">
            <input
              className="quantity"
              aria-label="购买数量"
              type="number"
              min={1}
              max={Math.min(99, sku?.available ?? 99)}
              value={quantity}
              onChange={(event) =>
                setQuantity(Math.max(1, Math.min(99, Number(event.target.value) || 1)))
              }
            />
          </Field>
          <div className="product-buy-actions">
            <Button
              busy={add.isPending}
              disabled={!sku || quantity > sku.available || !Number.isInteger(quantity)}
              onClick={() => add.mutate()}
            >
              <ShoppingBag size={20} />
              {add.isPending ? "正在加入…" : "加入购物车"}
            </Button>
            <Button
              variant="secondary"
              busy={favorite.isPending}
              onClick={() => favorite.mutate()}
              aria-label={saved ? "取消收藏" : "收藏商品"}
            >
              <Heart size={22} weight={saved ? "fill" : "regular"} />
            </Button>
          </div>
          <ErrorNotice error={add.error || favorite.error} />
          {message && (
            <Notice>
              <Check size={20} />
              <span>
                {message} <Link href="/cart">查看购物车</Link>
              </span>
            </Notice>
          )}
          <Link className="text-link" href={`/compare?ids=${product.id}`}>
            <Scales size={18} />
            加入规格比较
          </Link>
          <div className="product-policies">
            <p>
              <Check size={16} />
              下单前确认商品、运费和总价
            </p>
            <p>
              <Check size={16} />
              订单状态与售后进度可查询
            </p>
          </div>
        </section>
      </div>
      <section className="product-description">
        <div>
          <h2>商品说明</h2>
          <p className="preserve-lines">
            {product.description || "商家尚未补充详细说明。购买前请核对右侧规格信息。"}
          </p>
        </div>
        <div>
          <h2>规格参数</h2>
          {sku ? (
            <dl className="spec-table">
              {Object.entries(sku.attributes ?? {}).map(([name, value]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{value}</dd>
                </div>
              ))}
              <div>
                <dt>规格编号</dt>
                <dd>{sku.code}</dd>
              </div>
            </dl>
          ) : (
            <p className="muted">选择一个规格后查看完整参数。</p>
          )}
        </div>
      </section>
      <Modal open={zoom} title={product.title} wide onClose={() => setZoom(false)}>
        <ProductImage key={cover} mediaId={cover} title={product.title} className="zoom-image" />
      </Modal>
    </>
  );
}
