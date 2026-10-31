"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState, type ReactNode, type FormEvent } from "react";
import {
  ArrowUpRight,
  ShoppingBag,
  UserCircle,
  MagnifyingGlass,
  List,
  ArrowLeft,
  SignOut,
  Package,
  Receipt,
  ArrowsClockwise,
  ChartBar,
  ShieldCheck,
  Users,
  Truck,
  SlidersHorizontal,
  Heart,
  CaretRight,
} from "@phosphor-icons/react";
import { useAuth, useStore } from "./providers";
import { Button, ErrorNotice, Modal } from "./ui";

export const adminLinks = [
  { href: "/admin/shops", title: "店铺管理", permission: "identity", icon: Users },
  { href: "/admin/products", title: "商品", permission: "catalog", icon: Package },
  { href: "/admin/flash-sales", title: "秒杀管理", permission: "catalog", icon: Receipt },
  { href: "/admin/inventory", title: "库存", permission: "catalog", icon: SlidersHorizontal },
  { href: "/admin/orders", title: "订单履约", permission: "fulfillment", icon: Receipt },
  { href: "/admin/returns", title: "售后申请", permission: "aftersales", icon: ArrowsClockwise },
  { href: "/admin/refunds", title: "退款", permission: "aftersales", icon: Receipt },
  { href: "/admin/reports", title: "经营报表", permission: "audit", icon: ChartBar },
  { href: "/admin/customer-insights", title: "用户画像", permission: "audit", icon: Users },
  { href: "/admin/reconciliations", title: "资金对账", permission: "audit", icon: ShieldCheck },
  { href: "/admin/operations", title: "异常任务", permission: "audit", icon: SlidersHorizontal },
  { href: "/admin/audits", title: "操作审计", permission: "audit", icon: ShieldCheck },
  { href: "/admin/shipping", title: "配送规则", permission: "catalog", icon: Truck },
  { href: "/admin/staff", title: "员工权限", permission: "identity", icon: Users },
  { href: "/admin/account", title: "我的会话", permission: "", icon: UserCircle },
];

const mobileLinks = [
  ["/", "首页"],
  ["/products", "全部商品"],
  ["/flash-sales", "限时秒杀"],
  ["/compare", "规格比较"],
  ["/orders", "我的订单"],
  ["/account", "我的账户"],
  ["/account/favorites", "我的收藏"],
  ["/returns", "售后进度"],
  ["/help", "购物帮助"],
  ["/support", "商城客服"],
  ["/merchant", "商家工作台"],
] as const;

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchTerm = useSearchParams().get("q") ?? "";
  const flashCheckout = !!useSearchParams().get("flash_sale");
  const admin = pathname.startsWith("/admin");
  const auth = useAuth();
  const store = useStore();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState<unknown>();
  const checkout = pathname === "/checkout";
  const activeMobileLink = mobileLinks
    .filter(([href]) => pathname === href || (href !== "/" && pathname.startsWith(`${href}/`)))
    .sort(([left], [right]) => right.length - left.length)[0]?.[0];
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    router.push(
      `/products?q=${encodeURIComponent(String(new FormData(event.currentTarget).get("q") ?? ""))}`,
    );
  }
  const links = adminLinks.filter(
    (item) => !item.permission || auth.admin?.permissions.includes(item.permission),
  );
  const adminNavigation = (
    <nav className="admin-nav" aria-label="商家导航">
      {links.map(({ href, title, icon: Icon }) => (
        <Link
          key={href}
          href={href}
          aria-current={pathname === href || pathname.startsWith(`${href}/`) ? "page" : undefined}
          onClick={() => setMenu(false)}
        >
          <Icon size={20} />
          {title}
        </Link>
      ))}
    </nav>
  );
  return (
    <>
      <a href="#main" className="skip-link">
        跳转到主要内容
      </a>
      {pathname === "/merchant" ? (
        <div className="merchant-shell">
          <main id="main" className="merchant-main">
            {children}
          </main>
        </div>
      ) : admin ? (
        <div className="admin-app">
          <aside className="sidebar">
            <Link href="/admin" className="sidebar-title">
              平台管理
            </Link>
            {adminNavigation}
            <Link className="back-store" href="/">
              <ArrowUpRight size={18} />
              查看商城
            </Link>
          </aside>
          <div className="admin-workspace">
            <header className="admin-topbar">
              <div className="mobile-only">
                <Button variant="ghost" onClick={() => setMenu(true)} aria-label="打开商家导航">
                  <List size={22} />
                </Button>
              </div>
              <span className="topbar-label">平台管理</span>
              <Link href="/admin/account" className="account-label">
                <UserCircle size={20} />
                {auth.admin?.email ?? "未登录"}
              </Link>
            </header>
            <main id="main" className="admin-main">
              {children}
            </main>
          </div>
          <Modal open={menu} title="商家导航" onClose={() => setMenu(false)}>
            {adminNavigation}
            <Link href="/" className="text-link" onClick={() => setMenu(false)}>
              查看商城
            </Link>
          </Modal>
        </div>
      ) : (
        <div className="storefront">
          <div className="store-topline">
            <div className="container">
              <span className="store-environment">
                {store.data?.payment_mode === "simulator"
                  ? "演示环境 · 使用模拟支付"
                  : "规格与价格，以结算报价为准"}
              </span>
              {!checkout && (
                <nav className="header-actions" aria-label="账户快捷入口">
                  <Link
                    href={auth.customer ? "/account" : "/login"}
                    aria-label={auth.customer ? "我的账户" : "登录"}
                  >
                    <UserCircle size={20} />
                    <span>{auth.customer ? "我的账户" : "登录 / 注册"}</span>
                  </Link>
                  <Link
                    className="header-favorites"
                    href="/account/favorites"
                    aria-label="我的收藏"
                  >
                    <Heart size={20} />
                  </Link>
                  <Link href="/cart" aria-label="购物车">
                    <ShoppingBag size={20} />
                    <span>购物车</span>
                  </Link>
                  <Button
                    className="mobile-only"
                    variant="ghost"
                    onClick={() => setMenu(true)}
                    aria-label="打开导航"
                    aria-expanded={menu}
                  >
                    <List size={22} />
                  </Button>
                </nav>
              )}
              <Link className="topline-help" href="/help">
                帮助与服务 <CaretRight size={12} />
              </Link>
            </div>
          </div>
          <header className="store-header">
            <div className="container header-main">
              {checkout ? (
                <Link className="text-link" href={flashCheckout ? "/flash-sales" : "/cart"}>
                  <ArrowLeft />
                  {flashCheckout ? "返回限时秒杀" : "返回购物车"}
                </Link>
              ) : (
                <>
                  <div className="header-search-area">
                    <form
                      className="header-search"
                      role="search"
                      action="/products"
                      method="get"
                      onSubmit={search}
                    >
                      <label className="sr-only" htmlFor="header-search">
                        搜索商品
                      </label>
                      <MagnifyingGlass size={20} />
                      <input
                        key={`${pathname}:${searchTerm}`}
                        id="header-search"
                        name="q"
                        defaultValue={searchTerm}
                        placeholder="搜索商品名称"
                        maxLength={80}
                      />
                      <button aria-label="搜索" type="submit">
                        搜索
                      </button>
                    </form>
                    <nav className="search-suggestions" aria-label="常用搜索">
                      <span>常用搜索</span>
                      {["护肤套装", "吸尘器", "电饭煲", "数据线"].map((keyword) => (
                        <Link key={keyword} href={{ pathname: "/products", query: { q: keyword } }}>
                          {keyword}
                        </Link>
                      ))}
                    </nav>
                  </div>
                </>
              )}
            </div>
            {!checkout && (
              <div className="store-nav-row">
                <div className="container">
                  <nav className="store-nav" aria-label="主要导航">
                    {[
                      ["/", "首页"],
                      ["/products", "全部商品"],
                      ["/flash-sales", "限时秒杀"],
                      ["/compare", "规格比较"],
                      ["/help", "选购指南"],
                      ["/support", "商城客服"],
                      ["/merchant", "商家工作台"],
                    ].map(([href, title]) => (
                      <Link
                        key={href}
                        href={href}
                        aria-current={
                          pathname === href ||
                          (href === "/products" && pathname.startsWith("/products/"))
                            ? "page"
                            : undefined
                        }
                      >
                        {title}
                      </Link>
                    ))}
                  </nav>
                  <Link className="store-order-link" href="/orders">
                    <Package size={16} /> 我的订单 <CaretRight size={12} />
                  </Link>
                </div>
              </div>
            )}
          </header>
          <main id="main" className="container store-main">
            {children}
          </main>
          <footer className="store-footer">
            <div className="container footer-main">
              <nav aria-label="选购与订单">
                <h2>选购与订单</h2>
                <Link href="/products">全部商品</Link>
                <Link href="/compare">规格比较</Link>
                <Link href="/orders">我的订单</Link>
              </nav>
              <nav aria-label="帮助与服务">
                <h2>帮助与服务</h2>
                <Link href="/help">购物帮助</Link>
                <Link href="/support">商城客服</Link>
                <Link href="/returns">售后进度</Link>
                <Link href="/account">账户管理</Link>
              </nav>
              <nav aria-label="关于商城">
                <h2>关于商城</h2>
                <Link href="/policies/privacy">隐私说明</Link>
                <Link href="/policies/terms">使用条款</Link>
                <Link href="/merchant">
                  商家入口 <ArrowUpRight size={14} />
                </Link>
              </nav>
            </div>
            <div className="container footer-bottom">
              <span>人民币 CNY</span>
            </div>
          </footer>
          <Modal open={menu} title="浏览商城" onClose={() => setMenu(false)}>
            <nav className="mobile-navigation">
              {mobileLinks.map(([href, title]) => (
                <Link
                  key={href}
                  href={href}
                  aria-current={activeMobileLink === href ? "page" : undefined}
                  onClick={() => setMenu(false)}
                >
                  {title}
                  <CaretRight />
                </Link>
              ))}
            </nav>
          </Modal>
        </div>
      )}
      {error !== undefined && (
        <div className="floating-error">
          <ErrorNotice error={error} />
          <Button variant="ghost" onClick={() => setError(undefined)}>
            关闭
          </Button>
        </div>
      )}
    </>
  );
}

export function AccountNavigation({ admin = false }: { admin?: boolean }) {
  const pathname = usePathname();
  const auth = useAuth();
  const router = useRouter();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  return (
    <>
      <div className="account-tabs">
        <nav aria-label="账户导航">
          {(admin
            ? [["/admin/account", "会话与密码"]]
            : [
                ["/account", "账户概览"],
                ["/merchant", "经营店铺"],
                ["/account/addresses", "收货地址"],
                ["/account/favorites", "收藏"],
                ["/account/notifications", "通知"],
                ["/account/memory", "偏好与记忆"],
                ["/account/security", "登录与安全"],
              ]
          ).map(([href, title]) => (
            <Link key={href} href={href} aria-current={pathname === href ? "page" : undefined}>
              {title}
            </Link>
          ))}
        </nav>
        <Button
          variant="ghost"
          busy={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await auth.logout(admin ? "admin" : "customer");
              router.push(admin ? "/admin/login" : "/login");
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <SignOut size={18} />
          退出登录
        </Button>
      </div>
      <ErrorNotice error={error} />
    </>
  );
}
