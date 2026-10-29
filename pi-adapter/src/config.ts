import { homedir } from "node:os";
import { join, resolve } from "node:path";

function integer(name: string, fallback: number, min: number, max: number) {
  const value = Number(process.env[name] ?? fallback);
  if (!Number.isInteger(value) || value < min || value > max)
    throw new Error(`Invalid ${name}`);
  return value;
}

export const config = {
  agentDir:
    process.env.COMMERCE_PI_AGENT_DIR ?? join(homedir(), ".pi", "agent"),
  stateDir: resolve(process.env.COMMERCE_PI_STATE_DIR ?? "var/commerce-pi"),
  maxTurns: integer("COMMERCE_PI_MAX_TURNS", 8, 1, 16),
  maxTools: integer("COMMERCE_PI_MAX_TOOLS", 12, 1, 12),
  maxTokens: integer("COMMERCE_PI_MAX_OUTPUT_TOKENS", 1800, 128, 8192),
  tokenBudget: integer("COMMERCE_PI_TOKEN_BUDGET", 64000, 2048, 1000000),
  reserveTokens: integer("COMMERCE_PI_RESERVE_TOKENS", 4096, 2048, 32768),
  contextTokens: integer("COMMERCE_PI_CONTEXT_TOKENS", 32768, 8192, 1048576),
  keepRecentTokens: integer(
    "COMMERCE_PI_KEEP_RECENT_TOKENS",
    8000,
    1024,
    64000,
  ),
  timeoutMs: integer("COMMERCE_PI_TIMEOUT_MS", 85000, 5000, 170000),
  concurrency: integer("COMMERCE_PI_CONCURRENCY", 4, 1, 32),
  retentionDays: integer("COMMERCE_PI_RETENTION_DAYS", 30, 1, 365),
};

export class HarnessError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(code);
    this.code = code;
  }
}
