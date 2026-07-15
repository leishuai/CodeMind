import { describe, expect, it } from "vitest";
import { CapabilityRegistry } from "./capability-registry.js";
import type { ConversationResponse } from "./conversation.js";
import type { TaskRef } from "./session-map.js";

const task: TaskRef = {
  taskCode: "task09",
  shortCode: "t1",
  name: "登录页",
  createdAt: "2026-07-14T00:00:00",
};

function response(actions: ConversationResponse["actions"]): ConversationResponse {
  return { version: 1, reply: "", contextSummary: "", actions };
}

describe("CapabilityRegistry", () => {
  const registry = new CapabilityRegistry();

  it("accepts read actions bound to a conversation-owned task", () => {
    const result = registry.validate(response([{
      capability: "task.status",
      arguments: { taskCode: "task09" },
      confidence: 1,
      reason: "",
    }]), [task]);
    expect(result.ok).toBe(true);
  });

  it("rejects a cross-conversation task before execution", () => {
    const result = registry.validate(response([{
      capability: "task.resume",
      arguments: { taskCode: "other-task" },
      confidence: 1,
      reason: "",
    }]), [task]);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toContain("不属于当前会话");
  });

  it("rejects the entire plan when it contains multiple writes", () => {
    const result = registry.validate(response([
      {
        capability: "task.resume",
        arguments: { taskCode: "task09" },
        confidence: 1,
        reason: "",
      },
      {
        capability: "task.modify",
        arguments: { taskCode: "task09", instruction: "改蓝色" },
        confidence: 1,
        reason: "",
      },
    ]), [task]);
    expect(result.ok).toBe(false);
  });

  it("rejects mixing a write with read actions to prevent partial execution", () => {
    const result = registry.validate(response([
      {
        capability: "task.create",
        arguments: { requirementSummary: "实现 X" },
        confidence: 1,
        reason: "",
      },
      {
        capability: "workspace.get",
        arguments: {},
        confidence: 1,
        reason: "",
      },
    ]), [task]);
    expect(result.ok).toBe(false);
  });

  it("rejects plans over the action limit", () => {
    const action = {
      capability: "task.list" as const,
      arguments: {},
      confidence: 1,
      reason: "",
    };
    expect(registry.validate(response([action, action, action, action, action]), [task]).ok)
      .toBe(false);
  });
});
