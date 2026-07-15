/**
 * gitdiff.ts (design §7.2 / §13.2) — bridge-side git diff collection for the
 * change-summary card and the final report. The CodeMind core never collects
 * diffs; this stays entirely on the bridge.
 */
import type { CommandRunner } from "./automind-cli.js";

export interface GitDiffSummary {
  /** `git diff --stat` full text. */
  stat: string;
  /** Truncated unified diff for the collapsible detail. */
  detail: string;
  filesChanged: number;
}

const DEFAULT_DETAIL_LIMIT = 8000;

/**
 * Collect a diff summary against the workspace. `runner` is injectable so tests
 * can feed canned git output.
 */
export async function collectGitDiff(
  runner: CommandRunner,
  workspaceRoot: string,
  detailLimit = DEFAULT_DETAIL_LIMIT,
): Promise<GitDiffSummary> {
  const stat = await runner("git", ["diff", "--stat"], workspaceRoot);
  const full = await runner("git", ["diff"], workspaceRoot);
  const detail = truncate(full.stdout, detailLimit);
  return {
    stat: stat.stdout.trim(),
    detail,
    filesChanged: countChangedFiles(stat.stdout),
  };
}

/** Format a diff summary as a markdown block for the final report (§7.2). */
export function formatReportSection(summary: GitDiffSummary): string {
  if (summary.filesChanged === 0) {
    return "## 当前工作区未提交变更（非任务归因）\n\n（无改动）";
  }
  return [
    "## 当前工作区未提交变更（非任务归因）",
    "",
    "```",
    summary.stat,
    "```",
  ].join("\n");
}

function countChangedFiles(stat: string): number {
  // `git diff --stat` last line: " N files changed, ..."
  const match = stat.match(/(\d+)\s+files?\s+changed/);
  if (match) return Number.parseInt(match[1], 10);
  // Fallback: count non-summary lines with a `|` column.
  return stat
    .split("\n")
    .filter((line) => line.includes("|"))
    .length;
}

function truncate(text: string, limit: number): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n... (truncated)`;
}
