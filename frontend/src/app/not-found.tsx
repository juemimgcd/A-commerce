import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main" className="empty">
      <p className="eyebrow">404</p>
      <h1>这个页面暂时无法访问</h1>
      <p className="muted">检查链接是否完整，或返回商城继续浏览。</p>
      <Link href="/products" className="button primary">
        浏览全部商品
      </Link>
    </main>
  );
}
