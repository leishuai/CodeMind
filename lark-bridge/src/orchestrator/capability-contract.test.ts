import { describe, expect, it } from "vitest";
import {
  CAPABILITY_CATALOG,
  capabilityPromptLines,
  conversationActionSchema,
  type CapabilityName,
} from "./capability-catalog.js";
import { CapabilityRegistry } from "./capability-registry.js";

const examples: Record<CapabilityName, unknown> = {
  "workspace.get": { capability: "workspace.get", arguments: {} },
  "workspace.changes": { capability: "workspace.changes", arguments: {} },
  "task.list": { capability: "task.list", arguments: {} },
  "task.select": { capability: "task.select", arguments: { taskCode: "task01" } },
  "task.status": { capability: "task.status", arguments: { taskCode: "task01" } },
  "task.inspect": {
    capability: "task.inspect",
    arguments: { taskCode: "task01", view: "overview" },
  },
  "task.create": {
    capability: "task.create",
    arguments: { requirementSummary: "实现登录页" },
  },
  "task.modify": {
    capability: "task.modify",
    arguments: { taskCode: "task01", instruction: "按钮改为蓝色" },
  },
  "task.resume": { capability: "task.resume", arguments: { taskCode: "task01" } },
  "task.answer": {
    capability: "task.answer",
    arguments: { taskCode: "task01", answer: "同意" },
  },
  "clarification.request": {
    capability: "clarification.request",
    arguments: { question: "请说明目标任务" },
  },
};

describe("capability contract completeness", () => {
  it("keeps catalog, schema examples, prompt and policies in one-to-one coverage", () => {
    const names = Object.keys(CAPABILITY_CATALOG).sort() as CapabilityName[];
    expect(Object.keys(examples).sort()).toEqual(names);
    const prompt = capabilityPromptLines().join("\n");
    const registry = new CapabilityRegistry();
    for (const name of names) {
      const parsed = conversationActionSchema.safeParse(examples[name]);
      expect(parsed.success, `${name} must parse`).toBe(true);
      expect(prompt, `${name} must be documented to the model`).toContain(`- ${name}:`);
      expect(registry.policyFor(name)).toEqual(CAPABILITY_CATALOG[name].policy);
    }
  });

  it("covers the complete stable Lark task lifecycle surface", () => {
    expect(Object.keys(CAPABILITY_CATALOG).sort()).toEqual([
      "clarification.request",
      "task.answer",
      "task.create",
      "task.inspect",
      "task.list",
      "task.modify",
      "task.resume",
      "task.select",
      "task.status",
      "workspace.changes",
      "workspace.get",
    ]);
  });
});
