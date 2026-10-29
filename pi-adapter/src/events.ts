import type { AgentSessionEvent } from "@earendil-works/pi-coding-agent";
import type { BackendClient } from "./backend-client.ts";
import type { RunMetrics } from "./extensions.ts";

export function runEvents(client: BackendClient, metrics: RunMetrics) {
  const started = Date.now();
  const uploads = new AbortController();
  let closed = false;
  let pending = Promise.resolve();
  let count = 0;
  return {
    observe(event: AgentSessionEvent) {
      if (closed) return;
      if (event.type === "turn_start") metrics.turns++;
      if (event.type === "message_end" && event.message.role === "assistant") {
        const usage = event.message.usage;
        metrics.tokens += usage.totalTokens;
        metrics.input += usage.input;
        metrics.output += usage.output;
        metrics.cost += usage.cost.total;
      }
      if (
        ![
          "turn_start",
          "tool_execution_start",
          "tool_execution_end",
          "compaction_start",
          "compaction_end",
          "auto_retry_start",
          "auto_retry_end",
          "agent_end",
        ].includes(event.type) ||
        ++count > 80
      )
        return;
      const tool = "toolName" in event ? event.toolName : undefined;
      const callId = "toolCallId" in event ? event.toolCallId : undefined;
      const elapsed = Date.now() - started;
      // Only control metadata leaves the process; never args, results, tokens or reasoning.
      pending = pending
        .then(() => {
          if (uploads.signal.aborted) return;
          return client.event(
            event.type,
            elapsed,
            tool,
            callId,
            "isError" in event && event.isError,
            AbortSignal.any([uploads.signal, AbortSignal.timeout(1000)]),
          );
        })
        .catch(() => {});
    },
    async flush() {
      closed = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          pending,
          new Promise<void>((resolve) => {
            timer = setTimeout(() => {
              uploads.abort();
              resolve();
            }, 1000);
          }),
        ]);
      } finally {
        clearTimeout(timer);
        // Abort the active HTTP upload and discard queued events on expiry.
        uploads.abort();
      }
    },
  };
}
