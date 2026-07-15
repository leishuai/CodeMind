import { describe, it, expect } from "vitest";
import { CodeMindCli, type CliResult, type CommandRunner } from "./automind-cli.js";

interface Call {
  bin: string;
  args: string[];
  cwd: string;
}

function recordingRunner(result: Partial<CliResult> = {}): {
  runner: CommandRunner;
  calls: Call[];
} {
  const calls: Call[] = [];
  const runner: CommandRunner = async (bin, args, cwd) => {
    calls.push({ bin, args, cwd });
    return { code: result.code ?? 0, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
  };
  return { runner, calls };
}

function makeCli(runner: CommandRunner): CodeMindCli {
  return new CodeMindCli({
    bin: "/repo/automind.sh",
    workspaceRoot: "/ws",
    runner,
    backgroundRunner: runner,
  });
}

describe("CodeMindCli", () => {
  it("ask uses --detached and forwards agent", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).ask("做个登录页", "codex");
    expect(calls[0].args).toEqual(["ask", "做个登录页", "codex", "--detached"]);
    expect(calls[0].cwd).toBe("/ws");
  });

  it("chatCreate targets the chat-create subcommand with --json", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).chatCreate("lark_chat_abc");
    expect(calls[0].args).toEqual(["chat-create", "lark_chat_abc", "--json"]);
    expect(calls[0].cwd).toBe("/ws");
  });

  it("scaffold creates task artifacts without starting a long harness loop", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).scaffold("做个登录页");
    expect(calls[0].args).toEqual(["scaffold", "做个登录页", "--no-current"]);
  });

  it("message without resume appends only", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).message("task01", "改成深色");
    expect(calls[0].args).toEqual(["message", "task01", "--text", "改成深色"]);
  });

  it("message with resume agent adds --resume", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).message("chat01", "你好", "auto");
    expect(calls[0].args).toEqual(["message", "chat01", "--text", "你好", "--resume", "auto"]);
  });

  it("classify targets the stateless classify subcommand (no --resume)", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).classify("chat01", "帮我做登录", "auto");
    expect(calls[0].args).toEqual(["classify", "chat01", "--text", "帮我做登录", "--agent", "auto"]);
    expect(calls[0].args).not.toContain("--resume");
  });

  it("converse targets the persistent conversation command", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).converse("chat01", "structured prompt", "你好", "auto");
    expect(calls[0].args).toEqual([
      "converse",
      "chat01",
      "--text",
      "structured prompt",
      "--user-text",
      "你好",
      "--agent",
      "auto",
    ]);
  });

  it("converse marks tool-result follow-ups as internal", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).converse(
      "chat01",
      "tool result prompt",
      "测试通过了吗",
      "auto",
      true,
    );
    expect(calls[0].args.at(-1)).toBe("--internal");
  });

  it("records deterministic slash commands without a model call", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).recordConversationInput("chat01", "/status");
    expect(calls[0].args).toEqual([
      "converse",
      "chat01",
      "--text",
      "deterministic slash command",
      "--user-text",
      "/status",
      "--record-only",
    ]);
  });

  it("observe sends one JSON batch to the core", async () => {
    const { runner, calls } = recordingRunner();
    const observation = {
      source: "lark-bridge",
      audit: [],
      metrics: [{ name: "conversation_turn_count", value: 1, unit: "count" }],
    };
    await makeCli(runner).observe("chat01", observation);
    expect(calls[0].args).toEqual([
      "observe",
      "chat01",
      "--json",
      JSON.stringify(observation),
    ]);
  });

  it("answerOption maps to --option", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).answerOption("task01", "2");
    expect(calls[0].args).toEqual(["answer", "task01", "--option", "2"]);
  });

  it("resume uses --detached", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).resume("task01", "claude");
    expect(calls[0].args).toEqual(["resume", "task01", "claude", "--detached"]);
  });

  it("resumeInBackground uses the same resume contract", async () => {
    const { runner, calls } = recordingRunner();
    await makeCli(runner).resumeInBackground("task01", "claude");
    expect(calls[0].args).toEqual(["resume", "task01", "claude", "--detached"]);
  });

  it("propagates non-zero exit code", async () => {
    const { runner } = recordingRunner({ code: 1, stderr: "boom" });
    const res = await makeCli(runner).status("task01");
    expect(res.code).toBe(1);
    expect(res.stderr).toBe("boom");
  });
});
