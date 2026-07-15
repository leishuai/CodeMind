/**
 * register.ts (design §5 / M3) — two-step scan-to-register flow.
 *
 * Step 1 (stable, official): `@larksuiteoapi/node-sdk` `registerApp()` runs the
 * OAuth 2.0 Device Flow. The user scans a QR code; the SDK polls until it
 * returns client_id/client_secret (= AppID/AppSecret) and the scanning user's
 * open_id. The required scopes/events/callbacks are pre-filled into the confirm
 * page via `addons` so most of the "scan and go" config happens here.
 *
 * Step 2 (private, best-effort): auto-configuring extra permissions through the
 * reverse-engineered developer-console internal API is NOT an official stable
 * contract (design §5). It is exposed here as an injectable seam (`autoConfig`)
 * that defaults to a no-op which triggers the manual fallback: we print the
 * scope manifest and guide the user to import it manually. This keeps the
 * feature usable even when the private API changes.
 *
 * The Feishu SDK + IO surfaces are injected so this module is unit-testable
 * without real credentials, network, or a terminal.
 */
import type { BridgeConfig } from "../../config.js";
import { detectLang, messages } from "../../i18n.js";
import {
  REQUIRED_CALLBACKS,
  REQUIRED_EVENTS,
  REQUIRED_TENANT_SCOPES,
  buildScopeManifest,
} from "./scopes.js";
import { autoConfigure } from "./auto-config.js";

export interface RegisterResult {
  appId: string;
  appSecret: string;
  scannedUserOpenId?: string;
  /** True when step-2 auto permission config succeeded; false ⇒ used fallback. */
  autoConfigured: boolean;
}

/** QR-code info surfaced by the SDK device flow (mirrors SDK QRCodeInfo). */
export interface QrCodeInfo {
  url: string;
  expireIn: number;
}

/** Result shape returned by the SDK `registerApp` (mirrors RegisterAppResult). */
export interface RegisterAppSdkResult {
  client_id: string;
  client_secret: string;
  user_info?: { open_id?: string };
}

/** Injectable SDK `registerApp` call. */
export type RegisterAppFn = (options: {
  onQRCodeReady: (info: QrCodeInfo) => void;
  addons?: {
    scopes?: { tenant?: string[]; user?: string[] };
    events?: { items?: { tenant?: string[] } };
    callbacks?: { items?: string[] };
  };
}) => Promise<RegisterAppSdkResult>;

/**
 * Injectable step-2 auto permission config (private console API). Returns true
 * on success. Defaults to a no-op returning false so the manual fallback runs.
 */
export type AutoConfigFn = (
  appId: string,
  manifest: Record<string, unknown>,
) => Promise<boolean>;

export interface RegisterDeps {
  registerApp: RegisterAppFn;
  /** Renders the QR code / link for the user to scan. */
  renderQrCode: (info: QrCodeInfo) => void;
  /** Prints human-facing guidance / manifest. */
  log: (message: string) => void;
  /** Step-2 auto config; defaults to fallback-only. */
  autoConfig?: AutoConfigFn;
}

async function defaultRegisterDeps(): Promise<RegisterDeps> {
  const [lark, qrcode] = await Promise.all([
    import("@larksuiteoapi/node-sdk"),
    import("qrcode-terminal"),
  ]);
  const t = messages(detectLang());
  return {
    registerApp: (options) =>
      (lark.registerApp as unknown as RegisterAppFn)(options),
    renderQrCode: (info) => {
      qrcode.default.generate(info.url, { small: true });
      console.log(t.qrLinkFallback(info.url));
    },
    log: (message) => console.log(message),
    autoConfig: async (appId, manifest) => {
      const result = await autoConfigure(appId, manifest);
      return result.success;
    },
  };
}

/** Build the SDK `addons` payload from the required scope manifest. */
function buildAddons(): NonNullable<Parameters<RegisterAppFn>[0]["addons"]> {
  return {
    scopes: { tenant: REQUIRED_TENANT_SCOPES, user: [] },
    events: { items: { tenant: REQUIRED_EVENTS } },
    callbacks: { items: REQUIRED_CALLBACKS },
  };
}

/** Print the manual-import fallback guidance (design §5, hard requirement). */
function printFallback(log: (m: string) => void, appId: string): void {
  const manifest = buildScopeManifest();
  const t = messages(detectLang());
  log(t.manualFallback(appId, JSON.stringify(manifest, null, 2)));
}

/**
 * Run the scan-to-register flow. Step 1 is the official device flow; step 2 is
 * best-effort with a mandatory manual fallback.
 */
export async function registerApp(
  _config: BridgeConfig,
  deps?: RegisterDeps,
): Promise<RegisterResult> {
  const resolved = deps ?? (await defaultRegisterDeps());

  const sdkResult = await resolved.registerApp({
    onQRCodeReady: (info) => resolved.renderQrCode(info),
    addons: buildAddons(),
  });

  const appId = sdkResult.client_id;
  const appSecret = sdkResult.client_secret;
  const scannedUserOpenId = sdkResult.user_info?.open_id;

  let autoConfigured = false;
  if (resolved.autoConfig) {
    try {
      autoConfigured = await resolved.autoConfig(appId, buildScopeManifest());
    } catch {
      autoConfigured = false;
    }
  }

  if (!autoConfigured) {
    printFallback(resolved.log, appId);
  } else {
    resolved.log(messages(detectLang()).autoConfigured);
  }

  return { appId, appSecret, scannedUserOpenId, autoConfigured };
}
