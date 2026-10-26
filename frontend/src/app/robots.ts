import type { MetadataRoute } from "next";
import { webOrigin } from "@/lib/server";
export const dynamic = "force-dynamic";
export default function robots(): MetadataRoute.Robots {
  return process.env.COMMERCE_INDEXABLE === "true"
    ? {
        rules: {
          userAgent: "*",
          allow: ["/", "/products"],
          disallow: [
            "/admin",
            "/account",
            "/cart",
            "/checkout",
            "/orders",
            "/returns",
            "/api",
            "/compare",
            "/login",
            "/register",
            "/reset-password",
            "/*?*",
          ],
        },
        sitemap: `${webOrigin()}/sitemap.xml`,
      }
    : { rules: { userAgent: "*", disallow: "/" } };
}
