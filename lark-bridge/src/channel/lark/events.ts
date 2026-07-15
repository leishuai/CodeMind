/**
 * events.ts (design §13.3 / M1) — pure parsers that translate raw Feishu event
 * payloads into the channel-neutral shapes the orchestrator consumes. Kept free
 * of SDK/network calls so they are fully unit-testable.
 *
 * The @larksuiteoapi/node-sdk EventDispatcher delivers the event body with
 * `message`/`sender` at the top level (not nested under `event`), and card
 * actions carry `operator.open_id` + `action.value`. These parsers accept that
 * shape and tolerate missing fields.
 */
import type { IncomingMessage } from "../types.js";

/** Minimal shape of an `im.message.receive_v1` event body. */
export interface RawMessageEvent {
  message?: {
    message_id?: string;
    chat_id?: string;
    thread_id?: string;
    message_type?: string;
    content?: string;
  };
  sender?: {
    sender_id?: { open_id?: string; user_id?: string };
  };
}

/** Minimal shape of a `card.action.trigger` event body. */
export interface RawCardAction {
  operator?: { open_id?: string; user_id?: string };
  action?: {
    value?: unknown;
    tag?: string;
    option?: string;
  };
}

export interface ParsedCardAction {
  threadId: string;
  optionId: string;
  cardKind: string;
  userId: string;
  /** Anti-replay token carried by the button value ("" when absent). */
  token: string;
}

/**
 * Parse an inbound message event into an IncomingMessage. Returns null for
 * unsupported message types (only plain text is handled in M1) or malformed
 * payloads.
 */
export function parseMessageEvent(
  raw: RawMessageEvent,
  channelId: string,
): IncomingMessage | null {
  const message = raw.message;
  if (!message) return null;
  if ((message.message_type ?? "text") !== "text") return null;

  const text = extractText(message.content);
  if (!text) return null;

  // Thread dimension: use chat_id as the key because Feishu `im.message.create`
  // can address a chat_id but not a thread_id (replies into a thread need the
  // reply API + a message_id). One chat maps to one S_chat under session model B,
  // so chat_id is the stable, send-able thread key. Fall back to thread_id only
  // when chat_id is somehow absent.
  const threadId = message.chat_id || message.thread_id || "";
  if (!threadId) return null;

  const userId = raw.sender?.sender_id?.open_id ?? "";

  return {
    channelId,
    threadId,
    userId,
    text,
    isSlashCommand: text.trim().startsWith("/"),
    messageId: message.message_id ?? "",
  };
}

/**
 * Coerce a card action `value` (object or JSON string) into a plain object.
 * Feishu may deliver the button value either already parsed or as a string.
 */
function coerceValue(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object") {
    return value as Record<string, unknown>;
  }
  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object"
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

/** Parse a card action into the fields the router's onCardAction expects. */
export function parseCardAction(raw: RawCardAction): ParsedCardAction | null {
  const value = coerceValue(raw.action?.value);
  if (!value) return null;
  const optionId = String(value.optionId ?? "");
  const cardKind = String(value.cardKind ?? "");
  if (!optionId || !cardKind) return null;
  // card.action.trigger has no chat_id; the button value must carry threadId.
  const threadId = String(value.threadId ?? "");
  if (!threadId) return null;
  return {
    threadId,
    optionId,
    cardKind,
    userId: raw.operator?.open_id ?? "",
    token: String(value.token ?? ""),
  };
}

/** Extract text from a Feishu text-message content JSON string. */
export function extractText(content: string | undefined): string {
  if (!content) return "";
  try {
    const parsed = JSON.parse(content) as { text?: unknown };
    return typeof parsed.text === "string" ? parsed.text.trim() : "";
  } catch {
    return "";
  }
}
