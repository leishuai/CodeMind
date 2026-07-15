/**
 * main.ts (design §13.1 / §multi-bot) — the `codemind channel` entrypoint.
 *
 * CodeMind supports MANY Feishu bots. Each bot is one daemon process bound to
 * ONE project workspace; a task therefore always belongs to exactly one bot, so
 * progress can never be routed to the wrong bot. Bots live in a persistent
 * registry under `~/.automind/channels/`.
 *
 * Subcommands:
 *   start [botId]   create-or-resume a bot's daemon (scan + workspace are
 *                   interactive and BOTH skippable), then hold the long
 *                   connection. This is the merged create/resume launcher.
 *   ls              list registered bots + their status.
 *   dashboard       show every bot's connection / process / workspace / tasks.
 *   stop [botId]    stop a running bot daemon.
 *
 * The orchestrator layer is channel-neutral; only this file knows about Lark.
 */
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import readline from "node:readline";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import dotenv from "dotenv";

import { loadConfig, applyBotRecord, type BridgeConfig } from "./config.js";
import { LarkChannel } from "./channel/lark/lark-channel.js";
import { registerApp } from "./channel/lark/register.js";
import { autoConfigure } from "./channel/lark/auto-config.js";
import { buildScopeManifest } from "./channel/lark/scopes.js";
import { fetchAppName } from "./channel/lark/app-name.js";
import { CodeMindCli, defaultRunner } from "./orchestrator/automind-cli.js";
import { SessionMap, createFileStore } from "./orchestrator/session-map.js";
import { DefaultConversationOrchestrator } from "./orchestrator/conversation-orchestrator.js";
import { createSnapshotReader } from "./orchestrator/task-artifacts.js";
import { collectGitDiff } from "./orchestrator/gitdiff.js";
import { Router } from "./orchestrator/router.js";
import { TaskWatcher, createFileCursorStore } from "./orchestrator/task-watcher.js";
import { Workspace } from "./orchestrator/workspace.js";
import { ChannelRegistry, isSafeBotId, type BotRecord } from "./orchestrator/registry.js";
import { detectLang, messages, type ChannelMessages } from "./i18n.js";
import {
  writeStatus,
  readStatus,
  isPidAlive,
  type ChannelStatus,
  type ConnectionState,
} from "./orchestrator/channel-status.js";

/** Launcher UI language, chosen from AUTOMIND_LANG / timezone (see i18n.ts). */
const t: ChannelMessages = messages(detectLang());

/** Resolve a user-supplied project path (expand ~) to an existing dir, or null. */
function resolveProjectPath(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const expanded = trimmed.startsWith("~")
    ? path.join(os.homedir(), trimmed.slice(1))
    : trimmed;
  const abs = path.resolve(expanded);
  try {
    return fs.statSync(abs).isDirectory() ? abs : null;
  } catch {
    return null;
  }
}

function ask(rl: readline.Interface, prompt: string): Promise<string> {
  return new Promise((resolve) => rl.question(prompt, resolve));
}

/** Outcome of an interactive credential-acquisition step. */
type CredOutcome = "ok" | "abort";

/**
 * Create a new Feishu bot via scan-to-register (design §5). Mutates `config`
 * with the registered AppID/AppSecret and the scanning user's open_id.
 */
async function createBotByScan(config: BridgeConfig): Promise<void> {
  console.log(t.scanStarting);
  const registered = await registerApp(config);
  config.lark.appId = registered.appId;
  config.lark.appSecret = registered.appSecret;
  if (registered.scannedUserOpenId && config.allowedUsers.length === 0) {
    config.allowedUsers = [registered.scannedUserOpenId];
  }
  console.log(t.scanDone(registered.appId));
}

/**
 * Bind an existing Feishu bot: read AppID + AppSecret from the TTY, store them
 * on `config`, then best-effort auto-configure its permissions/events/callbacks.
 * Returns "abort" when the user did not supply both credentials.
 */
async function bindExistingBot(
  rl: readline.Interface,
  config: BridgeConfig,
): Promise<CredOutcome> {
  const appId = (await ask(rl, t.bindAppIdPrompt)).trim();
  const appSecret = (await ask(rl, t.bindAppSecretPrompt)).trim();
  if (!appId || !appSecret) {
    console.log(t.bindMissingCreds);
    return "abort";
  }
  config.lark.appId = appId;
  config.lark.appSecret = appSecret;
  console.log(t.bindSaved(appId));
  console.log(t.bindAutoConfigStarting);
  try {
    await autoConfigure(appId, buildScopeManifest());
  } catch {
    // Auto-config is best-effort; connecting can still proceed and the user can
    // configure permissions manually if this step fails.
  }
  return "ok";
}

/**
 * Interactively confirm the project dir at startup. SKIPPABLE: pressing enter
 * (or a non-TTY session) keeps the current fallback and leaves it unconfirmed;
 * the user can point at a project later in chat via `#dir <path>`.
 */
async function confirmProjectDir(
  rl: readline.Interface | null,
  fallbackRoot: string,
): Promise<{ root: string; confirmed: boolean }> {
  if (!rl) return { root: fallbackRoot, confirmed: false };
  console.log(t.askWorkspaceIntro);
  for (;;) {
    const answer = await ask(rl, t.workspacePrompt(fallbackRoot));
    if (!answer.trim()) return { root: fallbackRoot, confirmed: false };
    const resolved = resolveProjectPath(answer);
    if (resolved) return { root: resolved, confirmed: true };
    console.log(t.workspaceInvalid(answer.trim()));
  }
}

/** Generate a fresh, filesystem-safe bot id. */
function newBotId(): string {
  return `bot_${Date.now().toString(36)}`;
}

/** Spawn a fully-detached background daemon for a configured bot; returns pid. */
function spawnBotDaemon(registry: ChannelRegistry, bot: BotRecord): number {
  const daemonPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    "main.js",
  );
  // The daemon's stdout/stderr go to a per-bot log file so post-launch failures
  // remain diagnosable after the foreground launcher has exited.
  const logFile = path.join(registry.dirOf(bot.botId), "daemon.log");
  fs.mkdirSync(path.dirname(logFile), { recursive: true });
  const out = fs.openSync(logFile, "a");
  // Use spawn (not fork): a fully detached background daemon has no IPC channel
  // to the parent, and fork() mandates an "ipc" entry in stdio. spawn + unref
  // lets the parent exit and hand the terminal back immediately.
  const daemon = spawn(process.execPath, [daemonPath, "daemon", bot.botId], {
    detached: true,
    stdio: ["ignore", out, out],
  });
  daemon.unref();
  return daemon.pid!;
}

/**
 * Batch-launch EVERY registered bot (design: default = connect all automind
 * bots, no picker). Bots with a live daemon or without credentials yet are
 * skipped with a one-line note; every other bot gets a background daemon.
 * Bots whose name is still the raw id get a best-effort real-name backfill.
 */
async function startAllBots(registry: ChannelRegistry): Promise<void> {
  for (const bot of registry.list()) {
    const status = readStatus(registry.statusOf(bot.botId));
    if (status && status.pid !== process.pid && isPidAlive(status.pid)) {
      console.log(t.batchAlreadyRunning(bot.name, bot.botId, status.pid));
      continue;
    }
    if (!bot.appId || !bot.appSecret) {
      console.log(t.batchNoCreds(bot.name, bot.botId));
      continue;
    }
    // Backfill the real Feishu app name for bots never resolved yet (name is
    // still the raw bot id). Best-effort; never blocks the launch.
    let launched = bot;
    if (bot.name === bot.botId) {
      try {
        const { name: real, missingScope } = await fetchAppName(
          bot.appId,
          bot.appSecret,
        );
        if (real) launched = registry.upsert({ botId: bot.botId, name: real });
        else if (missingScope) console.log(t.appNameMissingScope(bot.botId));
      } catch {
        // Ignore; keep the current name.
      }
    }
    const pid = spawnBotDaemon(registry, launched);
    console.log(t.batchStarted(launched.name, launched.botId, pid));
  }
}

/**
 * Interactively confirm workspace + credentials for ONE bot, then launch its
 * background daemon. Used for `start <botId>`, first-bot creation, and the
 * "add another bot" prompt. Returns without launching when the bot already has
 * a live daemon or the user skips credential setup.
 */
async function configureAndStartBot(
  registry: ChannelRegistry,
  rl: readline.Interface | null,
  baseConfig: BridgeConfig,
  bot: BotRecord,
): Promise<void> {
  // If the chosen bot already has a live daemon, we WON'T start a second one
  // (two processes would corrupt the same state files). But we keep the flow
  // uniform: the user can still re-point its workspace below.
  const existingStatus = readStatus(registry.statusOf(bot.botId));
  const alreadyRunning = Boolean(
    existingStatus &&
      existingStatus.pid !== process.pid &&
      isPidAlive(existingStatus.pid),
  );
  if (alreadyRunning) {
    console.log(t.alreadyRunning(bot.botId, existingStatus!.pid));
  }

  const config = applyBotRecord(baseConfig, bot);

  // Workspace: confirm interactively unless the bot already has a confirmed one.
  // SKIPPABLE — falls back to the home dir and asks again in chat via `#dir`.
  let root = config.workspaceRoot;
  let confirmed = config.workspaceConfirmed;
  if (!confirmed) {
    const picked = await confirmProjectDir(rl, config.workspaceRoot);
    root = picked.root;
    confirmed = picked.confirmed;
  }
  const workspace = new Workspace({ root, confirmed });
  console.log(
    confirmed
      ? t.workspaceConfirmed(workspace.getRoot())
      : t.workspaceFallback(workspace.getRoot()),
  );

  // Already-running bot: it's already configured/connected, so there is no
  // credential setup and no new daemon to spawn. Persist whatever workspace the
  // user (re)confirmed and remind them how it takes effect.
  if (alreadyRunning) {
    registry.upsert({
      botId: bot.botId,
      workspaceRoot: workspace.getRoot(),
      workspaceConfirmed: workspace.isConfirmed(),
    });
    console.log(t.alreadyRunningWorkspaceHint(bot.botId));
    return;
  }

  // Credential setup. Two entry points, always offering the SAME create/bind
  // choice so the flow is uniform whether or not this bot already has creds.
  if (!config.lark.appId || !config.lark.appSecret) {
    const doScan = rl
      ? (await ask(rl, t.scanPrompt)).trim().toLowerCase()
      : "y";
    if (doScan === "n" || doScan === "no") {
      registry.upsert({
        botId: bot.botId,
        workspaceRoot: workspace.getRoot(),
        workspaceConfirmed: workspace.isConfirmed(),
      });
      console.log(t.scanSkipped(bot.botId));
      return;
    }
    let choice = "1";
    if (rl) {
      console.log(t.credChoiceHeader);
      console.log(t.credChoiceCreate);
      console.log(t.credChoiceBind);
      choice = (await ask(rl, t.credChoicePrompt)).trim() || "1";
    }
    if (rl && choice === "2") {
      if ((await bindExistingBot(rl, config)) === "abort") return;
    } else {
      await createBotByScan(config);
    }
  } else if (rl) {
    console.log(t.credManageHeader);
    console.log(t.credManageUseCurrent);
    console.log(t.credManageCreate);
    console.log(t.credManageBind);
    const choice = (await ask(rl, t.credManagePrompt)).trim() || "1";
    if (choice === "2") {
      await createBotByScan(config);
    } else if (choice === "3") {
      if ((await bindExistingBot(rl, config)) === "abort") return;
    }
    // choice "1" (or anything else): keep current credentials and connect.
  }

  // Persist everything learned this run back into the registry, then launch.
  // Best-effort: resolve the bot's real Feishu app name so the registry/dashboard
  // show it instead of the internal bot id. Never blocks connecting.
  let resolvedName: string | undefined;
  try {
    const { name, missingScope } = await fetchAppName(
      config.lark.appId,
      config.lark.appSecret,
    );
    resolvedName = name ?? undefined;
    if (resolvedName) console.log(t.appNameResolved(resolvedName));
    else if (missingScope) console.log(t.appNameMissingScope(bot.botId));
  } catch {
    // Name resolution is a nice-to-have; ignore failures (bad creds, offline)
    // and keep the current name.
  }
  const saved = registry.upsert({
    botId: bot.botId,
    name: resolvedName ?? bot.name,
    appId: config.lark.appId,
    appSecret: config.lark.appSecret,
    workspaceRoot: workspace.getRoot(),
    workspaceConfirmed: workspace.isConfirmed(),
    allowedUsers: config.allowedUsers,
    agent: config.agent,
  });
  const pid = spawnBotDaemon(registry, saved);
  console.log(t.daemonStarted(saved.name, saved.botId, pid));
}

/** Build a status object snapshot for a bot's current runtime state. */
function buildStatus(
  bot: BotRecord,
  config: BridgeConfig,
  workspace: Workspace,
  connection: ConnectionState,
  reconnects: number,
  activeTasks: number,
): ChannelStatus {
  return {
    botId: bot.botId,
    name: bot.name,
    connection,
    pid: process.pid,
    workspaceRoot: workspace.getRoot(),
    workspaceConfirmed: workspace.isConfirmed(),
    hasCredentials: Boolean(config.lark.appId && config.lark.appSecret),
    reconnects,
    activeTasks,
    // Persist the allow-list the daemon actually enforces so the dashboard can
    // show the in-force value instead of the (possibly drifted) registry value.
    allowedUsers: config.allowedUsers,
    updatedAt: new Date().toISOString(),
  };
}

/** `codemind channel daemon [botId]` — run the long connection + watcher in background.
 * Called by cmdStart after interactive configuration completes. */
async function cmdDaemon(botIdArg: string): Promise<void> {
  const bridgeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  dotenv.config({ path: path.resolve(bridgeRoot, ".env") });
  const baseConfig = loadConfig({ ...process.env, BRIDGE_DIR: bridgeRoot });

  if (!fs.existsSync(baseConfig.automindBin)) {
    throw new Error(`automind binary not found: ${baseConfig.automindBin}`);
  }

  const registry = new ChannelRegistry();
  const bot = registry.get(botIdArg);
  if (!bot) {
    throw new Error(t.invalidBotId(botIdArg));
  }

  const existingStatus = readStatus(registry.statusOf(bot.botId));
  if (
    existingStatus &&
    existingStatus.pid !== process.pid &&
    isPidAlive(existingStatus.pid)
  ) {
    throw new Error(t.alreadyRunning(bot.botId, existingStatus.pid));
  }

  // Guard: a single Feishu app allows only a limited number of long
  // connections. Two daemons bound to DIFFERENT botIds but sharing the same
  // appId will fight over the connection quota and fail with
  // "connections exceeded the limit". Refuse to start a second one and tell the
  // user which daemon already holds the connection.
  if (bot.appId) {
    for (const other of registry.list()) {
      if (other.botId === bot.botId || other.appId !== bot.appId) continue;
      const otherStatus = readStatus(registry.statusOf(other.botId));
      if (
        otherStatus &&
        otherStatus.pid !== process.pid &&
        isPidAlive(otherStatus.pid)
      ) {
        throw new Error(
          t.appAlreadyConnected(other.botId, otherStatus.pid, bot.appId),
        );
      }
    }
  }

  const config = applyBotRecord(baseConfig, bot);
  const workspace = new Workspace({ root: config.workspaceRoot, confirmed: config.workspaceConfirmed });

  const sessionMap = new SessionMap(createFileStore(registry.sessionMapOf(bot.botId)));
  const statusFile = registry.statusOf(bot.botId);
  let reconnects = 0;

  const cli = new CodeMindCli({
    bin: config.automindBin,
    workspaceRoot: () => workspace.getRoot(),
  });
  const channel = new LarkChannel(config, undefined, {
    backoff: { baseMs: 1000, maxMs: 30_000, factor: 2 },
    onError: (err, attempt) => {
      reconnects = attempt + 1;
      console.error(`[channel] 长连中断，准备第 ${attempt + 1} 次重连:`, err);
      writeStatus(
        statusFile,
        buildStatus(bot, config, workspace, "reconnecting", reconnects, countActiveTasks(sessionMap)),
      );
    },
  });
  const router = new Router({
    channel,
    cli,
    orchestrator: new DefaultConversationOrchestrator(cli, config.agent),
    sessionMap,
    snapshotReader: createSnapshotReader(() => workspace.getRoot()),
    agent: config.agent,
    allowedUsers: config.allowedUsers,
    gitDiff: () => collectGitDiff(defaultRunner, workspace.getRoot()),
    workspace,
    resolveWorkspacePath: resolveProjectPath,
  });

  const watcher = new TaskWatcher({
    channel,
    sessionMap,
    snapshotReader: createSnapshotReader(() => workspace.getRoot()),
    cursorStore: createFileCursorStore(
      path.join(registry.dirOf(bot.botId), "push-cursor.json"),
    ),
    pollIntervalMs: config.pollIntervalMs,
    gitDiff: () => collectGitDiff(defaultRunner, workspace.getRoot()),
  });

  writeStatus(
    statusFile,
    buildStatus(bot, config, workspace, "starting", reconnects, countActiveTasks(sessionMap)),
  );

  const markStopped = (): void => {
    writeStatus(
      statusFile,
      buildStatus(bot, config, workspace, "stopped", reconnects, countActiveTasks(sessionMap)),
    );
  };
  process.on("SIGTERM", () => {
    watcher.stop();
    markStopped();
    process.exit(0);
  });
  process.on("SIGINT", () => {
    watcher.stop();
    markStopped();
    process.exit(0);
  });

  await channel.start(
    (message) => router.onMessage(message),
    (threadId, optionId, cardKind, token, userId) =>
      router.onCardAction(threadId, optionId, cardKind, token, userId),
  );
  watcher.start();
  writeStatus(
    statusFile,
    buildStatus(bot, config, workspace, "connected", reconnects, countActiveTasks(sessionMap)),
  );
}

/**
 * `codemind channel start [botId]` — launch bot daemons.
 *
 * Default (no botId): batch-connect EVERY registered automind bot. Bots that
 * already have a live daemon or that have no credentials yet are skipped with a
 * one-line note. When there are no bots at all, fall into the single-bot
 * create-and-configure flow. After batch-launching, offer to add one more bot
 * (create new / bind existing) in a TTY.
 *
 * With an explicit botId: resume/create-and-configure just that one bot.
 */
async function cmdStart(botIdArg: string | undefined): Promise<void> {
  const bridgeRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  dotenv.config({ path: path.resolve(bridgeRoot, ".env") });
  const baseConfig = loadConfig({ ...process.env, BRIDGE_DIR: bridgeRoot });

  if (!fs.existsSync(baseConfig.automindBin)) {
    throw new Error(`automind binary not found: ${baseConfig.automindBin}`);
  }

  const registry = new ChannelRegistry();
  const rl = process.stdin.isTTY
    ? readline.createInterface({ input: process.stdin, output: process.stdout })
    : null;

  try {
    // Explicit botId: configure/launch just that one bot.
    if (botIdArg) {
      if (!isSafeBotId(botIdArg)) {
        throw new Error(t.invalidBotId(botIdArg));
      }
      const existing = registry.get(botIdArg);
      const bot = existing
        ? (console.log(t.resumeBot(existing.name, existing.botId)), existing)
        : (console.log(t.createBot(botIdArg)),
          registry.upsert({ botId: botIdArg, name: botIdArg }));
      await configureAndStartBot(registry, rl, baseConfig, bot);
      return;
    }

    const bots = registry.list();

    // No bots yet: create the first one and walk through its full setup.
    if (bots.length === 0) {
      const id = newBotId();
      console.log(t.noBotsCreating(id));
      const bot = registry.upsert({ botId: id, name: id });
      await configureAndStartBot(registry, rl, baseConfig, bot);
      return;
    }

    // Default: batch-connect every registered bot (no picker).
    console.log(t.batchStartingHeader(bots.length));
    await startAllBots(registry);

    // Then offer to add one more bot (create new / bind existing) — TTY only.
    if (rl) {
      const add = (await ask(rl, t.addBotPrompt)).trim().toLowerCase();
      if (add === "y" || add === "yes") {
        const id = newBotId();
        console.log(t.createBot(id));
        const bot = registry.upsert({ botId: id, name: id });
        await configureAndStartBot(registry, rl, baseConfig, bot);
      }
    }
  } finally {
    rl?.close();
  }
}

/** Count active harness tasks across all threads in a bot's session map. */
function countActiveTasks(sessionMap: SessionMap): number {
  const snap = sessionMap.snapshot();
  return Object.values(snap).reduce((sum, b) => sum + b.tasks.length, 0);
}

/** `codemind channel dashboard` — richer live view of every bot. */
function cmdDashboard(): void {
  const registry = new ChannelRegistry();
  const bots = registry.list();
  console.log("CodeMind Channels Dashboard");
  console.log("===========================\n");
  if (bots.length === 0) {
    console.log("尚无已注册机器人。运行 `codemind channel start` 创建一个。");
    return;
  }
  for (const bot of bots) {
    const status = readStatus(registry.statusOf(bot.botId));
    const alive = status ? isPidAlive(status.pid) : false;
    console.log(`● ${bot.name}  [${bot.botId}]`);
    if (!status) {
      console.log("    状态: 未启动");
    } else {
      console.log(`    连接: ${alive ? status.connection : "已停止(残留状态)"}`);
      console.log(`    进程: pid=${status.pid} ${alive ? "存活" : "不存在"}`);
      console.log(`    重连次数: ${status.reconnects}`);
      console.log(`    活跃任务: ${status.activeTasks}`);
      console.log(`    更新时间: ${status.updatedAt}`);
    }
    console.log(`    工程目录: ${bot.workspaceRoot || "(未指定)"}${bot.workspaceConfirmed ? "" : " [兜底]"}`);
    console.log(`    凭据: ${bot.appId ? `已配置 (${bot.appId})` : "未配置(需扫码)"}`);
    // Prefer the allow-list the RUNNING daemon actually enforces (persisted in
    // status.json). It is loaded into memory once at startup and never re-read,
    // so it can drift from the registry file; showing the enforced value avoids
    // the confusing "dashboard says 不限 but messages are rejected" case.
    const effectiveAllow = alive && status?.allowedUsers ? status.allowedUsers : bot.allowedUsers;
    console.log(`    白名单: ${effectiveAllow.length ? effectiveAllow.join(", ") : "(不限)"}`);
    if (
      alive &&
      status?.allowedUsers &&
      JSON.stringify([...status.allowedUsers].sort()) !== JSON.stringify([...bot.allowedUsers].sort())
    ) {
      console.log(`    ⚠ 白名单已变更但未生效：运行中的 daemon 仍用启动时的白名单，改动需 stop 后重启。`);
    }
    console.log("");
  }
}

/** `codemind channel stop [botId]` — stop a running bot daemon. */
function cmdStop(botIdArg: string | undefined): void {
  const registry = new ChannelRegistry();
  const bots = registry.list();
  const targets = botIdArg
    ? bots.filter((b) => b.botId === botIdArg)
    : bots;
  if (botIdArg && targets.length === 0) {
    console.error(`未找到机器人：${botIdArg}`);
    process.exitCode = 1;
    return;
  }
  let stopped = 0;
  for (const bot of targets) {
    const status = readStatus(registry.statusOf(bot.botId));
    if (status && isPidAlive(status.pid)) {
      try {
        process.kill(status.pid, "SIGTERM");
        console.log(`[channel] 已发送停止信号给 ${bot.botId} (pid=${status.pid})。`);
        stopped += 1;
      } catch (err) {
        console.error(`[channel] 停止 ${bot.botId} 失败:`, err);
      }
    }
  }
  if (stopped === 0) console.log("没有正在运行的机器人可停止。");
}

async function main(): Promise<void> {
  const [sub, arg] = process.argv.slice(2);
  switch (sub) {
    case "start":
      await cmdStart(arg);
      return;
    case "daemon":
      await cmdDaemon(arg || "");
      return;
    case "dashboard":
      cmdDashboard();
      return;
    case "stop":
      cmdStop(arg);
      return;
    default:
      console.log(t.usage);
      return;
  }
}

main().catch((err) => {
  console.error(t.fatal, err);
  process.exitCode = 1;
});
