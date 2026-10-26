import type { MetadataRoute } from "next";
import { publicData, webOrigin } from "@/lib/server";
import type { ProductPage } from "@/lib/types";
export const dynamic = "force-dynamic";
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  if (process.env.COMMERCE_INDEXABLE !== "true") return [];
  const urls: MetadataRoute.Sitemap = [{ url: webOrigin() }, { url: `${webOrigin()}/products` }];
  let cursor: string | null = null;
  for (let page = 0; page < 200; page++) {
    const products: ProductPage | undefined = await publicData<ProductPage>(
      `products?limit=50${cursor ? `&cursor=${cursor}` : ""}`,
    );
    if (!products) break;
    urls.push(
      ...products.items.map((product) => ({ url: `${webOrigin()}/products/${product.id}` })),
    );
    cursor = products.next_cursor ?? null;
    if (!cursor) break;
  }
  return urls;
}
