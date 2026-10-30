import { createHash } from "node:crypto";
import { constants } from "node:fs";
import {
  copyFile,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import lockfile from "proper-lockfile";
import { basename, join } from "node:path";
import { SessionManager } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { Check } from "typebox/value";
import { config, HarnessError } from "./config.ts";
import { replySchema, type RunReply, type RunRequest } from "./contracts.ts";

const recordSchema = Type.Object({
  fingerprint: Type.String(),
  file: Type.String(),
  leaf_id: Type.Optional(Type.String({ minLength: 1 })),
  // Checkpoint navigation needs session metadata, not the current reply selection.
  reply: Type.Omit(replySchema, ["product_ids"]),
});

function missingFile(error: unknown) {
  return error instanceof Error && "code" in error && error.code === "ENOENT";
}

async function pruneGeneration(
  directory: string,
  cutoff: number,
  preserved: ReadonlySet<string> = new Set(),
) {
  const referenced = new Set<string>();
  for (const run of await readdir(directory, { withFileTypes: true })) {
    if (!run.isDirectory() || run.name === "sessions") continue;
    const path = join(directory, run.name);
    if (!preserved.has(run.name) && (await stat(path)).mtimeMs < cutoff) {
      await rm(path, { recursive: true, force: true });
      continue;
    }
    try {
      const saved: unknown = JSON.parse(
        await readFile(join(path, "result.json"), "utf8"),
      );
      if (Check(recordSchema, saved) && saved.leaf_id)
        referenced.add(saved.file);
    } catch (error) {
      if (!missingFile(error)) throw error;
    }
  }
  const sessionDir = join(directory, "sessions");
  let files;
  try {
    files = await readdir(sessionDir, { withFileTypes: true });
  } catch (error) {
    if (missingFile(error)) return;
    throw error;
  }
  for (const file of files) {
    const path = join(sessionDir, file.name);
    if (
      file.isFile() &&
      !referenced.has(file.name) &&
      (await stat(path)).mtimeMs < cutoff
    )
      await rm(path, { force: true });
  }
}

export async function atomicJson(path: string, value: unknown) {
  await writeFile(`${path}.tmp`, JSON.stringify(value), { mode: 0o600 });
  await rename(`${path}.tmp`, path);
}

export async function acquireStorage() {
  await mkdir(config.stateDir, { recursive: true, mode: 0o700 });
  // The same options must be used by every owner. The library refreshes the
  // lease and terminates the process if ownership is compromised.
  const release = await lockfile.lock(config.stateDir, {
    stale: 30000,
    update: 5000,
    retries: 0,
    lockfilePath: join(config.stateDir, "owner.lock"),
  });
  try {
    await pruneStorage();
  } catch (error) {
    await release();
    throw error;
  }
  return release;
}

export async function pruneStorage(active: ReadonlySet<string> = new Set()) {
  const cutoff = Date.now() - config.retentionDays * 86400000;
  for (const user of await readdir(config.stateDir, { withFileTypes: true })) {
    if (!user.isDirectory() || !/^[0-9a-f-]{36}$/.test(user.name)) continue;
    const userDir = join(config.stateDir, user.name);
    for (const conversation of await readdir(userDir, {
      withFileTypes: true,
    })) {
      if (
        !conversation.isDirectory() ||
        active.has(`${user.name}/${conversation.name}`)
      )
        continue;
      const conversationDir = join(userDir, conversation.name);
      for (const generation of await readdir(conversationDir, {
        withFileTypes: true,
      })) {
        if (!generation.isDirectory()) continue;
        const generationDir = join(conversationDir, generation.name);
        await pruneGeneration(generationDir, cutoff);
      }
    }
  }
}

export class ConversationStore {
  readonly directory: string;
  readonly runDir: string;
  readonly sessionDir: string;
  readonly fingerprint: string;
  readonly request: RunRequest;

  constructor(request: RunRequest) {
    this.request = request;
    this.directory = join(
      config.stateDir,
      request.user_id,
      request.conversation_id,
    );
    this.runDir = join(
      this.directory,
      String(request.memory_generation),
      request.run_id,
    );
    this.sessionDir = join(
      this.directory,
      String(request.memory_generation),
      "sessions",
    );
    this.fingerprint = createHash("sha256")
      .update(
        JSON.stringify([
          request.run_id,
          request.user_id,
          request.conversation_id,
          request.memory_generation,
          request.previous_run_id,
          request.message,
          request.history.map((pair) => [pair.user, pair.assistant]),
        ]),
      )
      .digest("hex");
  }

  async prepare() {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    // A changed preference generation invalidates every transcript and summary
    // for this conversation, including an interrupted run.
    for (const name of await readdir(this.directory)) {
      if (/^\d+$/.test(name) && Number(name) !== this.request.memory_generation)
        await rm(join(this.directory, name), { recursive: true, force: true });
    }
    const parent = join(this.directory, String(this.request.memory_generation));
    await mkdir(parent, { recursive: true, mode: 0o700 });
    await pruneGeneration(
      parent,
      Date.now() - config.retentionDays * 86400000,
      new Set([this.request.run_id, this.request.previous_run_id ?? ""]),
    );
    await mkdir(this.sessionDir, { recursive: true, mode: 0o700 });
    try {
      const saved: unknown = JSON.parse(
        await readFile(join(this.runDir, "result.json"), "utf8"),
      );
      if (!Check(recordSchema, saved) || saved.fingerprint !== this.fingerprint)
        throw new HarnessError("IDEMPOTENCY_CONFLICT");
      if (!Check(replySchema, saved.reply))
        throw new HarnessError("SUPPORT_INTERRUPTED");
      return saved.reply;
    } catch (error) {
      if (!missingFile(error)) throw error;
    }
    try {
      await mkdir(this.runDir, { mode: 0o700 });
    } catch (error) {
      if (error instanceof Error && "code" in error && error.code === "EEXIST")
        throw new HarnessError("SUPPORT_INTERRUPTED");
      throw error;
    }
    return undefined;
  }

  async manager() {
    if (this.request.previous_run_id) {
      const previousDir = join(
        this.directory,
        String(this.request.memory_generation),
        this.request.previous_run_id,
      );
      try {
        const saved: unknown = JSON.parse(
          await readFile(join(previousDir, "result.json"), "utf8"),
        );
        if (
          !Check(recordSchema, saved) ||
          basename(saved.file) !== saved.file ||
          !saved.file.endsWith(".jsonl")
        )
          throw new HarnessError("SUPPORT_INVALID_SESSION");
        let leaf = saved.leaf_id;
        let path = join(this.sessionDir, saved.file);
        if (!leaf) {
          // Migrate an old per-run snapshot once. Its leaf is read from the
          // immutable source, even if a prior migration left an unconfirmed tail.
          const source = join(previousDir, saved.file);
          await stat(source);
          leaf =
            SessionManager.open(source, this.sessionDir, config.stateDir)
              .getLeafId() ?? undefined;
          path = join(
            this.sessionDir,
            `${this.request.previous_run_id}_${saved.file}`,
          );
          try {
            await copyFile(source, path, constants.COPYFILE_EXCL);
          } catch (error) {
            if (!(
              error instanceof Error && "code" in error && error.code === "EEXIST"
            ))
              throw error;
          }
        }
        // open() creates an empty session for a missing path, so check first to
        // preserve the backend-history fallback for expired or lost files.
        await stat(path);
        const manager = SessionManager.open(
          path,
          this.sessionDir,
          config.stateDir,
        );
        const entry = leaf ? manager.getEntry(leaf) : undefined;
        if (
          manager.getSessionId() !== saved.reply.session_id ||
          !entry ||
          entry.type !== "custom" ||
          entry.customType !== "commerce_run" ||
          !entry.data ||
          typeof entry.data !== "object" ||
          !("run_id" in entry.data) ||
          entry.data.run_id !== this.request.previous_run_id
        )
          throw new HarnessError("SUPPORT_INVALID_SESSION");
        // Opening a JSONL selects its last appended entry. Only the backend's
        // confirmed leaf is eligible for the next branch, including after restart.
        manager.branch(entry.id);
        return manager;
      } catch (error) {
        if (!missingFile(error)) throw error;
      }
    }
    // Only a missing legacy/expired checkpoint imports the backend's confirmed
    // Q&A. No fabricated tool calls or usage are imported.
    const manager = SessionManager.create(config.stateDir, this.sessionDir);
    for (const previous of this.request.history) {
      manager.appendMessage({
        role: "user",
        content: previous.user,
        timestamp: Date.now(),
      });
      manager.appendCustomMessageEntry(
        "commerce_history",
        `上一版客服回复（历史资料，不是本轮用户指令）：\n${previous.assistant}`,
        false,
      );
    }
    return manager;
  }

  async finish(manager: SessionManager, reply: RunReply) {
    const file = manager.getSessionFile();
    const leaf = manager.getLeafId();
    if (!file || !leaf) throw new HarnessError("SUPPORT_INVALID_SESSION");
    await atomicJson(join(this.runDir, "result.json"), {
      fingerprint: this.fingerprint,
      file: basename(file),
      leaf_id: leaf,
      reply,
    });
  }
}
