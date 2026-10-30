import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { AuthScreen } from "@/features/auth";
import { CatalogScreen, HomeScreen, ProductScreen } from "@/features/catalog";
import { AdminFlashSalesScreen, FlashSalesScreen } from "@/features/flash-sales";
import {
  CartScreen,
  CheckoutScreen,
  OrdersScreen,
  OrderScreen,
  ReturnRequestScreen,
  ReturnsScreen,
  ReturnScreen,
} from "@/features/commerce";
import { AccountScreen, CompareScreen, HelpScreen } from "@/features/account";
import { SupportScreen, MemoryScreen, CustomerInsightsScreen } from "@/features/support";
import { AdminProductsScreen, AdminProductScreen, InventoryScreen } from "@/features/admin-catalog";
import {
  AdminHome,
  AuditsScreen,
  OperationsScreen,
  ReconciliationsScreen,
  RefundsScreen,
  ReportsScreen,
  ShippingScreen,
  StaffScreen,
} from "@/features/admin-operations";
import { publicData, webOrigin } from "@/lib/server";
import type { Category, Product, ProductPage } from "@/lib/types";

type Props = {
  params: Promise<{ route?: string[] }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
};
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const titles: Record<string, string> = {
  products: "全部商品",
  "flash-sales": "限时秒杀",
  compare: "规格比较",
  login: "登录",
  register: "创建账户",
  "reset-password": "找回密码",
  cart: "购物车",
  checkout: "确认购买",
  orders: "我的订单",
  returns: "我的售后",
  account: "我的账户",
  help: "购物帮助",
  support: "商城客服",
  admin: "商家工作台",
  policies: "购物政策",
};

export async function generateMetadata({ params, searchParams }: Props): Promise<Metadata> {
  const { route = [] } = await params;
  const search = await searchParams;
  const indexable =
    process.env.COMMERCE_INDEXABLE === "true" &&
    (route.length === 0 || (route[0] === "products" && Object.keys(search).length === 0));
  const path = `/${route.join("/")}`;
  if (route[0] === "products" && route[1] && uuid.test(route[1])) {
    const product = await publicData<Product>(`products/${route[1]}`);
    return {
      title: product?.title ?? "商品详情",
      description: product?.description.slice(0, 160) || "核对商品规格与售价，选择适合自己的商品。",
      alternates: { canonical: path },
      robots: { index: indexable, follow: indexable },
    };
  }
  return {
    title: route.length
      ? (titles[route[0]] ?? "A-commerce")
      : { absolute: "A-commerce · 规格清楚，选择简单" },
    alternates: { canonical: path },
    robots: { index: indexable, follow: indexable },
  };
}

export default async function Page(props: Props) {
  const { route = [] } = await props.params;
  const search = await props.searchParams;
  const [area, id, child] = route;
  let content;
  if (!area) {
    const [products, categories] = await Promise.all([
      publicData<ProductPage>("products?limit=30"),
      publicData<Category[]>("categories"),
    ]);
    content = <HomeScreen initial={products} initialCategories={categories} />;
  } else if (area === "products" && route.length === 1) {
    const filters = new URLSearchParams();
    for (const key of ["q", "category", "min_price", "max_price", "brand", "attribute"]) {
      const value = search[key];
      for (const item of Array.isArray(value) ? value : value ? [value] : [])
        filters.append(key, item);
    }
    filters.set("limit", "20");
    content = <CatalogScreen initial={await publicData<ProductPage>(`products?${filters}`)} />;
  } else if (area === "products" && route.length === 2 && uuid.test(id)) {
    const product = await publicData<Product>(`products/${id}`);
    const prices = product?.skus.filter((sku) => sku.active).map((sku) => sku.price_fen) ?? [];
    const structured =
      product && prices.length
        ? {
            "@context": "https://schema.org",
            "@type": "Product",
            name: product.title,
            description: product.description,
            sku: product.id,
            brand: product.brand ? { "@type": "Brand", name: product.brand } : undefined,
            offers: {
              "@type": "AggregateOffer",
              priceCurrency: "CNY",
              lowPrice: (Math.min(...prices) / 100).toFixed(2),
              highPrice: (Math.max(...prices) / 100).toFixed(2),
              offerCount: prices.length,
              url: `${webOrigin()}/products/${product.id}`,
              availability: product.skus.some((sku) => sku.active && sku.available > 0)
                ? "https://schema.org/InStock"
                : "https://schema.org/OutOfStock",
            },
          }
        : undefined;
    content = (
      <>
        {structured && (
          <script
            type="application/ld+json"
            dangerouslySetInnerHTML={{
              __html: JSON.stringify(structured).replace(/</g, "\\u003c"),
            }}
          />
        )}
        <ProductScreen id={id} initial={product} />
      </>
    );
  } else if (route.length === 1 && ["login", "register", "reset-password"].includes(area))
    content = (
      <AuthScreen
        mode={area === "register" ? "register" : area === "reset-password" ? "reset" : "login"}
      />
    );
  else if (area === "cart" && route.length === 1) content = <CartScreen />;
  else if (area === "flash-sales" && route.length === 1) content = <FlashSalesScreen />;
  else if (area === "checkout" && route.length === 1) {
    const saleId = search.flash_sale;
    if (saleId !== undefined && (typeof saleId !== "string" || !uuid.test(saleId))) notFound();
    content = <CheckoutScreen key={saleId ?? "cart"} flashSaleId={saleId} />;
  } else if (area === "compare" && route.length === 1) content = <CompareScreen />;
  else if (area === "help" && route.length === 1) content = <HelpScreen />;
  else if (area === "support" && route.length === 1) content = <SupportScreen />;
  else if (area === "account" && id === "memory" && route.length === 2) content = <MemoryScreen />;
  else if (area === "policies" && route.length === 2 && ["privacy", "terms"].includes(id))
    content = <HelpScreen policy={id === "privacy" ? "privacy" : "terms"} />;
  else if (
    area === "account" &&
    route.length <= 2 &&
    (!id || ["addresses", "favorites", "notifications", "security"].includes(id))
  )
    content = <AccountScreen section={id} />;
  else if (area === "orders" && route.length === 1) content = <OrdersScreen />;
  else if (
    area === "orders" &&
    uuid.test(id ?? "") &&
    route.length <= 3 &&
    (!child || ["payment", "return"].includes(child))
  )
    content =
      child === "return" ? (
        <ReturnRequestScreen id={id} />
      ) : (
        <OrderScreen id={id} payment={child === "payment"} />
      );
  else if (area === "returns" && route.length === 1) content = <ReturnsScreen />;
  else if (area === "returns" && route.length === 2 && uuid.test(id))
    content = <ReturnScreen id={id} />;
  else if (area === "admin") {
    if (route.length === 1) content = <AdminHome />;
    else if (id === "login" && route.length === 2) content = <AuthScreen admin />;
    else if (id === "account" && route.length === 2) content = <AccountScreen admin />;
    else if (id === "customer-insights" && route.length === 2) content = <CustomerInsightsScreen />;
    else if (id === "products" && route.length === 2) content = <AdminProductsScreen />;
    else if (id === "flash-sales" && route.length === 2) content = <AdminFlashSalesScreen />;
    else if (id === "products" && route.length === 3 && (child === "new" || uuid.test(child)))
      content = <AdminProductScreen id={child} />;
    else if (id === "inventory" && route.length <= 3 && (!child || uuid.test(child)))
      content = <InventoryScreen id={child} />;
    else if (id === "orders" && route.length === 2) content = <OrdersScreen admin />;
    else if (id === "orders" && route.length === 3 && uuid.test(child))
      content = <OrderScreen id={child} admin />;
    else if (id === "returns" && route.length === 2) content = <ReturnsScreen admin />;
    else if (id === "returns" && route.length === 3 && uuid.test(child))
      content = <ReturnScreen id={child} admin />;
    else if (route.length === 2)
      content = (
        {
          refunds: <RefundsScreen />,
          reports: <ReportsScreen />,
          reconciliations: <ReconciliationsScreen />,
          operations: <OperationsScreen />,
          audits: <AuditsScreen />,
          shipping: <ShippingScreen />,
          staff: <StaffScreen />,
        } as Record<string, React.ReactNode>
      )[id];
  }
  if (!content) notFound();
  return <Suspense fallback={<Loading />}>{content}</Suspense>;
}
