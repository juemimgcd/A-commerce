import { type NextRequest, NextResponse } from "next/server";

const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const pages = [
  /^\/$/,
  /^\/(products|compare|login|register|reset-password|cart|checkout|help|orders|returns)$/,
  /^\/policies\/(privacy|terms)$/,
  /^\/account(\/(addresses|favorites|notifications|security))?$/,
  new RegExp(`^/products/${uuid}$`),
  new RegExp(`^/orders/${uuid}(/(payment|return))?$`),
  new RegExp(`^/returns/${uuid}$`),
  /^\/admin(\/(login|account|products|inventory|orders|returns|refunds|reports|reconciliations|operations|audits|shipping|staff))?$/,
  new RegExp(`^/admin/products/(new|${uuid})$`),
  new RegExp(`^/admin/(inventory|orders|returns)/${uuid}$`),
];

export function proxy(request: NextRequest) {
  if (pages.some((page) => page.test(request.nextUrl.pathname))) return NextResponse.next();
  // Resolve unknown page paths before the async layout starts streaming a 200 response.
  return NextResponse.next({ status: 404 });
}

export const config = {
  matcher: ["/((?!api/|_next/|icon.svg|opengraph-image|robots.txt|sitemap.xml).*)"],
};
