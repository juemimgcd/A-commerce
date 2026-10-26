import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import { Suspense, type ReactNode } from "react";
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "./globals.css";
import { Providers } from "@/components/providers";
import { Shell } from "@/components/shell";
import { Loading } from "@/components/ui";
import { cookieNames, webOrigin } from "@/lib/server";

export const metadata: Metadata = {
  metadataBase: new URL(webOrigin()),
  title: { default: "A-commerce · 规格清楚，选择简单", template: "%s · A-commerce" },
  description: "按接口、功率与预算选择数码配件。核对规格、运费和总价，追踪订单与售后进度。",
  robots: { index: false, follow: false },
  openGraph: { siteName: "A-commerce", locale: "zh_CN", type: "website" },
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
            商品内容可直接阅读。登录、筛选与交易操作需要启用 JavaScript。
          </div>
        </noscript>
      </body>
    </html>
  );
}
