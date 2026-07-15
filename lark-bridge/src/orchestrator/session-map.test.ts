import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { SessionMap, createMemoryStore, createFileStore } from "./session-map.js";

describe("SessionMap", () => {
  it("derives a stable, sanitized chat task code per thread", () => {
    const map = new SessionMap(createMemoryStore());
    const code = map.chatTaskCodeFor("oc_ab.12/34");
    expect(code).toBe("lark_chat_oc_ab_12_34");
  });

  it("ensure() creates and persists a binding", () => {
    const store = createMemoryStore();
    const map = new SessionMap(store);
    const binding = map.ensure("thread-1");
    expect(binding.chatTaskCode).toBe("lark_chat_thread-1");
    expect(binding.activeTaskCode).toBeNull();
    expect(store.read()["thread-1"]).toBeDefined();
  });

  it("ensure() is idempotent (keeps chat code stable)", () => {
    const map = new SessionMap(createMemoryStore());
    const first = map.ensure("t");
    const second = map.ensure("t");
    expect(second.chatTaskCode).toBe(first.chatTaskCode);
  });

  it("setActiveTask binds and clears the active harness task", () => {
    const map = new SessionMap(createMemoryStore());
    map.ensure("t");
    const bound = map.setActiveTask("t", "task07");
    expect(bound.activeTaskCode).toBe("task07");
    const cleared = map.setActiveTask("t", null);
    expect(cleared.activeTaskCode).toBeNull();
    // chat code never changes across task lifecycle
    expect(cleared.chatTaskCode).toBe(bound.chatTaskCode);
  });

  it("addTask assigns sequential short codes and selects the new task", () => {
    const map = new SessionMap(createMemoryStore());
    const first = map.addTask("t", "task07", "实现登录页");
    expect(first.ref.shortCode).toBe("t1");
    expect(first.ref.name).toBe("实现登录页");
    expect(first.binding.activeTaskCode).toBe("task07");
    const second = map.addTask("t", "task08", "修复崩溃");
    expect(second.ref.shortCode).toBe("t2");
    // multiple concurrent tasks coexist in one conversation (§6.3)
    expect(second.binding.tasks).toHaveLength(2);
    expect(second.binding.activeTaskCode).toBe("task08");
  });

  it("addTask is idempotent on task code (re-selects, refreshes name)", () => {
    const map = new SessionMap(createMemoryStore());
    map.addTask("t", "task07", "旧名");
    const again = map.addTask("t", "task07", "新名");
    expect(again.binding.tasks).toHaveLength(1);
    expect(again.ref.shortCode).toBe("t1");
    expect(again.ref.name).toBe("新名");
  });

  it("resolveShortCode maps a #short code back to its task ref (case-insensitive)", () => {
    const map = new SessionMap(createMemoryStore());
    map.addTask("t", "task07", "A");
    map.addTask("t", "task08", "B");
    expect(map.resolveShortCode("t", "t2")?.taskCode).toBe("task08");
    expect(map.resolveShortCode("t", "T1")?.taskCode).toBe("task07");
    expect(map.resolveShortCode("t", "t9")).toBeUndefined();
  });

  it("findTask locates a task ref by full code", () => {
    const map = new SessionMap(createMemoryStore());
    map.addTask("t", "task07", "A");
    expect(map.findTask("t", "task07")?.shortCode).toBe("t1");
    expect(map.findTask("t", "missing")).toBeUndefined();
  });

  it("snapshot() returns the full multi-thread map", () => {
    const map = new SessionMap(createMemoryStore());
    map.addTask("t1", "task01");
    map.addTask("t2", "task02");
    const snap = map.snapshot();
    expect(Object.keys(snap).sort()).toEqual(["t1", "t2"]);
    expect(snap.t1.tasks).toHaveLength(1);
  });

  it("stores and clears a pending confirmation", () => {
    const map = new SessionMap(createMemoryStore());
    map.setPendingConfirm("t", {
      kind: "handoff",
      requirementSummary: "实现登录",
      token: "nonce-1",
    });
    expect(map.getPendingConfirm("t")).toMatchObject({
      kind: "handoff",
      token: "nonce-1",
    });
    map.setPendingConfirm("t", null);
    expect(map.getPendingConfirm("t")).toBeNull();
  });
});

describe("createFileStore", () => {
  let dir: string;
  let file: string;

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "automind-sessionmap-"));
    file = path.join(dir, "nested", "session-map.json");
  });

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("returns an empty snapshot when the file is missing", () => {
    expect(createFileStore(file).read()).toEqual({});
  });

  it("persists writes across store instances (survives daemon restart)", () => {
    const map = new SessionMap(createFileStore(file));
    map.addTask("thread-1", "task07", "实现登录");
    map.setPendingConfirm("thread-1", {
      kind: "inject",
      taskCode: "task07",
      rewrittenInstruction: "改成蓝色",
      token: "nonce-2",
    });
    expect(fs.existsSync(file)).toBe(true);
    // A fresh store reading the same file recovers the binding.
    const map2 = new SessionMap(createFileStore(file));
    expect(map2.findTask("thread-1", "task07")?.name).toBe("实现登录");
    expect(map2.getPendingConfirm("thread-1")).toMatchObject({
      kind: "inject",
      token: "nonce-2",
    });
  });

  it("tolerates a corrupt file by returning an empty snapshot", () => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "not json", "utf8");
    expect(createFileStore(file).read()).toEqual({});
  });

  it("backs up a corrupt file to a .corrupt-* sibling instead of losing it", () => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "not json", "utf8");
    createFileStore(file).read();
    const dirName = path.dirname(file);
    const base = path.basename(file);
    const backups = fs
      .readdirSync(dirName)
      .filter((f) => f.startsWith(`${base}.corrupt-`));
    expect(backups).toHaveLength(1);
    expect(fs.readFileSync(path.join(dirName, backups[0]), "utf8")).toBe("not json");
  });
});
