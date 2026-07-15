import { describe, it, expect } from "vitest";
import { fetchAppName } from "./app-name.js";

/** Build a fake fetch that returns queued JSON responses in call order. */
function fakeFetch(responses: unknown[]): {
  fetchImpl: typeof fetch;
  calls: Array<{ url: string; init?: RequestInit }>;
} {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let i = 0;
  const fetchImpl = (async (url: unknown, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    const body = responses[i++];
    return { json: async () => body } as Response;
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

describe("fetchAppName", () => {
  it("returns null without credentials (no network call)", async () => {
    const { fetchImpl, calls } = fakeFetch([]);
    expect(await fetchAppName("", "", { fetchImpl })).toEqual({
      name: null,
      missingScope: false,
    });
    expect(await fetchAppName("cli_x", "", { fetchImpl })).toEqual({
      name: null,
      missingScope: false,
    });
    expect(calls).toHaveLength(0);
  });

  it("exchanges a tenant token then reads data.app.app_name", async () => {
    const { fetchImpl, calls } = fakeFetch([
      { code: 0, tenant_access_token: "t-abc" },
      { code: 0, data: { app: { app_name: "CodeMind 助手" } } },
    ]);
    const result = await fetchAppName("cli_x", "sec", { fetchImpl });
    expect(result).toEqual({ name: "CodeMind 助手", missingScope: false });
    expect(calls[0].url).toContain("/auth/v3/tenant_access_token/internal");
    expect(calls[1].url).toContain("/application/v6/applications/me");
    const authHeader = (calls[1].init?.headers as Record<string, string>)?.[
      "Authorization"
    ];
    expect(authHeader).toBe("Bearer t-abc");
  });

  it("returns null when the token exchange fails", async () => {
    const { fetchImpl, calls } = fakeFetch([{ code: 10003 }]);
    expect(await fetchAppName("cli_x", "sec", { fetchImpl })).toEqual({
      name: null,
      missingScope: false,
    });
    expect(calls).toHaveLength(1);
  });

  it("returns null when app_name is missing or empty", async () => {
    const { fetchImpl } = fakeFetch([
      { tenant_access_token: "t" },
      { data: { app: { app_name: "  " } } },
    ]);
    expect(await fetchAppName("cli_x", "sec", { fetchImpl })).toEqual({
      name: null,
      missingScope: false,
    });
  });

  it("flags missingScope on error code 210508", async () => {
    const { fetchImpl } = fakeFetch([
      { tenant_access_token: "t" },
      { code: 210508, msg: "no permission" },
    ]);
    expect(await fetchAppName("cli_x", "sec", { fetchImpl })).toEqual({
      name: null,
      missingScope: true,
    });
  });
});
