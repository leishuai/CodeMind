/**
 * i18n.ts — minimal locale support for the `codemind channel` launcher UI.
 *
 * Requirement: the interactive launcher speaks English by default, and switches
 * to Chinese only when the user's timezone indicates mainland China (e.g.
 * Asia/Shanghai). `AUTOMIND_LANG=zh|en` can force a language for testing.
 *
 * Only the launcher's terminal-facing strings live here; the channel/router
 * chat replies to Feishu are separate.
 */

export type Lang = "en" | "zh";

/** Timezones we treat as Chinese-speaking (mainland). */
const ZH_TIMEZONES = new Set<string>([
  "Asia/Shanghai",
  "Asia/Chongqing",
  "Asia/Harbin",
  "Asia/Urumqi",
  "Asia/Kashgar",
]);

function safeTimeZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return undefined;
  }
}

/**
 * Decide the launcher language. `AUTOMIND_LANG` (zh.../en...) wins; otherwise we
 * use the runtime timezone (Chinese zones => zh, everything else => en).
 */
export function detectLang(
  env: Record<string, string | undefined> = process.env,
  tz: string | undefined = safeTimeZone(),
): Lang {
  const forced = env.AUTOMIND_LANG?.trim().toLowerCase();
  if (forced) return forced.startsWith("zh") ? "zh" : "en";
  return tz && ZH_TIMEZONES.has(tz) ? "zh" : "en";
}

/** All launcher-facing strings, parameterized. */
export interface ChannelMessages {
  resumeBot(name: string, id: string): string;
  createBot(id: string): string;
  noBotsCreating(id: string): string;
  invalidBotId(id: string): string;
  // batch start (default: connect every registered bot)
  batchStartingHeader(count: number): string;
  batchStarted(name: string, id: string, pid: number): string;
  batchAlreadyRunning(name: string, id: string, pid: number): string;
  batchNoCreds(name: string, id: string): string;
  addBotPrompt: string;
  askWorkspaceIntro: string;
  workspacePrompt(fallback: string): string;
  workspaceInvalid(input: string): string;
  workspaceConfirmed(root: string): string;
  workspaceFallback(root: string): string;
  scanPrompt: string;
  scanSkipped(id: string): string;
  scanStarting: string;
  scanDone(appId: string): string;
  appNameResolved(name: string): string;
  appNameMissingScope(id: string): string;
  // credential-source choice (new vs. bind existing)
  credChoiceHeader: string;
  credChoiceCreate: string;
  credChoiceBind: string;
  credChoicePrompt: string;
  bindAppIdPrompt: string;
  bindAppSecretPrompt: string;
  bindMissingCreds: string;
  bindSaved(appId: string): string;
  bindAutoConfigStarting: string;
  // credential management when a bot already has saved credentials
  credManageHeader: string;
  credManageUseCurrent: string;
  credManageCreate: string;
  credManageBind: string;
  credManagePrompt: string;
  connected(name: string, id: string): string;
  alreadyRunning(id: string, pid: number): string;
  alreadyRunningWorkspaceHint(id: string): string;
  appAlreadyConnected(otherId: string, pid: number, appId: string): string;
  daemonStarted(name: string, id: string, pid: number): string;
  usage: string;
  fatal: string;
  // register.ts (scan-to-register)
  qrLinkFallback(url: string): string;
  autoConfigured: string;
  manualFallback(appId: string, manifestJson: string): string;
  // auto-config.ts
  autoConfigSessionCacheHit: string;
  autoConfigSessionPrompt: string;
  autoConfigSessionTimeout: string;
  autoConfigStepSuccess(step: string): string;
  autoConfigStepFailed(step: string): string;
}

const EN: ChannelMessages = {
  resumeBot: (name, id) => `[channel] Resuming bot: ${name} (${id})`,
  createBot: (id) => `[channel] Creating bot: ${id}`,
  noBotsCreating: (id) => `[channel] No bots yet, creating a new one: ${id}`,
  invalidBotId: (id) => `Invalid botId: ${id}`,
  batchStartingHeader: (count) =>
    `\n[channel] Connecting all ${count} registered bot(s):`,
  batchStarted: (name, id, pid) =>
    `  ✓ ${name} (${id}) started (pid=${pid}).`,
  batchAlreadyRunning: (name, id, pid) =>
    `  • ${name} (${id}) already running (pid=${pid}), skipped.`,
  batchNoCreds: (name, id) =>
    `  • ${name} (${id}) has no credentials yet, skipped (run codemind channel start ${id} to configure).`,
  addBotPrompt: "\nAdd another bot (create new / bind existing)? [y/N] ",
  askWorkspaceIntro:
    "\nWhich project directory should this bot work on? This is NOT auto-detected " +
    "from where you launched — please set it explicitly.\n" +
    "(Press Enter to skip for now; you can set it later in Feishu with `#dir <path>`.)",
  workspacePrompt: (fallback) => `Project directory [skip => fallback ${fallback}]: `,
  workspaceInvalid: (input) =>
    `Invalid or non-existent path: ${input}. Try again or press Enter to skip.`,
  workspaceConfirmed: (root) => `[channel] Project directory: ${root}`,
  workspaceFallback: (root) =>
    `[channel] No project directory set, using fallback: ${root} (set it later in Feishu with #dir <path>)`,
  scanPrompt:
    "\nCreate/bind a Feishu bot now by scanning a QR code with the Feishu app? [Y/n] ",
  scanSkipped: (id) =>
    "[channel] Skipped QR scan. No credentials yet, so no Feishu connection is established.\n" +
    `Run codemind channel start ${id} again to continue scanning with the Feishu app.`,
  scanStarting: "[channel] Starting Feishu QR scan-to-register…",
  scanDone: (appId) => `[channel] Feishu scan complete, AppID=${appId}.`,
  appNameResolved: (name) => `[channel] Resolved Feishu app name: ${name}`,
  appNameMissingScope: (id) =>
    `[channel] Could not read this bot's Feishu app name: it's missing the ` +
    `"application:application:self_manage" permission. To show the real app name, ` +
    `re-authorize it: run codemind channel start ${id} and re-scan (create/bind) to ` +
    `re-import permissions and publish a new version. (This does not affect connecting.)`,
  credChoiceHeader: "\nHow do you want to set up this bot's Feishu credentials?",
  credChoiceCreate: "  1. Create a new Feishu bot (scan QR to register)",
  credChoiceBind: "  2. Bind an existing Feishu bot (enter AppID + AppSecret)",
  credChoicePrompt: "Select [1-2] (Enter = 1): ",
  bindAppIdPrompt: "AppID: ",
  bindAppSecretPrompt: "AppSecret: ",
  bindMissingCreds:
    "[channel] AppID and AppSecret are both required to bind an existing bot.",
  bindSaved: (appId) => `[channel] Bound existing bot, AppID=${appId}.`,
  bindAutoConfigStarting:
    "[channel] Auto-configuring permissions for the bound bot (scan to authorize the console)…",
  credManageHeader: "\nThis bot already has saved Feishu credentials. What would you like to do?",
  credManageUseCurrent: "  1. Keep the current bot and connect (skip)",
  credManageCreate: "  2. Create a new Feishu bot (scan QR to register)",
  credManageBind: "  3. Bind a different existing bot (enter AppID + AppSecret)",
  credManagePrompt: "Select [1-3] (Enter = 1): ",
  connected: (name, id) =>
    `[channel] Bot ${name} (${id}) is connected; the long connection is running.`,
  alreadyRunning: (id, pid) =>
    `[channel] Bot ${id} is already running (pid=${pid}); it stays running — no second daemon is started.`,
  alreadyRunningWorkspaceHint: (id) =>
    `[channel] Saved the workspace for ${id}. The running daemon keeps its current one until you restart it ` +
    `(codemind channel stop ${id} then start), or change it live in Feishu with \`#dir <path>\`.`,
  appAlreadyConnected: (otherId, pid, appId) =>
    `[channel] Feishu app ${appId} is already connected by bot ${otherId} (pid=${pid}). ` +
    `A single Feishu app allows only a limited number of long connections, so a second daemon would fail with ` +
    `"connections exceeded the limit". Stop the other daemon first: codemind channel stop ${otherId}.`,
  daemonStarted: (name, id, pid) =>
    `[channel] Daemon started for ${name} (${id}) as pid ${pid}. Gateway running in background.`,
  usage: "Usage: codemind channel <start [botId] | dashboard | stop [botId]>",
  fatal: "[channel] fatal:",
  qrLinkFallback: (url) => `\nFeishu scan link (if the QR code does not render): ${url}\n`,
  autoConfigured: "[lark-bridge] Permissions auto-configured and version published (visible to you only).",
  manualFallback: (appId, manifestJson) =>
    "\n[lark-bridge] Automatic permission setup was not completed. Please import the manifest manually:\n" +
    "  1. Open the Feishu Open Platform → your app → Permissions → Batch import;\n" +
    "  2. Paste the scopes from the JSON below; under Events & Callbacks subscribe the events / callbacks;\n" +
    "  3. Create a version and publish it, choosing \"visible to yourself only\" to skip review.\n" +
    `  App AppID: ${appId}\n` +
    `  Permission manifest:\n${manifestJson}\n`,
  autoConfigSessionCacheHit: "[lark-bridge] Using cached Feishu console session",
  autoConfigSessionPrompt: "[lark-bridge] Please scan with Feishu app to configure permissions:",
  autoConfigSessionTimeout: "[lark-bridge] Scan timeout, please retry",
  autoConfigStepSuccess: (step) => `[lark-bridge] ${step} completed`,
  autoConfigStepFailed: (step) => `[lark-bridge] ${step} failed`,
};

const ZH: ChannelMessages = {
  resumeBot: (name, id) => `[channel] 恢复机器人：${name} (${id})`,
  createBot: (id) => `[channel] 新建机器人：${id}`,
  noBotsCreating: (id) => `[channel] 尚无机器人，创建新机器人：${id}`,
  invalidBotId: (id) => `非法 botId：${id}`,
  batchStartingHeader: (count) => `\n[channel] 正在为全部 ${count} 个已注册机器人建连：`,
  batchStarted: (name, id, pid) => `  ✓ ${name} (${id}) 已启动 (pid=${pid})。`,
  batchAlreadyRunning: (name, id, pid) =>
    `  • ${name} (${id}) 已在运行 (pid=${pid})，跳过。`,
  batchNoCreds: (name, id) =>
    `  • ${name} (${id}) 尚未配置凭据，跳过（运行 codemind channel start ${id} 进行配置）。`,
  addBotPrompt: "\n再添加一个机器人（新建 / 绑定已有）？[y/N] ",
  askWorkspaceIntro:
    "\n请指定该机器人要处理的工程目录。它不会根据你启动命令时所在的位置自动确定，请显式设置。\n" +
    "（直接回车可暂时跳过；之后可在飞书里用 `#dir <路径>` 指定。）",
  workspacePrompt: (fallback) => `工程目录 [跳过则兜底 ${fallback}]: `,
  workspaceInvalid: (input) => `路径无效或不存在：${input}，请重试或直接回车跳过。`,
  workspaceConfirmed: (root) => `[channel] 工程目录：${root}`,
  workspaceFallback: (root) =>
    `[channel] 未指定工程目录，兜底：${root}（可在飞书里用 #dir <路径> 指定）`,
  scanPrompt: "\n现在使用飞书 App 扫码创建/绑定机器人？[Y/n] ",
  scanSkipped: (id) =>
    "[channel] 已跳过飞书扫码建 bot。当前无凭据，暂不建立飞书连接。\n" +
    `再次运行 codemind channel start ${id} 可继续使用飞书 App 扫码。`,
  scanStarting: "[channel] 启动飞书扫码建连…",
  scanDone: (appId) => `[channel] 飞书扫码完成，AppID=${appId}。`,
  appNameResolved: (name) => `[channel] 已获取飞书应用名称：${name}`,
  appNameMissingScope: (id) =>
    `[channel] 无法获取该机器人的飞书应用名称：缺少 ` +
    `“application:application:self_manage” 权限。若想显示真实应用名，请重新授权：` +
    `运行 codemind channel start ${id} 并重新扫码（新建/绑定）以重新导入权限并发布新版本。` +
    `（不影响正常连接。）`,
  credChoiceHeader: "\n如何配置这个机器人的飞书凭据？",
  credChoiceCreate: "  1. 新建飞书机器人（扫码注册）",
  credChoiceBind: "  2. 绑定已有飞书机器人（输入 AppID + AppSecret）",
  credChoicePrompt: "选择 [1-2]（回车默认 1）: ",
  bindAppIdPrompt: "AppID: ",
  bindAppSecretPrompt: "AppSecret: ",
  bindMissingCreds: "[channel] 绑定已有机器人需要同时提供 AppID 和 AppSecret。",
  bindSaved: (appId) => `[channel] 已绑定现有机器人，AppID=${appId}。`,
  bindAutoConfigStarting:
    "[channel] 正在为绑定的机器人自动配置权限（请扫码授权控制台）…",
  credManageHeader: "\n该机器人已保存飞书凭据，你想做什么？",
  credManageUseCurrent: "  1. 保持当前机器人并连接（跳过）",
  credManageCreate: "  2. 新建飞书机器人（扫码注册）",
  credManageBind: "  3. 绑定另一个已有机器人（输入 AppID + AppSecret）",
  credManagePrompt: "选择 [1-3]（回车默认 1）: ",
  connected: (name, id) => `[channel] 机器人 ${name} (${id}) 已连接，长连接运行中。`,
  alreadyRunning: (id, pid) =>
    `[channel] 机器人 ${id} 已在运行 (pid=${pid})，保持运行中，不会启动第二个 daemon。`,
  alreadyRunningWorkspaceHint: (id) =>
    `[channel] 已保存 ${id} 的工程目录。运行中的 daemon 仍用原目录，重启后生效` +
    `（codemind channel stop ${id} 再 start），或在飞书里用 \`#dir <路径>\` 即时切换。`,
  appAlreadyConnected: (otherId, pid, appId) =>
    `[channel] 飞书应用 ${appId} 已被机器人 ${otherId} (pid=${pid}) 建立连接。` +
    `同一个飞书应用的长连接数有上限，再启动第二个 daemon 会报 “connections exceeded the limit”。` +
    `请先停掉另一个：codemind channel stop ${otherId}。`,
  daemonStarted: (name, id, pid) =>
    `[channel] ${name} (${id}) 的 daemon 已启动，pid=${pid}。Gateway 在后台运行。`,
  usage: "用法：codemind channel <start [botId] | dashboard | stop [botId]>",
  fatal: "[channel] 致命错误:",
  qrLinkFallback: (url) => `\n飞书扫码链接（若二维码无法显示）：${url}\n`,
  autoConfigured: "[lark-bridge] 已自动配置权限并发布版本（仅自己可见）。",
  manualFallback: (appId, manifestJson) =>
    "\n[lark-bridge] 自动配置权限未完成，请手动导入以下清单：\n" +
    "  1. 打开飞书开放平台 → 你的应用 → 权限管理 → 批量导入；\n" +
    "  2. 粘贴下方 JSON 的 scopes；在「事件与回调」订阅 events / callbacks；\n" +
    "  3. 创建版本并发布，可见范围选「仅自己可见」免审批。\n" +
    `  应用 AppID：${appId}\n` +
    `  权限清单：\n${manifestJson}\n`,
  autoConfigSessionCacheHit: "[lark-bridge] 使用缓存的飞书控制台 session",
  autoConfigSessionPrompt: "[lark-bridge] 请用飞书 App 扫码完成权限配置：",
  autoConfigSessionTimeout: "[lark-bridge] 扫码超时，请重试",
  autoConfigStepSuccess: (step) => `[lark-bridge] ${step} 完成`,
  autoConfigStepFailed: (step) => `[lark-bridge] ${step} 失败`,
};

/** Return the message catalog for a language. */
export function messages(lang: Lang): ChannelMessages {
  return lang === "zh" ? ZH : EN;
}
