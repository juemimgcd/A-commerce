import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import { Suspense, type ReactNode } from "react";
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
import "./globals.css";
import "./storefront.css";
import "./support.css";
import { Providers } from "@/components/providers";
import { Shell } from "@/components/shell";
import { Loading } from "@/components/ui";
import { cookieNames, webOrigin } from "@/lib/server";

export const metadata: Metadata = {
  metadataBase: new URL(webOrigin()),
  title: { default: "A-commerce · 规格清楚，选择简单", template: "%s · A-commerce" },
  description: "浏览护肤、小家电与日常用品，按分类、品牌和预算选购，查看规格、价格及订单进度。",
  robots: { index: false, follow: false },
  openGraph: { locale: "zh_CN", type: "website" },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#f7f8fa" };

export default async function RootLayout({ children }: { children: ReactNode }) {
  const jar = await cookies();
  const hints = { customer: jar.has(cookieNames.customer), admin: jar.has(cookieNames.admin) };
  return (
    <html lang="zh-CN">
      <body>
        <Providers hints={hints}>
          <Suspense fallback={<Loading />}>
            <Shell>{children}</Shell>
          </Suspense>
        </Providers>
        <noscript>
          <div className="noscript-notice">
            请启用 JavaScript 以完整加载商品，并使用筛选、登录与交易功能。
          </div>
        </noscript>
      </body>
    </html>
  );
}
