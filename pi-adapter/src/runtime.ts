import { readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  createAgentSession,
  ModelRuntime,
  SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Check } from "typebox/value";
import { BackendClient } from "./backend-client.ts";
import { config, HarnessError } from "./config.ts";
import type { RunReply, RunRequest } from "./contracts.ts";
import { runEvents } from "./events.ts";
import { commerceExtension, type RunMetrics } from "./extensions.ts";
import { commerceResources, guideTool } from "./resources.ts";
import { ConversationStore, pruneStorage } from "./sessions.ts";
import { commerceTools } from "./tools.ts";
import { guardModelRequests, type ModelStream } from "./model-requests.ts";

export async function createSupportRuntime() {
  const agentDir = config.agentDir;
  let settings: unknown = {};
  try {
    settings = JSON.parse(
      await readFile(join(agentDir, "settings.json"), "utf8"),
    );
  } catch (error) {
    if (!(error instanceof Error && "code" in error && error.code === "ENOENT"))
      throw error;
  }
  const saved = Check(
    Type.Object({
      defaultProvider: Type.Optional(Type.String()),
      defaultModel: Type.Optional(Type.String()),
    }),
    settings,
  )
    ? settings
    : {};
  const provider = process.env.COMMERCE_PI_PROVIDER ?? saved.defaultProvider;
  const modelId = process.env.COMMERCE_PI_MODEL ?? saved.defaultModel;
  if (!provider || !modelId)
    throw new Error(
      "Configure COMMERCE_PI_PROVIDER and COMMERCE_PI_MODEL or pi settings.",
    );
  const runtime = await ModelRuntime.create({
    authPath: join(agentDir, "auth.json"),
    modelsPath: join(agentDir, "models.json"),
    allowModelNetwork: false,
  });
  if (process.env.COMMERCE_PI_API_KEY) {
    await runtime.setRuntimeApiKey(provider, process.env.COMMERCE_PI_API_KEY);
  }
  const model = runtime.getModel(provider, modelId);
  if (!model || !runtime.hasConfiguredAuth(provider))
    throw new Error("Configured pi model or credentials unavailable.");
  const backend = new URL(
    process.env.COMMERCE_API_ORIGIN ?? "http://127.0.0.1:58000",
  );
  if (
    !["http:", "https:"].includes(backend.protocol) ||
    backend.username ||
    backend.password
  ) {
    throw new Error(
      "COMMERCE_API_ORIGIN must be an HTTP origin without credentials.",
    );
  }

  const active = new Set<string>();
  const contextLimit = Math.min(config.contextTokens, model.contextWindow);
  if (
    config.reserveTokens < config.maxTokens ||
    config.reserveTokens + config.keepRecentTokens >= contextLimit
  )
    throw new Error(
      "Compaction settings exceed the configured model context window.",
    );
  return {
    provider,
    model: modelId,
    prune: () => pruneStorage(active),
    async run(request: RunRequest, signal: AbortSignal): Promise<RunReply> {
      const key = `${request.user_id}/${request.conversation_id}`;
      if (active.has(key)) throw new HarnessError("SUPPORT_BUSY");
      active.add(key);
      try {
        const client = new BackendClient(request, backend, signal);
        await client.authorize();
        const store = new ConversationStore(request);
        const cached = await store.prepare();
        if (cached) return cached;
        const manager = await store.manager();
        const tools = [...commerceTools(client), guideTool];
        const metrics: RunMetrics = {
          turns: 0,
          tools: 0,
          tokens: 0,
          input: 0,
          output: 0,
          cost: 0,
          limited: false,
        };
        const settings = SettingsManager.inMemory({
          compaction: {
            enabled: true,
            reserveTokens:
              model.contextWindow - contextLimit + config.reserveTokens,
            keepRecentTokens: config.keepRecentTokens,
          },
          retry: {
            enabled: true,
            maxRetries: 1,
            baseDelayMs: 500,
            maxAgentDelayMs: 2000,
            provider: {
              maxRetries: 0,
              timeoutMs: config.timeoutMs,
              maxRetryDelayMs: 2000,
            },
          },
          cacheWarming: "off",
          enableSkillCommands: false,
        });
        let summaryStream: ModelStream;
        const loader = await commerceResources(
          settings,
          commerceExtension(
            client,
            metrics,
            new Set(tools.map((tool) => tool.name)),
            (...args) => summaryStream(...args),
          ),
        );
        if (loader.getExtensions().errors.length)
          throw new HarnessError("SUPPORT_EXTENSION_FAILED");
        const { session } = await createAgentSession({
          cwd: config.stateDir,
          agentDir,
          modelRuntime: runtime,
          model,
          thinkingLevel: "off",
          resourceLoader: loader,
          sessionManager: manager,
          settingsManager: settings,
          tools: tools.map((tool) => tool.name),
          customTools: tools,
        });
        const events = runEvents(client, metrics);
        const unsubscribe = session.subscribe((event) => events.observe(event));
        const cancel = () => {
          void session.abort();
        };
        signal.addEventListener("abort", cancel, { once: true });
        try {
          let extensionFailed = false;
          await session.bindExtensions({
            onError: () => {
              extensionFailed = true;
              cancel();
            },
          });
          // The public stream boundary preserves the SDK's loop and finishTurn.
          // It is also the hard gate: observational hooks may report an error and continue.
          const stream = session.agent.streamFunction;
          session.agent.streamFunction = guardModelRequests(
            stream,
            client,
            metrics,
            config.maxTokens,
          );
          summaryStream = guardModelRequests(
            stream,
            client,
            metrics,
            config.reserveTokens,
            true,
          );
          signal.throwIfAborted();
          await session.prompt(request.message);
          signal.throwIfAborted();
          if (extensionFailed)
            throw new HarnessError(
              metrics.limited
                ? "SUPPORT_BUDGET_EXHAUSTED"
                : "SUPPORT_EXTENSION_FAILED",
            );
          await client.authorize();
          const last = session.messages.at(-1);
          if (last?.role !== "assistant" || last.stopReason !== "stop")
            throw new HarnessError(
              metrics.limited
                ? "SUPPORT_BUDGET_EXHAUSTED"
                : "SUPPORT_INCOMPLETE",
            );
          const text = session.getLastAssistantText()?.trim();
          if (!text || text.length > 12000)
            throw new HarnessError("SUPPORT_INCOMPLETE");
          const reply: RunReply = {
            text,
            session_id: session.sessionId,
            outcome: metrics.limited ? "limited" : "answered",
            input_tokens: metrics.input,
            output_tokens: metrics.output,
            cost: metrics.cost,
            total_tokens: metrics.tokens,
            provider,
            model: modelId,
          };
          manager.appendCustomEntry("commerce_run", {
            run_id: request.run_id,
            version: "commerce-harness-v1",
            ...reply,
            text: undefined,
          });
          await store.finish(manager, reply);
          return reply;
        } finally {
          signal.removeEventListener("abort", cancel);
          unsubscribe();
          session.dispose();
          await events.flush();
        }
      } finally {
        active.delete(key);
      }
    },
  };
}
