/**
 * task-watcher.ts (design §7 / §7.1 / §12) — the proactive push engine.
 *
 * The router only reacts to inbound messages, so key progress (ask_user, phase
 * changes, build/test results, completion) was previously visible ONLY when the
 * user asked. This watcher closes that gap: it polls each task's on-disk
 * artifacts (`runtime-state.json` / `events.jsonl`) on `pollIntervalMs` and
 * PROACTIVELY pushes a card to the task's conversation the moment a new key
 * signal appears — without the user having to poll.
 *
 * Design guarantees:
 *  - CodeMind core is never mutated; this reads task files only.
 *  - One bot == one daemon == one workspace, so a watcher only ever sees this
 *    bot's tasks (physical isolation prevents cross-bot pushes).
 *  - A durable per-task cursor records what was already delivered, so a signal
 *    is pushed EXACTLY ONCE even across daemon restarts (§ "只推一次").
 *  - A push that fails does NOT advance the cursor, so a transient send error is
 *    retried on the next tick instead of being lost.
 *
 * All card rendering reuses progress.ts; the watcher only decides WHEN to push,
 * TO WHOM, and dedupes via the cursor.
 */
import fs from "node:fs";
import path from "node:path";
import type { Card, Channel } from "../channel/types.js";
import type { SessionMap, TaskRef } from "./session-map.js";
import type { SnapshotReader } from "./task-artifacts.js";
import type { TaskSnapshot } from "./progress.js";
import {
  progressCard,
  askUserCard,
  reportCard,
  formatTaskLabel,
  withTaskLabel,
} from "./progress.js";
import { progressLinesFromEvents } from "./task-events.js";
import { formatReportSection, type GitDiffSummary } from "./gitdiff.js";
import { backupCorruptFile } from "./registry.js";

/**
 * What has already been pushed for one task. Persisted so a signal is delivered
 * exactly once, even across daemon restarts.
 */
export interface TaskCursor {
  /** Conversation this task belongs to (push target). */
  threadId: string;
  /** Id of the last ask_user question already surfaced (null = none yet). */
  askUserId: string | null;
  /** Last phase already announced (null = none yet). */
  phase: string | null;
  /** Number of semantic progress lines already delivered. */
  eventCount: number;
  /** Whether the terminal report was already delivered. */
  finished: boolean;
}

export interface CursorSnapshot {
  [taskCode: string]: TaskCursor;
}

export interface CursorStore {
  read(): CursorSnapshot;
  write(snapshot: CursorSnapshot): void;
}

/** In-memory cursor store (tests / non-persistent runs). */
export function createMemoryCursorStore(
  initial: CursorSnapshot = {},
): CursorStore {
  let state: CursorSnapshot = { ...initial };
  return {
    read: () => ({ ...state }),
    write: (snapshot) => {
      state = { ...snapshot };
    },
  };
}

/**
 * File-backed cursor store (`~/.automind/channels/<botId>/push-cursor.json`).
 * Reads tolerate a missing file (first run) and preserve a corrupt one to a
 * `.corrupt-<ts>` backup instead of silently discarding delivered-state (which
 * would re-push everything).
 */
export function createFileCursorStore(filePath: string): CursorStore {
  return {
    read: () => {
      let raw: string;
      try {
        raw = fs.readFileSync(filePath, "utf8");
      } catch {
        return {};
      }
      try {
        const parsed = JSON.parse(raw) as CursorSnapshot;
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch {
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

export interface TaskWatcherDeps {
  /** Only the send surface is needed; keeps the watcher channel-neutral. */
  channel: Pick<Channel, "sendCard">;
  sessionMap: SessionMap;
  snapshotReader: SnapshotReader;
  cursorStore: CursorStore;
  /** Poll cadence in ms (from BridgeConfig.pollIntervalMs). */
  pollIntervalMs: number;
  /** Collect a git-diff summary for the terminal report (optional). */
  gitDiff?: (taskCode: string) => Promise<GitDiffSummary>;
  /** Injectable logger for send failures (defaults to console.error). */
  logger?: (message: string, err?: unknown) => void;
}

/**
 * Polls task artifacts and proactively pushes cards for new key signals. Call
 * `start()` after the channel connects and `stop()` on shutdown. `tick()` runs a
 * single pass and is public so tests can drive it deterministically.
 */
export class TaskWatcher {
  private readonly deps: TaskWatcherDeps;
  private readonly log: (message: string, err?: unknown) => void;
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;

  constructor(deps: TaskWatcherDeps) {
    this.deps = deps;
    this.log = deps.logger ?? ((m, e) => console.error(m, e ?? ""));
  }

  /** Begin polling. Idempotent. */
  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.tick();
    }, this.deps.pollIntervalMs);
    // Do not keep the event loop alive solely for the poll timer.
    this.timer.unref?.();
  }

  /** Stop polling. Idempotent. */
  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  /**
   * Run one poll pass over every task in the session map. Overlapping ticks are
   * skipped (a slow git-diff must not let two passes race the cursor).
   */
  async tick(): Promise<void> {
    if (this.running) return;
    this.running = true;
    const cursors = this.deps.cursorStore.read();
    try {
      const snap = this.deps.sessionMap.snapshot();
      for (const [threadId, binding] of Object.entries(snap)) {
        for (const ref of binding.tasks) {
          await this.checkTask(threadId, ref, cursors);
        }
      }
    } catch (err) {
      this.log("[watcher] tick failed:", err);
    } finally {
      // Persist whatever cursor advances succeeded this pass.
      try {
        this.deps.cursorStore.write(cursors);
      } catch (err) {
        this.log("[watcher] failed to persist cursor:", err);
      }
      this.running = false;
    }
  }

  /** Compare one task's live snapshot to its cursor and push new signals. */
  private async checkTask(
    threadId: string,
    ref: TaskRef,
    cursors: CursorSnapshot,
  ): Promise<void> {
    const snapshot = this.deps.snapshotReader.read(ref.taskCode);
    if (!snapshot) return;
    const cursor: TaskCursor = cursors[ref.taskCode] ?? {
      threadId,
      askUserId: null,
      phase: null,
      eventCount: 0,
      finished: false,
    };
    // Keep the push target current (thread should be stable, but be safe).
    cursor.threadId = threadId;
    cursors[ref.taskCode] = cursor;
    const label = formatTaskLabel(ref);

    // 1) ask_user — highest priority: the core is blocked on a human decision.
    //    Token = questionId so the tapped button matches the current question
    //    (same contract the router uses in sendProgress).
    if (snapshot.askUser && snapshot.askUser.id !== cursor.askUserId) {
      const card: Card = {
        ...withTaskLabel(askUserCard(snapshot.askUser), label),
        token: snapshot.askUser.id,
      };
      if (await this.trySend(threadId, card)) {
        cursor.askUserId = snapshot.askUser.id;
      }
    }

    // 2) phase change — entered a new macro phase (Build/Verify/...).
    if (snapshot.phase && snapshot.phase !== cursor.phase) {
      const card = withTaskLabel(progressCard(snapshot), label);
      if (await this.trySend(threadId, card)) {
        cursor.phase = snapshot.phase;
      }
    }

    // 3) new semantic events — build/test verdicts, UI actions, "corrected".
    const lines = progressLinesFromEvents(
      this.deps.snapshotReader.readEvents(ref.taskCode),
    );
    if (lines.length > cursor.eventCount) {
      const newLines = lines.slice(cursor.eventCount);
      const card = withTaskLabel(progressCard(snapshot, newLines), label);
      if (await this.trySend(threadId, card)) {
        cursor.eventCount = lines.length;
      }
    }

    // 4) terminal — task finished/failed: push the final report once, with the
    //    git-diff change summary folded in when a collector is available.
    if (snapshot.finished && !cursor.finished) {
      const card = await this.buildReportCard(snapshot, ref.taskCode, label);
      if (await this.trySend(threadId, card)) {
        cursor.finished = true;
      }
    }
  }

  private async buildReportCard(
    snapshot: TaskSnapshot,
    taskCode: string,
    label: string,
  ): Promise<Card> {
    const title = snapshot.status === "finished" ? "任务完成" : `任务${snapshot.status}`;
    let changeSummary: string | undefined;
    if (this.deps.gitDiff) {
      try {
        changeSummary = formatReportSection(await this.deps.gitDiff(taskCode));
      } catch {
        changeSummary = undefined;
      }
    }
    return withTaskLabel(
      reportCard(title, `状态：${snapshot.status}`, changeSummary),
      label,
    );
  }

  /** Send a card; a failure is logged and reported so the cursor is not advanced. */
  private async trySend(threadId: string, card: Card): Promise<boolean> {
    try {
      await this.deps.channel.sendCard(threadId, card);
      return true;
    } catch (err) {
      this.log(`[watcher] push failed for ${threadId}:`, err);
      return false;
    }
  }
}
