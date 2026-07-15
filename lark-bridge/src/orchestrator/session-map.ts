/**
 * session-map.ts (design §13.1 / §6.3) — persistent mapping between a channel
 * conversation (thread) and its {S_chat chat task code, the set of harness
 * tasks started in that conversation, the currently-addressed task}.
 *
 * Design note (§6.3, flat message stream): Feishu threads (topics) are NOT used
 * to isolate tasks anymore — new replies inside an older topic are easy to miss.
 * Instead every conversation is a single flat message stream, and multiple
 * concurrent tasks live under ONE conversation. Each task carries a short code
 * (e.g. `t1`, `t2`) and a human name so every card/message can be labeled, and
 * the user addresses a specific task with a `#<shortCode>` prefix. The map keeps
 * a "current" task pointer as the default target when no prefix is given.
 *
 * File-backed JSON so the daemon survives restarts. The store is injectable
 * (read/write functions) to keep it unit-testable without real disk I/O.
 */
import fs from "node:fs";
import path from "node:path";
import { backupCorruptFile } from "./registry.js";

/** One harness task started inside a conversation. */
export interface TaskRef {
  /** Full harness task code (as returned by `automind scaffold`). */
  taskCode: string;
  /** Short, conversation-local addressing code (e.g. `t1`, `t2`). */
  shortCode: string;
  /** Human-readable task name (usually the requirement summary's first line). */
  name: string;
  createdAt: string;
}

export type PendingConfirmation =
  | { kind: "handoff"; requirementSummary: string; token: string }
  | {
      kind: "inject";
      taskCode: string;
      rewrittenInstruction: string;
      token: string;
    };

export interface ThreadBinding {
  /** Fixed chat session task code for this conversation (never switches). */
  chatTaskCode: string;
  /** All harness tasks started in this conversation, in creation order. */
  tasks: TaskRef[];
  /** The currently-addressed harness task code (default target), or null. */
  activeTaskCode: string | null;
  /** Pending confirmation card state; persisted across daemon restarts. */
  pendingConfirm?: PendingConfirmation | null;
  updatedAt: string;
}

export interface SessionMapSnapshot {
  [threadId: string]: ThreadBinding;
}

export interface SessionMapStore {
  read(): SessionMapSnapshot;
  write(snapshot: SessionMapSnapshot): void;
}

/** In-memory store (used by tests and as a base for the file store). */
export function createMemoryStore(
  initial: SessionMapSnapshot = {},
): SessionMapStore {
  let state: SessionMapSnapshot = { ...initial };
  return {
    read: () => ({ ...state }),
    write: (snapshot) => {
      state = { ...snapshot };
    },
  };
}

/**
 * File-backed store persisting the session map to a JSON file so a per-bot
 * daemon survives restarts (design §multi-bot). Reads tolerate a missing/corrupt
 * file (returns empty); writes create the parent directory as needed.
 */
export function createFileStore(filePath: string): SessionMapStore {
  return {
    read: () => {
      let raw: string;
      try {
        raw = fs.readFileSync(filePath, "utf8");
      } catch {
        // Missing file is the normal first-run case.
        return {};
      }
      try {
        const parsed = JSON.parse(raw) as SessionMapSnapshot;
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch {
        // Corrupt existing file: preserve it to a `.corrupt-<ts>` backup instead
        // of silently discarding + overwriting the thread<->task bindings.
        backupCorruptFile(filePath);
        return {};
      }
    },
    write: (snapshot) => {
      fs.mkdirSync(path.dirname(filePath), { recursive: true });
      fs.writeFileSync(filePath, JSON.stringify(snapshot, null, 2), "utf8");
    },
  };
}

export class SessionMap {
  private readonly store: SessionMapStore;

  constructor(store: SessionMapStore) {
    this.store = store;
  }

  get(threadId: string): ThreadBinding | undefined {
    return this.store.read()[threadId];
  }

  /** Full map snapshot (all threads). Used to summarize active tasks. */
  snapshot(): SessionMapSnapshot {
    return this.store.read();
  }

  /** Derive a stable, thread-scoped chat task code (≠ tui_chat_code). */
  chatTaskCodeFor(threadId: string): string {
    const existing = this.get(threadId);
    if (existing) return existing.chatTaskCode;
    return `lark_chat_${sanitize(threadId)}`;
  }

  /** Ensure a binding exists and return it. */
  ensure(threadId: string): ThreadBinding {
    const snapshot = this.store.read();
    const existing = snapshot[threadId];
    if (existing) return existing;
    const binding: ThreadBinding = {
      chatTaskCode: `lark_chat_${sanitize(threadId)}`,
      tasks: [],
      activeTaskCode: null,
      pendingConfirm: null,
      updatedAt: new Date().toISOString(),
    };
    snapshot[threadId] = binding;
    this.store.write(snapshot);
    return binding;
  }

  /**
   * Register a newly-started harness task under a conversation, assign it a
   * conversation-local short code, make it the current addressed task, and
   * return the created {binding, ref}. Idempotent on taskCode: re-adding an
   * existing task just re-selects it (and refreshes its name if provided).
   */
  addTask(
    threadId: string,
    taskCode: string,
    name = "",
  ): { binding: ThreadBinding; ref: TaskRef } {
    const binding = this.ensure(threadId);
    const snapshot = this.store.read();
    const current = snapshot[threadId] ?? binding;
    const tasks = [...current.tasks];
    let ref = tasks.find((t) => t.taskCode === taskCode);
    if (ref) {
      if (name) ref = { ...ref, name };
      const idx = tasks.findIndex((t) => t.taskCode === taskCode);
      tasks[idx] = ref;
    } else {
      ref = {
        taskCode,
        shortCode: `t${tasks.length + 1}`,
        name: name || taskCode,
        createdAt: new Date().toISOString(),
      };
      tasks.push(ref);
    }
    const updated: ThreadBinding = {
      ...current,
      tasks,
      activeTaskCode: taskCode,
      updatedAt: new Date().toISOString(),
    };
    snapshot[threadId] = updated;
    this.store.write(snapshot);
    return { binding: updated, ref };
  }

  /**
   * Set the current addressed harness task for a conversation. Passing null
   * clears the pointer. A non-null task code that is not yet registered is
   * auto-registered (so callers that only know the task code still work).
   */
  setActiveTask(threadId: string, taskCode: string | null): ThreadBinding {
    if (taskCode === null) {
      const binding = this.ensure(threadId);
      const snapshot = this.store.read();
      const current = snapshot[threadId] ?? binding;
      const updated: ThreadBinding = {
        ...current,
        activeTaskCode: null,
        updatedAt: new Date().toISOString(),
      };
      snapshot[threadId] = updated;
      this.store.write(snapshot);
      return updated;
    }
    return this.addTask(threadId, taskCode).binding;
  }

  /** Resolve a conversation-local short code to its task ref. */
  resolveShortCode(threadId: string, shortCode: string): TaskRef | undefined {
    const binding = this.get(threadId);
    if (!binding) return undefined;
    const needle = shortCode.trim().toLowerCase();
    return binding.tasks.find((t) => t.shortCode.toLowerCase() === needle);
  }

  /** Find a task ref by full task code. */
  findTask(threadId: string, taskCode: string): TaskRef | undefined {
    return this.get(threadId)?.tasks.find((t) => t.taskCode === taskCode);
  }

  setPendingConfirm(
    threadId: string,
    pending: PendingConfirmation | null,
  ): ThreadBinding {
    const binding = this.ensure(threadId);
    const snapshot = this.store.read();
    const current = snapshot[threadId] ?? binding;
    const updated = {
      ...current,
      pendingConfirm: pending,
      updatedAt: new Date().toISOString(),
    };
    snapshot[threadId] = updated;
    this.store.write(snapshot);
    return updated;
  }

  getPendingConfirm(threadId: string): PendingConfirmation | null {
    return this.get(threadId)?.pendingConfirm ?? null;
  }
}

function sanitize(threadId: string): string {
  return threadId.replace(/[^A-Za-z0-9_-]/g, "_");
}
