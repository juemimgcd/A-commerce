import { timingSafeEqual } from "node:crypto";
import { createServer, type ServerResponse } from "node:http";
import { Check } from "typebox/value";
import { createSupportRuntime } from "./runtime.ts";
import { requestSchema } from "./contracts.ts";
import { config, HarnessError } from "./config.ts";
import { acquireStorage } from "./sessions.ts";

const secret = process.env.COMMERCE_SUPPORT_BRIDGE_SECRET;
if (!secret || secret.length < 32)
  throw new Error(
    "COMMERCE_SUPPORT_BRIDGE_SECRET must have at least 32 characters.",
  );
const expected = Buffer.from(`Bearer ${secret}`);
process.umask(0o077);
const release = await acquireStorage();
const runtime = await createSupportRuntime().catch(async (error) => {
  await release();
  throw error;
});
const controllers = new Map<string, AbortController>();
let closing = false;
const cleanup = setInterval(
  () => {
    void runtime.prune().catch(() => console.warn("support_retention_failed"));
  },
  12 * 60 * 60 * 1000,
);
cleanup.unref();

function respond(response: ServerResponse, status: number, body: object) {
  if (response.destroyed || response.writableEnded) return;
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(body));
}

const server = createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/health") {
    respond(response, closing ? 503 : 200, {
      status: closing ? "closing" : "ready",
      version: "commerce-harness-v1",
    });
    return;
  }
  const provided = Buffer.from(request.headers.authorization ?? "");
  if (
    provided.length !== expected.length ||
    !timingSafeEqual(provided, expected)
  ) {
    respond(response, 401, { error: "UNAUTHENTICATED" });
    return;
  }
  const cancel = request.url?.match(/^\/runs\/([0-9a-f-]{36})\/cancel$/);
  if (request.method === "POST" && cancel) {
    controllers.get(cancel[1])?.abort();
    respond(response, 200, { status: "cancelled" });
    return;
  }
  if (request.method !== "POST" || request.url !== "/run") {
    respond(response, 404, { error: "NOT_FOUND" });
    return;
  }
  if (closing || controllers.size >= config.concurrency) {
    respond(response, 503, { error: "SUPPORT_BUSY" });
    return;
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  response.on("close", () => controller.abort());
  let runId: string | undefined;
  try {
    const chunks: Buffer[] = [];
    let length = 0;
    for await (const chunk of request) {
      const bytes = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
      length += bytes.length;
      if (length > 64 * 1024) {
        respond(response, 413, { error: "BODY_TOO_LARGE" });
        return;
      }
      chunks.push(bytes);
    }
    let body: unknown;
    try {
      body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    } catch {
      respond(response, 422, { error: "INVALID_REQUEST" });
      return;
    }
    if (!Check(requestSchema, body)) {
      respond(response, 422, { error: "INVALID_REQUEST" });
      return;
    }
    if (
      controllers.has(body.run_id) ||
      controllers.size >= config.concurrency
    ) {
      respond(response, 409, { error: "SUPPORT_BUSY" });
      return;
    }
    runId = body.run_id;
    controllers.set(runId, controller);
    const reply = await runtime.run(body, controller.signal);
    respond(response, 200, reply);
  } catch (error) {
    // 不输出模型异常原文，其中可能包含请求上下文或认证信息。
    respond(response, 502, {
      error: error instanceof HarnessError ? error.code : "SUPPORT_UNAVAILABLE",
    });
  } finally {
    clearTimeout(timer);
    if (runId) controllers.delete(runId);
  }
});

server.requestTimeout = 10000;
server.headersTimeout = 5000;
server.listen(
  Number(process.env.COMMERCE_PI_PORT ?? 58010),
  process.env.COMMERCE_PI_HOST ?? "127.0.0.1",
  () => {
    console.log(
      `A-commerce support ready (${runtime.provider}/${runtime.model})`,
    );
  },
);
for (const signal of ["SIGINT", "SIGTERM"] as const) {
  process.on(signal, () => {
    closing = true;
    clearInterval(cleanup);
    for (const controller of controllers.values()) controller.abort();
    server.close(() => {
      void release();
    });
    server.closeIdleConnections();
  });
}
