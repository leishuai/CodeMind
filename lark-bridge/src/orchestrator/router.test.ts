import { describe, it, expect, beforeEach } from "vitest";
import { Router } from "./router.js";
import { SessionMap, createMemoryStore } from "./session-map.js";
import { Workspace } from "./workspace.js";
import type { Card, Channel, IncomingMessage } from "../channel/types.js";
import type { MessageAck } from "../channel/types.js";
import type { CodeMindCli, CliResult } from "./automind-cli.js";
import type { ConversationOrchestrator } from "./conversation-orchestrator.js";
import type {
  ConversationContext,
  ConversationResponse,
} from "./conversation.js";
import type { SnapshotReader } from "./task-artifacts.js";
import type { TaskSnapshot } from "./progress.js";

interface SentText {
  threadId: string;
  text: string;
}

class FakeChannel implements Channel {
  texts: SentText[] = [];
  cards: { threadId: string; card: Card }[] = [];
  acknowledgments: string[] = [];
  finishedAcknowledgments: MessageAck[] = [];
  failAcknowledgment = false;
  async start(): Promise<void> {}
  async send(threadId: string, text: string): Promise<void> {
    this.texts.push({ threadId, text });
  }
  async sendCard(threadId: string, card: Card): Promise<string> {
    this.cards.push({ threadId, card });
    return "msg-1";
  }
  async ackMessage(messageId: string): Promise<MessageAck | null> {
    this.acknowledgments.push(messageId);
    if (this.failAcknowledgment) throw new Error("reaction failed");
    return { messageId, token: `ack_${messageId}` };
  }
  async finishMessageAck(ack: MessageAck): Promise<void> {
    this.finishedAcknowledgments.push(ack);
  }
}

class FakeCli {
  calls: string[][] = [];
  askResult: CliResult = { code: 0, stdout: '{"task":"task09"}', stderr: "" };
  messageResult: CliResult = { code: 0, stdout: "codex> 你好呀", stderr: "" };
  backgroundResult: CliResult = { code: 0, stdout: '{"result":"started"}', stderr: "" };
  observeResult: CliResult = { code: 0, stdout: '{"result":"ok"}', stderr: "" };
  async ask(requirement: string, agent: string): Promise<CliResult> {
    this.calls.push(["ask", requirement, agent]);
    return this.askResult;
  }
  async scaffold(requirement: string): Promise<CliResult> {
    this.calls.push(["scaffold", requirement]);
    return this.askResult;
  }
  async chatCreate(taskCode: string): Promise<CliResult> {
    this.calls.push(["chatCreate", taskCode]);
    return { code: 0, stdout: '{"result":"ok"}', stderr: "" };
  }
  async message(taskCode: string, text: string, resume?: string): Promise<CliResult> {
    this.calls.push(["message", taskCode, text, resume ?? ""]);
    return this.messageResult;
  }
  async answerOption(taskCode: string, optionId: string): Promise<CliResult> {
    this.calls.push(["answerOption", taskCode, optionId]);
    return { code: 0, stdout: "", stderr: "" };
  }
  async answerText(taskCode: string, text: string): Promise<CliResult> {
    this.calls.push(["answerText", taskCode, text]);
    return { code: 0, stdout: "", stderr: "" };
  }
  async resume(taskCode: string, agent: string): Promise<CliResult> {
    this.calls.push(["resume", taskCode, agent]);
    return { code: 0, stdout: "", stderr: "" };
  }
  async resumeInBackground(taskCode: string, agent: string): Promise<CliResult> {
    this.calls.push(["resumeInBackground", taskCode, agent]);
    return this.backgroundResult;
  }
  async status(taskCode: string): Promise<CliResult> {
    this.calls.push(["status", taskCode]);
    return { code: 0, stdout: "", stderr: "" };
  }
  async observe(taskCode: string, observation: unknown): Promise<CliResult> {
    this.calls.push(["observe", taskCode, JSON.stringify(observation)]);
    return this.observeResult;
  }
}

interface LegacyVerdict {
  intent?: "chat" | "develop" | "modify_task" | "query" | "answer" | "resume" | "status" | "clarify";
  needsClarification?: boolean;
  clarifyQuestion?: string | null;
  rewrittenInstruction?: string | null;
  requirementSummary?: string | null;
  assistantReply?: string | null;
}

class FakeOrchestrator implements ConversationOrchestrator {
  calls: ConversationContext[] = [];

  constructor(private verdict: LegacyVerdict) {}

  async interpret(context: ConversationContext): Promise<ConversationResponse> {
    this.calls.push(context);
    const base = { version: 1 as const, reply: "", contextSummary: "", actions: [] };
    if (this.verdict.needsClarification || this.verdict.intent === "clarify") {
      return {
        ...base,
        actions: [{
          capability: "clarification.request",
          arguments: { question: this.verdict.clarifyQuestion ?? "请补充信息。" },
          confidence: 1,
          reason: "",
        }],
      };
    }
    switch (this.verdict.intent) {
      case "develop":
        return {
          ...base,
          actions: [{
            capability: "task.create",
            arguments: { requirementSummary: this.verdict.requirementSummary ?? context.userText },
            confidence: 1,
            reason: "",
          }],
        };
      case "modify_task":
        return context.targetTaskCode ? {
          ...base,
          actions: [{
            capability: "task.modify",
            arguments: {
              taskCode: context.targetTaskCode,
              instruction: this.verdict.rewrittenInstruction ?? context.userText,
            },
            confidence: 1,
            reason: "",
          }],
        } : { ...base, reply: "当前没有进行中的任务可修改。" };
      case "query":
      case "status":
        return context.targetTaskCode ? {
          ...base,
          actions: [{
            capability: "task.status",
            arguments: { taskCode: context.targetTaskCode },
            confidence: 1,
            reason: "",
          }],
        } : { ...base, reply: "当前没有进行中的任务。" };
      case "resume":
        return context.targetTaskCode ? {
          ...base,
          actions: [{
            capability: "task.resume",
            arguments: { taskCode: context.targetTaskCode },
            confidence: 1,
            reason: "",
          }],
        } : { ...base, reply: "当前没有可恢复的任务。" };
      case "answer":
        return context.targetTaskCode ? {
          ...base,
          actions: [{
            capability: "task.answer",
            arguments: { taskCode: context.targetTaskCode, answer: context.userText },
            confidence: 1,
            reason: "",
          }],
        } : { ...base, reply: "当前没有待回答的问题。" };
      case "chat":
      default:
        return { ...base, reply: this.verdict.assistantReply ?? "你好呀" };
    }
  }

  async respondToResults(
    _context: ConversationContext,
    results: import("./conversation.js").CapabilityExecutionResult[],
  ): Promise<ConversationResponse> {
    return {
      version: 1,
      reply: results.map((result) => result.message).join("\n"),
      contextSummary: "",
      actions: [],
    };
  }
}

function verdict(partial: LegacyVerdict): LegacyVerdict {
  return {
    intent: "chat",
    needsClarification: false,
    clarifyQuestion: null,
    rewrittenInstruction: null,
    requirementSummary: null,
    assistantReply: null,
    ...partial,
  };
}

const activeSnapshot: TaskSnapshot = {
  status: "generating",
  nextAction: "run_generator",
  phase: "delivery",
  iteration: 1,
  askUser: null,
  finished: false,
};

function fakeSnapshotReader(snap: TaskSnapshot | null = activeSnapshot): SnapshotReader {
  return {
    read: () => snap,
    readEvents: () => null,
    readArtifact: () => null,
    readLatestIterationArtifact: () => null,
  };
}

function buildRouter(
  v: LegacyVerdict,
  opts: { allowedUsers?: string[]; snapshot?: TaskSnapshot | null } = {},
) {
  const channel = new FakeChannel();
  const cli = new FakeCli();
  const sessionMap = new SessionMap(createMemoryStore());
  const orchestrator = new FakeOrchestrator(v);
  const snapshot = "snapshot" in opts ? opts.snapshot ?? null : activeSnapshot;
  const router = new Router({
    channel,
    cli: cli as unknown as CodeMindCli,
    orchestrator,
    sessionMap,
    snapshotReader: fakeSnapshotReader(snapshot),
    agent: "auto",
    allowedUsers: opts.allowedUsers ?? [],
    nonce: () => "nonce-1",
  });
  return { router, channel, cli, sessionMap, orchestrator };
}

function msg(partial: Partial<IncomingMessage>): IncomingMessage {
  return {
    channelId: "c1",
    threadId: "t1",
    userId: "u1",
    text: "hi",
    isSlashCommand: false,
    messageId: "",
    ...partial,
  };
}

describe("Router.onMessage", () => {
  it("rejects users outside the allow-list", async () => {
    const { router, channel, cli } = buildRouter(verdict({}), { allowedUsers: ["ou_ok"] });
    await router.onMessage(msg({ userId: "ou_bad" }));
    expect(channel.texts[0].text).toContain("没有权限");
    expect(cli.calls).toHaveLength(0);
  });

  it("drops a duplicate message by messageId (at-least-once delivery)", async () => {
    const { router, orchestrator, cli, channel } = buildRouter(verdict({ intent: "chat" }));
    await router.onMessage(msg({ threadId: "t1", text: "在吗", messageId: "om_1" }));
    await router.onMessage(msg({ threadId: "t1", text: "在吗", messageId: "om_1" }));
    expect(orchestrator.calls).toHaveLength(1);
    expect(channel.acknowledgments).toEqual(["om_1"]);
    expect(channel.finishedAcknowledgments).toEqual([
      { messageId: "om_1", token: "ack_om_1" },
    ]);
    const observations = cli.calls.filter((call) => call[0] === "observe");
    expect(observations).toHaveLength(2);
    expect(observations.some((call) =>
      call[2].includes("capability_duplicate_suppressed_count"))).toBe(true);
  });

  it("acknowledges receipt before invoking the conversation model", async () => {
    const { router, channel, orchestrator } = buildRouter(verdict({ intent: "chat" }));
    await router.onMessage(msg({ text: "在吗", messageId: "om_ack" }));
    expect(channel.acknowledgments).toEqual(["om_ack"]);
    expect(channel.finishedAcknowledgments[0]).toEqual({
      messageId: "om_ack",
      token: "ack_om_ack",
    });
    expect(orchestrator.calls).toHaveLength(1);
  });

  it("does not acknowledge a message from an unauthorized user", async () => {
    const { router, channel } = buildRouter(verdict({}), {
      allowedUsers: ["ou_ok"],
    });
    await router.onMessage(msg({ userId: "ou_bad", messageId: "om_denied" }));
    expect(channel.acknowledgments).toEqual([]);
  });

  it("continues processing when the receipt acknowledgment fails", async () => {
    const { router, channel } = buildRouter(verdict({ intent: "chat" }));
    channel.failAcknowledgment = true;
    await expect(router.onMessage(
      msg({ text: "在吗", messageId: "om_ack_fail" }),
    )).resolves.toBeUndefined();
    expect(channel.texts.at(-1)?.text).toBe("你好呀");
    expect(channel.finishedAcknowledgments).toEqual([]);
  });

  it("removes typing without done when message processing fails", async () => {
    const { router, channel } = buildRouter(verdict({ intent: "chat" }));
    channel.send = async () => {
      throw new Error("send failed");
    };
    await expect(router.onMessage(
      msg({ text: "在吗", messageId: "om_fail" }),
    )).rejects.toThrow("send failed");
    expect(channel.finishedAcknowledgments).toEqual([
      { messageId: "om_fail", token: "ack_om_fail" },
    ]);
  });

  it("chat intent replies with the extracted agent reply", async () => {
    const { router, channel, cli } = buildRouter(verdict({ intent: "chat" }));
    await router.onMessage(msg({ text: "在吗" }));
    expect(cli.calls.some((c) => c[0] === "message")).toBe(false);
    expect(channel.texts[0].text).toBe("你好呀");
    const observation = cli.calls.find((call) => call[0] === "observe");
    expect(observation?.[2]).toContain("capability_no_action_count");
    expect(observation?.[2]).not.toContain("在吗");
  });

  it("chat with a carried assistantReply answers in one call (no second model call)", async () => {
    const { router, channel, cli } = buildRouter(
      verdict({ intent: "chat", assistantReply: "可以，你想了解哪一部分？" }),
    );
    await router.onMessage(msg({ text: "介绍一下" }));
    // The reply comes straight from the classifier verdict; the router must NOT
    // make a second `message` call to generate a chat reply.
    expect(cli.calls.some((c) => c[0] === "message")).toBe(false);
    expect(channel.texts[0].text).toBe("可以，你想了解哪一部分？");
  });

  it("ensures the chat task exists before routing (chat-create once)", async () => {
    const { router, cli } = buildRouter(verdict({ intent: "chat" }));
    await router.onMessage(msg({ threadId: "t1", text: "一" }));
    await router.onMessage(msg({ threadId: "t1", text: "二" }));
    const creates = cli.calls.filter((c) => c[0] === "chatCreate");
    expect(creates).toHaveLength(1);
  });

  it("develop intent posts a confirm card and holds it pending", async () => {
    const { router, channel, cli } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现登录页" }),
    );
    await router.onMessage(msg({ text: "做登录" }));
    expect(channel.cards[0].card.kind).toBe("confirm");
    expect(channel.cards[0].card.body).toBe("实现登录页");
    // No task started until the user confirms.
    expect(cli.calls.find((c) => c[0] === "ask")).toBeUndefined();
  });

  it("modify_task without an active task tells the user", async () => {
    const { router, channel } = buildRouter(
      verdict({ intent: "modify_task", rewrittenInstruction: "改深色" }),
    );
    await router.onMessage(msg({}));
    expect(channel.texts[0].text).toContain("没有进行中的任务");
  });

  it("needsClarification posts a clarify card", async () => {
    const { router, channel } = buildRouter(
      verdict({ needsClarification: true, clarifyQuestion: "你指哪个任务?" }),
    );
    await router.onMessage(msg({}));
    expect(channel.cards[0].card.kind).toBe("clarify");
    expect(channel.cards[0].card.body).toBe("你指哪个任务?");
  });

  it("query with no active task reports no task", async () => {
    const { router, channel } = buildRouter(verdict({ intent: "query" }));
    await router.onMessage(msg({}));
    expect(channel.texts[0].text).toContain("没有进行中的任务");
  });

  it("query on a finished task sends a report card with change summary", async () => {
    const finishedSnapshot: TaskSnapshot = {
      status: "finished",
      nextAction: "finish",
      phase: "finished",
      iteration: 3,
      askUser: null,
      finished: true,
    };
    const channel = new FakeChannel();
    const cli = new FakeCli();
    const sessionMap = new SessionMap(createMemoryStore());
    const router = new Router({
      channel,
      cli: cli as unknown as CodeMindCli,
      orchestrator: new FakeOrchestrator(verdict({ intent: "query" })),
      sessionMap,
      snapshotReader: fakeSnapshotReader(finishedSnapshot),
      agent: "auto",
      allowedUsers: [],
      nonce: () => "nonce-1",
      gitDiff: async () => ({ stat: " foo.ts | 3 +++", detail: "diff", filesChanged: 1 }),
    });
    sessionMap.ensure("t1");
    sessionMap.setActiveTask("t1", "task09");
    await router.onMessage(msg({ threadId: "t1" }));
    const card = channel.cards.at(-1)?.card;
    expect(card?.kind).toBe("report");
    // Flat message stream (§6.3): the report card title carries the task label.
    expect(card?.title).toContain("任务完成");
    expect(card?.title).toContain("#t1");
    expect(card?.collapsible?.[0].label).toBe("当前工作区变更");
  });
});

describe("Router.onCardAction", () => {
  it("confirm=confirm on a handoff starts the task and binds it", async () => {
    const { router, channel, cli, sessionMap } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现 X" }),
    );
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    expect(cli.calls.find((c) => c[0] === "scaffold")).toBeDefined();
    expect(cli.calls.find((c) => c[0] === "resumeInBackground")).toBeDefined();
    expect(sessionMap.get("t1")?.activeTaskCode).toBe("task09");
    expect(channel.texts.some((t) => t.text.includes("task09"))).toBe(true);
  });

  it("confirm=cancel discards the pending handoff", async () => {
    const { router, channel, cli } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现 X" }),
    );
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "cancel", "confirm", "nonce-1");
    expect(cli.calls.find((c) => c[0] === "ask")).toBeUndefined();
    expect(channel.texts.some((t) => t.text.includes("取消"))).toBe(true);
  });

  it("rejects a confirm tap whose token no longer matches (stale/replayed)", async () => {
    const { router, channel, cli } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现 X" }),
    );
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "confirm", "confirm", "stale-token");
    expect(cli.calls.find((c) => c[0] === "ask")).toBeUndefined();
    expect(channel.texts.some((t) => t.text.includes("已失效"))).toBe(true);
  });

  it("ask_user card action submits the chosen option when the token matches", async () => {
    const askSnapshot: TaskSnapshot = {
      ...activeSnapshot,
      askUser: { id: "ask-123", question: "选哪个?", options: [] },
    };
    const { router, cli, sessionMap } = buildRouter(verdict({ intent: "chat" }), {
      snapshot: askSnapshot,
    });
    sessionMap.ensure("t1");
    sessionMap.setActiveTask("t1", "task09");
    await router.onCardAction("t1", "2", "ask_user", "ask-123");
    expect(cli.calls).toContainEqual(["answerOption", "task09", "2"]);
  });

  it("rejects an ask_user tap whose token is not the current questionId", async () => {
    const askSnapshot: TaskSnapshot = {
      ...activeSnapshot,
      askUser: { id: "ask-123", question: "选哪个?", options: [] },
    };
    const { router, channel, cli, sessionMap } = buildRouter(verdict({ intent: "chat" }), {
      snapshot: askSnapshot,
    });
    sessionMap.ensure("t1");
    sessionMap.setActiveTask("t1", "task09");
    await router.onCardAction("t1", "2", "ask_user", "ask-OLD");
    expect(cli.calls.find((c) => c[0] === "answerOption")).toBeUndefined();
    expect(channel.texts.some((t) => t.text.includes("已失效"))).toBe(true);
  });

  it("read-only card taps are ignored", async () => {
    const { router, cli } = buildRouter(verdict({ intent: "chat" }));
    await router.onCardAction("t1", "x", "progress", "");
    expect(cli.calls).toHaveLength(0);
  });

  it("rejects a card tap from a user outside the allow-list", async () => {
    const { router, channel, cli } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "X" }),
      { allowedUsers: ["ou_ok"] },
    );
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1", "ou_bad");
    expect(channel.texts.some((t) => t.text.includes("没有权限"))).toBe(true);
    expect(cli.calls.find((c) => c[0] === "ask")).toBeUndefined();
  });

  it("drops a duplicate card tap (idempotent, at-least-once delivery)", async () => {
    const { router, cli } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现 X" }),
    );
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    const asksAfterFirst = cli.calls.filter((c) => c[0] === "ask").length;
    // A redelivered identical tap must not start the task a second time.
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    expect(cli.calls.filter((c) => c[0] === "ask").length).toBe(asksAfterFirst);
  });

  it("does not duplicate a created task when its background start failed", async () => {
    const { router, channel, cli, sessionMap } = buildRouter(
      verdict({ intent: "develop", requirementSummary: "实现 X" }),
    );
    cli.backgroundResult = { code: 1, stdout: "", stderr: "spawn failed" };
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    expect(sessionMap.get("t1")?.activeTaskCode).toBe("task09");
    expect(channel.texts.at(-1)?.text).toContain("任务已创建");
    const scaffoldCount = cli.calls.filter((call) => call[0] === "scaffold").length;
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    expect(cli.calls.filter((call) => call[0] === "scaffold")).toHaveLength(
      scaffoldCount,
    );
  });

  it("confirming a modify_task on an active loop sends a busy notice card", async () => {
    // activeSnapshot.status = "generating" -> loop active -> queued, no resume.
    const { router, channel, cli, sessionMap } = buildRouter(
      verdict({ intent: "modify_task", rewrittenInstruction: "改深色" }),
    );
    sessionMap.ensure("t1");
    sessionMap.setActiveTask("t1", "task09");
    await router.onMessage(msg({ threadId: "t1" }));
    await router.onCardAction("t1", "confirm", "confirm", "nonce-1");
    // message queued, no resume triggered while the loop is active.
    expect(cli.calls.some((c) => c[0] === "message")).toBe(true);
    expect(cli.calls.find((c) => c[0] === "resume")).toBeUndefined();
    const card = channel.cards.at(-1)?.card;
    expect(card?.kind).toBe("notice");
    expect(card?.body).toContain("已收到");
  });
});

describe("Router workspace (project dir) flow", () => {
  function buildWithWorkspace(v: LegacyVerdict, workspace: Workspace) {
    const channel = new FakeChannel();
    const cli = new FakeCli();
    const sessionMap = new SessionMap(createMemoryStore());
    const router = new Router({
      channel,
      cli: cli as unknown as CodeMindCli,
      orchestrator: new FakeOrchestrator(v),
      sessionMap,
      snapshotReader: fakeSnapshotReader(activeSnapshot),
      agent: "auto",
      allowedUsers: [],
      nonce: () => "nonce-1",
      workspace,
      resolveWorkspacePath: (raw) => (raw === "/good/proj" ? "/good/proj" : null),
    });
    return { router, channel, cli, sessionMap };
  }

  it("asks for the project dir when develop is attempted without a confirmed workspace", async () => {
    const ws = new Workspace({ root: "/home/u", confirmed: false });
    const { router, channel, cli } = buildWithWorkspace(
      verdict({ intent: "develop", requirementSummary: "实现登录页" }),
      ws,
    );
    await router.onMessage(msg({ text: "做登录" }));
    // No confirm card, no task started; instead a prompt to set the dir.
    expect(channel.cards.find((c) => c.card.kind === "confirm")).toBeUndefined();
    expect(channel.texts.some((t) => t.text.includes("#dir"))).toBe(true);
    expect(cli.calls.find((c) => c[0] === "ask")).toBeUndefined();
  });

  it("#dir <path> confirms the workspace and later develop proceeds to a confirm card", async () => {
    const ws = new Workspace({ root: "/home/u", confirmed: false });
    const { router, channel } = buildWithWorkspace(
      verdict({ intent: "develop", requirementSummary: "实现登录页" }),
      ws,
    );
    await router.onMessage(msg({ text: "#dir /good/proj" }));
    expect(ws.isConfirmed()).toBe(true);
    expect(ws.getRoot()).toBe("/good/proj");
    expect(channel.texts.some((t) => t.text.includes("已将工程目录设为"))).toBe(true);
    // Now a develop intent should reach the confirm card.
    await router.onMessage(msg({ text: "做登录" }));
    expect(channel.cards.some((c) => c.card.kind === "confirm")).toBe(true);
  });

  it("accepts the CJK aliases #工程 / #项目 / #目录 (JS \\b fails after CJK)", async () => {
    for (const alias of ["#工程", "#项目", "#目录"]) {
      const ws = new Workspace({ root: "/home/u", confirmed: false });
      const { router } = buildWithWorkspace(verdict({ intent: "chat" }), ws);
      await router.onMessage(msg({ text: `${alias} /good/proj` }));
      expect(ws.isConfirmed()).toBe(true);
      expect(ws.getRoot()).toBe("/good/proj");
    }
  });

  it("shows the set-dir help when a bare #工程 is sent with no path", async () => {
    const ws = new Workspace({ root: "/home/u", confirmed: false });
    const { router, channel } = buildWithWorkspace(verdict({ intent: "chat" }), ws);
    await router.onMessage(msg({ text: "#工程" }));
    expect(ws.isConfirmed()).toBe(false);
    expect(channel.texts.some((t) => t.text.includes("#dir"))).toBe(true);
  });

  it("#dir with an invalid path is rejected and stays unconfirmed", async () => {
    const ws = new Workspace({ root: "/home/u", confirmed: false });
    const { router, channel } = buildWithWorkspace(verdict({ intent: "chat" }), ws);
    await router.onMessage(msg({ text: "#dir /nope" }));
    expect(ws.isConfirmed()).toBe(false);
    expect(channel.texts.some((t) => t.text.includes("路径无效或不存在"))).toBe(true);
  });

  it("develop proceeds when the workspace is already confirmed", async () => {
    const ws = new Workspace({ root: "/good/proj", confirmed: true });
    const { router, channel } = buildWithWorkspace(
      verdict({ intent: "develop", requirementSummary: "实现登录页" }),
      ws,
    );
    await router.onMessage(msg({ text: "做登录" }));
    expect(channel.cards.some((c) => c.card.kind === "confirm")).toBe(true);
  });
});

describe("Router capability execution", () => {
  function buildWithResponse(
    response: ConversationResponse,
    options: {
      artifacts?: Record<string, string>;
      latest?: { path: string; content: string } | null;
    } = {},
  ) {
    const channel = new FakeChannel();
    const cli = new FakeCli();
    const sessionMap = new SessionMap(createMemoryStore());
    const orchestrator: ConversationOrchestrator = {
      interpret: async () => response,
      respondToResults: async (_context, results) => ({
        version: 1,
        reply: results.map((result) => result.message).join("\n"),
        contextSummary: "",
        actions: [],
      }),
    };
    const router = new Router({
      channel,
      cli: cli as unknown as CodeMindCli,
      orchestrator,
      sessionMap,
      snapshotReader: {
        read: () => activeSnapshot,
        readEvents: () => null,
        readArtifact: (_taskCode, relativePath) =>
          options.artifacts?.[relativePath] ?? null,
        readLatestIterationArtifact: () => options.latest ?? null,
      },
      agent: "auto",
      allowedUsers: [],
      nonce: () => "nonce-1",
      workspace: new Workspace({ root: "/good/proj", confirmed: true }),
    });
    return { router, channel, cli, sessionMap };
  }

  it("executes task.list and task.select through registered capabilities", async () => {
    const listResponse: ConversationResponse = {
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "task.list",
        arguments: {},
        confidence: 1,
        reason: "",
      }],
    };
    const built = buildWithResponse(listResponse);
    built.sessionMap.addTask("t1", "task09", "登录页");
    await built.router.onMessage(msg({ threadId: "t1", text: "有哪些任务" }));
    expect(built.channel.texts.at(-1)?.text).toContain("task09");

    const selectResponse: ConversationResponse = {
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "task.select",
        arguments: { taskCode: "task09" },
        confidence: 1,
        reason: "",
      }],
    };
    const selected = buildWithResponse(selectResponse);
    selected.sessionMap.addTask("t1", "task09", "登录页");
    await selected.router.onMessage(msg({ threadId: "t1", text: "切到登录任务" }));
    expect(selected.sessionMap.get("t1")?.activeTaskCode).toBe("task09");
  });

  it("rejects a cross-conversation task plan before any CLI side effect", async () => {
    const response: ConversationResponse = {
      version: 1,
      reply: "我来恢复它。",
      contextSummary: "",
      actions: [{
        capability: "task.resume",
        arguments: { taskCode: "other-task" },
        confidence: 1,
        reason: "",
      }],
    };
    const { router, channel, cli, sessionMap } = buildWithResponse(response);
    sessionMap.addTask("t1", "task09", "登录页");
    await router.onMessage(msg({ threadId: "t1", text: "恢复另一个任务" }));
    expect(cli.calls.find((call) => call[0] === "resume")).toBeUndefined();
    expect(channel.texts.at(-1)?.text).toContain("没有执行");
    expect(cli.calls.find((call) => call[0] === "observe")?.[2]).toContain(
      "capability_policy_reject_count",
    );
  });

  it("does not fail or repeat the turn when observe returns an error", async () => {
    const { router, channel, cli } = buildWithResponse({
      version: 1,
      reply: "你好",
      contextSummary: "",
      actions: [],
    });
    cli.observeResult = { code: 1, stdout: "", stderr: "observe failed" };
    await router.onMessage(msg({ threadId: "t1", text: "你好", messageId: "om_obs" }));
    expect(channel.texts).toHaveLength(1);
    expect(channel.texts[0].text).toBe("你好");
  });

  it("keeps workspace mutation outside the model capability surface", async () => {
    const response: ConversationResponse = {
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "workspace.get",
        arguments: {},
        confidence: 1,
        reason: "",
      }],
    };
    const { router, channel } = buildWithResponse(response);
    await router.onMessage(msg({ threadId: "t1", text: "工作目录是什么" }));
    expect(channel.texts.at(-1)?.text).toBe("当前工程目录：/good/proj");
  });

  it("feeds task.inspect artifacts back through the orchestrator response loop", async () => {
    const response: ConversationResponse = {
      version: 1,
      reply: "我先读取验证结果。",
      contextSummary: "",
      actions: [{
        capability: "task.inspect",
        arguments: { taskCode: "task09", view: "validation" },
        confidence: 1,
        reason: "",
      }],
    };
    const built = buildWithResponse(response, {
      artifacts: {
        "Validation.md": "# Validation\nPASS",
        "evaluation.json": '{"result":"pass","nextAction":"finish"}',
      },
    });
    built.sessionMap.addTask("t1", "task09", "登录页");
    await built.router.onMessage(msg({ threadId: "t1", text: "测试通过了吗" }));
    expect(built.channel.texts.at(-1)?.text).toContain("已读取任务 validation 产物");
  });

  it("reads only the latest bounded log digest for task.inspect logs", async () => {
    const response: ConversationResponse = {
      version: 1,
      reply: "",
      contextSummary: "",
      actions: [{
        capability: "task.inspect",
        arguments: { taskCode: "task09", view: "logs" },
        confidence: 1,
        reason: "",
      }],
    };
    const built = buildWithResponse(response, {
      latest: { path: "logs/iter-3/log-digest.md", content: "latest failure summary" },
    });
    built.sessionMap.addTask("t1", "task09", "登录页");
    await built.router.onMessage(msg({ threadId: "t1", text: "最近日志是什么" }));
    expect(built.channel.texts.at(-1)?.text).toContain("已读取最新日志摘要");
  });
});
