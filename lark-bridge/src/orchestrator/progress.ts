/**
 * progress.ts (design §7 / §13.2) — read CodeMind task artifacts and derive
 * channel-neutral progress/result/ask_user snapshots. Read-only; the core is
 * never mutated here.
 *
 * File access is injectable so unit tests do not need real disk I/O.
 */
import type { Card } from "../channel/types.js";

export interface AskUserQuestion {
  id: string;
  question: string;
  options: { id: string; label: string; recommended?: boolean }[];
}

export interface TaskSnapshot {
  status: string;
  nextAction: string;
  phase: string | null;
  iteration: number | null;
  /** Present when the core is waiting on an ask_user decision. */
  askUser: AskUserQuestion | null;
  /** True when status indicates the task ended. */
  finished: boolean;
}

/** Reads a task-dir file; returns null when missing. Injectable for tests. */
export type FileReader = (relativePath: string) => string | null;

function safeJson(text: string | null): Record<string, unknown> | null {
  if (!text) return null;
  try {
    const value = JSON.parse(text);
    return typeof value === "object" && value !== null
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function normalizeOptions(raw: unknown): AskUserQuestion["options"] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item, index) => {
    if (typeof item === "string") {
      return { id: String(index + 1), label: item };
    }
    const obj = (item ?? {}) as Record<string, unknown>;
    return {
      id: String(obj.id ?? index + 1),
      label: String(obj.label ?? obj.text ?? obj.title ?? ""),
      recommended: Boolean(obj.recommended),
    };
  });
}

function extractAskUser(state: Record<string, unknown>): AskUserQuestion | null {
  const ask = state.askUserQuestion as Record<string, unknown> | undefined;
  if (!ask) return null;
  const question = String(ask.question ?? ask.text ?? "").trim();
  const options = normalizeOptions(ask.options);
  if (!question && options.length === 0) return null;
  return {
    id: String(ask.id ?? "ask-001"),
    question: question || "CodeMind 需要你的确认。",
    options,
  };
}

const FINISHED_STATUSES = new Set(["finished", "failed", "stopped", "blocked"]);

/** Read runtime-state.json into a channel-neutral snapshot. */
export function readTaskSnapshot(readFile: FileReader): TaskSnapshot | null {
  const state = safeJson(readFile("runtime-state.json"));
  if (!state) return null;
  const status = String(state.status ?? "");
  const nextAction = String(state.nextAction ?? "");
  const iterationRaw = state.iteration;
  const iteration =
    typeof iterationRaw === "number" ? iterationRaw : null;
  const stateSummary = state.stateSummary as Record<string, unknown> | undefined;
  const phase =
    (stateSummary && typeof stateSummary.phase === "string"
      ? (stateSummary.phase as string)
      : null) ??
    (typeof state.phase === "string" ? (state.phase as string) : null);

  const askUser = extractAskUser(state);

  return {
    status,
    nextAction,
    phase,
    iteration,
    askUser,
    finished: FINISHED_STATUSES.has(status),
  };
}

/** Build an ask_user card from a snapshot's pending question. */
export function askUserCard(question: AskUserQuestion): Card {
  return {
    kind: "ask_user",
    title: "需要你的确认",
    body: question.question,
    options: question.options.map((opt) => ({
      id: opt.id,
      label: opt.label,
      recommended: opt.recommended,
    })),
  };
}

/**
 * Flat message-stream task labeling (§6.3). Multiple tasks share one
 * conversation, so every task-scoped card/message must announce which task it
 * belongs to. Renders `[#<shortCode> · <name> · <taskCode>]`, where taskCode is
 * the on-disk task path component (`.automind/tasks/<taskCode>`).
 */
export function formatTaskLabel(ref: {
  shortCode: string;
  name: string;
  taskCode: string;
}): string {
  const name = ref.name?.trim() || ref.shortCode;
  return `[#${ref.shortCode} · ${name} · ${ref.taskCode}]`;
}

/**
 * Prepend a task label to a card's title so a card seen in a flat message
 * stream is unambiguously attributed to its task. No-op when label is empty.
 */
export function withTaskLabel(card: Card, label: string): Card {
  if (!label) return card;
  return { ...card, title: `${label} ${card.title}` };
}

/** Prepend a task label to a plain-text message. No-op when label is empty. */
export function labelText(text: string, label: string): string {
  return label ? `${label} ${text}` : text;
}

/** Build a read-only progress card from a snapshot. */
export function progressCard(snapshot: TaskSnapshot, extraLines: string[] = []): Card {
  const lines = [
    `状态：${snapshot.status || "-"}`,
    snapshot.phase ? `阶段：${snapshot.phase}` : null,
    snapshot.iteration !== null ? `迭代：${snapshot.iteration}` : null,
    ...extraLines,
  ].filter((line): line is string => line !== null && line !== "");
  return {
    kind: "progress",
    title: "任务进度",
    body: lines.join("\n"),
  };
}

/**
 * Build a read-only git-diff change-summary card (§7.2). `stat` is the
 * `git diff --stat` text; `detail` (optional) is folded into a collapsible.
 */
export function gitdiffCard(
  stat: string,
  filesChanged: number,
  detail?: string,
): Card {
  const body =
    filesChanged === 0
      ? "本阶段无代码改动。"
      : `本阶段改动 ${filesChanged} 个文件：\n\`\`\`\n${stat}\n\`\`\``;
  const card: Card = { kind: "gitdiff", title: "代码变更摘要", body };
  if (filesChanged > 0 && detail && detail.trim()) {
    card.collapsible = [{ label: "查看关键 diff", content: `\`\`\`\n${detail}\n\`\`\`` }];
  }
  return card;
}

/** Build a lightweight read-only "received, busy" notice card (§6.4 / §7). */
export function noticeCard(message: string): Card {
  return { kind: "notice", title: "已收到", body: message };
}

/**
 * Build the final read-only report card (§7.2). `verdict` is the pass/fail
 * conclusion; `changeSummary` (optional) is the git-diff report section.
 */
export function reportCard(
  title: string,
  verdict: string,
  changeSummary?: string,
): Card {
  const card: Card = { kind: "report", title, body: verdict };
  if (changeSummary && changeSummary.trim()) {
    card.collapsible = [{ label: "当前工作区变更", content: changeSummary }];
  }
  return card;
}
