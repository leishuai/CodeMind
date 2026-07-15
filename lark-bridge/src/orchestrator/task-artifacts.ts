/**
 * task-artifacts.ts (design §13.2) — disk-backed access to a task's artifacts
 * under `<workspaceRoot>/.automind/tasks/<taskCode>/`. Read-only; never mutates
 * the CodeMind core.
 */
import fs from "node:fs";
import path from "node:path";

import { readTaskSnapshot, type FileReader, type TaskSnapshot } from "./progress.js";

/**
 * Task codes are model/CLI-generated slugs. Restrict to a safe charset so a
 * crafted code (e.g. "../../etc") can never escape the tasks directory (§13.5).
 */
const SAFE_TASK_CODE = /^[A-Za-z0-9._-]+$/;

export function isSafeTaskCode(taskCode: string): boolean {
  return (
    SAFE_TASK_CODE.test(taskCode) &&
    taskCode !== "." &&
    taskCode !== ".." &&
    !taskCode.includes("..")
  );
}

/** Build a FileReader scoped to one task directory. */
export function taskFileReader(
  workspaceRoot: string,
  taskCode: string,
): FileReader {
  const tasksRoot = path.resolve(workspaceRoot, ".automind", "tasks");
  const taskDir = path.resolve(tasksRoot, taskCode);
  return (relativePath) => {
    // Reject unsafe task codes and any relativePath that escapes the task dir.
    if (!isSafeTaskCode(taskCode)) return null;
    const target = path.resolve(taskDir, relativePath);
    const withSep = taskDir + path.sep;
    if (target !== taskDir && !target.startsWith(withSep)) return null;
    try {
      return fs.readFileSync(target, "utf8");
    } catch {
      return null;
    }
  };
}

export interface SnapshotReader {
  read(taskCode: string): TaskSnapshot | null;
  /** Raw events.jsonl text for deriving semantic progress lines (§7.3). */
  readEvents(taskCode: string): string | null;
  /** Read one allow-listed task artifact through the same traversal guard. */
  readArtifact(taskCode: string, relativePath: string): string | null;
  /** Read an allow-listed file from the newest iteration that contains it. */
  readLatestIterationArtifact(taskCode: string, fileName: string): {
    path: string;
    content: string;
  } | null;
}

/**
 * Disk-backed SnapshotReader for the daemon; injectable in the router.
 * `workspaceRoot` accepts a string or a getter so the reader follows a runtime
 * workspace change (the project dir may be confirmed after startup).
 */
export function createSnapshotReader(
  workspaceRoot: string | (() => string),
): SnapshotReader {
  const rootOf = typeof workspaceRoot === "function" ? workspaceRoot : () => workspaceRoot;
  return {
    read: (taskCode) => readTaskSnapshot(taskFileReader(rootOf(), taskCode)),
    readEvents: (taskCode) => taskFileReader(rootOf(), taskCode)("events.jsonl"),
    readArtifact: (taskCode, relativePath) =>
      taskFileReader(rootOf(), taskCode)(relativePath),
    readLatestIterationArtifact: (taskCode, fileName) => {
      if (!isSafeTaskCode(taskCode) || !/^[A-Za-z0-9._-]+$/.test(fileName)) {
        return null;
      }
      const taskDir = path.resolve(rootOf(), ".automind", "tasks", taskCode);
      const logsDir = path.resolve(taskDir, "logs");
      try {
        const iterations = fs.readdirSync(logsDir)
          .map((name) => ({ name, match: /^iter-(\d+)$/.exec(name) }))
          .filter((item): item is { name: string; match: RegExpExecArray } =>
            item.match !== null)
          .sort((a, b) => Number(b.match[1]) - Number(a.match[1]));
        for (const iteration of iterations) {
          const relative = path.join("logs", iteration.name, fileName);
          const content = taskFileReader(rootOf(), taskCode)(relative);
          if (content !== null) return { path: relative, content };
        }
      } catch {
        return null;
      }
      return null;
    },
  };
}
