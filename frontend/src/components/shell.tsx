"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
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
} from "@phosphor-icons/react";
import { useAuth, useStore } from "./providers";
import { Button, ErrorNotice, Modal } from "./ui";

export const adminLinks = [
  { href: "/admin/products", title: "商品", permission: "catalog", icon: Package },
  { href: "/admin/inventory", title: "库存", permission: "catalog", icon: SlidersHorizontal },
  { href: "/admin/orders", title: "订单履约", permission: "fulfillment", icon: Receipt },
  { href: "/admin/returns", title: "售后申请", permission: "aftersales", icon: ArrowsClockwise },
  { href: "/admin/refunds", title: "退款", permission: "aftersales", icon: Receipt },
  { href: "/admin/reports", title: "经营报表", permission: "audit", icon: ChartBar },
  { href: "/admin/reconciliations", title: "资金对账", permission: "audit", icon: ShieldCheck },
  { href: "/admin/operations", title: "异常任务", permission: "audit", icon: SlidersHorizontal },
  { href: "/admin/audits", title: "操作审计", permission: "audit", icon: ShieldCheck },
  { href: "/admin/shipping", title: "配送规则", permission: "catalog", icon: Truck },
  { href: "/admin/staff", title: "员工权限", permission: "identity", icon: Users },
  { href: "/admin/account", title: "我的会话", permission: "", icon: UserCircle },
];

function Logo({ admin = false }: { admin?: boolean }) {
  return (
    <Link
      href={admin ? "/admin" : "/"}
      className="logo"
      aria-label={admin ? "A-commerce 商家工作台" : "A-commerce 首页"}
    >
      <span className="brand-mark" aria-hidden>
        A<span>·</span>
      </span>
      <span>A-commerce{admin && <small>商家工作台</small>}</span>
    </Link>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const admin = pathname.startsWith("/admin");
  const auth = useAuth();
  const store = useStore();
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const [error, setError] = useState<unknown>();
  const checkout = pathname === "/checkout";
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
      {admin ? (
        <div className="admin-app">
          <aside className="sidebar">
            <Logo admin />
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
              <span className="topbar-label">A-commerce / 商家工作台</span>
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
        <>
          <div className="store-topline">
            <div className="container">
              <span>数码配件 · 按需选择</span>
              <span>
                {store.data?.payment_mode === "simulator"
                  ? "演示环境 · 使用模拟支付"
                  : "规格与价格，以结算报价为准"}
              </span>
            </div>
          </div>
          <header className="store-header">
            <div className="container header-main">
              <Logo />
              {checkout ? (
                <Link className="text-link" href="/cart">
                  <ArrowLeft />
                  返回购物车
                </Link>
              ) : (
                <>
                  <nav className="desktop-nav" aria-label="主要导航">
                    <Link
                      href="/products"
                      aria-current={pathname.startsWith("/products") ? "page" : undefined}
                    >
                      全部商品
                    </Link>
                    <Link
                      href="/compare"
                      aria-current={pathname === "/compare" ? "page" : undefined}
                    >
                      规格比较
                    </Link>
                    <Link href="/help">购物帮助</Link>
                  </nav>
                  <form className="header-search" onSubmit={search}>
                    <label className="sr-only" htmlFor="header-search">
                      搜索配件
                    </label>
                    <MagnifyingGlass size={20} />
                    <input
                      id="header-search"
                      name="q"
                      placeholder="搜索商品或规格"
                      maxLength={80}
                    />
                    <button aria-label="搜索" type="submit">
                      <ArrowUpRight size={20} />
                    </button>
                  </form>
                  <div className="header-actions">
                    <Link href="/cart" aria-label="购物车">
                      <ShoppingBag size={24} />
                    </Link>
                    <Link
                      href={auth.customer ? "/account" : "/login"}
                      aria-label={auth.customer ? "我的账户" : "登录"}
                    >
                      <UserCircle size={25} />
                    </Link>
                    <Button
                      className="mobile-only"
                      variant="ghost"
                      onClick={() => setMenu(true)}
                      aria-label="打开导航"
                    >
                      <List size={24} />
                    </Button>
                  </div>
                </>
              )}
            </div>
          </header>
          <main id="main" className="container store-main">
            {children}
          </main>
          <footer className="store-footer">
            <div className="container footer-main">
              <div>
                <Logo />
                <p>规格清楚，选择简单。</p>
              </div>
              <nav aria-label="页脚导航">
                <Link href="/help">购物帮助</Link>
                <Link href="/orders">我的订单</Link>
                <Link href="/policies/privacy">隐私说明</Link>
                <Link href="/policies/terms">使用条款</Link>
                <Link href="/admin">
                  商家入口 <ArrowUpRight size={14} />
                </Link>
              </nav>
            </div>
            <div className="container footer-bottom">
              <span>
                © {new Date().getFullYear()} {store.data?.name ?? "A-commerce"}
              </span>
              <span>人民币 CNY · 单店数码配件</span>
            </div>
          </footer>
          <Modal open={menu} title="浏览商城" onClose={() => setMenu(false)}>
            <nav className="mobile-navigation">
              {[
                ["/", "首页"],
                ["/products", "全部商品"],
                ["/compare", "规格比较"],
                ["/orders", "我的订单"],
                ["/account", "我的账户"],
                ["/help", "购物帮助"],
              ].map(([href, title]) => (
                <Link key={href} href={href} onClick={() => setMenu(false)}>
                  {title}
                  <ArrowUpRight />
                </Link>
              ))}
            </nav>
          </Modal>
        </>
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
                ["/account/addresses", "收货地址"],
                ["/account/favorites", "收藏"],
                ["/account/notifications", "通知"],
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
