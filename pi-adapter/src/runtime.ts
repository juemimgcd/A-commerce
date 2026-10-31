import { readFile } from "node:fs/promises";
import { join } from "node:path";
import {
  createAgentSession,
  ModelRuntime,
  SettingsManager,
  type ToolDefinition,
} from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Check } from "typebox/value";
import { BackendClient } from "./backend-client.ts";
import { config, HarnessError } from "./config.ts";
import { replySchema, type RunReply, type RunRequest } from "./contracts.ts";
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
        let finalReply: Pick<RunReply, "text" | "product_ids"> | undefined;
        const finalSchema = Type.Pick(replySchema, ["text", "product_ids"]);
        const tools = [
          ...commerceTools(client),
          ...(request.shop_id ? [] : [guideTool]),
          {
            name: "commerce_respond",
            label: "提交最终回复和选中的商品",
            description:
              "结束本轮咨询。text 为给用户的完整回复，product_ids 只列最终介绍的本轮已查商品，无商品时传空数组。",
            parameters: finalSchema,
            executionMode: "sequential",
            async execute(_id, params) {
              if (!Check(finalSchema, params))
                throw new Error("最终回复格式不正确。");
              finalReply = params;
              return {
                content: [{ type: "text", text: "回复已提交。" }],
                details: {},
                terminate: true,
              };
            },
          } satisfies ToolDefinition,
        ];
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
          Boolean(request.shop_id),
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
        const started = performance.now();
        let completed = false;
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
          const last = session.messages.at(-1);
          if (
            !finalReply &&
            !extensionFailed &&
            metrics.turns < config.maxTurns &&
            last?.role === "assistant" &&
            last.stopReason === "stop"
          ) {
            // Format once without re-querying; native hooks still enforce all budgets.
            session.setActiveToolsByName(["commerce_respond"]);
            await session.prompt(
              "请把刚才的答案通过 commerce_respond 提交，text 保留完整回复，product_ids 只选择本轮成功查到且答案中介绍的商品；无商品则为空数组。不要再查询。",
            );
            signal.throwIfAborted();
          }
          if (extensionFailed)
            throw new HarnessError(
              metrics.limited
                ? "SUPPORT_BUDGET_EXHAUSTED"
                : "SUPPORT_EXTENSION_FAILED",
            );
          await client.authorize();
          if (!finalReply)
            throw new HarnessError(
              metrics.limited
                ? "SUPPORT_BUDGET_EXHAUSTED"
                : "SUPPORT_INCOMPLETE",
            );
          const text = finalReply.text.trim();
          if (!text || text.length > 12000)
            throw new HarnessError("SUPPORT_INCOMPLETE");
          const reply: RunReply = {
            text,
            product_ids: finalReply.product_ids,
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
          completed = true;
          return reply;
        } finally {
          console.log(
            JSON.stringify({
              event: "support_run",
              run_id: request.run_id,
              provider,
              model: modelId,
              completed,
              duration_ms: Math.round(performance.now() - started),
              turns: metrics.turns,
              tools: client.trace,
              usage: {
                input: metrics.input,
                output: metrics.output,
                totalTokens: metrics.tokens,
                cost: metrics.cost,
              },
            }),
          );
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
