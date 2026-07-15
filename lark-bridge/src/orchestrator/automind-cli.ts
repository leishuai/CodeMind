/**
 * automind-cli.ts (design §13.2) — the ONLY place the bridge touches the
 * CodeMind core, via automind.sh subprocess calls. No cross-language import.
 *
 * The runner is injectable so unit tests can mock the subprocess boundary.
 */
import { spawn } from "node:child_process";

export interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

/** Runs a command and captures output. Injectable for tests. */
export type CommandRunner = (
  bin: string,
  args: string[],
  cwd: string,
) => Promise<CliResult>;

export type BackgroundCommandRunner = (
  bin: string,
  args: string[],
  cwd: string,
) => Promise<CliResult>;

/** Default subprocess runner (spawn + capture). Reused for git in main.ts. */
export const defaultRunner: CommandRunner = (bin, args, cwd) =>
  new Promise<CliResult>((resolve, reject) => {
    const child = spawn(bin, args, { cwd });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", reject);
    child.on("close", (code) => {
      resolve({ code: code ?? -1, stdout, stderr });
    });
  });

/** Spawn a detached command and resolve once the process is successfully started. */
export const defaultBackgroundRunner: BackgroundCommandRunner = (bin, args, cwd) =>
  new Promise<CliResult>((resolve, reject) => {
    const child = spawn(bin, args, {
      cwd,
      detached: true,
      stdio: "ignore",
    });
    child.once("error", reject);
    child.once("spawn", () => {
      child.unref();
      resolve({
        code: 0,
        stdout: JSON.stringify({ result: "started", pid: child.pid ?? null }),
        stderr: "",
      });
    });
  });

export interface CodeMindCliOptions {
  bin: string;
  /**
   * Project root used as the subprocess cwd. Accepts a string, or a getter so
   * the cwd can follow a runtime workspace change (design: `automind start` is a
   * generic launcher and the project dir is confirmed later, see workspace.ts).
   */
  workspaceRoot: string | (() => string);
  runner?: CommandRunner;
  backgroundRunner?: BackgroundCommandRunner;
}

/**
 * Thin, typed wrapper over the verified automind subcommands
 * (ask / message / answer / resume / status). Signatures match automind.sh.
 */
export class CodeMindCli {
  private readonly bin: string;
  private readonly workspaceRoot: () => string;
  private readonly runner: CommandRunner;
  private readonly backgroundRunner: BackgroundCommandRunner;

  constructor(options: CodeMindCliOptions) {
    this.bin = options.bin;
    const ws = options.workspaceRoot;
    this.workspaceRoot = typeof ws === "function" ? ws : () => ws;
    this.runner = options.runner ?? defaultRunner;
    this.backgroundRunner = options.backgroundRunner ?? defaultBackgroundRunner;
  }

  private run(args: string[]): Promise<CliResult> {
    return this.runner(this.bin, args, this.workspaceRoot());
  }

  /** Create/upgrade to a harness task from a requirement summary. */
  ask(requirement: string, agent: string): Promise<CliResult> {
    return this.run(["ask", requirement, agent, "--detached"]);
  }

  /** Create task artifacts immediately without running the long harness loop. */
  scaffold(requirement: string): Promise<CliResult> {
    return this.run(["scaffold", requirement, "--no-current"]);
  }

  /**
   * Ensure a resident chat-mode task shell exists (design §5 fix). Idempotent;
   * required before `message --resume` can run the S_chat agent, because the
   * core rejects `message` on a non-existent task dir.
   */
  chatCreate(taskCode: string): Promise<CliResult> {
    return this.run(["chat-create", taskCode, "--json"]);
  }

  /**
   * Send a chat/message to a task. In chat state (`--resume`) this runs the
   * agent and prints its reply; otherwise it appends a user-message.
   */
  message(taskCode: string, text: string, resumeAgent?: string): Promise<CliResult> {
    const args = ["message", taskCode, "--text", text];
    if (resumeAgent) {
      args.push("--resume", resumeAgent);
    }
    return this.run(args);
  }

  /**
   * Stateless one-shot classification call (de-pollution, design §6.2/§13.4).
   *
   * Unlike `message`, this does NOT write into the resident S_chat history and
   * never resumes/records the persistent primary session: the core runs the
   * agent with a fresh session role, so classification prompts/JSON/retries
   * never contaminate the user's long-lived chat session. The stdout is the raw
   * agent reply (caller parses the JSON verdict).
   */
  classify(taskCode: string, text: string, agent: string): Promise<CliResult> {
    return this.run(["classify", taskCode, "--text", text, "--agent", agent]);
  }

  /**
   * Run a persistent conversation-orchestrator turn. The core keeps visible
   * recovery state separately and does not append the protocol prompt to the
   * formal task instruction queue.
   */
  converse(
    taskCode: string,
    prompt: string,
    userText: string,
    agent: string,
    internal = false,
  ): Promise<CliResult> {
    const args = [
      "converse",
      taskCode,
      "--text",
      prompt,
      "--user-text",
      userText,
      "--agent",
      agent,
    ];
    if (internal) args.push("--internal");
    return this.run(args);
  }

  /** Persist a deterministic slash-command turn without invoking the model. */
  recordConversationInput(taskCode: string, userText: string): Promise<CliResult> {
    return this.run([
      "converse",
      taskCode,
      "--text",
      "deterministic slash command",
      "--user-text",
      userText,
      "--record-only",
    ]);
  }

  /** Record one validated external audit/metrics observation batch. */
  observe(taskCode: string, observation: unknown): Promise<CliResult> {
    return this.run([
      "observe",
      taskCode,
      "--json",
      JSON.stringify(observation),
    ]);
  }

  /** Answer a pending ask_user question by option id. */
  answerOption(taskCode: string, optionId: string): Promise<CliResult> {
    return this.run(["answer", taskCode, "--option", optionId]);
  }

  /** Answer a pending ask_user question with free text. */
  answerText(taskCode: string, text: string): Promise<CliResult> {
    return this.run(["answer", taskCode, "--text", text]);
  }

  /** Resume a paused/finished task once (e.g. to consume pending messages). */
  resume(taskCode: string, agent: string): Promise<CliResult> {
    return this.run(["resume", taskCode, agent, "--detached"]);
  }

  /** Start/resume the harness without blocking the channel event callback. */
  resumeInBackground(taskCode: string, agent: string): Promise<CliResult> {
    return this.backgroundRunner(
      this.bin,
      ["resume", taskCode, agent, "--detached"],
      this.workspaceRoot(),
    );
  }

  /** Read task status (raw stdout; caller parses if JSON). */
  status(taskCode: string): Promise<CliResult> {
    return this.run(["status", taskCode]);
  }
}
