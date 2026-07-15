import { describe, it, expect } from "vitest";
import { renderFeishuCard } from "./cards.js";

describe("renderFeishuCard", () => {
  it("renders body and header template by kind", () => {
    const card = renderFeishuCard({ kind: "report", title: "任务完成", body: "全部通过" });
    expect((card.header as any).template).toBe("green");
    expect((card.header as any).title.content).toBe("任务完成");
    const elements = card.elements as any[];
    expect(elements[0].text.content).toBe("全部通过");
  });

  it("renders interactive buttons with option/kind values", () => {
    const card = renderFeishuCard({
      kind: "ask_user",
      title: "确认",
      body: "选一个",
      options: [
        { id: "1", label: "同意", recommended: true },
        { id: "2", label: "拒绝" },
      ],
    });
    const action = (card.elements as any[]).find((e) => e.tag === "action");
    expect(action.actions).toHaveLength(2);
    expect(action.actions[0].type).toBe("primary");
    expect(action.actions[0].value).toEqual({ optionId: "1", cardKind: "ask_user" });
    expect(action.actions[1].type).toBe("default");
  });

  it("folds threadId into button values when provided", () => {
    const card = renderFeishuCard(
      {
        kind: "confirm",
        title: "确认",
        body: "开始？",
        options: [{ id: "confirm", label: "开始", recommended: true }],
      },
      "omt_9",
    );
    const action = (card.elements as any[]).find((e) => e.tag === "action");
    expect(action.actions[0].value).toEqual({
      optionId: "confirm",
      cardKind: "confirm",
      threadId: "omt_9",
    });
  });

  it("folds the anti-replay token into button values when present", () => {
    const card = renderFeishuCard(
      {
        kind: "confirm",
        title: "确认",
        body: "开始？",
        token: "nonce-abc",
        options: [{ id: "confirm", label: "开始", recommended: true }],
      },
      "omt_9",
    );
    const action = (card.elements as any[]).find((e) => e.tag === "action");
    expect(action.actions[0].value).toEqual({
      optionId: "confirm",
      cardKind: "confirm",
      threadId: "omt_9",
      token: "nonce-abc",
    });
  });

  it("renders collapsible sections", () => {
    const card = renderFeishuCard({
      kind: "gitdiff",
      title: "变更",
      body: "2 files changed",
      collapsible: [{ label: "详细 diff", content: "diff content" }],
    });
    const contents = (card.elements as any[]).map((e) => e.text?.content).filter(Boolean);
    expect(contents.some((c: string) => c.includes("详细 diff"))).toBe(true);
  });
});
