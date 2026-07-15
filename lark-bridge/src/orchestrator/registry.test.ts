import { describe, it, expect, beforeEach, afterEach } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { ChannelRegistry, isSafeBotId, channelsRoot } from "./registry.js";

let home: string;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "automind-registry-"));
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

describe("isSafeBotId", () => {
  it("accepts safe ids and rejects traversal", () => {
    expect(isSafeBotId("bot_1")).toBe(true);
    expect(isSafeBotId("pay-service.v2")).toBe(true);
    expect(isSafeBotId("../etc")).toBe(false);
    expect(isSafeBotId("..")).toBe(false);
    expect(isSafeBotId("a/b")).toBe(false);
  });
});

describe("ChannelRegistry", () => {
  it("returns an empty roster before anything is registered", () => {
    const reg = new ChannelRegistry(home);
    expect(reg.list()).toEqual([]);
    expect(reg.get("nope")).toBeUndefined();
  });

  it("upserts a bot, creates its directory, and persists to disk", () => {
    const reg = new ChannelRegistry(home);
    const saved = reg.upsert({ botId: "bot_1", name: "支付", appId: "cli_x" });
    expect(saved.botId).toBe("bot_1");
    expect(saved.appId).toBe("cli_x");
    expect(fs.existsSync(reg.dirOf("bot_1"))).toBe(true);
    // A fresh registry instance reads the same persisted data.
    const reg2 = new ChannelRegistry(home);
    expect(reg2.get("bot_1")?.name).toBe("支付");
    expect(fs.existsSync(path.join(channelsRoot(home), "registry.json"))).toBe(true);
  });

  it("merges partial updates onto an existing record without clobbering", () => {
    const reg = new ChannelRegistry(home);
    reg.upsert({ botId: "bot_1", name: "支付", appId: "cli_x", appSecret: "s" });
    reg.upsert({ botId: "bot_1", workspaceRoot: "/proj", workspaceConfirmed: true });
    const bot = reg.get("bot_1")!;
    expect(bot.appId).toBe("cli_x"); // preserved
    expect(bot.appSecret).toBe("s"); // preserved
    expect(bot.workspaceRoot).toBe("/proj"); // updated
    expect(bot.workspaceConfirmed).toBe(true);
  });

  it("supports multiple bots side by side", () => {
    const reg = new ChannelRegistry(home);
    reg.upsert({ botId: "bot_a", workspaceRoot: "/a" });
    reg.upsert({ botId: "bot_b", workspaceRoot: "/b" });
    expect(reg.list().map((b) => b.botId).sort()).toEqual(["bot_a", "bot_b"]);
    expect(reg.get("bot_a")?.workspaceRoot).toBe("/a");
    expect(reg.get("bot_b")?.workspaceRoot).toBe("/b");
  });

  it("removes a bot from the roster", () => {
    const reg = new ChannelRegistry(home);
    reg.upsert({ botId: "bot_1" });
    expect(reg.remove("bot_1")).toBe(true);
    expect(reg.get("bot_1")).toBeUndefined();
    expect(reg.remove("bot_1")).toBe(false);
  });

  it("rejects an unsafe botId on upsert", () => {
    const reg = new ChannelRegistry(home);
    expect(() => reg.upsert({ botId: "../evil" })).toThrow();
  });

  it("scopes session-map and status paths per bot", () => {
    const reg = new ChannelRegistry(home);
    expect(reg.sessionMapOf("bot_1")).toBe(
      path.join(channelsRoot(home), "bot_1", "session-map.json"),
    );
    expect(reg.statusOf("bot_1")).toBe(
      path.join(channelsRoot(home), "bot_1", "status.json"),
    );
  });

  it("backs up a corrupt registry file instead of silently overwriting it", () => {
    const reg = new ChannelRegistry(home);
    reg.upsert({ botId: "bot_1", name: "支付" });
    const file = path.join(channelsRoot(home), "registry.json");
    fs.writeFileSync(file, "{ this is not json", "utf8");
    // Reading tolerates the corruption (empty roster) ...
    const reg2 = new ChannelRegistry(home);
    expect(reg2.list()).toEqual([]);
    // ... but preserves the corrupt bytes to a `.corrupt-*` sibling.
    const backups = fs
      .readdirSync(channelsRoot(home))
      .filter((f) => f.startsWith("registry.json.corrupt-"));
    expect(backups).toHaveLength(1);
    const preserved = fs.readFileSync(
      path.join(channelsRoot(home), backups[0]),
      "utf8",
    );
    expect(preserved).toBe("{ this is not json");
  });
});
