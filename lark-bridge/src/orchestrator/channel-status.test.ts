import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  writeStatus,
  readStatus,
  isPidAlive,
  type ChannelStatus,
} from "./channel-status.js";

function sampleStatus(overrides: Partial<ChannelStatus> = {}): ChannelStatus {
  return {
    botId: "bot_1",
    name: "支付服务",
    connection: "connected",
    pid: process.pid,
    workspaceRoot: "/projects/pay",
    workspaceConfirmed: true,
    hasCredentials: true,
    reconnects: 0,
    activeTasks: 2,
    updatedAt: new Date().toISOString(),
    ...overrides,
  };
}

describe("channel-status", () => {
  let dir: string;
  let file: string;

  beforeEach(() => {
    dir = fs.mkdtempSync(path.join(os.tmpdir(), "automind-status-"));
    file = path.join(dir, "bot_1", "status.json");
  });

  afterEach(() => {
    fs.rmSync(dir, { recursive: true, force: true });
  });

  it("writeStatus creates the parent dir and roundtrips via readStatus", () => {
    const status = sampleStatus();
    writeStatus(file, status);
    expect(fs.existsSync(file)).toBe(true);
    expect(readStatus(file)).toEqual(status);
  });

  it("writeStatus overwrites an existing status file", () => {
    writeStatus(file, sampleStatus({ connection: "starting" }));
    writeStatus(file, sampleStatus({ connection: "reconnecting", reconnects: 3 }));
    const read = readStatus(file);
    expect(read?.connection).toBe("reconnecting");
    expect(read?.reconnects).toBe(3);
  });

  it("readStatus returns null when the file is missing", () => {
    expect(readStatus(file)).toBeNull();
  });

  it("readStatus returns null for a corrupt file", () => {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, "{ not json", "utf8");
    expect(readStatus(file)).toBeNull();
  });
});

describe("isPidAlive", () => {
  it("detects the current process as alive", () => {
    expect(isPidAlive(process.pid)).toBe(true);
  });

  it("treats invalid pids as not alive", () => {
    expect(isPidAlive(0)).toBe(false);
    expect(isPidAlive(-1)).toBe(false);
  });

  it("reports a non-existent pid as not alive", () => {
    // Very high pid that is virtually guaranteed not to exist.
    expect(isPidAlive(2_147_483_646)).toBe(false);
  });
});
