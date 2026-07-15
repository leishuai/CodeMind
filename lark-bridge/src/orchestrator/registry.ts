/**
 * registry.ts — persistent multi-bot channel registry under
 * `~/.automind/channels/`. CodeMind supports MANY Feishu bots; each bot is one
 * daemon process bound to ONE project workspace, so a task always belongs to
 * exactly one bot (physical isolation prevents cross-bot mixups).
 *
 * Layout (design §multi-bot):
 *   ~/.automind/channels/
 *     registry.json              # the bot roster (this module)
 *     <botId>/session-map.json   # per-bot thread<->task map (persistent)
 *     <botId>/status.json        # per-bot runtime status (written by daemon)
 *     <botId>/.env               # per-bot credentials (optional convenience)
 *
 * Pure-ish: filesystem access is confined here and the base dir is injectable
 * so the module is unit-testable against a temp dir.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/** One registered Feishu bot. bot <-> workspace is 1:1; workspace <-> task 1:N. */
export interface BotRecord {
  /** Stable, filesystem-safe bot identifier (also the channel dir name). */
  botId: string;
  /** Human-readable name shown in `channel ls` / dashboard. */
  name: string;
  /** Feishu credentials; empty until the scan-to-register flow fills them. */
  appId: string;
  appSecret: string;
  /** The project this bot works on. Empty/unconfirmed => home-dir fallback. */
  workspaceRoot: string;
  /** Whether the user explicitly confirmed the workspace (vs. home fallback). */
  workspaceConfirmed: boolean;
  /** Feishu open_id allow-list for this bot; empty = allow all. */
  allowedUsers: string[];
  /** Default coding agent for this bot. */
  agent: string;
  createdAt: string;
  updatedAt: string;
}

export interface RegistryData {
  bots: BotRecord[];
}

/** Restrict bot ids to a safe charset so `<botId>` can never escape the dir. */
const SAFE_BOT_ID = /^[A-Za-z0-9._-]+$/;

export function isSafeBotId(botId: string): boolean {
  return (
    SAFE_BOT_ID.test(botId) &&
    botId !== "." &&
    botId !== ".." &&
    !botId.includes("..")
  );
}

/** Absolute path to the channels root (`~/.automind/channels` by default). */
export function channelsRoot(homeDir: string = os.homedir()): string {
  return path.join(homeDir, ".automind", "channels");
}

/**
 * Preserve a corrupt JSON file instead of silently overwriting it. Renames it to
 * a timestamped `<file>.corrupt-<ts>` sibling so the (possibly recoverable) data
 * is not lost when the caller falls back to an empty state. Best-effort: any
 * failure here is swallowed so it never blocks the daemon from continuing.
 */
export function backupCorruptFile(file: string): void {
  try {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    fs.renameSync(file, `${file}.corrupt-${stamp}`);
  } catch {
    // Ignore: preserving the backup is best-effort.
  }
}

/** Absolute path to a single bot's directory. */
export function botDir(botId: string, homeDir: string = os.homedir()): string {
  return path.join(channelsRoot(homeDir), botId);
}

/** The per-bot session-map file (persistent thread<->task map). */
export function sessionMapPath(botId: string, homeDir: string = os.homedir()): string {
  return path.join(botDir(botId, homeDir), "session-map.json");
}

/** The per-bot status file the daemon writes for the dashboard. */
export function statusPath(botId: string, homeDir: string = os.homedir()): string {
  return path.join(botDir(botId, homeDir), "status.json");
}

/**
 * File-backed multi-bot registry. `homeDir` is injectable so tests can point at
 * a temp directory without touching the real `~/.automind`.
 */
export class ChannelRegistry {
  private readonly home: string;

  constructor(homeDir: string = os.homedir()) {
    this.home = homeDir;
  }

  private registryFile(): string {
    return path.join(channelsRoot(this.home), "registry.json");
  }

  /** Read the roster; returns an empty roster when the file is missing/corrupt. */
  read(): RegistryData {
    const file = this.registryFile();
    let raw: string;
    try {
      raw = fs.readFileSync(file, "utf8");
    } catch {
      // Missing file is the normal first-run case: start with an empty roster.
      return { bots: [] };
    }
    try {
      const parsed = JSON.parse(raw) as Partial<RegistryData>;
      return { bots: Array.isArray(parsed.bots) ? parsed.bots : [] };
    } catch {
      // The file exists but is corrupt. Do NOT silently overwrite it: preserve
      // the bytes to a timestamped `.corrupt` backup so the roster is not lost.
      backupCorruptFile(file);
      return { bots: [] };
    }
  }

  private write(data: RegistryData): void {
    const dir = channelsRoot(this.home);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(this.registryFile(), JSON.stringify(data, null, 2), "utf8");
  }

  /** All registered bots, in creation order. */
  list(): BotRecord[] {
    return this.read().bots;
  }

  /** Look up one bot by id. */
  get(botId: string): BotRecord | undefined {
    return this.read().bots.find((b) => b.botId === botId);
  }

  /**
   * Create or update a bot record. Merges onto any existing record with the same
   * botId. Ensures the bot's directory exists. Returns the saved record.
   */
  upsert(
    record: Partial<BotRecord> & { botId: string },
  ): BotRecord {
    if (!isSafeBotId(record.botId)) {
      throw new Error(`unsafe botId: ${record.botId}`);
    }
    const data = this.read();
    const now = new Date().toISOString();
    const idx = data.bots.findIndex((b) => b.botId === record.botId);
    const existing = idx >= 0 ? data.bots[idx] : undefined;
    const merged: BotRecord = {
      botId: record.botId,
      name: record.name ?? existing?.name ?? record.botId,
      appId: record.appId ?? existing?.appId ?? "",
      appSecret: record.appSecret ?? existing?.appSecret ?? "",
      workspaceRoot: record.workspaceRoot ?? existing?.workspaceRoot ?? "",
      workspaceConfirmed:
        record.workspaceConfirmed ?? existing?.workspaceConfirmed ?? false,
      allowedUsers: record.allowedUsers ?? existing?.allowedUsers ?? [],
      agent: record.agent ?? existing?.agent ?? "auto",
      createdAt: existing?.createdAt ?? now,
      updatedAt: now,
    };
    if (idx >= 0) {
      data.bots[idx] = merged;
    } else {
      data.bots.push(merged);
    }
    this.write(data);
    fs.mkdirSync(botDir(record.botId, this.home), { recursive: true });
    return merged;
  }

  /** Remove a bot from the roster (does not delete its directory contents). */
  remove(botId: string): boolean {
    const data = this.read();
    const next = data.bots.filter((b) => b.botId !== botId);
    if (next.length === data.bots.length) return false;
    this.write({ bots: next });
    return true;
  }

  /** Absolute dir for a bot under this registry's home. */
  dirOf(botId: string): string {
    return botDir(botId, this.home);
  }

  /** Per-bot session-map path under this registry's home. */
  sessionMapOf(botId: string): string {
    return sessionMapPath(botId, this.home);
  }

  /** Per-bot status file path under this registry's home. */
  statusOf(botId: string): string {
    return statusPath(botId, this.home);
  }
}
