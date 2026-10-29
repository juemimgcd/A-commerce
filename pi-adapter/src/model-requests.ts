import {
  estimateTokens,
  type AgentSession,
} from "@earendil-works/pi-coding-agent";
import type { BackendClient } from "./backend-client.ts";
import { config, HarnessError } from "./config.ts";
import type { RunMetrics } from "./extensions.ts";

export type ModelStream = AgentSession["agent"]["streamFunction"];

export function guardModelRequests(
  stream: ModelStream,
  client: BackendClient,
  metrics: RunMetrics,
  maxTokens: number,
  accountSummary = false,
): ModelStream {
  return async (model, context, options) => {
    const signal = AbortSignal.any([
      client.signal,
      ...(options?.signal ? [options.signal] : []),
    ]);
    signal.throwIfAborted();
    await client.authorize(signal);
    signal.throwIfAborted();
    const outputLimit = Math.min(maxTokens, model.maxTokens);
    const estimated = context.messages.reduce(
      (sum, message) => sum + estimateTokens(message),
      0,
    );
    if (
      metrics.turns > config.maxTurns ||
      metrics.tokens + estimated + outputLimit > config.tokenBudget
    ) {
      metrics.limited = true;
      throw new HarnessError("SUPPORT_BUDGET_EXHAUSTED");
    }
    const response = await stream(model, context, {
      ...options,
      signal,
      maxTokens: outputLimit,
      maxRetries: 0,
    });
    if (accountSummary) {
      // compact() only needs result(), so waiting here preserves the native
      // stream and charges each summary before a split turn can request another.
      // This also accounts for the first summary when a later one fails.
      const { usage } = await response.result();
      metrics.tokens += usage.totalTokens;
      metrics.input += usage.input;
      metrics.output += usage.output;
      metrics.cost += usage.cost.total;
    }
    return response;
  };
}
