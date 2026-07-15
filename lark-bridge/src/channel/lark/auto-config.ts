/**
 * auto-config.ts — Feishu QR-login Web session + open-platform console
 * configuration. Self-contained: no external runtime import.
 *
 * Flow:
 *   1. prepareSession: load cached cookies -> validate; otherwise run the QR
 *      login (init -> poll -> follow cross-login/redirect) and cache cookies.
 *   2. console config: load the console app page with those cookies, extract
 *      `window.csrfToken` (following any open.larkoffice.com redirect), map the
 *      manifest scopes to open-platform scope IDs and call `/developers/v1/*`
 *      (scope update is non-fatal), then create + publish an app version.
 */
import fs from "node:fs";
import path from "node:path";
import qrcode from "qrcode-terminal";
import { detectLang, messages } from "../../i18n.js";

export interface AutoConfigResult {
  success: boolean;
  reason?: string;
}

/** A single persisted cookie record (cookie-jar format). */
export interface StoredCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure: boolean;
  httpOnly: boolean;
  hostOnly: boolean;
  expiresAt?: number;
  sameSite?: string;
}

/** Session cache is an array of cookie records. */
export interface SessionCache {
  cookies: StoredCookie[];
}

export interface AutoConfigDeps {
  fetchImpl: typeof fetch;
  /**
   * Render the login QR. `qrContent` is the compact payload actually encoded
   * into the QR (`{"qrlogin":{"token":...}}`); `displayUrl` is a human display
   * string (login hint / URL) printed as a text fallback.
   */
  renderQrCode: (qrContent: string, displayUrl: string) => void;
  log: (message: string) => void;
  sessionCachePath: string;
}

const FEISHU_ACCOUNTS_ORIGIN = "https://accounts.feishu.cn";
const ASK_FEISHU_ORIGIN = "https://ask.feishu.cn";
const FEISHU_APP_ID = "12";
const FEISHU_COMMON_HEADERS: Record<string, string> = {
  "x-api-version": "1.0.28",
  "x-device-info":
    "device_id=0;device_name=Chrome;device_os=Mac;device_model=Chrome;lark_version=;channel=Release;package_name=feishu;tt_app_id=1658;is_dpop_support=true;is_iframe=false",
  "x-locale": "zh-CN",
  "x-terminal-type": "2",
};
const DEFAULT_BROWSER_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36";
const REDIRECT_URL = "http://127.0.0.1:9768/callback";
const QR_MAX_WAIT_MS = 120_000;
const QR_POLL_INTERVAL_MS = 1500;

function defaultDeps(): AutoConfigDeps {
  const t = messages(detectLang());
  return {
    fetchImpl: fetch,
    renderQrCode: (qrContent, displayUrl) => {
      // Encode the compact `{"qrlogin":{"token"}}` payload so the code stays
      // small; print the URL/hint as a text fallback.
      qrcode.generate(qrContent, { small: true });
      console.log(t.autoConfigSessionPrompt);
      console.log(`[channel] ${displayUrl}`);
    },
    log: (msg) => console.log(msg),
    sessionCachePath: path.join(
      process.env.HOME || process.env.USERPROFILE || "/tmp",
      ".automind",
      "channels",
      "feishu-session.json",
    ),
  };
}

// ---------------------------------------------------------------------------
// Session cache (cookie-array format)
// ---------------------------------------------------------------------------

export async function loadSessionCache(filePath: string): Promise<SessionCache | null> {
  try {
    const raw = fs.readFileSync(filePath, "utf8");
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const cookies = (parsed as { cookies?: unknown }).cookies;
    if (!Array.isArray(cookies)) return null;
    const pruned = pruneExpiredCookies(cookies.filter(isStoredCookieRecord));
    if (pruned.length === 0) return null;
    return { cookies: pruned };
  } catch {
    return null;
  }
}

export async function saveSessionCache(filePath: string, cache: SessionCache): Promise<void> {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(
    filePath,
    JSON.stringify({ cookies: pruneExpiredCookies(cache.cookies) }, null, 2),
    "utf8",
  );
}

// ---------------------------------------------------------------------------
// Cookie jar
// ---------------------------------------------------------------------------

const finalUrlMap = new WeakMap<Response, string>();

class MutableCookieJar {
  private cookies: StoredCookie[];

  constructor(cookies: StoredCookie[]) {
    this.cookies = pruneExpiredCookies(cookies);
  }

  toJSON(): StoredCookie[] {
    this.cookies = pruneExpiredCookies(this.cookies);
    return this.cookies.map((cookie) => ({ ...cookie }));
  }

  async fetchTextWithUrl(
    fetcher: typeof fetch,
    url: string,
  ): Promise<{ text: string; finalUrl: string }> {
    const response = await this.fetchRaw(fetcher, url, { method: "GET" });
    return { text: await response.text(), finalUrl: finalResponseUrl(response, url) };
  }

  async fetchRaw(
    fetcher: typeof fetch,
    url: string,
    init: RequestInit = {},
    maxHops = 10,
  ): Promise<Response> {
    let current = url;
    let referer: string | undefined;
    for (let hop = 0; hop <= maxHops; hop += 1) {
      const headers = new Headers(init.headers);
      const cookieHeader = getCookieHeader(this.cookies, current);
      if (cookieHeader) headers.set("cookie", cookieHeader);
      if (!headers.has("user-agent")) headers.set("user-agent", DEFAULT_BROWSER_USER_AGENT);
      if (referer && !headers.has("referer")) headers.set("referer", referer);
      const response = await fetcher(current, { ...init, headers, redirect: "manual" });
      this.loadFromResponse(current, response.headers);
      if (response.status >= 300 && response.status < 400) {
        const location = response.headers.get("location");
        if (!location) return response;
        referer = current;
        current = new URL(location, current).toString();
        continue;
      }
      finalUrlMap.set(response, current);
      return response;
    }
    throw new Error("Too many redirects while accessing open platform");
  }

  private loadFromResponse(responseUrl: string, headers: Headers): void {
    const rawSetCookies =
      typeof headers.getSetCookie === "function"
        ? headers.getSetCookie()
        : splitSetCookieHeader(headers.get("set-cookie"));
    for (const raw of rawSetCookies) {
      const cookie = parseSetCookie(responseUrl, raw);
      if (!cookie) continue;
      const idx = this.cookies.findIndex(
        (item) =>
          item.name === cookie.name && item.domain === cookie.domain && item.path === cookie.path,
      );
      if (cookie.expiresAt !== undefined && cookie.expiresAt <= Date.now()) {
        if (idx >= 0) this.cookies.splice(idx, 1);
        continue;
      }
      if (idx >= 0) this.cookies[idx] = cookie;
      else this.cookies.push(cookie);
    }
    this.cookies = pruneExpiredCookies(this.cookies);
  }
}

// ---------------------------------------------------------------------------
// QR login
// ---------------------------------------------------------------------------

class FeishuWebSessionError extends Error {}

async function initFeishuQrLogin(
  session: MutableCookieJar,
  fetcher: typeof fetch,
  authorizeUrl: string,
): Promise<{ flowKey: string; token: string }> {
  const endpoint = `${FEISHU_ACCOUNTS_ORIGIN}/accounts/qrlogin/init?_r${
    10000 + Math.floor(Math.random() * 80000)
  }=${Date.now()}`;
  const response = await session.fetchRaw(fetcher, endpoint, {
    method: "POST",
    headers: {
      ...FEISHU_COMMON_HEADERS,
      "x-app-id": FEISHU_APP_ID,
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({ biz_type: null, redirect_uri: authorizeUrl }),
  });
  const data = (await response.json()) as unknown;
  assertFeishuApiOk(data, "Feishu QR init failed");
  const stepInfo = asRecord(asRecord(asRecord(data).data).step_info);
  const token = pickString(stepInfo, ["token"]);
  const flowKey = response.headers.get("x-flow-key") ?? "";
  if (!flowKey || !token) {
    throw new FeishuWebSessionError("Feishu QR init missing flow key or token");
  }
  return { flowKey, token };
}

async function pollFeishuQrLogin(
  session: MutableCookieJar,
  fetcher: typeof fetch,
  flowKey: string,
): Promise<{ nextStep: string | null; status: number | null; crossLoginUri: string | null }> {
  const endpoint = `${FEISHU_ACCOUNTS_ORIGIN}/accounts/qrlogin/polling?_r${
    10000 + Math.floor(Math.random() * 80000)
  }=${Date.now()}`;
  const response = await session.fetchRaw(fetcher, endpoint, {
    method: "POST",
    headers: {
      ...FEISHU_COMMON_HEADERS,
      "x-app-id": FEISHU_APP_ID,
      "x-flow-key": flowKey,
      accept: "application/json",
      "content-type": "application/json",
    },
    body: JSON.stringify({ biz_type: null }),
  });
  const data = (await response.json()) as unknown;
  assertFeishuApiOk(data, "Feishu QR polling failed");
  const payload = asRecord(asRecord(data).data);
  const stepInfo = asRecord(payload.step_info);
  return {
    nextStep: pickString(payload, ["next_step"]) ?? null,
    status: typeof stepInfo.status === "number" ? stepInfo.status : null,
    crossLoginUri: pickString(stepInfo, ["cross_login_uri"]) ?? null,
  };
}

async function validateFeishuWebSession(
  cookies: StoredCookie[],
  fetcher: typeof fetch,
): Promise<boolean> {
  if (cookies.length === 0) return false;
  const session = new MutableCookieJar(cookies);
  try {
    const response = await session.fetchRaw(fetcher, `${ASK_FEISHU_ORIGIN}/`, { method: "GET" });
    if (!response.ok) return false;
    const text = await response.text();
    return !isFeishuLoginLikeValue(text);
  } catch {
    return false;
  }
}

async function loginFeishuWebSession(
  deps: AutoConfigDeps,
  fetcher: typeof fetch,
): Promise<StoredCookie[]> {
  const session = new MutableCookieJar([]);
  const redirectUrl = `${ASK_FEISHU_ORIGIN}/`;
  const qrInit = await initFeishuQrLogin(session, fetcher, redirectUrl);
  deps.renderQrCode(buildFeishuQrPayload(qrInit.token), redirectUrl);

  const start = Date.now();
  for (;;) {
    if (Date.now() - start > QR_MAX_WAIT_MS) {
      throw new FeishuWebSessionError("等待飞书扫码超时");
    }
    const poll = await pollFeishuQrLogin(session, fetcher, qrInit.flowKey);
    if (poll.nextStep === "enter_app") {
      if (poll.crossLoginUri) {
        await session.fetchRaw(fetcher, poll.crossLoginUri, { method: "GET" });
      }
      await session.fetchRaw(fetcher, redirectUrl, { method: "GET" });
      const cookies = session.toJSON();
      if (!(await validateFeishuWebSession(cookies, fetcher))) {
        throw new FeishuWebSessionError("飞书扫码已完成，但没有拿到可复用的 Web session");
      }
      return cookies;
    }
    if (poll.status === 5) {
      throw new FeishuWebSessionError("二维码已过期");
    }
    await sleep(QR_POLL_INTERVAL_MS);
  }
}

async function prepareSession(deps: AutoConfigDeps): Promise<StoredCookie[] | null> {
  const t = messages(detectLang());
  const cached = await loadSessionCache(deps.sessionCachePath);
  if (cached && cached.cookies.length > 0 && (await validateFeishuWebSession(cached.cookies, deps.fetchImpl))) {
    deps.log(t.autoConfigSessionCacheHit);
    return cached.cookies;
  }
  try {
    const cookies = await loginFeishuWebSession(deps, deps.fetchImpl);
    await saveSessionCache(deps.sessionCachePath, { cookies });
    return cookies;
  } catch (err) {
    if (err instanceof FeishuWebSessionError && /超时/.test(err.message)) {
      deps.log(t.autoConfigSessionTimeout);
    } else {
      deps.log(`[lark-bridge] Failed to acquire Feishu session: ${safeErrorMessage(err)}`);
    }
    return null;
  }
}

// ---------------------------------------------------------------------------
// Open platform console configuration
// ---------------------------------------------------------------------------

class OpenPlatformApiError extends Error {}

interface ScopeEntry {
  name: string;
  id: string;
  bucket?: "tenant" | "user";
}

interface MappedScopes {
  tenantScopeIds: string[];
  userScopeIds: string[];
  missingTenantScopes: string[];
  missingUserScopes: string[];
}

async function configureConsole(
  deps: AutoConfigDeps,
  appId: string,
  cookies: StoredCookie[],
  manifest: Record<string, unknown>,
): Promise<AutoConfigResult> {
  const t = messages(detectLang());
  const session = new MutableCookieJar(cookies);
  const defaultOrigin = "https://open.feishu.cn";
  const defaultAppHome = `${defaultOrigin}/app/${appId}`;

  // Feishu tenants can redirect the console to open.larkoffice.com; keep using
  // the FINAL origin for all subsequent calls, referer and csrf.
  let csrfToken: string | null = null;
  let apiOrigin = defaultOrigin;
  let appHome = defaultAppHome;
  try {
    const authPage = await session.fetchTextWithUrl(deps.fetchImpl, `${defaultAppHome}/auth`);
    apiOrigin = new URL(authPage.finalUrl).origin;
    appHome = `${apiOrigin}/app/${appId}`;
    csrfToken = extractOpenPlatformCsrfToken(authPage.text);
    if (!csrfToken) {
      const homePage = await session.fetchTextWithUrl(deps.fetchImpl, appHome);
      apiOrigin = new URL(homePage.finalUrl).origin;
      appHome = `${apiOrigin}/app/${appId}`;
      csrfToken = extractOpenPlatformCsrfToken(homePage.text);
    }
  } catch (err) {
    return { success: false, reason: `读取开放平台页面失败: ${safeErrorMessage(err)}` };
  }
  if (!csrfToken) {
    return {
      success: false,
      reason: "开放平台页面没有返回 window.csrfToken；可能需要在浏览器完成开放平台登录",
    };
  }
  const token = csrfToken;

  const postJson = async (apiPath: string, body?: unknown): Promise<unknown> => {
    const url = `${apiOrigin}${apiPath}`;
    const response = await session.fetchRaw(deps.fetchImpl, url, {
      method: "POST",
      headers: {
        accept: "application/json, text/plain, */*",
        origin: apiOrigin,
        referer: appHome,
        "x-csrf-token": token,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data: unknown = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      throw new OpenPlatformApiError(`HTTP ${response.status} ${apiPath}`);
    }
    const record = asRecord(data);
    if (typeof record.code === "number" && record.code !== 0) {
      throw new OpenPlatformApiError(
        `code=${record.code} msg=${pickString(record, ["msg", "message"]) ?? ""}`,
      );
    }
    return data;
  };

  // Scope catalog + mapping. Scope failures are NON-FATAL (warn + continue).
  try {
    const allScopesPayload = await postJson(`/developers/v1/scope/all/${appId}`);
    const catalog = extractOpenPlatformScopeEntries(allScopesPayload);
    const mapped = mapManifestScopesToOpenPlatformIds(manifest, catalog);
    const importedScopeCount = mapped.tenantScopeIds.length + mapped.userScopeIds.length;
    if (importedScopeCount > 0) {
      try {
        await postJson(`/developers/v1/scope/update/${appId}`, buildScopeUpdatePayload(appId, mapped));
        deps.log(t.autoConfigStepSuccess(`权限配置 (${appId})`));
      } catch (err) {
        deps.log(t.autoConfigStepFailed(`权限配置 (${appId}): ${safeErrorMessage(err)}`));
      }
    }
  } catch (err) {
    // Scope catalog read failure is also non-fatal — keep configuring.
    deps.log(t.autoConfigStepFailed(`权限配置 (${appId}): ${safeErrorMessage(err)}`));
  }

  try {
    await postJson(`/developers/v1/safe_setting/update/${appId}`, buildSafeSettingPayload(appId));
    const contactRange = await postJson(`/developers/v1/contact_range/${appId}`, {});
    const visibleMemberIds = extractContactRangeMemberIds(contactRange);
    const versionList = await postJson(`/developers/v1/app_version/list/${appId}`, {});
    const appVersion = nextAppVersion(versionList);
    const created = await postJson(
      `/developers/v1/app_version/create/${appId}`,
      buildAppVersionCreatePayload(appVersion, visibleMemberIds),
    );
    const versionId = extractVersionId(created);
    if (versionId) {
      await postJson(`/developers/v1/publish/commit/${appId}/${versionId}`, { clientId: appId });
    }
    deps.log(t.autoConfigStepSuccess(`版本发布 (${appId}@${appVersion})`));
    return { success: true };
  } catch (err) {
    deps.log(t.autoConfigStepFailed(`版本发布 (${appId})`));
    return { success: false, reason: `开放平台自动配置失败: ${safeErrorMessage(err)}` };
  }
}

// ---------------------------------------------------------------------------
// Public entry
// ---------------------------------------------------------------------------

export async function autoConfigure(
  appId: string,
  manifest: Record<string, unknown>,
  deps?: Partial<AutoConfigDeps>,
): Promise<AutoConfigResult> {
  const resolved: AutoConfigDeps = { ...defaultDeps(), ...deps };

  const cookies = await prepareSession(resolved);
  if (!cookies) {
    return { success: false, reason: "无法获取飞书控制台 session" };
  }

  return configureConsole(resolved, appId, cookies, manifest);
}

// ---------------------------------------------------------------------------
// Payload builders / mappers
// ---------------------------------------------------------------------------

function buildFeishuQrPayload(token: string): string {
  return JSON.stringify({ qrlogin: { token } });
}

function extractOpenPlatformCsrfToken(html: string): string | null {
  const match =
    html.match(/\bwindow\.csrfToken\s*=\s*(['"])([^'"]+)\1/) ??
    html.match(/\bcsrfToken\s*:\s*(['"])([^'"]+)\1/);
  return match?.[2] ?? null;
}

function extractOpenPlatformScopeEntries(payload: unknown): ScopeEntry[] {
  const out: ScopeEntry[] = [];
  collectScopeEntries(payload, undefined, out);
  const seen = new Set<string>();
  return out.filter((entry) => {
    const key = `${entry.bucket ?? "any"}:${entry.name}:${entry.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function collectScopeEntries(
  value: unknown,
  bucket: "tenant" | "user" | undefined,
  out: ScopeEntry[],
): void {
  if (Array.isArray(value)) {
    for (const item of value) collectScopeEntries(item, bucket, out);
    return;
  }
  if (!value || typeof value !== "object") return;
  const record = value as Record<string, unknown>;
  const name = pickString(record, ["scope_name", "scopeName", "name", "key", "scopeKey"]);
  const id = pickString(record, ["id", "scope_id", "scopeId", "scopeID"]);
  if (name && id) out.push({ name, id, bucket });
  for (const [key, child] of Object.entries(record)) {
    const nextBucket: "tenant" | "user" | undefined = /user/i.test(key)
      ? "user"
      : /app|client|tenant/i.test(key)
        ? "tenant"
        : bucket;
    if (child && typeof child === "object") collectScopeEntries(child, nextBucket, out);
  }
}

function mapManifestScopesToOpenPlatformIds(
  manifest: Record<string, unknown>,
  catalog: ScopeEntry[],
): MappedScopes {
  const scopes = asRecord(manifest.scopes);
  const tenant = uniqueStrings(toStringArray(scopes.tenant));
  const user = uniqueStrings(toStringArray(scopes.user));
  const tenantMapped = mapScopeIds(tenant, catalog, "tenant");
  const userMapped = mapScopeIds(user, catalog, "user");
  return {
    tenantScopeIds: tenantMapped.ids,
    userScopeIds: userMapped.ids,
    missingTenantScopes: tenantMapped.missing,
    missingUserScopes: userMapped.missing,
  };
}

function mapScopeIds(
  scopeNames: string[],
  catalog: ScopeEntry[],
  bucket: "tenant" | "user",
): { ids: string[]; missing: string[] } {
  const ids: string[] = [];
  const missing: string[] = [];
  for (const scopeName of scopeNames) {
    const matched =
      catalog.find((entry) => entry.name === scopeName && entry.bucket === bucket) ??
      catalog.find((entry) => entry.name === scopeName && entry.bucket === undefined) ??
      catalog.find((entry) => entry.name === scopeName);
    if (matched) ids.push(matched.id);
    else missing.push(scopeName);
  }
  return { ids: uniqueStrings(ids), missing };
}

function buildScopeUpdatePayload(appId: string, mapped: MappedScopes): Record<string, unknown> {
  return {
    clientId: appId,
    appScopeIDs: mapped.tenantScopeIds,
    userScopeIDs: mapped.userScopeIds,
    scopeIds: [],
    operation: "add",
    isDeveloperPanel: true,
  };
}

function buildSafeSettingPayload(appId: string): Record<string, unknown> {
  return { clientId: appId, redirectURL: [REDIRECT_URL] };
}

function buildAppVersionCreatePayload(
  appVersion: string,
  visibleMemberIds: string[] = [],
): Record<string, unknown> {
  return {
    appVersion,
    mobileDefaultAbility: "bot",
    pcDefaultAbility: "bot",
    changeLog: "Init version",
    visibleSuggest: { departments: [], members: visibleMemberIds, groups: [], isAll: 0 },
    applyReasonConfig: {
      apiPrivilegeNeedReason: true,
      contactPrivilegeNeedReason: true,
      dataPrivilegeReasonMap: {},
      visibleScopeNeedReason: true,
      apiPrivilegeReasonMap: {},
      contactPrivilegeReason: "",
      isDataPrivilegeExpandMap: {},
      visibleScopeReason: "",
      dataPrivilegeNeedReason: true,
      isAutoAudit: false,
      isContactExpand: false,
    },
    b2cShareSuggest: false,
    autoPublish: false,
    remark: "Personal AI assistant for self use",
    blackVisibleSuggest: { departments: [], members: [], groups: [], isAll: 0 },
  };
}

function nextAppVersion(payload: unknown): string {
  const data = asRecord(asRecord(payload).data);
  const versions = Array.isArray(data.versions) ? data.versions : [];
  const published = versions
    .map((item) => asRecord(item))
    .filter((item) => item.versionStatus === 2)
    .map((item) => pickString(item, ["appVersion"]))
    .filter((version): version is string => Boolean(version));
  if (published.length === 0) return "0.0.1";
  const latest = published[0];
  const parts = latest.split(".").map((part) => Number.parseInt(part, 10));
  if (parts.length < 3 || parts.some((part) => !Number.isFinite(part))) return "0.0.1";
  parts[parts.length - 1] += 1;
  return parts.join(".");
}

function extractContactRangeMemberIds(payload: unknown): string[] {
  const data = asRecord(asRecord(payload).data);
  const detail = asRecord(data.contactRangeDetail);
  const members = Array.isArray(detail.members) ? detail.members : [];
  return uniqueStrings(
    members.map((item) => pickString(asRecord(item), ["id"])).filter((id): id is string => Boolean(id)),
  );
}

function extractVersionId(payload: unknown): string | undefined {
  const direct = pickString(asRecord(payload), ["versionId", "version_id", "id"]);
  if (direct) return direct;
  const data = asRecord(asRecord(payload).data);
  return (
    pickString(data, ["versionId", "version_id", "id"]) ??
    pickString(asRecord(data.appVersion), ["versionId", "version_id", "id"])
  );
}

// ---------------------------------------------------------------------------
// Cookie helpers
// ---------------------------------------------------------------------------

function getCookieHeader(cookies: StoredCookie[], requestUrl: string): string {
  const url = new URL(requestUrl);
  return pruneExpiredCookies(cookies)
    .filter((cookie) => {
      if (cookie.secure && url.protocol !== "https:") return false;
      if (!domainMatches(url.hostname, cookie)) return false;
      return pathMatches(url.pathname || "/", cookie.path || "/");
    })
    .sort((a, b) => b.path.length - a.path.length)
    .map((cookie) => `${cookie.name}=${cookie.value}`)
    .join("; ");
}

function domainMatches(hostname: string, cookie: StoredCookie): boolean {
  const host = hostname.toLowerCase();
  const domain = cookie.domain.replace(/^\./, "").toLowerCase();
  if (cookie.hostOnly) return host === domain;
  return host === domain || host.endsWith(`.${domain}`);
}

function pathMatches(requestPath: string, cookiePath: string): boolean {
  if (requestPath === cookiePath) return true;
  if (!requestPath.startsWith(cookiePath)) return false;
  return cookiePath.endsWith("/") || requestPath[cookiePath.length] === "/";
}

function splitSetCookieHeader(header: string | null): string[] {
  if (!header) return [];
  const parts: string[] = [];
  let start = 0;
  let inExpires = false;
  for (let i = 0; i < header.length; i += 1) {
    const slice = header.slice(Math.max(0, i - 8), i + 1).toLowerCase();
    if (slice.endsWith("expires=")) inExpires = true;
    if (inExpires && header[i] === ";") inExpires = false;
    if (!inExpires && header[i] === ",") {
      parts.push(header.slice(start, i).trim());
      start = i + 1;
    }
  }
  parts.push(header.slice(start).trim());
  return parts.filter(Boolean);
}

function parseSetCookie(responseUrl: string, header: string): StoredCookie | null {
  const url = new URL(responseUrl);
  const parts = header.split(";").map((part) => part.trim()).filter(Boolean);
  const first = parts.shift();
  if (!first) return null;
  const eq = first.indexOf("=");
  if (eq <= 0) return null;
  const cookie: StoredCookie = {
    name: first.slice(0, eq),
    value: first.slice(eq + 1),
    domain: url.hostname,
    path: "/",
    secure: false,
    httpOnly: false,
    hostOnly: true,
  };
  for (const part of parts) {
    const partEq = part.indexOf("=");
    const key = (partEq >= 0 ? part.slice(0, partEq) : part).trim().toLowerCase();
    const value = partEq >= 0 ? part.slice(partEq + 1).trim() : "";
    if (key === "domain" && value) {
      cookie.domain = value.toLowerCase();
      cookie.hostOnly = false;
    } else if (key === "path" && value) {
      cookie.path = value;
    } else if (key === "secure") {
      cookie.secure = true;
    } else if (key === "httponly") {
      cookie.httpOnly = true;
    } else if (key === "expires" && value) {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) cookie.expiresAt = parsed;
    } else if (key === "max-age" && value) {
      const seconds = Number(value);
      if (Number.isFinite(seconds)) cookie.expiresAt = Date.now() + seconds * 1000;
    } else if (key === "samesite" && value) {
      cookie.sameSite = value;
    }
  }
  return cookie;
}

function isStoredCookieRecord(value: unknown): value is StoredCookie {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const cookie = value as Record<string, unknown>;
  return (
    typeof cookie.name === "string" &&
    typeof cookie.value === "string" &&
    typeof cookie.domain === "string" &&
    typeof cookie.path === "string" &&
    typeof cookie.secure === "boolean" &&
    typeof cookie.httpOnly === "boolean" &&
    typeof cookie.hostOnly === "boolean"
  );
}

function pruneExpiredCookies(cookies: StoredCookie[]): StoredCookie[] {
  const now = Date.now();
  return cookies.filter((cookie) => cookie.expiresAt === undefined || cookie.expiresAt > now);
}

// ---------------------------------------------------------------------------
// Generic helpers
// ---------------------------------------------------------------------------

function finalResponseUrl(response: Response, fallbackUrl: string): string {
  return finalUrlMap.get(response) ?? response.url ?? fallbackUrl;
}

function isFeishuLoginLikeValue(value: string): boolean {
  const normalized = value.toLowerCase();
  return (
    normalized.includes("/accounts/") ||
    normalized.includes("/login") ||
    normalized.includes("qrlogin")
  );
}

function assertFeishuApiOk(payload: unknown, message: string): void {
  const record = asRecord(payload);
  if (record.code === 0) return;
  const msg = pickString(record, ["message", "msg"]) ?? "unknown error";
  throw new FeishuWebSessionError(`${message}: ${msg}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function pickString(record: Record<string, unknown>, keys: string[]): string | undefined {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value) return value;
    if (typeof value === "number" && Number.isFinite(value)) return String(value);
  }
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function uniqueStrings(values: (string | undefined | null)[]): string[] {
  return [...new Set(values.filter((v): v is string => Boolean(v)))];
}

function toStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function safeErrorMessage(err: unknown): string {
  const message = err instanceof Error ? err.message : String(err);
  return message.replace(/[A-Za-z0-9_=-]{24,}/g, "***");
}
