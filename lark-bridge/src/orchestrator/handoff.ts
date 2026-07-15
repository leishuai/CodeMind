/**
 * handoff.ts (design §6.1.1 / §13.2) — summary-based hand-off that starts a new
 * harness task from an S_chat-generated requirement summary without blocking
 * the channel callback for the full harness duration.
 *
 * This layer is channel-neutral: it orchestrates CodeMindCli calls only.
 */
import type { CodeMindCli } from "./automind-cli.js";

export interface HandoffInput {
  /** S_chat-generated requirement summary (primary task input). */
  requirementSummary: string;
  /** Coding agent to run the task with. */
  agent: string;
  /** Raw recent conversation turns, seeded as backup evidence. */
  recentMessages?: string[];
}

export interface HandoffResult {
  ok: boolean;
  taskCode: string | null;
  stdout: string;
  stderr: string;
}

/** Best-effort extraction of the created task code from `ask` output. */
export function parseTaskCode(stdout: string): string | null {
  const envMatch = stdout.match(/\bTASK_CODE=([A-Za-z0-9._-]+)/);
  if (envMatch) return envMatch[1];
  // Try JSON first.
  const jsonMatch = stdout.match(/"(?:task|taskCode|taskId)"\s*:\s*"([^"]+)"/);
  if (jsonMatch) return jsonMatch[1];
  // Fallback: a task-code-like token (e.g. task07, lark_chat_x).
  const tokenMatch = stdout.match(/\b(task[0-9]+|[a-z0-9_]+_[a-z0-9]+)\b/);
  return tokenMatch ? tokenMatch[1] : null;
}

/**
 * Start a harness task via summary hand-off. The task uses an independent
 * task code (never the S_chat code), so the core's copy-style seed is not
 * triggered — core stays untouched.
 */
export async function startTaskFromSummary(
  cli: CodeMindCli,
  input: HandoffInput,
): Promise<HandoffResult> {
  const scaffolded = await cli.scaffold(input.requirementSummary);
  if (scaffolded.code !== 0) {
    return {
      ok: false,
      taskCode: null,
      stdout: scaffolded.stdout,
      stderr: scaffolded.stderr,
    };
  }
  const taskCode = parseTaskCode(scaffolded.stdout);
  if (!taskCode) {
    return {
      ok: false,
      taskCode: null,
      stdout: scaffolded.stdout,
      stderr: "scaffold output did not contain TASK_CODE",
    };
  }
  if (taskCode && input.recentMessages && input.recentMessages.length > 0) {
    // Seed raw recent conversation as backup evidence (no --resume: just append).
    for (const message of input.recentMessages) {
      await cli.message(taskCode, message);
    }
  }
  const started = await cli.resumeInBackground(taskCode, input.agent);
  return {
    ok: started.code === 0,
    taskCode,
    stdout: `${scaffolded.stdout}\n${started.stdout}`.trim(),
    stderr: `${scaffolded.stderr}\n${started.stderr}`.trim(),
  };
}
