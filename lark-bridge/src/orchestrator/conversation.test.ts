import { describe, expect, it } from "vitest";
import {
  buildConversationPrompt,
  conversationFromSlash,
  degradedConversation,
  diagnoseConversationFailure,
  parseConversationJson,
  type ConversationContext,
} from "./conversation.js";

const context: ConversationContext = {
  chatTaskCode: "lark_chat_t1",
  userText: "看看 task09 的状态",
  targetTaskCode: "task09",
  tasks: [{
    taskCode: "task09",
    shortCode: "t1",
    name: "登录页",
    createdAt: "2026-07-14T00:00:00",
  }],
  workspace: { root: "/repo", confirmed: true },
};

describe("ConversationResponse", () => {
  it("parses natural reply with no finite intent label", () => {
    const parsed = parseConversationJson(JSON.stringify({
      version: 1,
      reply: "可以，我们继续聊。",
      contextSummary: "用户在讨论登录页。",
      actions: [],
    }));
    expect(parsed?.reply).toBe("可以，我们继续聊。");
    expect(parsed).not.toHaveProperty("intent");
  });

  it("parses registered actions and rejects unknown capabilities", () => {
    const valid = parseConversationJson(JSON.stringify({
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "task.status",
        arguments: { taskCode: "task09" },
      }],
    }));
    expect(valid?.actions[0].capability).toBe("task.status");
    expect(parseConversationJson(JSON.stringify({
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{ capability: "shell.exec", arguments: { command: "rm -rf /" } }],
    }))).toBeNull();
  });

  it("parses bounded task inspection views", () => {
    const parsed = parseConversationJson(JSON.stringify({
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "task.inspect",
        arguments: { taskCode: "task09", view: "validation" },
      }],
    }));
    expect(parsed?.actions[0]).toMatchObject({
      capability: "task.inspect",
      arguments: { taskCode: "task09", view: "validation" },
    });
  });

  it("extracts a balanced JSON object from noisy output", () => {
    const parsed = parseConversationJson(
      'prefix\n{"version":1,"reply":"ok","contextSummary":"","actions":[]}\nsuffix',
    );
    expect(parsed?.reply).toBe("ok");
  });

  it("degrades malformed structured output to zero actions", () => {
    const parsed = degradedConversation('{"reply":"broken"');
    expect(parsed.actions).toEqual([]);
    expect(parsed.reply).toContain("无法可靠解析");
  });

  it("distinguishes malformed JSON from schema rejection", () => {
    expect(diagnoseConversationFailure('{"reply":')).toBe("parse_failure");
    expect(diagnoseConversationFailure(JSON.stringify({
      version: 1,
      reply: "",
      actions: [{ capability: "shell.exec", arguments: {} }],
    }))).toBe("schema_reject");
  });

  it("builds a prompt with live tasks and no legacy intent enum", () => {
    const prompt = buildConversationPrompt(context);
    expect(prompt).toContain("CodeMind Coding Assistant");
    expect(prompt).toContain("高度自动化");
    expect(prompt).toContain("可靠的工程同事");
    expect(prompt).toContain("task.status");
    expect(prompt).toContain("task09");
    expect(prompt).not.toContain("chat|develop|modify_task");
    expect(prompt).toContain("普通交流 actions=[]");
    expect(prompt).toContain("即使历史对话或实时上下文看起来已有答案也不能直接回答");
    expect(prompt).not.toContain("/repo");
    expect(prompt).toContain('"workspace":{"configured":true,"confirmed":true}');
  });

  it("tells the model not to expose internal protocol language to users", () => {
    const prompt = buildConversationPrompt(context);
    expect(prompt).toContain("不要自称分类器、路由器或 JSON 生成器");
    expect(prompt).toContain("Conversation Orchestrator 是内部实现名称");
    expect(prompt).toContain("绝不能作为对外身份");
    expect(prompt).toContain("reply 必须是可以直接发送给用户的最终文本");
    expect(prompt).toContain("不包含内部推理、协议解释或调试信息");
  });

  it("keeps explicit slash commands deterministic", () => {
    const create = conversationFromSlash("/ask 做登录页", null);
    expect(create?.actions[0].capability).toBe("task.create");
    const status = conversationFromSlash("/status", "task09");
    expect(status?.actions[0]).toMatchObject({
      capability: "task.status",
      arguments: { taskCode: "task09" },
    });
  });
});
