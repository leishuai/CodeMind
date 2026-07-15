import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  TaskWatcher,
  createMemoryCursorStore,
  createFileCursorStore,
  type CursorSnapshot,
} from "./task-watcher.js";
import { SessionMap, createMemoryStore } from "./session-map.js";
import type { SnapshotReader } from "./task-artifacts.js";
import type { TaskSnapshot } from "./progress.js";
import type { Card } from "../channel/types.js";

/** A channel stub that records every card it is asked to send. */
function makeChannel() {
  const sent: { threadId: string; card: Card }[] = [];
  return {
    sent,
    sendCard: async (threadId: string, card: Card): Promise<string> => {
      sent.push({ threadId, card });
      return `msg-${sent.length}`;
    },
  };
}

/** A mutable in-memory SnapshotReader keyed by task code. */
function makeReader(
  snapshots: Record<string, TaskSnapshot | null>,
  events: Record<string, string | null> = {},
): SnapshotReader {
  return {
    read: (taskCode) => snapshots[taskCode] ?? null,
    readEvents: (taskCode) => events[taskCode] ?? null,
  };
}

function baseSnapshot(over: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    status: "generating",
    nextAction: "run_generator",
    phase: null,
    iteration: 1,
    askUser: null,
    finished: false,
    ...over,
  };
}

/** SessionMap with a single thread carrying one task. */
function mapWithTask(threadId: string, taskCode: string): SessionMap {
  const map = new SessionMap(createMemoryStore());
  map.addTask(threadId, taskCode, "demo task");
  return map;
}

describe("TaskWatcher signal pushing", () => {
  const threadId = "oc_thread_1";
  const taskCode = "task09";

  it("pushes an ask_user card when the core is waiting", async () => {
    const channel = makeChannel();
    const reader = makeReader({
      [taskCode]: baseSnapshot({
        status: "human_input_pending",
        askUser: { id: "q-1", question: "继续吗？", options: [] },
      }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    expect(channel.sent).toHaveLength(1);
    expect(channel.sent[0].threadId).toBe(threadId);
    expect(channel.sent[0].card.kind).toBe("ask_user");
    expect(channel.sent[0].card.token).toBe("q-1");
  });

  it("pushes a phase card when the phase changes", async () => {
    const channel = makeChannel();
    const reader = makeReader({
      [taskCode]: baseSnapshot({ phase: "Build" }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    expect(channel.sent).toHaveLength(1);
    expect(channel.sent[0].card.kind).toBe("progress");
  });

  it("pushes a card for new semantic events only", async () => {
    const channel = makeChannel();
    const events: Record<string, string | null> = {
      [taskCode]: JSON.stringify({ type: "build_result", data: { succeeded: true } }),
    };
    const reader = makeReader({ [taskCode]: baseSnapshot() }, events);
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    expect(channel.sent).toHaveLength(1);
    expect(channel.sent[0].card.body).toContain("编译/测试通过");
  });

  it("pushes a terminal report once when finished, with the git diff", async () => {
    const channel = makeChannel();
    const reader = makeReader({
      [taskCode]: baseSnapshot({ status: "finished", finished: true }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
      gitDiff: async () => ({ stat: "1 file changed", detail: "diff", filesChanged: 1 }),
    });
    await watcher.tick();
    const report = channel.sent.find((s) => s.card.kind === "report");
    expect(report).toBeDefined();
    expect(report?.card.collapsible?.[0].content).toContain("当前工作区未提交变更");
    expect(report?.card.collapsible?.[0].content).toContain("非任务归因");
  });
});

describe("TaskWatcher dedupe (只推一次)", () => {
  const threadId = "oc_thread_1";
  const taskCode = "task09";

  it("does not re-push the same ask_user across ticks", async () => {
    const channel = makeChannel();
    const reader = makeReader({
      [taskCode]: baseSnapshot({
        status: "human_input_pending",
        askUser: { id: "q-1", question: "继续吗？", options: [] },
      }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    await watcher.tick();
    await watcher.tick();
    expect(channel.sent.filter((s) => s.card.kind === "ask_user")).toHaveLength(1);
  });

  it("re-pushes when a NEW ask_user question appears", async () => {
    const channel = makeChannel();
    const snap = baseSnapshot({
      status: "human_input_pending",
      askUser: { id: "q-1", question: "第一问", options: [] },
    });
    const snapshots: Record<string, TaskSnapshot | null> = { [taskCode]: snap };
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: makeReader(snapshots),
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    snapshots[taskCode] = baseSnapshot({
      status: "human_input_pending",
      askUser: { id: "q-2", question: "第二问", options: [] },
    });
    await watcher.tick();
    const asks = channel.sent.filter((s) => s.card.kind === "ask_user");
    expect(asks).toHaveLength(2);
    expect(asks[1].card.token).toBe("q-2");
  });

  it("only pushes the terminal report once", async () => {
    const channel = makeChannel();
    const reader = makeReader({
      [taskCode]: baseSnapshot({ status: "finished", finished: true }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
    });
    await watcher.tick();
    await watcher.tick();
    expect(channel.sent.filter((s) => s.card.kind === "report")).toHaveLength(1);
  });
});

describe("TaskWatcher retry on send failure", () => {
  const threadId = "oc_thread_1";
  const taskCode = "task09";

  it("does not advance the cursor when a send fails, so the next tick retries", async () => {
    let fail = true;
    const sent: Card[] = [];
    const channel = {
      sendCard: async (_threadId: string, card: Card): Promise<string> => {
        if (fail) throw new Error("network down");
        sent.push(card);
        return "ok";
      },
    };
    const reader = makeReader({
      [taskCode]: baseSnapshot({
        status: "human_input_pending",
        askUser: { id: "q-1", question: "继续吗？", options: [] },
      }),
    });
    const watcher = new TaskWatcher({
      channel,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createMemoryCursorStore(),
      pollIntervalMs: 1000,
      logger: () => {},
    });
    await watcher.tick(); // fails, cursor not advanced
    expect(sent).toHaveLength(0);
    fail = false;
    await watcher.tick(); // retries successfully
    expect(sent).toHaveLength(1);
    expect(sent[0].kind).toBe("ask_user");
  });
});

describe("createFileCursorStore persistence", () => {
  let dir: string;
  let file: string;

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "am-cursor-"));
    file = path.join(dir, "push-cursor.json");
  });

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("returns an empty snapshot when the file is missing", () => {
    const store = createFileCursorStore(file);
    expect(store.read()).toEqual({});
  });

  it("round-trips a cursor snapshot to disk", () => {
    const store = createFileCursorStore(file);
    const snapshot: CursorSnapshot = {
      task09: {
        threadId: "oc_thread_1",
        askUserId: "q-1",
        phase: "Build",
        eventCount: 2,
        finished: false,
      },
    };
    store.write(snapshot);
    expect(createFileCursorStore(file).read()).toEqual(snapshot);
  });

  it("backs up a corrupt file and returns empty", () => {
    fs.writeFileSync(file, "{not json");
    const store = createFileCursorStore(file);
    expect(store.read()).toEqual({});
    const backups = fs.readdirSync(dir).filter((f) => f.includes(".corrupt-"));
    expect(backups.length).toBe(1);
  });

  it("persists delivered signals across a simulated daemon restart", async () => {
    const threadId = "oc_thread_1";
    const taskCode = "task09";
    const reader = makeReader({
      [taskCode]: baseSnapshot({ status: "finished", finished: true }),
    });
    const channel1 = makeChannel();
    const w1 = new TaskWatcher({
      channel: channel1,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createFileCursorStore(file),
      pollIntervalMs: 1000,
    });
    await w1.tick();
    expect(channel1.sent.filter((s) => s.card.kind === "report")).toHaveLength(1);

    // "Restart": a brand-new watcher backed by the same on-disk cursor file.
    const channel2 = makeChannel();
    const w2 = new TaskWatcher({
      channel: channel2,
      sessionMap: mapWithTask(threadId, taskCode),
      snapshotReader: reader,
      cursorStore: createFileCursorStore(file),
      pollIntervalMs: 1000,
    });
    await w2.tick();
    expect(channel2.sent.filter((s) => s.card.kind === "report")).toHaveLength(0);
  });
});
