/**
 * app-name.ts — fetch a self-built Feishu app's own display name so the channel
 * launcher / dashboard can show the real app name instead of the internal bot
 * id (e.g. `CodeMind 助手` instead of `bot_mriugur2`).
 *
 * Two official OpenAPI calls (both stable):
 *   1. POST /open-apis/auth/v3/tenant_access_token/internal  (appId+appSecret)
 *   2. GET  /open-apis/application/v6/applications/me?lang=zh_cn
 *      Authorization: Bearer <tenant_access_token>
 *      -> data.app.app_name   (requires scope application:application:self_manage)
 *
 * `fetch` is injected so this is unit-testable without real credentials/network.
 * All failures are swallowed by the caller: the real name is a nice-to-have and
 * must never block connecting a bot.
 */

const FEISHU_OPEN_ORIGIN = "https://open.feishu.cn";

/**
 * Feishu error code returned by `applications/me` when the app lacks the
 * `application:application:self_manage` scope. We surface this specifically so
 * the launcher can nudge the user to re-authorize (bots created before this
 * scope existed will hit it until they publish a new version).
 */
const MISSING_SCOPE_CODE = 210508;

/** Options for {@link fetchAppName}. */
export interface FetchAppNameDeps {
  fetchImpl?: typeof fetch;
}

/**
 * Outcome of {@link fetchAppName}. `name` is the resolved app name (or null when
 * it couldn't be read); `missingScope` is true only when the read failed
 * specifically because the app is missing the self_manage scope.
 */
export interface FetchAppNameResult {
  name: string | null;
  missingScope: boolean;
}

interface TenantTokenResponse {
  code?: number;
  tenant_access_token?: string;
}

interface AppInfoResponse {
  code?: number;
  data?: { app?: { app_name?: string } };
}

/**
 * Resolve a self-built app's display name from its AppID + AppSecret. Returns
 * the trimmed app_name (or null when it cannot be determined), plus a
 * `missingScope` flag set when the failure is specifically a missing
 * self_manage scope (so the caller can prompt the user to re-authorize).
 * Bad creds / network / empty name all resolve to `{ name: null, missingScope: false }`.
 */
export async function fetchAppName(
  appId: string,
  appSecret: string,
  deps: FetchAppNameDeps = {},
): Promise<FetchAppNameResult> {
  if (!appId || !appSecret) return { name: null, missingScope: false };
  const doFetch = deps.fetchImpl ?? fetch;

  const tokenRes = await doFetch(
    `${FEISHU_OPEN_ORIGIN}/open-apis/auth/v3/tenant_access_token/internal`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json; charset=utf-8" },
      body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
    },
  );
  const tokenJson = (await tokenRes.json()) as TenantTokenResponse;
  const token = tokenJson.tenant_access_token;
  if (!token) return { name: null, missingScope: false };

  const infoRes = await doFetch(
    `${FEISHU_OPEN_ORIGIN}/open-apis/application/v6/applications/me?lang=zh_cn`,
    { headers: { Authorization: `Bearer ${token}` } },
  );
  const infoJson = (await infoRes.json()) as AppInfoResponse;
  if (infoJson.code === MISSING_SCOPE_CODE) {
    return { name: null, missingScope: true };
  }
  const name = infoJson.data?.app?.app_name?.trim();
  return { name: name ? name : null, missingScope: false };
}
