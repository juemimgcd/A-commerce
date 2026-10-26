import "server-only";
import { headers } from "next/headers";
import { cache } from "react";

export const cookieNames = { customer: "commerce_customer", admin: "commerce_admin" } as const;
export function apiOrigin() {
  return process.env.COMMERCE_API_ORIGIN ?? "http://127.0.0.1:58000";
}
export function webOrigin() {
  return process.env.COMMERCE_WEB_ORIGIN ?? "http://127.0.0.1:3000";
}

const readPublic = cache(async (path: string): Promise<unknown> => {
  try {
    const upstreamHeaders = new Headers();
    if (process.env.COMMERCE_TRUST_GATEWAY === "true") {
      const clientIp = (await headers()).get("x-commerce-client-ip");
      if (clientIp) upstreamHeaders.set("X-Commerce-Client-IP", clientIp);
    }
    const response = await fetch(`${apiOrigin()}/api/v1/customer/${path}`, {
      headers: upstreamHeaders,
      cache: "no-store",
      signal: AbortSignal.timeout(3500),
    });
    if (!response.ok) return undefined;
    return await response.json();
  } catch {
    return undefined;
  }
});

export async function publicData<T>(path: string): Promise<T | undefined> {
  return (await readPublic(path)) as T | undefined;
}
