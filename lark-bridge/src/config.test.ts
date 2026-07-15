import { describe, it, expect } from "vitest";
import os from "node:os";
import { loadConfig, isUserAllowed, applyBotRecord } from "./config.js";
import type { BotRecord } from "./orchestrator/registry.js";

describe("loadConfig", () => {
  it("applies defaults and resolves paths", () => {
    const config = loadConfig({
      BRIDGE_DIR: "/repo/lark-bridge",
      LARK_APP_ID: "cli_x",
      LARK_APP_SECRET: "secret_x",
    });
    expect(config.lark.appId).toBe("cli_x");
    expect(config.automindBin).toBe("/repo/automind.sh");
    // workspaceRoot is OPTIONAL: `automind start` is a generic launcher. When
    // unset it falls back to the home dir and is marked NOT confirmed, so the
    // bridge proactively asks which project to work on.
    expect(config.workspaceRoot).toBe(os.homedir());
    expect(config.workspaceConfirmed).toBe(false);
    expect(config.agent).toBe("auto");
    expect(config.pollIntervalMs).toBe(2000);
    expect(config.allowedUsers).toEqual([]);
    expect(config.lark.ackEmojiType).toBe("Typing");
  });

  it("parses the allow-list and custom poll interval", () => {
    const config = loadConfig({
      BRIDGE_DIR: "/repo/lark-bridge",
      LARK_ALLOWED_USERS: "ou_a, ou_b ,ou_c",
      BRIDGE_POLL_INTERVAL_MS: "500",
      AUTOMIND_WORKSPACE_ROOT: "/projects/target",
    });
    expect(config.allowedUsers).toEqual(["ou_a", "ou_b", "ou_c"]);
    expect(config.pollIntervalMs).toBe(500);
    expect(config.workspaceRoot).toBe("/projects/target");
    // An explicit AUTOMIND_WORKSPACE_ROOT marks the workspace confirmed.
    expect(config.workspaceConfirmed).toBe(true);
  });

  it("falls back to 2000 for a non-numeric poll interval", () => {
    const config = loadConfig({ BRIDGE_DIR: "/repo/lark-bridge", BRIDGE_POLL_INTERVAL_MS: "abc" });
    expect(config.pollIntervalMs).toBe(2000);
  });

  it("allows overriding the receipt reaction emoji type", () => {
    const config = loadConfig({
      BRIDGE_DIR: "/repo/lark-bridge",
      LARK_ACK_EMOJI_TYPE: "Get",
    });
    expect(config.lark.ackEmojiType).toBe("Get");
  });
});

describe("isUserAllowed", () => {
  const base = loadConfig({ BRIDGE_DIR: "/repo/lark-bridge" });

  it("allows everyone when the list is empty", () => {
    expect(isUserAllowed(base, "anyone")).toBe(true);
  });

  it("restricts to listed users", () => {
    const config = { ...base, allowedUsers: ["ou_a"] };
    expect(isUserAllowed(config, "ou_a")).toBe(true);
    expect(isUserAllowed(config, "ou_b")).toBe(false);
  });
});

describe("applyBotRecord", () => {
  const base = loadConfig({ BRIDGE_DIR: "/repo/lark-bridge" });
  const bot: BotRecord = {
    botId: "bot_1",
    name: "支付服务",
    appId: "cli_pay",
    appSecret: "sec_pay",
    workspaceRoot: "/projects/pay",
    workspaceConfirmed: true,
    allowedUsers: ["ou_pay"],
    agent: "codex",
    createdAt: "t",
    updatedAt: "t",
  };

  it("overlays a bot's identity, credentials, workspace and allow-list", () => {
    const config = applyBotRecord(base, bot);
    expect(config.botId).toBe("bot_1");
    expect(config.lark.appId).toBe("cli_pay");
    expect(config.lark.appSecret).toBe("sec_pay");
    expect(config.workspaceRoot).toBe("/projects/pay");
    expect(config.workspaceConfirmed).toBe(true);
    expect(config.allowedUsers).toEqual(["ou_pay"]);
    expect(config.agent).toBe("codex");
  });

  it("keeps base fields when the bot record leaves them empty", () => {
    const empty: BotRecord = {
      ...bot,
      appId: "",
      appSecret: "",
      workspaceRoot: "",
      workspaceConfirmed: false,
      allowedUsers: [],
      agent: "",
    };
    const config = applyBotRecord(base, empty);
    // Base falls back to home dir + not confirmed when the bot has no workspace.
    expect(config.workspaceRoot).toBe(os.homedir());
    expect(config.workspaceConfirmed).toBe(false);
    expect(config.allowedUsers).toEqual([]);
    expect(config.agent).toBe("auto");
  });
});
