/**
 * task-events.ts (design §7.1 / §7.3) — read the CodeMind core's
 * `events.jsonl` and derive channel-neutral, human-readable progress lines
 * from the semantic events (`build_result`, `ui_action_done`) plus the
 * "hit a problem -> corrected" pair (a failing build_result later followed by
 * a passing one).
 *
 * Read-only. Pure parsing over injected text, so unit tests need no disk I/O.
 * When the semantic events are absent, callers degrade to the file signals in
 * progress.ts (§7.1) — this module simply yields no extra lines.
 */

/** One raw event line from events.jsonl. */
export interface TaskEvent {
  type: string;
  message?: string;
  phase?: string;
  data?: Record<string, unknown>;
}

/** A human-readable progress line derived from semantic events. */
export interface ProgressLine {
  kind: "build" | "ui" | "recovered";
  text: string;
}

/** Parse events.jsonl text into TaskEvent[]; tolerates malformed lines. */
export function parseEvents(text: string | null): TaskEvent[] {
  if (!text) return [];
  const events: TaskEvent[] = [];
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const value = JSON.parse(trimmed);
      if (value && typeof value === "object" && typeof value.type === "string") {
        events.push(value as TaskEvent);
      }
    } catch {
      // skip malformed line
    }
  }
  return events;
}

function buildResultLine(ev: TaskEvent): ProgressLine {
  const data = ev.data ?? {};
  const succeeded = Boolean(data.succeeded);
  const failed = Array.isArray(data.failedChecks) ? data.failedChecks : [];
  if (succeeded) {
    return { kind: "build", text: "编译/测试通过 ✅" };
  }
  const detail = failed.length > 0 ? `（失败项：${failed.join("、")}）` : "";
  return { kind: "build", text: `编译/测试未通过 ❌${detail}` };
}

function uiActionLine(ev: TaskEvent): ProgressLine {
  const data = ev.data ?? {};
  const action = String(data.action ?? "操作");
  const name = data.name ? String(data.name) : "";
  const target = data.target ? `「${String(data.target)}」` : name ? `「${name}」` : "";
  const ok = data.ok === undefined ? true : Boolean(data.ok);
  const mark = ok ? "" : "（未成功）";
  return { kind: "ui", text: `已执行 UI 操作：${action}${target}${mark}` };
}

/**
 * Derive progress lines from a chronological event list.
 *
 * - `build_result` -> a build/test verdict line.
 * - `ui_action_done` -> a UI-action line.
 * - a failing `build_result` later followed by a passing one -> an extra
 *   "problem corrected" line right after the passing verdict.
 */
export function deriveProgressLines(events: TaskEvent[]): ProgressLine[] {
  const lines: ProgressLine[] = [];
  let sawFailedBuild = false;
  for (const ev of events) {
    switch (ev.type) {
      case "build_result": {
        lines.push(buildResultLine(ev));
        const succeeded = Boolean(ev.data?.succeeded);
        if (succeeded && sawFailedBuild) {
          sawFailedBuild = false;
          lines.push({ kind: "recovered", text: "先前的问题已纠正" });
        } else if (!succeeded) {
          sawFailedBuild = true;
        }
        break;
      }
      case "ui_action_done":
        lines.push(uiActionLine(ev));
        break;
      default:
        break;
    }
  }
  return lines;
}

/** Convenience: read + parse + derive, returning only the text lines. */
export function progressLinesFromEvents(text: string | null): string[] {
  return deriveProgressLines(parseEvents(text)).map((l) => l.text);
}
