import { describe, it, expect } from "vitest";
import { collectGitDiff, formatReportSection } from "./gitdiff.js";
import type { CommandRunner } from "./automind-cli.js";

function gitRunner(stat: string, diff: string): CommandRunner {
  return async (_bin, args) => {
    if (args.includes("--stat")) {
      return { code: 0, stdout: stat, stderr: "" };
    }
    return { code: 0, stdout: diff, stderr: "" };
  };
}

describe("collectGitDiff", () => {
  it("collects stat + detail and counts changed files", async () => {
    const stat = " src/a.ts | 2 +-\n src/b.ts | 5 +++++\n 2 files changed, 7 insertions(+)";
    const summary = await collectGitDiff(gitRunner(stat, "diff-body"), "/ws");
    expect(summary.filesChanged).toBe(2);
    expect(summary.stat).toContain("files changed");
    expect(summary.detail).toBe("diff-body");
  });

  it("truncates long diffs", async () => {
    const long = "x".repeat(20);
    const summary = await collectGitDiff(gitRunner("1 file changed", long), "/ws", 10);
    expect(summary.detail).toContain("truncated");
    expect(summary.detail.length).toBeLessThan(long.length + 30);
  });
});

describe("formatReportSection", () => {
  it("emits a no-change section when nothing changed", () => {
    const section = formatReportSection({ stat: "", detail: "", filesChanged: 0 });
    expect(section).toContain("无改动");
    expect(section).toContain("非任务归因");
  });

  it("emits the stat block when there are changes", () => {
    const section = formatReportSection({
      stat: " src/a.ts | 2 +-\n 1 file changed",
      detail: "",
      filesChanged: 1,
    });
    expect(section).toContain("当前工作区未提交变更");
    expect(section).toContain("非任务归因");
    expect(section).toContain("src/a.ts");
  });
});
