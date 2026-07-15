/**
 * Channel abstraction (design §13.3).
 *
 * These interfaces are intentionally free of any Feishu/Lark specifics so the
 * orchestrator layer depends only on this file. Adding another chat channel
 * (WeCom, Slack, Telegram, ...) means implementing `Channel` under
 * `channel/<name>/` and wiring it in main.ts; the orchestrator does not change.
 */

/** A message arriving from a chat channel. */
export interface IncomingMessage {
  /** Channel instance identifier (e.g. which bot/app). */
  channelId: string;
  /** Conversation/thread dimension. Maps to one S_chat + at most one task. */
  threadId: string;
  /** Originating user id, used for the allow-list. */
  userId: string;
  /** Raw message text. */
  text: string;
  /** True when text is an explicit slash command (e.g. `/ask`, `/status`). */
  isSlashCommand: boolean;
  /** Stable per-message id for idempotent handling ("" when unavailable). */
  messageId: string;
}

/** Kinds of cards the orchestrator can ask a channel to render (design §7). */
export type CardKind =
  | "confirm" // start-task summary confirmation / modify-task confirmation
  | "clarify" // model asked for clarification
  | "ask_user" // core ask_user gate
  | "progress" // key progress / phase state (read-only)
  | "gitdiff" // code change summary (read-only)
  | "notice" // busy "received" hint (read-only)
  | "report"; // final report

export interface CardOption {
  id: string;
  label: string;
  recommended?: boolean;
}

export interface CardCollapsible {
  label: string;
  content: string;
}

/** Channel-neutral card description; adapters render it to native cards. */
export interface Card {
  kind: CardKind;
  title: string;
  /** Markdown body. */
  body: string;
  /** Interactive button options (for confirm/clarify/ask_user). */
  options?: CardOption[];
  /** Collapsible sections (e.g. git diff detail). */
  collapsible?: CardCollapsible[];
  /**
   * Anti-replay token folded into every button value. For confirm cards it is
   * a per-decision nonce; for ask_user cards it is the core questionId. The
   * router rejects a tapped button whose token no longer matches the current
   * pending decision (stale/replayed card).
   */
  token?: string;
}

export interface MessageAck {
  messageId: string;
  token: string;
}

/** Handler invoked for each inbound message. */
export type MessageHandler = (message: IncomingMessage) => void | Promise<void>;

/** Handler invoked when a user taps a card button. */
export type CardActionHandler = (
  threadId: string,
  optionId: string,
  cardKind: CardKind | string,
  token: string,
  /** Originating user id, used for the allow-list on button taps. */
  userId: string,
) => void | Promise<void>;

/** A pluggable chat channel. */
export interface Channel {
  /** Begin receiving events (connect, long-poll, etc.). */
  start(onMessage: MessageHandler, onCardAction: CardActionHandler): Promise<void>;
  /** Send a plain-text reply into a thread. */
  send(threadId: string, text: string): Promise<void>;
  /** Send an interactive card; returns the sent message id. */
  sendCard(threadId: string, card: Card): Promise<string>;
  /** Optional immediate lightweight acknowledgment for an incoming message. */
  ackMessage?(messageId: string): Promise<MessageAck | null>;
  /** Clear the temporary acknowledgment when processing ends. */
  finishMessageAck?(ack: MessageAck): Promise<void>;
  /** Optional graceful shutdown. */
  stop?(): Promise<void>;
}
