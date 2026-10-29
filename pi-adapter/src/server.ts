import { timingSafeEqual } from "node:crypto";
import { createServer, type ServerResponse } from "node:http";
import { Check } from "typebox/value";
import { createSupportRuntime, requestSchema } from "./runtime.ts";

const secret = process.env.COMMERCE_SUPPORT_BRIDGE_SECRET;
if (!secret || secret.length < 32)
  throw new Error(
    "COMMERCE_SUPPORT_BRIDGE_SECRET must have at least 32 characters.",
  );
const expected = Buffer.from(`Bearer ${secret}`);
const runtime = await createSupportRuntime();
const controllers = new Set<AbortController>();

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
    respond(response, 200, { status: "ready" });
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
  if (request.method !== "POST" || request.url !== "/run") {
    respond(response, 404, { error: "NOT_FOUND" });
    return;
  }
  if (controllers.size >= 4) {
    respond(response, 503, { error: "SUPPORT_BUSY" });
    return;
  }
  const controller = new AbortController();
  controllers.add(controller);
  const timer = setTimeout(() => controller.abort(), 85000);
  response.on("close", () => controller.abort());
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
    const text = await runtime.run(body, controller.signal);
    respond(response, 200, { text });
  } catch {
    // 不输出模型异常原文，其中可能包含请求上下文或认证信息。
    respond(response, 502, { error: "SUPPORT_UNAVAILABLE" });
  } finally {
    clearTimeout(timer);
    controllers.delete(controller);
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
    for (const controller of controllers) controller.abort();
    server.close();
    server.closeIdleConnections();
  });
}
