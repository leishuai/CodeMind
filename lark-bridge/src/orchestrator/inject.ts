/**
 * inject.ts (design §6.6 / §13.2) — inject a modify-task instruction into an
 * active harness task by appending a self-contained user-message. When the task
 * is paused/finished, resume once to consume the pending message.
 */
import type { CodeMindCli } from "./automind-cli.js";
import type { TaskSnapshot } from "./progress.js";

export interface InjectResult {
  ok: boolean;
  appended: boolean;
  resumed: boolean;
  stdout: string;
  stderr: string;
}

/** Statuses where the loop is actively running and will pick up pending msgs. */
const LOOP_ACTIVE_STATUSES = new Set([
  "planning",
  "generating",
  "evaluating",
  "created",
]);

/**
 * Inject a self-contained (already rewritten) instruction into a task.
 * If the loop is not actively running, resume once so the message is consumed.
 */
export async function injectInstruction(
  cli: CodeMindCli,
  taskCode: string,
  rewrittenInstruction: string,
  snapshot: TaskSnapshot | null,
  agent: string,
): Promise<InjectResult> {
  const appended = await cli.message(taskCode, rewrittenInstruction);
  if (appended.code !== 0) {
    return {
      ok: false,
      appended: false,
      resumed: false,
      stdout: appended.stdout,
      stderr: appended.stderr,
    };
  }

  const loopActive =
    snapshot !== null && LOOP_ACTIVE_STATUSES.has(snapshot.status);
  if (loopActive) {
    return {
      ok: true,
      appended: true,
      resumed: false,
      stdout: appended.stdout,
      stderr: appended.stderr,
    };
  }

  const resumed = await cli.resumeInBackground(taskCode, agent);
  return {
    ok: resumed.code === 0,
    appended: true,
    resumed: true,
    stdout: resumed.stdout,
    stderr: resumed.stderr,
  };
}
