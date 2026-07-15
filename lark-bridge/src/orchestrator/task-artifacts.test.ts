import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  createSnapshotReader,
  taskFileReader,
  isSafeTaskCode,
} from "./task-artifacts.js";

describe("isSafeTaskCode", () => {
  it("accepts normal slugs", () => {
    expect(isSafeTaskCode("task09")).toBe(true);
    expect(isSafeTaskCode("A_b-c.1")).toBe(true);
  });

  it("rejects traversal and unsafe codes", () => {
    expect(isSafeTaskCode("..")).toBe(false);
    expect(isSafeTaskCode("../etc")).toBe(false);
    expect(isSafeTaskCode("a/b")).toBe(false);
    expect(isSafeTaskCode("")).toBe(false);
    expect(isSafeTaskCode("a b")).toBe(false);
  });
});

describe("taskFileReader path safety", () => {
  let root: string;

  beforeEach(() => {
    root = fs.mkdtempSync(path.join(os.tmpdir(), "am-tasks-"));
    const taskDir = path.join(root, ".automind", "tasks", "task09");
    fs.mkdirSync(taskDir, { recursive: true });
    fs.writeFileSync(path.join(taskDir, "runtime-state.json"), "{}");
    // A sibling secret outside the task dir, used for traversal attempts.
    fs.writeFileSync(path.join(root, "secret.txt"), "TOP SECRET");
  });

  afterEach(() => {
    fs.rmSync(root, { recursive: true, force: true });
  });

  it("reads a file within the task directory", () => {
    const read = taskFileReader(root, "task09");
    expect(read("runtime-state.json")).toBe("{}");
  });

  it("returns null for an unsafe task code", () => {
    const read = taskFileReader(root, "../../");
    expect(read("secret.txt")).toBeNull();
  });

  it("returns null when relativePath escapes the task directory", () => {
    const read = taskFileReader(root, "task09");
    expect(read("../../../secret.txt")).toBeNull();
  });

  it("reads an allow-listed artifact from the newest iteration", () => {
    const taskDir = path.join(root, ".automind", "tasks", "task09");
    fs.mkdirSync(path.join(taskDir, "logs", "iter-1"), { recursive: true });
    fs.mkdirSync(path.join(taskDir, "logs", "iter-3"), { recursive: true });
    fs.writeFileSync(path.join(taskDir, "logs", "iter-1", "log-digest.md"), "old");
    fs.writeFileSync(path.join(taskDir, "logs", "iter-3", "log-digest.md"), "new");
    const reader = createSnapshotReader(root);
    expect(reader.readLatestIterationArtifact("task09", "log-digest.md")).toEqual({
      path: "logs/iter-3/log-digest.md",
      content: "new",
    });
  });
});
