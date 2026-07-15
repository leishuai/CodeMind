import { describe, it, expect } from "vitest";
import {
  parseEvents,
  deriveProgressLines,
  progressLinesFromEvents,
} from "./task-events.js";

function jsonl(...events: unknown[]): string {
  return events.map((e) => JSON.stringify(e)).join("\n");
}

describe("parseEvents", () => {
  it("returns [] for null/empty input", () => {
    expect(parseEvents(null)).toEqual([]);
    expect(parseEvents("")).toEqual([]);
    expect(parseEvents("   \n  ")).toEqual([]);
  });

  it("skips malformed lines but keeps valid events", () => {
    const text = [
      "{not json",
      JSON.stringify({ type: "build_result", data: { succeeded: true } }),
      "",
      "42",
      JSON.stringify({ noType: true }),
      JSON.stringify({ type: "ui_action_done", data: { action: "tap" } }),
    ].join("\n");
    const events = parseEvents(text);
    expect(events).toHaveLength(2);
    expect(events[0].type).toBe("build_result");
    expect(events[1].type).toBe("ui_action_done");
  });
});

describe("deriveProgressLines", () => {
  it("renders a passing build_result", () => {
    const lines = deriveProgressLines([
      { type: "build_result", data: { succeeded: true } },
    ]);
    expect(lines).toEqual([{ kind: "build", text: "编译/测试通过 ✅" }]);
  });

  it("renders a failing build_result with failed checks", () => {
    const lines = deriveProgressLines([
      { type: "build_result", data: { succeeded: false, failedChecks: ["TC-1", "TC-2"] } },
    ]);
    expect(lines[0].kind).toBe("build");
    expect(lines[0].text).toContain("未通过");
    expect(lines[0].text).toContain("TC-1、TC-2");
  });

  it("renders ui_action_done with target and failure mark", () => {
    const lines = deriveProgressLines([
      { type: "ui_action_done", data: { action: "tap", target: "登录按钮", ok: true } },
      { type: "ui_action_done", data: { action: "input", name: "用户名", ok: false } },
    ]);
    expect(lines[0].text).toBe("已执行 UI 操作：tap「登录按钮」");
    expect(lines[1].text).toBe("已执行 UI 操作：input「用户名」（未成功）");
  });

  it("adds a 'problem corrected' line after a fail then a pass", () => {
    const lines = deriveProgressLines([
      { type: "build_result", data: { succeeded: false, failedChecks: ["TC-1"] } },
      { type: "build_result", data: { succeeded: true } },
    ]);
    expect(lines.map((l) => l.kind)).toEqual(["build", "build", "recovered"]);
    expect(lines[2].text).toBe("先前的问题已纠正");
  });

  it("does not add a recovery line when the first build passes", () => {
    const lines = deriveProgressLines([
      { type: "build_result", data: { succeeded: true } },
    ]);
    expect(lines.some((l) => l.kind === "recovered")).toBe(false);
  });

  it("ignores unrelated event types", () => {
    const lines = deriveProgressLines([
      { type: "heartbeat" },
      { type: "phase_start", data: {} },
    ]);
    expect(lines).toEqual([]);
  });
});

describe("progressLinesFromEvents", () => {
  it("reads, parses and derives text lines end to end", () => {
    const text = jsonl(
      { type: "build_result", data: { succeeded: false, failedChecks: ["build"] } },
      { type: "ui_action_done", data: { action: "tap", target: "确定" } },
      { type: "build_result", data: { succeeded: true } },
    );
    expect(progressLinesFromEvents(text)).toEqual([
      "编译/测试未通过 ❌（失败项：build）",
      "已执行 UI 操作：tap「确定」",
      "编译/测试通过 ✅",
      "先前的问题已纠正",
    ]);
  });

  it("returns [] when there are no events", () => {
    expect(progressLinesFromEvents(null)).toEqual([]);
  });
});
