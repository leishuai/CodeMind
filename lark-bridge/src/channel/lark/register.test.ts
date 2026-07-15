import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { registerApp, type RegisterDeps, type QrCodeInfo } from "./register.js";
import {
  REQUIRED_TENANT_SCOPES,
  REQUIRED_EVENTS,
  REQUIRED_CALLBACKS,
  buildScopeManifest,
} from "./scopes.js";
import type { BridgeConfig } from "../../config.js";

// The manual-fallback / auto-config log strings are localized (see i18n.ts).
// Force English so the assertions are deterministic regardless of the runner's
// timezone / AUTOMIND_LANG.
let prevLang: string | undefined;
beforeEach(() => {
  prevLang = process.env.AUTOMIND_LANG;
  process.env.AUTOMIND_LANG = "en";
});
afterEach(() => {
  if (prevLang === undefined) delete process.env.AUTOMIND_LANG;
  else process.env.AUTOMIND_LANG = prevLang;
});

function makeConfig(): BridgeConfig {
  return {
    lark: { appId: "", appSecret: "" },
    allowedUsers: [],
    automindBin: "/tmp/automind.sh",
    workspaceRoot: "/tmp",
    agent: "auto",
    pollIntervalMs: 2000,
  };
}

function makeDeps(overrides: Partial<RegisterDeps> = {}): {
  deps: RegisterDeps;
  logs: string[];
  qrs: QrCodeInfo[];
  addonsSeen: () => unknown;
} {
  const logs: string[] = [];
  const qrs: QrCodeInfo[] = [];
  let addonsSeen: unknown = null;
  const deps: RegisterDeps = {
    registerApp: async (options) => {
      addonsSeen = options.addons;
      options.onQRCodeReady({ url: "https://scan.example", expireIn: 300 });
      return {
        client_id: "cli_app",
        client_secret: "secret_xyz",
        user_info: { open_id: "ou_scanner" },
      };
    },
    renderQrCode: (info) => qrs.push(info),
    log: (m) => logs.push(m),
    ...overrides,
  };
  return { deps, logs, qrs, addonsSeen: () => addonsSeen };
}

describe("registerApp", () => {
  it("runs the device flow and returns credentials + scanning user", async () => {
    const { deps } = makeDeps();
    const result = await registerApp(makeConfig(), deps);
    expect(result.appId).toBe("cli_app");
    expect(result.appSecret).toBe("secret_xyz");
    expect(result.scannedUserOpenId).toBe("ou_scanner");
  });

  it("prefills the required scopes/events/callbacks into addons", async () => {
    const { deps, addonsSeen } = makeDeps();
    await registerApp(makeConfig(), deps);
    expect(addonsSeen()).toEqual({
      scopes: { tenant: REQUIRED_TENANT_SCOPES, user: [] },
      events: { items: { tenant: REQUIRED_EVENTS } },
      callbacks: { items: REQUIRED_CALLBACKS },
    });
  });

  it("renders the QR code for the user to scan", async () => {
    const { deps, qrs } = makeDeps();
    await registerApp(makeConfig(), deps);
    expect(qrs).toHaveLength(1);
    expect(qrs[0].url).toBe("https://scan.example");
  });

  it("prints the manual fallback manifest when autoConfig is absent", async () => {
    const { deps, logs } = makeDeps();
    const result = await registerApp(makeConfig(), deps);
    expect(result.autoConfigured).toBe(false);
    const joined = logs.join("\n");
    expect(joined).toContain("Batch import");
    expect(joined).toContain("im:message:send_as_bot");
  });

  it("skips the fallback when autoConfig succeeds", async () => {
    const autoConfig = vi.fn(async () => true);
    const { deps, logs } = makeDeps({ autoConfig });
    const result = await registerApp(makeConfig(), deps);
    expect(result.autoConfigured).toBe(true);
    expect(autoConfig).toHaveBeenCalledWith("cli_app", buildScopeManifest());
    const joined = logs.join("\n");
    expect(joined).not.toContain("Batch import");
    expect(joined).toContain("auto-configured");
  });

  it("falls back to manual import when autoConfig throws", async () => {
    const autoConfig = vi.fn(async () => {
      throw new Error("private API changed");
    });
    const { deps, logs } = makeDeps({ autoConfig });
    const result = await registerApp(makeConfig(), deps);
    expect(result.autoConfigured).toBe(false);
    expect(logs.join("\n")).toContain("Batch import");
  });
});

describe("buildScopeManifest", () => {
  it("includes the required scopes, events, and callbacks", () => {
    const manifest = buildScopeManifest();
    expect(manifest).toEqual({
      scopes: { tenant: REQUIRED_TENANT_SCOPES, user: [] },
      events: REQUIRED_EVENTS,
      callbacks: REQUIRED_CALLBACKS,
    });
  });
});
