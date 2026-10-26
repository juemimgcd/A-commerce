import { z } from "zod";

const errorSchema = z.object({
  error: z.object({ code: z.string(), message: z.string(), retryable: z.boolean().optional() }),
  request_id: z.string().optional(),
});

export class ApiError extends Error {
  status: number;
  code: string;
  requestId: string;
  retryAfter: number;
  constructor(message: string, status = 0, code = "NETWORK_ERROR", requestId = "", retryAfter = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.retryAfter = retryAfter;
  }
}

type Options = Omit<RequestInit, "body"> & {
  json?: unknown;
  body?: BodyInit;
  key?: string;
  validate?: (data: unknown) => unknown;
};

const cooldowns = new Map<string, { until: number; error: ApiError }>();

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { json, key, validate, ...init } = options;
  const headers = new Headers(init.headers);
  const cooldownKey = `${init.method ?? "GET"}:${path.split("?")[0]}`;
  const cooldown = cooldowns.get(cooldownKey);
  if (cooldown && cooldown.until > Date.now()) {
    throw new ApiError(
      cooldown.error.message,
      cooldown.error.status,
      cooldown.error.code,
      cooldown.error.requestId,
      Math.ceil((cooldown.until - Date.now()) / 1000),
    );
  }
  cooldowns.delete(cooldownKey);
  if (json !== undefined) headers.set("Content-Type", "application/json");
  if (key) headers.set("Idempotency-Key", key);
  let response: Response;
  try {
    response = await fetch(`/api/commerce/${path}`, {
      ...init,
      headers,
      body: json === undefined ? init.body : JSON.stringify(json),
      credentials: "same-origin",
      cache: "no-store",
      signal: init.signal
        ? AbortSignal.any([init.signal, AbortSignal.timeout(25000)])
        : AbortSignal.timeout(25000),
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError("连接中断或等待超时。交易操作请先查询已有结果。", 0, "RESULT_UNCONFIRMED");
  }
  if (response.status === 204) return undefined as T;
  const raw: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const parsed = errorSchema.safeParse(raw);
    const retry = response.headers.get("Retry-After");
    const seconds = retry
      ? Number(retry) || Math.max(0, (Date.parse(retry) - Date.now()) / 1000)
      : 0;
    const error = new ApiError(
      parsed.success ? parsed.data.error.message : "服务暂时不可用，请稍后重试。",
      response.status,
      parsed.success ? parsed.data.error.code : "HTTP_ERROR",
      parsed.success ? parsed.data.request_id : (response.headers.get("X-Request-ID") ?? ""),
      Number.isFinite(seconds) ? seconds : 0,
    );
    if (seconds > 0) cooldowns.set(cooldownKey, { until: Date.now() + seconds * 1000, error });
    throw error;
  }
  if (validate) {
    try {
      validate(raw);
    } catch {
      throw new ApiError("返回数据不完整，请重新查询结果。", 502, "INVALID_RESPONSE");
    }
  }
  return raw as T;
}

export const orderSchema = z.object({
  id: z.string().uuid(),
  version: z.number().int().positive(),
  status: z.string(),
  payment_status: z.string(),
  fulfillment_status: z.string(),
  total_fen: z.number().int().nonnegative(),
  shipping_fen: z.number().int().nonnegative(),
  refunded_fen: z.number().int().nonnegative(),
  refund_reserved_fen: z.number().int().nonnegative(),
  items: z.array(
    z.object({
      id: z.string().uuid(),
      quantity: z.number().int().positive(),
      price_fen: z.number().int(),
    }),
  ),
});
export const quoteSchema = z.object({
  id: z.string().uuid(),
  expires_at: z.string(),
  total_fen: z.number().int().nonnegative(),
  shipping_fen: z.number().int().nonnegative(),
  address_snapshot: z.record(z.string(), z.string()),
  items: z
    .array(
      z.object({
        sku_id: z.string().uuid(),
        quantity: z.number().int().positive(),
        price_fen: z.number().int(),
      }),
    )
    .min(1),
});

export function queryString(values: Record<string, string | number | boolean | undefined | null>) {
  const params = new URLSearchParams();
  for (const [name, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(name, String(value));
  }
  return params.size ? `?${params}` : "";
}

export function mediaUrl(id?: string | null, admin = false) {
  return id ? `/api/commerce/${admin ? "admin" : "customer"}/media/${id}` : undefined;
}
