import { NextRequest, NextResponse } from "next/server";
import { apiOrigin, cookieNames, webOrigin } from "@/lib/server";

export const runtime = "nodejs";
const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
const rules: Record<string, RegExp[]> = {
  GET: [
    /^(customer)\/(store|me|profile|categories|products|cart|favorites|addresses|sessions|notification-preferences|notifications|orders|returns|events)$/,
    new RegExp(
      `^customer/(products/${uuid}(/media)?|media/${uuid}|quotes/${uuid}|returns/${uuid}|event-snapshots/${uuid}|orders/${uuid}(/(shipment|history|payment-session|return-options|refunds))?)$`,
    ),
    /^customer\/order-requests\/[A-Za-z0-9._:-]{8,96}$/,
    /^admin\/(profile|products|inventory|inventory\/low-stock|orders|returns|refunds|staff|sessions|audits|message-failures|operations|shipping-rules|reports\/daily|reports\/ranking|reconciliations|reconciliation-cases)$/,
    new RegExp(
      `^admin/(products/${uuid}(/(media|changes))?|inventory/${uuid}(/movements)?|orders/${uuid}(/(history|shipment))?|returns/${uuid}|exports/${uuid}(/download)?|media/${uuid}|reconciliations/${uuid})$`,
    ),
  ],
  POST: [
    /^customer\/(auth\/(login|logout|register|password|reset-request|reset-confirm)|addresses|quotes|orders|event-snapshots)$/,
    new RegExp(
      `^customer/(orders/${uuid}/(cancel|simulate-payment|returns)|returns/${uuid}/ship|notifications/${uuid}/read)$`,
    ),
    /^admin\/(auth\/(login|logout|password)|products|categories|staff|catalog-imports|exports|reconciliations)$/,
    new RegExp(
      `^admin/(products/${uuid}/(skus|media)|inventory/${uuid}/adjust|orders/${uuid}/(pick|ship|deliver)|returns/${uuid}/(approve|reject|receive|inspect)|operations/${uuid}/replay|users/${uuid}/disable|reconciliation-cases/${uuid}/recheck)$`,
    ),
  ],
  PUT: [
    /^customer\/notification-preferences$/,
    /^admin\/shipping-rules$/,
    new RegExp(`^customer/(addresses/${uuid}|cart/items/${uuid}|favorites/${uuid})$`),
    new RegExp(`^admin/(products/${uuid}(/skus/${uuid})?|staff/${uuid})$`),
  ],
  DELETE: [new RegExp(`^(customer/(sessions|favorites)|admin/sessions)/${uuid}$`)],
};

function failure(status: number, code: string, message: string) {
  return NextResponse.json(
    { error: { code, message, retryable: status >= 500, details: {} }, request_id: "" },
    { status },
  );
}

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const target = path.join("/");
  if (!rules[request.method]?.some((rule) => rule.test(target)))
    return failure(404, "NOT_FOUND", "接口不存在。");
  const scope = path[0] === "admin" ? "admin" : "customer";
  const write = request.method !== "GET";
  if (write && request.headers.get("origin") !== new URL(webOrigin()).origin) {
    return failure(403, "INVALID_ORIGIN", "请求来源不匹配，请从本站页面操作。");
  }
  const limit = target.endsWith("/media") ? 8 * 1024 * 1024 : 1024 * 1024;
  if (Number(request.headers.get("content-length")) > limit)
    return failure(413, "BODY_TOO_LARGE", "提交内容过大。");
  const upstreamHeaders = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) upstreamHeaders.set("Content-Type", contentType);
  const key = request.headers.get("idempotency-key");
  if (key) upstreamHeaders.set("Idempotency-Key", key);
  const token = request.cookies.get(cookieNames[scope])?.value;
  if (token) upstreamHeaders.set("Authorization", `Bearer ${token}`);
  // This header is accepted only by the included private gateway from the fixed web service IP.
  if (process.env.COMMERCE_TRUST_GATEWAY === "true") {
    const clientIp = request.headers.get("x-commerce-client-ip");
    if (clientIp) upstreamHeaders.set("X-Commerce-Client-IP", clientIp);
  }
  let body: ArrayBuffer | undefined;
  if (write && request.body) {
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) {
        await reader.cancel();
        return failure(413, "BODY_TOO_LARGE", "提交内容过大。");
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    body = bytes.buffer;
  }
  let upstream: Response;
  try {
    upstream = await fetch(`${apiOrigin()}/api/v1/${target}${request.nextUrl.search}`, {
      method: request.method,
      headers: upstreamHeaders,
      body,
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(20000),
    });
  } catch {
    return failure(503, "RESULT_UNCONFIRMED", "服务连接中断。交易操作请先查询已有结果。");
  }
  const headers = new Headers({ "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
  for (const name of [
    "content-type",
    "content-disposition",
    "retry-after",
    "x-request-id",
    "location",
  ]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (target.endsWith("/auth/login") && upstream.ok) {
    const data: unknown = await upstream.json();
    if (
      !data ||
      typeof data !== "object" ||
      !("token" in data) ||
      !("expires_at" in data) ||
      typeof data.token !== "string" ||
      typeof data.expires_at !== "string"
    ) {
      return failure(502, "INVALID_RESPONSE", "登录响应不完整，请重试。");
    }
    const response = NextResponse.json({ expires_at: data.expires_at }, { headers });
    response.cookies.set(cookieNames[scope], data.token, {
      httpOnly: true,
      secure: new URL(webOrigin()).protocol === "https:",
      sameSite: "lax",
      path: "/",
      expires: new Date(data.expires_at),
    });
    return response;
  }
  const response = new NextResponse(upstream.body, { status: upstream.status, headers });
  if (
    (target.endsWith("/auth/logout") && (upstream.ok || upstream.status === 401)) ||
    (target.endsWith("/auth/password") && upstream.ok)
  )
    response.cookies.delete(cookieNames[scope]);
  return response;
}

export { handle as GET, handle as POST, handle as PUT, handle as DELETE };
