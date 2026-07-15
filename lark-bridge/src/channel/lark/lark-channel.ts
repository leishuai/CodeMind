/**
 * lark-channel.ts (design §13.3) — LarkChannel implements the generic Channel
 * interface. Encapsulates the WSClient long-connection, inbound message/card
 * parsing, and outbound text/card sending via the Feishu OpenAPI Client.
 *
 * The Feishu SDK surface is injected through `LarkSdk` so the pure wiring can be
 * unit-tested with a fake SDK (no real credentials / network). The default
 * factory builds the real @larksuiteoapi/node-sdk objects.
 */
import type {
  Card,
  CardActionHandler,
  Channel,
  MessageAck,
  MessageHandler,
} from "../types.js";
import type { BridgeConfig } from "../../config.js";
import { renderFeishuCard } from "./cards.js";
import { redactText } from "../../orchestrator/redact.js";
import {
  runWithReconnect,
  type BackoffOptions,
  type Sleep,
} from "../../orchestrator/backoff.js";
import {
  parseCardAction,
  parseMessageEvent,
  type RawCardAction,
  type RawMessageEvent,
} from "./events.js";

/** Handlers registered on the event dispatcher. */
export interface LarkEventHandles {
  "im.message.receive_v1"?: (data: RawMessageEvent) => unknown;
  "card.action.trigger"?: (data: RawCardAction) => unknown;
  [key: string]: ((data: any) => unknown) | undefined;
}

export interface LarkEventDispatcher {
  register(handles: LarkEventHandles): LarkEventDispatcher;
}

export interface LarkWsClient {
  start(params: { eventDispatcher: LarkEventDispatcher }): Promise<void>;
  close?(params?: { force?: boolean }): void;
}

export interface LarkApiClient {
  im: {
    message: {
      create(payload: {
        data: { receive_id: string; msg_type: string; content: string };
        params: { receive_id_type: string };
      }): Promise<{ data?: { message_id?: string } }>;
    };
    messageReaction?: {
      create(payload: {
        data: { reaction_type: { emoji_type: string } };
        path: { message_id: string };
      }): Promise<{ data?: { reaction_id?: string } }>;
      delete(payload: {
        path: { message_id: string; reaction_id: string };
      }): Promise<unknown>;
    };
  };
}

/** Injectable Feishu SDK factory. Defaults to @larksuiteoapi/node-sdk. */
export interface LarkSdk {
  createClient(config: BridgeConfig): LarkApiClient;
  createEventDispatcher(config: BridgeConfig): LarkEventDispatcher;
  createWsClient(config: BridgeConfig): LarkWsClient;
}

async function defaultSdk(): Promise<LarkSdk> {
  const lark = await import("@larksuiteoapi/node-sdk");
  // Silence the SDK's chatty info-level logs (e.g. the repeated "ws client
  // ready" / "event-dispatch is ready" lines that flood the TUI on every
  // reconnect). We set warn on the Client, WSClient AND EventDispatcher so all
  // three components stay quiet; warn/error still surface genuine problems, and
  // our own reconnect notices are logged separately and are unaffected.
  const loggerLevel = lark.LoggerLevel.warn;
  return {
    createClient: (config) =>
      new lark.Client({
        appId: config.lark.appId,
        appSecret: config.lark.appSecret,
        loggerLevel,
      }) as unknown as LarkApiClient,
    createEventDispatcher: () =>
      new lark.EventDispatcher({ loggerLevel }) as unknown as LarkEventDispatcher,
    createWsClient: (config) =>
      new lark.WSClient({
        appId: config.lark.appId,
        appSecret: config.lark.appSecret,
        loggerLevel,
      }) as unknown as LarkWsClient,
  };
}

/** Optional long-connection reconnect policy (design §13.5 / M5). */
export interface ReconnectOptions {
  /** Backoff schedule between reconnect attempts. */
  backoff?: BackoffOptions;
  /** Injectable sleep for deterministic tests. */
  sleep?: Sleep;
  /** Called on each failed/ended session with the attempt index. */
  onError?: (err: unknown, attempt: number) => void;
}

export class LarkChannel implements Channel {
  private readonly config: BridgeConfig;
  private readonly channelId: string;
  private readonly sdkPromise: Promise<LarkSdk>;
  private readonly reconnect?: ReconnectOptions;
  private client: LarkApiClient | null = null;
  private wsClient: LarkWsClient | null = null;
  private stopped = false;

  constructor(config: BridgeConfig, sdk?: LarkSdk, reconnect?: ReconnectOptions) {
    this.config = config;
    this.channelId = config.lark.appId || "lark";
    this.sdkPromise = sdk ? Promise.resolve(sdk) : defaultSdk();
    this.reconnect = reconnect;
  }

  async start(
    onMessage: MessageHandler,
    onCardAction: CardActionHandler,
  ): Promise<void> {
    const sdk = await this.sdkPromise;
    this.client = sdk.createClient(this.config);

    const dispatcher = sdk.createEventDispatcher(this.config);
    dispatcher.register({
      "im.message.receive_v1": (data) => {
        const message = parseMessageEvent(data, this.channelId);
        if (message) return onMessage(message);
        return undefined;
      },
      "card.action.trigger": (data) => {
        const action = parseCardAction(data);
        if (action) {
          return onCardAction(
            action.threadId,
            action.optionId,
            action.cardKind,
            action.token,
            action.userId,
          );
        }
        return undefined;
      },
    });

    // Each connection attempt tears down the PREVIOUS wsClient before opening a
    // fresh one. The Feishu SDK's WSClient auto-reconnects internally, so if our
    // outer reconnect loop reused one instance (or left an old one alive) two
    // long connections for the same appId could briefly coexist and the server
    // rejects the extra one with `code 1000040350 (connections exceeded the
    // limit)`. Closing before recreating guarantees at most one live connection.
    const connect = async () => {
      this.wsClient?.close?.({ force: true });
      const ws = sdk.createWsClient(this.config);
      this.wsClient = ws;
      await ws.start({ eventDispatcher: dispatcher });
    };

    if (!this.reconnect) {
      // No explicit policy: rely on the SDK's built-in reconnect, connect once.
      await connect();
      return;
    }
    // Explicit reconnect loop: keep re-establishing the long connection with a
    // backoff between attempts until stop() is called. Runs in the background
    // so start() resolves once the first attempt has been kicked off.
    void runWithReconnect(connect, {
      backoff: this.reconnect.backoff,
      sleep: this.reconnect.sleep,
      shouldStop: () => this.stopped,
      onError:
        this.reconnect.onError ??
        ((err, attempt) =>
          console.error(`[lark-bridge] 长连中断，准备第 ${attempt + 1} 次重连:`, err)),
    });
  }

  async send(threadId: string, text: string): Promise<void> {
    const client = this.requireClient();
    await client.im.message.create({
      data: {
        receive_id: threadId,
        msg_type: "text",
        content: JSON.stringify({ text: redactText(text) }),
      },
      params: { receive_id_type: "chat_id" },
    });
  }

  async sendCard(threadId: string, card: Card): Promise<string> {
    const client = this.requireClient();
    const rendered = renderFeishuCard(redactCard(card), threadId);
    const res = await client.im.message.create({
      data: {
        receive_id: threadId,
        msg_type: "interactive",
        content: JSON.stringify(rendered),
      },
      params: { receive_id_type: "chat_id" },
    });
    return res.data?.message_id ?? "";
  }

  async ackMessage(messageId: string): Promise<MessageAck | null> {
    if (!messageId) return null;
    const client = this.requireClient();
    if (!client.im.messageReaction) return null;
    try {
      const response = await client.im.messageReaction.create({
        path: { message_id: messageId },
        data: {
          reaction_type: {
            emoji_type: this.config.lark.ackEmojiType,
          },
        },
      });
      const reactionId = response.data?.reaction_id ?? "";
      return reactionId ? { messageId, token: reactionId } : null;
    } catch (error) {
      // A missing reaction scope or tenant-specific emoji key must never block
      // message processing; the normal reply/card flow remains authoritative.
      console.warn(
        `[lark-bridge] failed to add ack reaction ${this.config.lark.ackEmojiType} to ${messageId}:`,
        error,
      );
      return null;
    }
  }

  async finishMessageAck(ack: MessageAck): Promise<void> {
    const client = this.requireClient();
    if (!client.im.messageReaction) return;
    try {
      await client.im.messageReaction.delete({
        path: {
          message_id: ack.messageId,
          reaction_id: ack.token,
        },
      });
    } catch (error) {
      console.warn(
        `[lark-bridge] failed to clear message reaction for ${ack.messageId}:`,
        error,
      );
    }
  }

  async stop(): Promise<void> {
    this.stopped = true;
    this.wsClient?.close?.({ force: true });
  }

  private requireClient(): LarkApiClient {
    if (!this.client) {
      throw new Error("LarkChannel.start must be called before sending.");
    }
    return this.client;
  }
}

/** Redact secrets from a card's textual fields before it goes out to Feishu. */
function redactCard(card: Card): Card {
  return {
    ...card,
    body: redactText(card.body),
    collapsible: card.collapsible?.map((section) => ({
      label: section.label,
      content: redactText(section.content),
    })),
  };
}
