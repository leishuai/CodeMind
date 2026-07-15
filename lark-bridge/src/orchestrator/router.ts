/**
 * router.ts (design §6.2 / §6.6 / §7) — channel-neutral message router. Ties
 * the orchestrator pieces together:
 *   incoming message -> allow-list -> persistent conversation orchestrator
 *   -> reply + validated capability actions -> deterministic effects/cards
 *   card action -> execute pending handoff|inject / answer ask_user
 *
 * Depends only on generic interfaces (Channel, ConversationOrchestrator, SnapshotReader,
 * CodeMindCli), so it is fully unit-testable with fakes and never touches Feishu
 * or the CodeMind core directly.
 */
import type {
  Card,
  Channel,
  IncomingMessage,
  MessageAck,
} from "../channel/types.js";
import type { CodeMindCli } from "./automind-cli.js";
import type { ConversationOrchestrator } from "./conversation-orchestrator.js";
import type {
  CapabilityExecutionResult,
  ConversationAction,
  ConversationContext,
} from "./conversation.js";
import { CapabilityRegistry } from "./capability-registry.js";
import { CAPABILITY_CATALOG } from "./capability-catalog.js";
import type { PendingConfirmation, SessionMap } from "./session-map.js";
import type { SnapshotReader } from "./task-artifacts.js";
import type { Workspace } from "./workspace.js";
import { startTaskFromSummary } from "./handoff.js";
import { injectInstruction } from "./inject.js";
import {
  progressCard,
  askUserCard,
  reportCard,
  noticeCard,
  formatTaskLabel,
  withTaskLabel,
  labelText,
} from "./progress.js";
import { progressLinesFromEvents } from "./task-events.js";
import { formatReportSection, type GitDiffSummary } from "./gitdiff.js";
import { Deduper } from "./dedupe.js";
import { randomUUID } from "node:crypto";
import {
  buildCardObservation,
  buildDuplicateObservation,
  buildTurnObservation,
  type TurnObservation,
} from "./turn-observation.js";

/** A confirmation the router is waiting for the user to approve/cancel. */
export type PendingConfirm = PendingConfirmation;

export interface RouterDeps {
  channel: Channel;
  cli: CodeMindCli;
  orchestrator: ConversationOrchestrator;
  capabilities?: CapabilityRegistry;
  sessionMap: SessionMap;
  snapshotReader: SnapshotReader;
  agent: string;
  /** Feishu open_id allow-list; empty = allow all. */
  allowedUsers: string[];
  /** Anti-replay nonce generator; injectable for deterministic tests. */
  nonce?: () => string;
  /**
   * Collect the workspace git-diff summary for the final report (§7.2).
   * Injectable/optional; when absent the final report omits the change summary.
   */
  gitDiff?: (taskCode: string) => Promise<GitDiffSummary>;
  /**
   * Idempotency guard for at-least-once event delivery (§13.5 / M5).
   * Injectable so tests can assert dedupe behavior deterministically.
   */
  deduper?: Deduper;
  /**
   * The project the daemon works on. `automind start` is a generic launcher
   * with no project path, so this may be unconfirmed at first: the router then
   * proactively asks the user which project to use, and a `#dir <path>` message
   * confirms it at runtime. When omitted the workspace is treated as confirmed
   * (used by tests that don't exercise the project-dir flow).
   */
  workspace?: Workspace;
  /**
   * Validate + resolve a user-supplied project path to an absolute root, or
   * null if it is not a usable directory. Injectable for deterministic tests;
   * defaults in main.ts to a real filesystem check.
   */
  resolveWorkspacePath?: (raw: string) => string | null;
}

const CONFIRM_OPTIONS = [
  { id: "confirm", label: "就按这个开始", recommended: true },
  { id: "cancel", label: "取消" },
];

export class Router {
  private readonly deps: RouterDeps;
  /** Chat task codes already ensured via chat-create (avoid repeat spawns). */
  private readonly ensuredChatTasks = new Set<string>();
  private readonly nonce: () => string;
  private readonly deduper: Deduper;
  private readonly capabilities: CapabilityRegistry;

  constructor(deps: RouterDeps) {
    this.deps = deps;
    this.nonce = deps.nonce ?? (() => randomUUID());
    this.deduper = deps.deduper ?? new Deduper();
    this.capabilities = deps.capabilities ?? new CapabilityRegistry();
  }

  private isAllowed(userId: string): boolean {
    const list = this.deps.allowedUsers;
    return list.length === 0 || list.includes(userId);
  }

  /**
   * Whether the daemon is pointed at a user-confirmed project dir. When no
   * workspace is injected (tests), treat it as confirmed so the project-dir
   * flow does not interfere with unrelated router tests.
   */
  private workspaceConfirmed(): boolean {
    return this.deps.workspace ? this.deps.workspace.isConfirmed() : true;
  }

  /** Prompt asking the user to point CodeMind at a project directory. */
  private askForWorkspaceText(): string {
    const current = this.deps.workspace?.getRoot() ?? "";
    const hint = current
      ? `\n当前默认目录（兜底）：${current}`
      : "";
    return (
      "还没确定要处理的工程目录。请先用 `#dir <项目路径>` 指定，例如：\n" +
      "    #dir ~/projects/my-app\n" +
      "指定后我就能在该工程里起任务、分析代码。" +
      hint
    );
  }

  /** Confirm the project dir the user pointed at via `#dir <path>` (§start). */
  private async handleSetWorkspace(threadId: string, raw: string): Promise<void> {
    if (!raw) {
      await this.deps.channel.send(threadId, this.askForWorkspaceText());
      return;
    }
    if (!this.deps.workspace) {
      await this.deps.channel.send(threadId, "当前未启用工程目录切换。");
      return;
    }
    const resolve = this.deps.resolveWorkspacePath ?? ((p: string) => p);
    const resolved = resolve(raw);
    if (!resolved) {
      await this.deps.channel.send(threadId, `路径无效或不存在：${raw}`);
      return;
    }
    this.deps.workspace.confirm(resolved);
    await this.deps.channel.send(threadId, `已将工程目录设为：${resolved}\n现在可以直接说需求起任务了。`);
  }

  /**
   * Flat message-stream task label (§6.3): `[#<short> · <name> · <code>]` for a
   * task-scoped card/message, or "" when the code is unknown to this thread.
   */
  private taskLabel(threadId: string, taskCode: string | null): string {
    if (!taskCode) return "";
    const ref = this.deps.sessionMap.findTask(threadId, taskCode);
    return ref ? formatTaskLabel(ref) : "";
  }

  /**
   * Ensure the resident S_chat task exists before any `converse` call runs.
   * The core rejects session commands on a missing task dir, so the first message per
   * thread must create the chat shell. Idempotent + cached per task code.
   */
  private async ensureChatTask(chatTaskCode: string): Promise<void> {
    if (this.ensuredChatTasks.has(chatTaskCode)) return;
    await this.deps.cli.chatCreate(chatTaskCode);
    this.ensuredChatTasks.add(chatTaskCode);
  }

  /** Handle an inbound chat message. */
  async onMessage(message: IncomingMessage): Promise<void> {
    if (!this.isAllowed(message.userId)) {
      await this.deps.channel.send(message.threadId, "抱歉，你没有权限驱动 CodeMind。");
      return;
    }
    // Drop at-least-once duplicates (§13.5): the same message_id can be
    // redelivered on retries/reconnects; process it exactly once.
    const dedupeKey = message.messageId ? `msg:${message.messageId}` : "";
    if (dedupeKey && this.deduper.isDuplicate(dedupeKey)) {
      const chatTaskCode = this.deps.sessionMap.chatTaskCodeFor(message.threadId);
      await this.observeBestEffort(chatTaskCode, buildDuplicateObservation());
      return;
    }
    let ack: MessageAck | null = null;
    if (message.messageId) {
      try {
        ack = await this.deps.channel.ackMessage?.(message.messageId) ?? null;
      } catch {
        // A receipt reaction is a UX hint, never a processing prerequisite.
      }
    }
    const transaction = { sideEffectCommitted: false };
    try {
      await this.processMessage(message, transaction);
      if (ack) {
        await this.deps.channel.finishMessageAck?.(ack);
      }
    } catch (error) {
      if (ack) {
        await this.deps.channel.finishMessageAck?.(ack);
      }
      if (dedupeKey && !transaction.sideEffectCommitted) {
        this.deduper.forget(dedupeKey);
      }
      throw error;
    }
  }

  private async processMessage(
    message: IncomingMessage,
    transaction: { sideEffectCommitted: boolean },
  ): Promise<void> {
    const { threadId } = message;
    const binding = this.deps.sessionMap.ensure(threadId);
    const chatTaskCode = binding.chatTaskCode;
    await this.ensureChatTask(chatTaskCode);

    // Project-dir command (`#dir <path>` / `#工程 <path>`): `automind start` is a
    // generic launcher with no project path, so the user points at the project
    // here. This confirms the workspace at runtime for all later tasks.
    // NOTE: `\b` only recognizes ASCII word chars, so it never matches after a
    // CJK keyword (e.g. `#工程`). Match the keyword followed by end/whitespace/
    // separators (or the path directly) instead.
    const dirCmd = message.text.match(
      /^\s*#(?:dir|工程|项目|目录)(?:[\s,:：]+(.*))?$/s,
    );
    if (dirCmd) {
      await this.handleSetWorkspace(threadId, (dirCmd[1] ?? "").trim());
      return;
    }

    // Flat message-stream addressing (§6.3): a leading `#<shortCode>` selects a
    // specific task in this conversation for this message (and becomes the new
    // default). Without a prefix, the conversation's current task is targeted.
    const addressed = this.resolveAddress(threadId, message.text, binding.activeTaskCode);
    if (addressed.error) {
      await this.deps.channel.send(threadId, addressed.error);
      return;
    }
    const targetTaskCode = addressed.taskCode;
    const text = addressed.text;

    const currentBinding = this.deps.sessionMap.get(threadId) ?? binding;
    const context: ConversationContext = {
      chatTaskCode,
      userText: text,
      targetTaskCode,
      tasks: currentBinding.tasks,
      workspace: this.deps.workspace
        ? {
            root: this.deps.workspace.getRoot(),
            confirmed: this.deps.workspace.isConfirmed(),
          }
        : null,
    };
    const turnStarted = performance.now();
    const planStarted = performance.now();
    const response = await this.deps.orchestrator.interpret(context);
    const planSeconds = (performance.now() - planStarted) / 1000;
    // Validate the complete plan before executing any action. A malformed,
    // cross-thread, or over-broad plan has zero side effects.
    const plan = this.capabilities.validate(response, currentBinding.tasks);
    if (!plan.ok) {
      await this.observeBestEffort(chatTaskCode, buildTurnObservation({
        actions: response.actions,
        parseFallback: response.diagnostics?.parseFallback,
        schemaReject: response.diagnostics?.schemaReject,
        planAccepted: false,
        planRejectReasonCode: "capability_plan_rejected",
        timings: {
          planSeconds,
          totalSeconds: (performance.now() - turnStarted) / 1000,
        },
      }));
      await this.deps.channel.send(
        threadId,
        `我没有执行这次操作：${plan.error}请明确任务后重试。`,
      );
      return;
    }
    if (plan.actions.length === 0) {
      const reply = response.reply.trim();
      if (reply) await this.deps.channel.send(threadId, reply);
      if (!reply) await this.deps.channel.send(threadId, "（无回复）");
      await this.observeBestEffort(chatTaskCode, buildTurnObservation({
        actions: [],
        parseFallback: response.diagnostics?.parseFallback,
        schemaReject: response.diagnostics?.schemaReject,
        planAccepted: true,
        timings: {
          planSeconds,
          totalSeconds: (performance.now() - turnStarted) / 1000,
        },
      }));
      return;
    }
    const results: CapabilityExecutionResult[] = [];
    const executorStarted = performance.now();
    for (const action of plan.actions) {
      results.push(await this.executeAction(threadId, action));
    }
    const executorSeconds = (performance.now() - executorStarted) / 1000;
    transaction.sideEffectCommitted = plan.actions.some((action, index) =>
      CAPABILITY_CATALOG[action.capability].policy.effect === "write" &&
      results[index]?.status === "completed");
    const hasPendingCard = results.some(
      (result) => result.status === "pending_confirmation",
    );
    if (hasPendingCard) {
      const reply = response.reply.trim();
      if (reply) await this.deps.channel.send(threadId, reply);
      await this.observeBestEffort(chatTaskCode, buildTurnObservation({
        actions: plan.actions,
        parseFallback: response.diagnostics?.parseFallback,
        schemaReject: response.diagnostics?.schemaReject,
        results,
        planAccepted: true,
        timings: {
          planSeconds,
          executorSeconds,
          totalSeconds: (performance.now() - turnStarted) / 1000,
        },
      }));
      return;
    }
    const resultResponseStarted = performance.now();
    const finalResponse = await this.deps.orchestrator.respondToResults(
      context,
      results,
    );
    const finalReply = finalResponse.reply.trim();
    if (finalReply) {
      await this.deps.channel.send(threadId, finalReply);
    } else {
      await this.deps.channel.send(
        threadId,
        results.map((result) => result.message).filter(Boolean).join("\n") ||
          "操作已完成。",
      );
    }
    await this.observeBestEffort(chatTaskCode, buildTurnObservation({
      actions: plan.actions,
      parseFallback: Boolean(
        response.diagnostics?.parseFallback ||
        finalResponse.diagnostics?.parseFallback
      ),
      schemaReject: Boolean(
        response.diagnostics?.schemaReject ||
        finalResponse.diagnostics?.schemaReject
      ),
      results,
      planAccepted: true,
      timings: {
        planSeconds,
        executorSeconds,
        resultResponseSeconds: (performance.now() - resultResponseStarted) / 1000,
        totalSeconds: (performance.now() - turnStarted) / 1000,
      },
    }));
  }

  private async observeBestEffort(
    chatTaskCode: string,
    observation: TurnObservation,
  ): Promise<void> {
    try {
      await this.deps.cli.observe(chatTaskCode, observation);
    } catch {
      // Observability must not fail or repeat a user-visible action.
    }
  }

  private async executeAction(
    threadId: string,
    action: ConversationAction,
  ): Promise<CapabilityExecutionResult> {
    switch (action.capability) {
      case "workspace.get": {
        const root = this.deps.workspace?.getRoot();
        return {
          capability: "workspace.get",
          ok: true,
          status: "completed",
          message: root ? `当前工程目录：${root}` : "当前未配置工程目录。",
          data: root
            ? { root, confirmed: this.deps.workspace?.isConfirmed() ?? false }
            : null,
        };
      }
      case "workspace.changes": {
        if (!this.deps.gitDiff) {
          return {
            capability: action.capability,
            ok: false,
            status: "failed",
            message: "当前无法读取工作区代码变更。",
          };
        }
        try {
          const diff = await this.deps.gitDiff("");
          return {
            capability: action.capability,
            ok: true,
            status: "completed",
            message: `当前工作区有 ${diff.filesChanged} 个变更文件。`,
            data: {
              scope: "workspace_uncommitted",
              filesChanged: diff.filesChanged,
              stat: diff.stat.slice(0, 12000),
              detail: diff.detail.slice(0, 12000),
            },
          };
        } catch (error) {
          return {
            capability: action.capability,
            ok: false,
            status: "failed",
            message: "读取工作区代码变更失败。",
            data: { error: String(error).slice(0, 1000) },
          };
        }
      }
      case "task.list": {
        const tasks = this.deps.sessionMap.get(threadId)?.tasks ?? [];
        const lines = tasks.map((task) => `#${task.shortCode} · ${task.name} · ${task.taskCode}`);
        return {
          capability: "task.list",
          ok: true,
          status: "completed",
          message: lines.length ? `当前会话任务：\n${lines.join("\n")}` : "当前会话还没有任务。",
          data: tasks,
        };
      }
      case "task.select": {
        const { taskCode } = action.arguments;
        this.deps.sessionMap.setActiveTask(threadId, taskCode);
        const label = this.taskLabel(threadId, taskCode);
        return {
          capability: "task.select",
          ok: true,
          status: "completed",
          message: labelText("已切换当前任务。", label),
          data: { taskCode },
        };
      }
      case "task.status":
        return await this.sendProgress(threadId, action.arguments.taskCode);
      case "task.inspect":
        return await this.inspectTask(threadId, action.arguments.taskCode, action.arguments.view);
      case "task.create": {
        if (!this.workspaceConfirmed()) {
          return {
            capability: action.capability,
            ok: false,
            status: "rejected",
            message: this.askForWorkspaceText(),
          };
        }
        const summary = action.arguments.requirementSummary.trim();
        const token = this.nonce();
        this.deps.sessionMap.setPendingConfirm(threadId, {
          kind: "handoff",
          requirementSummary: summary,
          token,
        });
        await this.deps.channel.sendCard(threadId, confirmCard("确认起任务", summary, token));
        return {
          capability: action.capability,
          ok: true,
          status: "pending_confirmation",
          message: "已发送起任务确认卡片。",
          data: { requirementSummary: summary },
        };
      }
      case "task.modify": {
        const { taskCode, instruction } = action.arguments;
        const token = this.nonce();
        this.deps.sessionMap.setPendingConfirm(threadId, {
          kind: "inject",
          taskCode,
          rewrittenInstruction: instruction.trim(),
          token,
        });
        const label = this.taskLabel(threadId, taskCode);
        await this.deps.channel.sendCard(
          threadId,
          withTaskLabel(confirmCard("确认修改任务", instruction.trim(), token), label),
        );
        return {
          capability: action.capability,
          ok: true,
          status: "pending_confirmation",
          message: labelText("已发送修改确认卡片。", label),
          data: { taskCode, instruction: instruction.trim() },
        };
      }
      case "task.resume": {
        const { taskCode } = action.arguments;
        const result = await this.deps.cli.resumeInBackground(taskCode, this.deps.agent);
        const label = this.taskLabel(threadId, taskCode);
        return {
          capability: action.capability,
          ok: result.code === 0,
          status: result.code === 0 ? "completed" : "failed",
          message: labelText(result.code === 0 ? "已触发任务恢复。" : "任务恢复失败，请稍后重试。", label),
          data: { code: result.code, stderr: result.stderr.slice(0, 1000) },
        };
      }
      case "task.answer": {
        const { taskCode, answer } = action.arguments;
        const label = this.taskLabel(threadId, taskCode);
        const snapshot = this.deps.snapshotReader.read(taskCode);
        if (!snapshot?.askUser) {
          return {
            capability: action.capability,
            ok: false,
            status: "rejected",
            message: labelText("当前任务没有待回答的问题。", label),
          };
        }
        const result = await this.deps.cli.answerText(taskCode, answer);
        const resumed = result.code === 0
          ? await this.deps.cli.resumeInBackground(taskCode, this.deps.agent)
          : null;
        const committed = result.code === 0;
        const ok = committed && resumed?.code === 0;
        return {
          capability: action.capability,
          ok: committed,
          status: committed ? "completed" : "failed",
          message: labelText(
            ok
              ? "已记录回答并继续执行任务。"
              : committed
                ? "已记录回答，但自动恢复任务失败；请稍后要求恢复该任务。"
                : "回答提交失败，请稍后重试。",
            label,
          ),
          data: {
            answerCode: result.code,
            resumeCode: resumed?.code ?? null,
            stderr: `${result.stderr}\n${resumed?.stderr ?? ""}`.trim().slice(0, 1000),
          },
        };
      }
      case "clarification.request":
        await this.deps.channel.sendCard(
          threadId,
          clarifyCard(action.arguments.question),
        );
        return {
          capability: action.capability,
          ok: true,
          status: "pending_confirmation",
          message: "已发送澄清卡片。",
          data: { question: action.arguments.question },
        };
    }
  }

  /**
   * Parse an optional leading `#<shortCode>` task selector from a message (§6.3
   * flat message stream). When present and resolvable, it selects that task as
   * the target for this message and updates the conversation's current task.
   * When present but unresolvable, returns an error message to send back.
   * When absent, falls back to the conversation's current task.
   */
  private resolveAddress(
    threadId: string,
    rawText: string,
    currentTaskCode: string | null,
  ): { taskCode: string | null; text: string; error?: string } {
    const match = rawText.match(/^\s*#([A-Za-z0-9_-]+)\b[\s,:：]*(.*)$/s);
    if (!match) {
      return { taskCode: currentTaskCode, text: rawText };
    }
    const shortCode = match[1];
    const rest = match[2];
    const ref = this.deps.sessionMap.resolveShortCode(threadId, shortCode);
    if (!ref) {
      const binding = this.deps.sessionMap.get(threadId);
      const known = (binding?.tasks ?? [])
        .map((t) => `#${t.shortCode}(${t.name})`)
        .join("、");
      const hint = known ? `当前任务：${known}` : "当前还没有进行中的任务。";
      return {
        taskCode: currentTaskCode,
        text: rawText,
        error: `找不到任务 #${shortCode}。${hint}`,
      };
    }
    // Selecting a task makes it the conversation's current default.
    this.deps.sessionMap.setActiveTask(threadId, ref.taskCode);
    return { taskCode: ref.taskCode, text: rest.trim() || rawText };
  }

  /** Handle a card button tap. */
  async onCardAction(
    threadId: string,
    optionId: string,
    cardKind: string,
    token = "",
    userId = "",
  ): Promise<void> {
    // Allow-list also guards button taps (§5): a non-allowed user must not be
    // able to drive CodeMind by tapping a card, even one another user received.
    if (!this.isAllowed(userId)) {
      await this.deps.channel.send(threadId, "抱歉，你没有权限驱动 CodeMind。");
      return;
    }
    // Idempotent taps (§13.5): a token-carrying button represents one decision;
    // a repeated tap of the same (thread,kind,option,token) is dropped so a
    // double-click / redelivery cannot execute the action twice.
    const dedupeKey = token
      ? `card:${threadId}:${cardKind}:${optionId}:${token}`
      : "";
    if (dedupeKey && this.deduper.isDuplicate(dedupeKey)) {
      const chatTaskCode = this.deps.sessionMap.chatTaskCodeFor(threadId);
      await this.observeBestEffort(
        chatTaskCode,
        buildDuplicateObservation("duplicate_card"),
      );
      return;
    }
    let sideEffectCommitted = false;
    try {
      if (cardKind === "confirm") {
        const pending = this.deps.sessionMap.getPendingConfirm(threadId);
        const capability = pending?.kind === "inject" ? "task.modify" : "task.create";
        const result = await this.handleConfirmAction(threadId, optionId, token);
        const committed = result === "confirmed" || result === "cancelled";
        if (!committed && dedupeKey) this.deduper.forget(dedupeKey);
        await this.observeBestEffort(
          this.deps.sessionMap.chatTaskCodeFor(threadId),
          buildCardObservation({ capability, result }),
        );
        return;
      }
      if (cardKind === "ask_user") {
        const binding = this.deps.sessionMap.get(threadId);
        if (!binding?.activeTaskCode) return;
        const snapshot = this.deps.snapshotReader.read(binding.activeTaskCode);
        const currentId = snapshot?.askUser?.id ?? "";
        if (!currentId || token !== currentId) {
          await this.deps.channel.send(threadId, "该卡片已失效，请以最新消息为准。");
          await this.observeBestEffort(
            binding.chatTaskCode,
            buildCardObservation({ capability: "task.answer", result: "stale" }),
          );
          return;
        }
        const answered = await this.deps.cli.answerOption(binding.activeTaskCode, optionId);
        sideEffectCommitted = answered.code === 0;
        const resumed = answered.code === 0
          ? await this.deps.cli.resumeInBackground(binding.activeTaskCode, this.deps.agent)
          : null;
        const committed = answered.code === 0;
        if (!committed && dedupeKey) this.deduper.forget(dedupeKey);
        await this.observeBestEffort(
          binding.chatTaskCode,
          buildCardObservation({
            capability: "task.answer",
            result: committed ? "completed" : "failed",
          }),
        );
        await this.deps.channel.send(
          threadId,
          committed && resumed?.code === 0
            ? "已提交你的选择，任务继续执行。"
            : committed
              ? "已提交你的选择，但自动恢复任务失败；请稍后要求恢复该任务。"
              : "提交选择失败，请稍后重试。",
        );
        return;
      }
    } catch (error) {
      if (dedupeKey && !sideEffectCommitted) this.deduper.forget(dedupeKey);
      throw error;
    }
    // progress/gitdiff/notice/report cards are read-only: ignore taps.
  }

  private async handleConfirmAction(
    threadId: string,
    optionId: string,
    token: string,
  ): Promise<"confirmed" | "cancelled" | "failed" | "stale"> {
    const pending = this.deps.sessionMap.getPendingConfirm(threadId);
    // Reject stale/replayed confirm cards whose token no longer matches the
    // current pending decision. Do not clear the live pending on a bad token.
    if (!pending || token !== pending.token) {
      await this.deps.channel.send(threadId, "该卡片已失效，请以最新消息为准。");
      return "stale";
    }
    if (optionId !== "confirm") {
      this.deps.sessionMap.setPendingConfirm(threadId, null);
      await this.deps.channel.send(threadId, "已取消。");
      return "cancelled";
    }

    if (pending.kind === "handoff") {
      const result = await startTaskFromSummary(this.deps.cli, {
        requirementSummary: pending.requirementSummary,
        agent: this.deps.agent,
      });
      if (result.taskCode) {
        this.deps.sessionMap.setPendingConfirm(threadId, null);
        // Register under the conversation with a human name so every later
        // card/message can be labeled and the user can address it via #short.
        const name = deriveTaskName(pending.requirementSummary);
        const { ref } = this.deps.sessionMap.addTask(threadId, result.taskCode, name);
        const label = formatTaskLabel(ref);
        await this.deps.channel.send(threadId, labelText(
          result.ok
            ? `任务已启动：${result.taskCode}（用 #${ref.shortCode} 指向它）`
            : `任务已创建：${result.taskCode}，但后台启动失败；可用 #${ref.shortCode} 要求恢复。`,
          label,
        ));
      } else {
        await this.deps.channel.send(threadId, "起任务失败，请稍后重试。");
      }
      return result.taskCode ? "confirmed" : "failed";
    }

    // inject
    const label = this.taskLabel(threadId, pending.taskCode);
    const snapshot = this.deps.snapshotReader.read(pending.taskCode);
    const result = await injectInstruction(
      this.deps.cli,
      pending.taskCode,
      pending.rewrittenInstruction,
      snapshot,
      this.deps.agent,
    );
    if (!result.appended) {
      await this.deps.channel.send(threadId, labelText("修改指令发送失败。", label));
      return "failed";
    }
    this.deps.sessionMap.setPendingConfirm(threadId, null);
    if (!result.ok) {
      await this.deps.channel.send(
        threadId,
        labelText("修改指令已记录，但自动恢复任务失败；请稍后要求恢复该任务。", label),
      );
      return "confirmed";
    }
    // Busy-time behavior (§6.4): while the loop is active the instruction is
    // queued and answered at the next stage boundary, so send a read-only
    // "received, busy" notice instead of implying an immediate action.
    if (!result.resumed) {
      const phase = snapshot?.phase ?? snapshot?.status ?? "当前阶段";
      await this.deps.channel.sendCard(
        threadId,
        withTaskLabel(noticeCard(`已收到，正在处理 ${phase}，本阶段结束后回应。`), label),
      );
      return "confirmed";
    }
    await this.deps.channel.send(threadId, labelText("修改指令已送达任务。", label));
    return "confirmed";
  }

  private async inspectTask(
    threadId: string,
    taskCode: string,
    view: "overview" | "question" | "plan" | "delivery" | "validation" |
      "evaluation" | "summary" | "report" | "evidence" | "logs",
  ): Promise<CapabilityExecutionResult> {
    const label = this.taskLabel(threadId, taskCode);
    if (view === "logs") {
      const latest = this.deps.snapshotReader.readLatestIterationArtifact(
        taskCode,
        "log-digest.md",
      );
      if (!latest) {
        return {
          capability: "task.inspect",
          ok: false,
          status: "failed",
          message: labelText("暂时没有可用的日志摘要。", label),
        };
      }
      return {
        capability: "task.inspect",
        ok: true,
        status: "completed",
        message: labelText("已读取最新日志摘要。", label),
        data: {
          view,
          artifact: {
            path: latest.path,
            content: latest.content.slice(0, 12000),
            truncated: latest.content.length > 12000,
          },
        },
      };
    }

    const filesByView: Record<Exclude<typeof view, "logs">, string[]> = {
      overview: ["runtime-state.json", "Plan.md", "evaluation.json"],
      question: ["runtime-state.json"],
      plan: ["Plan.md", "Requirements.md", "TestCases.md"],
      delivery: ["Delivery.md", "delivery.json"],
      validation: ["Validation.md", "evaluation.json"],
      evaluation: ["evaluation.json", "VerificationLedger.json"],
      summary: ["summary.md"],
      report: ["summary.md", "completion-report.json", "VerificationLedger.json"],
      evidence: ["VerificationLedger.json", "evaluation.json"],
    };
    const artifacts = filesByView[view].map((path) => ({
      path,
      content: this.deps.snapshotReader.readArtifact(taskCode, path),
    }));
    const found = artifacts.filter((artifact) => artifact.content !== null);
    if (found.length === 0) {
      return {
        capability: "task.inspect",
        ok: false,
        status: "failed",
        message: labelText(`暂时没有可用的 ${view} 产物。`, label),
        data: { view, requested: artifacts.map((artifact) => artifact.path) },
      };
    }
    return {
      capability: "task.inspect",
      ok: true,
      status: "completed",
      message: labelText(`已读取任务 ${view} 产物。`, label),
      data: {
        view,
        artifacts: found.map((artifact) => ({
          path: artifact.path,
          content: artifact.content!.slice(0, 12000),
          truncated: artifact.content!.length > 12000,
        })),
      },
    };
  }

  private async sendProgress(
    threadId: string,
    taskCode: string | null,
  ): Promise<CapabilityExecutionResult> {
    if (!taskCode) {
      return {
        capability: "task.status",
        ok: false,
        status: "rejected",
        message: "当前没有进行中的任务。",
      };
    }
    const label = this.taskLabel(threadId, taskCode);
    const snapshot = this.deps.snapshotReader.read(taskCode);
    if (!snapshot) {
      return {
        capability: "task.status",
        ok: false,
        status: "failed",
        message: labelText("暂时读不到任务状态。", label),
      };
    }
    // When the core is waiting on ask_user, surface the question card with the
    // questionId as its anti-replay token so a tap can be matched to it.
    if (snapshot.askUser) {
      const card = askUserCard(snapshot.askUser);
      await this.deps.channel.sendCard(threadId, {
        ...withTaskLabel(card, label),
        token: snapshot.askUser.id,
      });
      return {
        capability: "task.status",
        ok: true,
        status: "pending_confirmation",
        message: labelText("任务正在等待用户回答，已发送确认卡片。", label),
        data: snapshot,
      };
    }
    // On a finished/failed task, deliver the final report card (§7.2) with the
    // git-diff change summary folded into a collapsible when a collector exists.
    if (snapshot.finished) {
      const title = snapshot.status === "finished" ? "任务完成" : `任务${snapshot.status}`;
      const verdict = `状态：${snapshot.status}`;
      let changeSummary: string | undefined;
      if (this.deps.gitDiff) {
        try {
          changeSummary = formatReportSection(await this.deps.gitDiff(taskCode));
        } catch {
          changeSummary = undefined;
        }
      }
      await this.deps.channel.sendCard(
        threadId,
        withTaskLabel(reportCard(title, verdict, changeSummary), label),
      );
      return {
        capability: "task.status",
        ok: true,
        status: "completed",
        message: labelText(verdict, label),
        data: { ...snapshot, changeSummary },
      };
    }
    // Fold semantic event lines (build_result/ui_action_done, §7.3) into the
    // read-only progress card; absent events simply add no extra lines.
    const eventLines = progressLinesFromEvents(this.deps.snapshotReader.readEvents(taskCode));
    await this.deps.channel.sendCard(
      threadId,
      withTaskLabel(progressCard(snapshot, eventLines.slice(-5)), label),
    );
    return {
      capability: "task.status",
      ok: true,
      status: "completed",
      message: labelText(`任务状态：${snapshot.status}`, label),
      data: { ...snapshot, recentEvents: eventLines.slice(-5) },
    };
  }

}

function confirmCard(title: string, body: string, token: string): Card {
  return { kind: "confirm", title, body, options: CONFIRM_OPTIONS, token };
}

/**
 * Derive a short human task name from the requirement summary: the first
 * non-empty line, trimmed to a reasonable length for a card label (§6.3).
 */
export function deriveTaskName(summary: string): string {
  const firstLine = summary
    .split("\n")
    .map((line) => line.trim())
    .find((line) => line.length > 0) ?? "";
  const cleaned = firstLine.replace(/^[#*\-\s]+/, "").trim();
  const max = 24;
  return cleaned.length > max ? `${cleaned.slice(0, max)}…` : cleaned;
}

function clarifyCard(question: string | null): Card {
  return {
    kind: "clarify",
    title: "需要澄清",
    body: question?.trim() || "能再具体说明一下吗？",
  };
}
