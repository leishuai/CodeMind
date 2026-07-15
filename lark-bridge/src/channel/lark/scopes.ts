/**
 * scopes.ts (design §5) — the permission scopes, event subscriptions, and card
 * callbacks the CodeMind Lark bot needs. Kept in one place so both the
 * scan-to-register confirm-page prefill (SDK `addons`) and the manual fallback
 * (printed manifest JSON) stay in sync.
 */

/** Tenant (app-identity) permission scopes required by the bot. */
export const REQUIRED_TENANT_SCOPES: string[] = [
  // Send messages / cards as the bot.
  "im:message:send_as_bot",
  // Add an emoji reaction to an incoming message as a lightweight receipt ack.
  "im:message.reactions:write_only",
  // Receive single-chat messages sent to the bot.
  "im:message.p2p_msg:readonly",
  // Receive group messages that @mention the bot.
  "im:message.group_at_msg:readonly",
  // Read this self-built app's own info (app_name) so `channel` can show the
  // real Feishu app name instead of the internal bot id.
  "application:application:self_manage",
];

/** Event subscriptions (tenant events) required by the bot. */
export const REQUIRED_EVENTS: string[] = ["im.message.receive_v1"];

/** Interactive-card action callbacks required by the bot. */
export const REQUIRED_CALLBACKS: string[] = ["card.action.trigger"];

/**
 * The manifest printed for the manual fallback (design §5): when auto-config
 * fails, the user imports this on the open platform ("权限管理 → 批量导入") and
 * subscribes the listed events/callbacks, choosing "仅自己可见" to skip review.
 */
export function buildScopeManifest(): Record<string, unknown> {
  return {
    scopes: { tenant: REQUIRED_TENANT_SCOPES, user: [] },
    events: REQUIRED_EVENTS,
    callbacks: REQUIRED_CALLBACKS,
  };
}
