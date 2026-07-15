import { describe, it, expect } from "vitest";
import {
  readTaskSnapshot,
  askUserCard,
  progressCard,
  gitdiffCard,
  noticeCard,
  reportCard,
  type FileReader,
} from "./progress.js";

function fileReaderFrom(files: Record<string, string>): FileReader {
  return (rel) => (rel in files ? files[rel] : null);
}

describe("readTaskSnapshot", () => {
  it("returns null when runtime-state is missing", () => {
    expect(readTaskSnapshot(fileReaderFrom({}))).toBeNull();
  });

  it("parses status/nextAction/iteration/phase", () => {
    const reader = fileReaderFrom({
      "runtime-state.json": JSON.stringify({
        status: "generating",
        nextAction: "run_generator",
        iteration: 3,
        stateSummary: { phase: "delivery" },
      }),
    });
    const snap = readTaskSnapshot(reader);
    expect(snap?.status).toBe("generating");
    expect(snap?.phase).toBe("delivery");
    expect(snap?.iteration).toBe(3);
    expect(snap?.finished).toBe(false);
  });

  it("flags finished statuses", () => {
    const reader = fileReaderFrom({
      "runtime-state.json": JSON.stringify({ status: "finished", nextAction: "finish" }),
    });
    expect(readTaskSnapshot(reader)?.finished).toBe(true);
  });

  it("extracts ask_user question and normalizes options", () => {
    const reader = fileReaderFrom({
      "runtime-state.json": JSON.stringify({
        status: "human_input_pending",
        nextAction: "ask_user",
        askUserQuestion: {
          id: "ask-plan-01",
          question: "用真机还是模拟器?",
          options: [
            { id: "1", label: "真机", recommended: true },
            { id: "2", label: "模拟器" },
          ],
        },
      }),
    });
    const snap = readTaskSnapshot(reader);
    expect(snap?.askUser?.id).toBe("ask-plan-01");
    expect(snap?.askUser?.options).toHaveLength(2);
    expect(snap?.askUser?.options[0].recommended).toBe(true);
  });

  it("tolerates malformed runtime-state json", () => {
    const reader = fileReaderFrom({ "runtime-state.json": "{not json" });
    expect(readTaskSnapshot(reader)).toBeNull();
  });
});

describe("card builders", () => {
  it("askUserCard renders options", () => {
    const card = askUserCard({
      id: "x",
      question: "确认?",
      options: [{ id: "1", label: "同意", recommended: true }],
    });
    expect(card.kind).toBe("ask_user");
    expect(card.options?.[0].label).toBe("同意");
  });

  it("progressCard summarizes status/phase/iteration", () => {
    const card = progressCard({
      status: "evaluating",
      nextAction: "run_evaluator",
      phase: "evaluation",
      iteration: 4,
      askUser: null,
      finished: false,
    });
    expect(card.kind).toBe("progress");
    expect(card.body).toContain("evaluating");
    expect(card.body).toContain("evaluation");
    expect(card.body).toContain("4");
  });

  it("progressCard appends extra semantic lines", () => {
    const card = progressCard(
      {
        status: "evaluating",
        nextAction: "run_evaluator",
        phase: "evaluation",
        iteration: 1,
        askUser: null,
        finished: false,
      },
      ["编译/测试通过 ✅"],
    );
    expect(card.body).toContain("编译/测试通过 ✅");
  });

  it("gitdiffCard folds detail into a collapsible when files changed", () => {
    const card = gitdiffCard(" foo.ts | 3 +++", 1, "diff --git a/foo.ts");
    expect(card.kind).toBe("gitdiff");
    expect(card.body).toContain("1 个文件");
    expect(card.collapsible?.[0].content).toContain("diff --git");
  });

  it("gitdiffCard reports no change without a collapsible", () => {
    const card = gitdiffCard("", 0);
    expect(card.body).toContain("无代码改动");
    expect(card.collapsible).toBeUndefined();
  });

  it("noticeCard is a read-only notice", () => {
    const card = noticeCard("正在处理 delivery，稍后回应");
    expect(card.kind).toBe("notice");
    expect(card.options).toBeUndefined();
  });

  it("reportCard folds a change summary into a collapsible", () => {
    const card = reportCard("任务完成", "全部通过", "## 代码变更汇总\n foo.ts | 3");
    expect(card.kind).toBe("report");
    expect(card.body).toBe("全部通过");
    expect(card.collapsible?.[0].label).toBe("当前工作区变更");
  });
});
