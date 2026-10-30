import { Type } from "typebox";
import { Check } from "typebox/value";
import { HarnessError } from "./config.ts";
import type { RunRequest } from "./contracts.ts";

const reply = Type.Object({
  data: Type.Record(Type.String(), Type.Unknown()),
  links: Type.Array(Type.Object({ title: Type.String(), href: Type.String() })),
});
const error = Type.Object({
  error: Type.Object({ code: Type.String(), message: Type.String() }),
});
const state = Type.Object({
  status: Type.String(),
  memory_generation: Type.Integer(),
  user_id: Type.String(),
  conversation_id: Type.String(),
});

type ToolTrace = {
  tool: string;
  status: number | null;
  outcome:
    | "success"
    | "repeated_tool"
    | "network_error"
    | "timeout"
    | "aborted"
    | "http_error"
    | "invalid_json"
    | "invalid_response"
    | "invalid_link"
    | "response_too_large";
  duration_ms: number;
};

export class BackendClient {
  readonly invocations = new Map<string, number>();
  readonly trace: ToolTrace[] = [];
  readonly request: RunRequest;
  readonly origin: URL;
  readonly signal: AbortSignal;
  constructor(request: RunRequest, origin: URL, signal: AbortSignal) {
    this.request = request;
    this.origin = origin;
    this.signal = signal;
  }

  async fetch(path: string, init?: RequestInit, observation?: ToolTrace) {
    const timeout = AbortSignal.timeout(15000);
    const signal = AbortSignal.any([
      this.signal,
      ...(init?.signal ? [init.signal] : []),
      timeout,
    ]);
    try {
      const response = await fetch(
        new URL(
          `/api/v1/agent/support/runs/${this.request.run_id}${path}`,
          this.origin,
        ),
        {
          ...init,
          redirect: "error",
          headers: {
            Authorization: `Bearer ${this.request.token}`,
            "Content-Type": "application/json",
            ...init?.headers,
          },
          signal,
        },
      );
      if (observation) {
        observation.status = response.status;
        observation.outcome = response.ok ? "network_error" : "http_error";
      }
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (response.body)
        for await (const chunk of response.body) {
          size += chunk.length;
          if (size > 64 * 1024) {
            if (observation) observation.outcome = "response_too_large";
            throw new HarnessError("SUPPORT_RESULT_TOO_LARGE");
          }
          chunks.push(chunk);
        }
      const text = Buffer.concat(chunks).toString("utf8");
      if (response.status === 204) return null;
      if (observation)
        observation.outcome = response.ok ? "invalid_json" : "http_error";
      const raw: unknown = JSON.parse(text);
      if (!response.ok)
        throw new HarnessError(
          Check(error, raw) ? raw.error.code : "SUPPORT_TOOL_FAILED",
        );
      return raw;
    } catch (error) {
      if (observation && signal.aborted) {
        observation.outcome =
          timeout.aborted && signal.reason === timeout.reason
            ? "timeout"
            : "aborted";
      }
      throw error;
    }
  }

  async authorize(signal?: AbortSignal) {
    const raw = await this.fetch("/state", { signal });
    if (!Check(state, raw) || raw.status !== "running")
      throw new HarnessError("SUPPORT_EXPIRED");
    if (
      raw.user_id !== this.request.user_id ||
      raw.conversation_id !== this.request.conversation_id
    )
      throw new HarnessError("SUPPORT_INVALID_IDENTITY");
    if (raw.memory_generation !== this.request.memory_generation)
      throw new HarnessError("MEMORY_CHANGED");
  }

  async call(
    name: string,
    params: object,
    invocation: string,
    signal?: AbortSignal,
  ) {
    const started = performance.now();
    const observation: ToolTrace = {
      tool: name,
      status: null,
      outcome: "repeated_tool",
      duration_ms: 0,
    };
    try {
      const signature = JSON.stringify([
        name,
        Object.entries(params).sort(([a], [b]) => a.localeCompare(b)),
      ]);
      const repeated = this.invocations.get(signature) ?? 0;
      if (repeated >= 2) throw new HarnessError("SUPPORT_REPEATED_TOOL");
      this.invocations.set(signature, repeated + 1);
      observation.outcome = "network_error";
      // Reads and proposals are not automatically retried here. Pi sees the error and
      // can choose another call within the same budget; proposals deduplicate in the backend.
      const raw = await this.fetch(
        "/tools",
        {
          method: "POST",
          signal,
          headers: { "X-Tool-Call-ID": invocation },
          body: JSON.stringify({ ...params, tool: name }),
        },
        observation,
      );
      observation.outcome = "invalid_response";
      if (!Check(reply, raw)) throw new HarnessError("SUPPORT_INVALID_RESULT");
      observation.outcome = "invalid_link";
      if (
        raw.links.some(
          (link) => !link.href.startsWith("/") || link.href.startsWith("//"),
        )
      )
        throw new HarnessError("SUPPORT_INVALID_LINK");
      observation.outcome = "success";
      return { ...raw, observed_at: new Date().toISOString(), source: name };
    } finally {
      observation.duration_ms = Math.round(performance.now() - started);
      this.trace.push(observation);
    }
  }

  async event(
    type: string,
    elapsedMs: number,
    tool?: string,
    callId?: string,
    isError = false,
    signal?: AbortSignal,
  ) {
    await this.fetch("/events", {
      method: "POST",
      signal,
      body: JSON.stringify({
        type,
        elapsed_ms: elapsedMs,
        tool: tool ?? null,
        call_id: callId?.slice(0, 200) ?? null,
        is_error: isError,
      }),
    });
  }
}
