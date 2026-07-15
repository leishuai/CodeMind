import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  autoConfigure,
  loadSessionCache,
  saveSessionCache,
  type StoredCookie,
} from "./auto-config.js";

// ---------------------------------------------------------------------------
// Fake Response helpers
// ---------------------------------------------------------------------------

function jsonResponse(
  body: unknown,
  init: { headers?: Record<string, string>; url?: string } = {},
): Response {
  return {
    ok: true,
    status: 200,
    url: init.url ?? "",
    headers: new Headers(init.headers),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function htmlResponse(html: string, url: string): Response {
  return {
    ok: true,
    status: 200,
    url,
    headers: new Headers(),
    json: async () => {
      throw new Error("not json");
    },
    text: async () => html,
  } as unknown as Response;
}

const VALID_COOKIE: StoredCookie = {
  name: "session",
  value: "abc",
  domain: "feishu.cn",
  path: "/",
  secure: true,
  httpOnly: true,
  hostOnly: false,
};

/**
 * Build a fake `fetch` that routes by pathname/method for the console-config
 * (and optionally QR-login) flows. Fully offline.
 */
function makeFetch(opts: {
  qr?: boolean;
  loginFail?: boolean;
  csrf?: string | null;
  scopeCatalog?: unknown;
} = {}): typeof fetch {
  const csrf = opts.csrf === undefined ? "tok" : opts.csrf;
  return (async (input: RequestInfo | URL): Promise<Response> => {
    const rawUrl = typeof input === "string" ? input : input.toString();
    const url = new URL(rawUrl);
    const p = url.pathname;

    // QR login
    if (p === "/accounts/qrlogin/init") {
      if (opts.loginFail) return jsonResponse({ code: 400, msg: "bad request" });
      return jsonResponse(
        { code: 0, data: { step_info: { token: "key" } } },
        { headers: { "x-flow-key": "flow123" } },
      );
    }
    if (p === "/accounts/qrlogin/polling") {
      return jsonResponse({
        code: 0,
        data: {
          next_step: "enter_app",
          step_info: { status: 2, cross_login_uri: "https://accounts.feishu.cn/cross" },
        },
      });
    }
    if (p === "/cross") {
      // Materialize a reusable cookie into the jar (as the real cross-login does).
      return jsonResponse(
        { code: 0 },
        { headers: { "set-cookie": "session=abc; Domain=.feishu.cn; Path=/; Secure" } },
      );
    }

    // Session validation (ask.feishu.cn)
    if (url.hostname === "ask.feishu.cn") {
      return htmlResponse("<html>welcome home</html>", rawUrl);
    }

    // Console page HTML (csrf extraction)
    if (p.endsWith("/auth") || /^\/app\/[^/]+$/.test(p)) {
      const html = csrf ? `<script>window.csrfToken="${csrf}";</script>` : "<html>no token</html>";
      return htmlResponse(html, rawUrl);
    }

    // /developers/v1/*
    if (p.includes("/scope/all/")) {
      return jsonResponse(
        opts.scopeCatalog ?? {
          code: 0,
          data: { scopes: [{ name: "im:message:send_as_bot", id: "scope1" }] },
        },
      );
    }
    if (p.includes("/scope/update/")) return jsonResponse({ code: 0 });
    if (p.includes("/safe_setting/update/")) return jsonResponse({ code: 0 });
    if (p.includes("/contact_range/")) {
      return jsonResponse({ code: 0, data: { contactRangeDetail: { members: [] } } });
    }
    if (p.includes("/app_version/list/")) {
      return jsonResponse({ code: 0, data: { versions: [] } });
    }
    if (p.includes("/app_version/create/")) {
      return jsonResponse({ code: 0, data: { versionId: "v1" } });
    }
    if (p.includes("/publish/commit/")) return jsonResponse({ code: 0 });

    return jsonResponse({ code: 0 });
  }) as unknown as typeof fetch;
}

describe("auto-config", () => {
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "automind-autoconfig-"));
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  describe("session cache", () => {
    it("loadSessionCache returns null when file is missing", async () => {
      const cachePath = path.join(tmpDir, "missing.json");
      expect(await loadSessionCache(cachePath)).toBeNull();
    });

    it("loadSessionCache returns null when all cookies are expired", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      fs.writeFileSync(
        cachePath,
        JSON.stringify({ cookies: [{ ...VALID_COOKIE, expiresAt: Date.now() - 1000 }] }),
        "utf8",
      );
      expect(await loadSessionCache(cachePath)).toBeNull();
    });

    it("loadSessionCache returns cached cookies when not expired", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      fs.writeFileSync(cachePath, JSON.stringify({ cookies: [VALID_COOKIE] }), "utf8");
      const cached = await loadSessionCache(cachePath);
      expect(cached?.cookies).toHaveLength(1);
      expect(cached?.cookies[0].name).toBe("session");
    });

    it("saveSessionCache creates parent directories and writes cookie array", async () => {
      const cachePath = path.join(tmpDir, "nested", "session.json");
      await saveSessionCache(cachePath, { cookies: [VALID_COOKIE] });
      expect(fs.existsSync(cachePath)).toBe(true);
      const content = JSON.parse(fs.readFileSync(cachePath, "utf8"));
      expect(Array.isArray(content.cookies)).toBe(true);
      expect(content.cookies[0].name).toBe("session");
    });

    it("saveSessionCache prunes expired cookies", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      await saveSessionCache(cachePath, {
        cookies: [VALID_COOKIE, { ...VALID_COOKIE, name: "old", expiresAt: Date.now() - 1000 }],
      });
      const content = JSON.parse(fs.readFileSync(cachePath, "utf8"));
      expect(content.cookies).toHaveLength(1);
      expect(content.cookies[0].name).toBe("session");
    });
  });

  describe("autoConfigure", () => {
    it("fails when session acquisition fails", async () => {
      const result = await autoConfigure(
        "cli_test",
        { scopes: { tenant: [], user: [] } },
        {
          fetchImpl: makeFetch({ loginFail: true }),
          renderQrCode: vi.fn(),
          log: vi.fn(),
          sessionCachePath: path.join(tmpDir, "session.json"),
        },
      );
      expect(result.success).toBe(false);
    });

    it("fails when console page has no csrf token", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      await saveSessionCache(cachePath, { cookies: [VALID_COOKIE] });

      const result = await autoConfigure(
        "cli_test",
        { scopes: { tenant: [], user: [] } },
        {
          fetchImpl: makeFetch({ csrf: null }),
          renderQrCode: vi.fn(),
          log: vi.fn(),
          sessionCachePath: cachePath,
        },
      );
      expect(result.success).toBe(false);
    });

    it("succeeds using a cached session (console-config path)", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      await saveSessionCache(cachePath, { cookies: [VALID_COOKIE] });

      const renderQrCode = vi.fn();
      const result = await autoConfigure(
        "cli_test",
        {
          scopes: { tenant: ["im:message:send_as_bot"], user: [] },
          events: ["im.message.receive_v1"],
        },
        {
          fetchImpl: makeFetch(),
          renderQrCode,
          log: vi.fn(),
          sessionCachePath: cachePath,
        },
      );
      expect(result.success).toBe(true);
      // Cached session ⇒ no QR render.
      expect(renderQrCode).not.toHaveBeenCalled();
    });

    it("succeeds via QR login and renders the compact qr payload", async () => {
      const renderQrCode = vi.fn();
      const result = await autoConfigure(
        "cli_test",
        { scopes: { tenant: ["im:message:send_as_bot"], user: [] } },
        {
          fetchImpl: makeFetch({ qr: true }),
          renderQrCode,
          log: vi.fn(),
          sessionCachePath: path.join(tmpDir, "session.json"),
        },
      );
      expect(result.success).toBe(true);
      expect(renderQrCode).toHaveBeenCalledWith(
        JSON.stringify({ qrlogin: { token: "key" } }),
        expect.any(String),
      );
    });

    it("treats scope-catalog failures as non-fatal and still publishes", async () => {
      const cachePath = path.join(tmpDir, "session.json");
      await saveSessionCache(cachePath, { cookies: [VALID_COOKIE] });

      const result = await autoConfigure(
        "cli_test",
        { scopes: { tenant: ["im:message:send_as_bot"], user: [] } },
        {
          // Scope catalog returns an API error code — must not abort the flow.
          fetchImpl: makeFetch({ scopeCatalog: { code: 1254001, msg: "scope error" } }),
          renderQrCode: vi.fn(),
          log: vi.fn(),
          sessionCachePath: cachePath,
        },
      );
      expect(result.success).toBe(true);
    });
  });
});
