/**
 * Bridge configuration (design §13.1 config.ts).
 *
 * Loads from environment (.env via dotenv in main.ts). Kept channel-neutral
 * except for the Lark credential fields, which the Lark adapter consumes.
 */
import path from "node:path";
import os from "node:os";
import type { BotRecord } from "./orchestrator/registry.js";

export interface BridgeConfig {
  /**
   * The bot this daemon serves. CodeMind supports many Feishu bots; each runs as
   * its own process bound to one workspace, so `botId` scopes credentials, the
   * workspace, the persistent session map and the status file (design §multi-bot).
   * Empty when running from a bare env (single implicit bot).
   */
  botId: string;
  lark: {
    appId: string;
    appSecret: string;
    /** Emoji type used as an immediate "message received" reaction. */
    ackEmojiType: string;
  };
  /** Feishu open_id allow-list. Empty means "not restricted here". */
  allowedUsers: string[];
  /** Absolute path to automind.sh. */
  automindBin: string;
  /**
   * Target project workspace root (contains .automind/tasks). This is where
   * CodeMind analyzes/works. It is OPTIONAL at startup: `automind start` is a
   * generic launcher and does not take a project path. When the user has not
   * confirmed a project dir yet, this falls back to the user's home directory
   * so the daemon can still run and chat, and the bridge proactively asks the
   * user to confirm/point at a project (see `workspaceConfirmed`).
   */
  workspaceRoot: string;
  /**
   * Whether the workspace root was explicitly provided by the user
   * (AUTOMIND_WORKSPACE_ROOT set) rather than defaulted to the home directory.
   * When false, the bridge should proactively ask the user which project to
   * work on before starting real tasks.
   */
  workspaceConfirmed: boolean;
  /** Default coding agent for ask/resume. */
  agent: string;
  /** Poll interval for watching task artifacts. */
  pollIntervalMs: number;
}

function splitList(value: string | undefined): string[] {
  if (!value) return [];
  return value
    .split(",")
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

/**
 * Build config from an environment map. Pure function (no process access) so
 * it is trivially unit-testable.
 */
export function loadConfig(env: Record<string, string | undefined>): BridgeConfig {
  const bridgeDir = path.resolve(env.BRIDGE_DIR ?? process.cwd());
  const automindBin = path.resolve(
    bridgeDir,
    env.AUTOMIND_BIN ?? "../automind.sh",
  );
  // The project root is OPTIONAL: `automind start` is a generic launcher that
  // does not take a project path. When AUTOMIND_WORKSPACE_ROOT is set (e.g. the
  // user pre-confirmed it, or confirmed it interactively at startup) we use and
  // mark it confirmed. Otherwise we fall back to the user's home directory so
  // the daemon can still run/chat, and the bridge will proactively ask the user
  // which project to work on (workspaceConfirmed=false).
  const explicitRoot = env.AUTOMIND_WORKSPACE_ROOT?.trim();
  const workspaceConfirmed = Boolean(explicitRoot);
  const workspaceRoot = explicitRoot
    ? path.resolve(explicitRoot)
    : os.homedir();

  const pollIntervalMs = Number.parseInt(
    env.BRIDGE_POLL_INTERVAL_MS ?? "2000",
    10,
  );

  return {
    botId: env.AUTOMIND_BOT_ID?.trim() ?? "",
    lark: {
      appId: env.LARK_APP_ID ?? "",
      appSecret: env.LARK_APP_SECRET ?? "",
      ackEmojiType: env.LARK_ACK_EMOJI_TYPE ?? "Typing",
    },
    allowedUsers: splitList(env.LARK_ALLOWED_USERS),
    automindBin,
    workspaceRoot,
    workspaceConfirmed,
    agent: env.AUTOMIND_AGENT ?? "auto",
    pollIntervalMs: Number.isFinite(pollIntervalMs) ? pollIntervalMs : 2000,
  };
}

/**
 * Overlay a registered bot record onto a base config (design §multi-bot). The
 * registry is the source of truth for a specific bot's identity, credentials,
 * workspace and allow-list; env-derived fields (automindBin, agent default,
 * poll interval) are preserved from the base. Non-empty registry values win.
 */
export function applyBotRecord(
  base: BridgeConfig,
  bot: BotRecord,
): BridgeConfig {
  const workspaceRoot = bot.workspaceRoot?.trim()
    ? path.resolve(bot.workspaceRoot)
    : base.workspaceRoot;
  return {
    ...base,
    botId: bot.botId,
    lark: {
      appId: bot.appId || base.lark.appId,
      appSecret: bot.appSecret || base.lark.appSecret,
      ackEmojiType: base.lark.ackEmojiType,
    },
    allowedUsers: bot.allowedUsers?.length ? bot.allowedUsers : base.allowedUsers,
    workspaceRoot,
    workspaceConfirmed: bot.workspaceConfirmed || base.workspaceConfirmed,
    agent: bot.agent || base.agent,
  };
}

/** Whether a user is allowed to drive CodeMind. Empty list = allow all. */
export function isUserAllowed(config: BridgeConfig, userId: string): boolean {
  if (config.allowedUsers.length === 0) return true;
  return config.allowedUsers.includes(userId);
}
