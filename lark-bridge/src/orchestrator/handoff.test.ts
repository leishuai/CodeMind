import { describe, it, expect } from "vitest";
import { startTaskFromSummary, parseTaskCode } from "./handoff.js";
import { injectInstruction } from "./inject.js";
import {
  CodeMindCli,
  type BackgroundCommandRunner,
  type CliResult,
  type CommandRunner,
} from "./automind-cli.js";
import type { TaskSnapshot } from "./progress.js";

function scriptedRunner(results: CliResult[]): {
  runner: CommandRunner;
  backgroundRunner: BackgroundCommandRunner;
  calls: string[][];
} {
  const calls: string[][] = [];
  let i = 0;
  const runner: CommandRunner = async (_bin, args) => {
    calls.push(args);
    const r = results[i] ?? { code: 0, stdout: "", stderr: "" };
    i += 1;
    return r;
  };
  const backgroundRunner: BackgroundCommandRunner = async (_bin, args) => {
    calls.push(args);
    return { code: 0, stdout: '{"result":"started"}', stderr: "" };
  };
  return { runner, backgroundRunner, calls };
}

function cliWith(
  runner: CommandRunner,
  backgroundRunner: BackgroundCommandRunner,
): CodeMindCli {
  return new CodeMindCli({
    bin: "/repo/automind.sh",
    workspaceRoot: "/ws",
    runner,
    backgroundRunner,
  });
}

describe("parseTaskCode", () => {
  it("reads task code from JSON output", () => {
    expect(parseTaskCode('{"result":"ok","task":"task09"}')).toBe("task09");
  });
  it("falls back to a task token", () => {
    expect(parseTaskCode("Created task07 successfully")).toBe("task07");
  });
});

describe("startTaskFromSummary", () => {
  it("starts a task and seeds recent messages", async () => {
    const { runner, backgroundRunner, calls } = scriptedRunner([
      { code: 0, stdout: 'TASK_CODE=task09', stderr: "" }, // scaffold
      { code: 0, stdout: "", stderr: "" }, // message 1
      { code: 0, stdout: "", stderr: "" }, // message 2
    ]);
    const res = await startTaskFromSummary(cliWith(runner, backgroundRunner), {
      requirementSummary: "实现 X",
      agent: "auto",
      recentMessages: ["原话1", "原话2"],
    });
    expect(res.ok).toBe(true);
    expect(res.taskCode).toBe("task09");
    expect(calls[0]).toEqual(["scaffold", "实现 X", "--no-current"]);
    expect(calls[1]).toEqual(["message", "task09", "--text", "原话1"]);
    expect(calls[2]).toEqual(["message", "task09", "--text", "原话2"]);
    expect(calls[3]).toEqual(["resume", "task09", "auto", "--detached"]);
  });

  it("reports failure when ask fails", async () => {
    const { runner, backgroundRunner } = scriptedRunner([{ code: 1, stdout: "", stderr: "no agent" }]);
    const res = await startTaskFromSummary(cliWith(runner, backgroundRunner), {
      requirementSummary: "X",
      agent: "auto",
    });
    expect(res.ok).toBe(false);
    expect(res.taskCode).toBeNull();
  });

  it("returns the created task code when background start fails", async () => {
    const { runner } = scriptedRunner([
      { code: 0, stdout: "TASK_CODE=task10", stderr: "" },
    ]);
    const failedBackground: BackgroundCommandRunner = async () => ({
      code: 1,
      stdout: "",
      stderr: "spawn failed",
    });
    const res = await startTaskFromSummary(
      cliWith(runner, failedBackground),
      { requirementSummary: "X", agent: "auto" },
    );
    expect(res.ok).toBe(false);
    expect(res.taskCode).toBe("task10");
  });
});

describe("injectInstruction", () => {
  const activeSnapshot: TaskSnapshot = {
    status: "generating",
    nextAction: "run_generator",
    phase: "delivery",
    iteration: 2,
    askUser: null,
    finished: false,
  };
  const pausedSnapshot: TaskSnapshot = {
    ...activeSnapshot,
    status: "finished",
    finished: true,
  };

  it("only appends when the loop is active (no resume)", async () => {
    const { runner, backgroundRunner, calls } = scriptedRunner([{ code: 0, stdout: "", stderr: "" }]);
    const res = await injectInstruction(cliWith(runner, backgroundRunner), "task01", "改成深色", activeSnapshot, "auto");
    expect(res.ok).toBe(true);
    expect(res.appended).toBe(true);
    expect(res.resumed).toBe(false);
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("message");
  });

  it("resumes once when the task is paused/finished", async () => {
    const { runner, backgroundRunner, calls } = scriptedRunner([
      { code: 0, stdout: "", stderr: "" }, // message
    ]);
    const res = await injectInstruction(cliWith(runner, backgroundRunner), "task01", "改成深色", pausedSnapshot, "auto");
    expect(res.ok).toBe(true);
    expect(res.appended).toBe(true);
    expect(res.resumed).toBe(true);
    expect(calls[1][0]).toBe("resume");
  });

  it("reports append committed when background resume fails", async () => {
    const { runner } = scriptedRunner([
      { code: 0, stdout: "", stderr: "" },
    ]);
    const failedBackground: BackgroundCommandRunner = async () => ({
      code: 1,
      stdout: "",
      stderr: "spawn failed",
    });
    const res = await injectInstruction(
      cliWith(runner, failedBackground),
      "task01",
      "改成深色",
      pausedSnapshot,
      "auto",
    );
    expect(res.ok).toBe(false);
    expect(res.appended).toBe(true);
    expect(res.resumed).toBe(true);
  });
});
