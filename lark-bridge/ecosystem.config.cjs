/**
 * ecosystem.config.cjs (design §13.5 / §multi-bot) — pm2 process definitions so
 * each Feishu bot runs as its own resilient resident daemon: auto-restart on
 * crash and auto-start on boot (via `pm2 startup` + `pm2 save`).
 *
 * Multi-bot model: CodeMind supports MANY bots, ONE process per bot. Add one app
 * entry per bot below, each launching `dist/main.js start <botId>` (non-TTY, so
 * scanning/workspace prompts are skipped — pre-register the bot once with an
 * interactive `automind channel start <botId>` before handing it to pm2).
 *
 * Usage:
 *   npm run build                            # compile TS -> dist/
 *   automind channel start bot_pay           # interactively create/scan once
 *   pm2 start ecosystem.config.cjs           # then run all bots under pm2
 *   pm2 logs automind-channel-bot_pay
 *   pm2 startup && pm2 save                  # survive machine reboots
 *
 * The bridge is a pure external front-end; it only talks to the CodeMind core
 * through the `automind` CLI and read-only task-dir JSON, so restarting it never
 * corrupts core state. Bot identity/credentials/workspace live in the persistent
 * registry under ~/.automind/channels/, so a restarted process resumes cleanly.
 */

/** Build a pm2 app entry for one bot (one process per bot). */
function botApp(botId) {
  return {
    name: `automind-channel-${botId}`,
    script: "dist/main.js",
    // main.js dispatches `automind channel <sub>`; here we resume this bot.
    args: ["start", botId],
    cwd: __dirname,
    exec_mode: "fork",
    instances: 1,
    // Resilience: restart on crash with a capped exponential backoff so a
    // hard-failing dependency does not spin the CPU.
    autorestart: true,
    max_restarts: 20,
    restart_delay: 2000,
    exp_backoff_restart_delay: 2000,
    // Recycle if the process leaks memory over a long uptime.
    max_memory_restart: "300M",
    env: {
      NODE_ENV: "production",
    },
    // Load .env from the bridge dir; pm2 does not read it automatically.
    // (main.ts also calls dotenv.config on the bundled .env.)
    time: true,
  };
}

// List every bot you want pm2 to keep alive (one process per bot). Replace/add
// entries to match the bots registered under ~/.automind/channels/.
module.exports = {
  apps: [
    botApp("bot_default"),
    // botApp("bot_pay"),
    // botApp("bot_web"),
  ],
};
