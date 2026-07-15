/**
 * channel-status.ts — per-bot runtime status file (design §multi-bot dashboard).
 * The daemon writes `~/.automind/channels/<botId>/status.json` on lifecycle
 * events (start, connect, disconnect/reconnect, stop) so `automind channel
 * dashboard` can show the live connection picture of every bot WITHOUT talking
 * to the daemon (it just reads files). Bridge stays a pure external front-end.
 */
import fs from "node:fs";
import path from "node:path";

export type ConnectionState = "starting" | "connected" | "reconnecting" | "stopped";

export interface ChannelStatus {
  botId: string;
  name: string;
  /** Current long-connection state. */
  connection: ConnectionState;
  /** OS pid of the running daemon (for the dashboard / stop). */
  pid: number;
  /** The workspace this bot is bound to (or the home-dir fallback). */
  workspaceRoot: string;
  workspaceConfirmed: boolean;
  /** Whether Feishu credentials are present (bot actually created). */
  hasCredentials: boolean;
  /** Reconnect attempts since the last clean session. */
  reconnects: number;
  /** Number of active harness tasks tracked in this bot's session map. */
  activeTasks: number;
  /**
   * The allow-list the RUNNING daemon actually enforces (design de-pollution
   * fix). The daemon loads its allow-list into memory once at startup and never
   * re-reads the registry, so the registry file can drift from what the process
   * enforces. Persisting the effective list here lets the dashboard show the
   * value in force instead of the (possibly stale) registry value. Optional so
   * older status files without the field still parse.
   */
  allowedUsers?: string[];
  updatedAt: string;
}

/** Write (atomically-ish) the status file for a bot. */
export function writeStatus(filePath: string, status: ChannelStatus): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, JSON.stringify(status, null, 2), "utf8");
  fs.renameSync(tmp, filePath);
}

/** Read a bot's status file, or null when missing/corrupt. */
export function readStatus(filePath: string): ChannelStatus | null {
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    return JSON.parse(raw) as ChannelStatus;
  } catch {
    return null;
  }
}

/** True when a pid is still alive (best-effort; used to detect stale status). */
export function isPidAlive(pid: number): boolean {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    // ESRCH => no such process; EPERM => exists but not ours (still alive).
    return (err as NodeJS.ErrnoException).code === "EPERM";
  }
}
