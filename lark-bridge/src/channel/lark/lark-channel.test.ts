import { describe, it, expect, vi } from "vitest";
import { LarkChannel, type LarkSdk, type LarkEventHandles } from "./lark-channel.js";
import type { BridgeConfig } from "../../config.js";
import type { RawCardAction, RawMessageEvent } from "./events.js";

function makeConfig(): BridgeConfig {
  return {
    lark: {
      appId: "cli_app",
      appSecret: "secret",
      ackEmojiType: "Typing",
    },
    allowedUsers: [],
    automindBin: "/tmp/automind.sh",
    workspaceRoot: "/tmp",
    agent: "auto",
    pollIntervalMs: 2000,
  };
}

interface CreateCall {
  data: { receive_id: string; msg_type: string; content: string };
  params: { receive_id_type: string };
}

function makeFakeSdk() {
  const createCalls: CreateCall[] = [];
  const reactionCalls: Array<{
    data: { reaction_type: { emoji_type: string } };
    path: { message_id: string };
  }> = [];
  const reactionDeleteCalls: Array<{
    path: { message_id: string; reaction_id: string };
  }> = [];
  let registered: LarkEventHandles | null = null;
  let started = false;
  const sdk: LarkSdk = {
    createClient: () => ({
      im: {
        message: {
          create: async (payload: CreateCall) => {
            createCalls.push(payload);
            return { data: { message_id: "om_sent" } };
          },
        },
        messageReaction: {
          create: async (payload) => {
            reactionCalls.push(payload);
            return { data: { reaction_id: "reaction_1" } };
          },
          delete: async (payload) => {
            reactionDeleteCalls.push(payload);
            return { data: { reaction_id: payload.path.reaction_id } };
          },
        },
      },
    }),
    createEventDispatcher: () => ({
      register(handles) {
        registered = handles;
        return this;
      },
    }),
    createWsClient: () => ({
      start: async () => {
        started = true;
      },
      close: () => {},
    }),
  };
  return {
    sdk,
    createCalls,
    reactionCalls,
    reactionDeleteCalls,
    getRegistered: () => registered,
    wasStarted: () => started,
  };
}

describe("LarkChannel", () => {
  it("registers handlers and starts the ws client", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(
      () => {},
      () => {},
    );
    expect(fake.wasStarted()).toBe(true);
    expect(fake.getRegistered()?.["im.message.receive_v1"]).toBeTypeOf("function");
    expect(fake.getRegistered()?.["card.action.trigger"]).toBeTypeOf("function");
  });

  it("routes parsed text messages to onMessage", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    const received: string[] = [];
    await channel.start(
      (m) => {
        received.push(m.text);
      },
      () => {},
    );
    const raw: RawMessageEvent = {
      message: { chat_id: "oc_1", content: JSON.stringify({ text: "hello" }) },
      sender: { sender_id: { open_id: "ou_1" } },
    };
    await fake.getRegistered()?.["im.message.receive_v1"]?.(raw);
    expect(received).toEqual(["hello"]);
  });

  it("ignores unparseable messages without invoking onMessage", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    let calls = 0;
    await channel.start(
      () => {
        calls += 1;
      },
      () => {},
    );
    await fake.getRegistered()?.["im.message.receive_v1"]?.({} as RawMessageEvent);
    expect(calls).toBe(0);
  });

  it("routes parsed card actions to onCardAction", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    const actions: Array<[string, string, string]> = [];
    await channel.start(
      () => {},
      (threadId, optionId, cardKind) => {
        actions.push([threadId, optionId, cardKind]);
      },
    );
    const raw: RawCardAction = {
      operator: { open_id: "ou_1" },
      action: {
        value: { optionId: "confirm", cardKind: "confirm", threadId: "oc_1" },
      },
    };
    await fake.getRegistered()?.["card.action.trigger"]?.(raw);
    expect(actions).toEqual([["oc_1", "confirm", "confirm"]]);
  });

  it("sends plain text via im.message.create", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(
      () => {},
      () => {},
    );
    await channel.send("oc_1", "hi there");
    expect(fake.createCalls).toHaveLength(1);
    const call = fake.createCalls[0];
    expect(call.data.msg_type).toBe("text");
    expect(call.data.receive_id).toBe("oc_1");
    expect(call.params.receive_id_type).toBe("chat_id");
    expect(JSON.parse(call.data.content)).toEqual({ text: "hi there" });
  });

  it("sends an interactive card and returns the message id", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(
      () => {},
      () => {},
    );
    const id = await channel.sendCard("oc_1", {
      kind: "confirm",
      title: "确认",
      body: "开始？",
      options: [{ id: "confirm", label: "开始", recommended: true }],
    });
    expect(id).toBe("om_sent");
    const call = fake.createCalls[0];
    expect(call.data.msg_type).toBe("interactive");
    const content = JSON.parse(call.data.content);
    const action = (content.elements as any[]).find((e) => e.tag === "action");
    // threadId folded into button value for card.action.trigger routing.
    expect(action.actions[0].value).toEqual({
      optionId: "confirm",
      cardKind: "confirm",
      threadId: "oc_1",
    });
  });

  it("adds the configured typing reaction to an incoming message", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(() => {}, () => {});
    const ack = await channel.ackMessage("om_incoming");
    expect(ack).toEqual({ messageId: "om_incoming", token: "reaction_1" });
    expect(fake.reactionCalls).toEqual([{
      path: { message_id: "om_incoming" },
      data: { reaction_type: { emoji_type: "Typing" } },
    }]);
  });

  it("removes typing without another reaction after successful processing", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(() => {}, () => {});
    const ack = await channel.ackMessage("om_incoming");
    expect(ack).not.toBeNull();
    await channel.finishMessageAck(ack!);
    expect(fake.reactionDeleteCalls).toEqual([{
      path: { message_id: "om_incoming", reaction_id: "reaction_1" },
    }]);
    expect(fake.reactionCalls).toHaveLength(1);
  });

  it("removes typing without another reaction after failed processing", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(() => {}, () => {});
    const ack = await channel.ackMessage("om_incoming");
    await channel.finishMessageAck(ack!);
    expect(fake.reactionDeleteCalls).toHaveLength(1);
    expect(fake.reactionCalls).toHaveLength(1);
  });

  it("does not fail when adding the receipt reaction is rejected", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const fake = makeFakeSdk();
    fake.sdk.createClient = () => ({
      im: {
        message: {
          create: async () => ({ data: { message_id: "x" } }),
        },
        messageReaction: {
          create: async () => {
            throw new Error("missing scope");
          },
          delete: async () => ({}),
        },
      },
    });
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(() => {}, () => {});
    await expect(channel.ackMessage("om_incoming")).resolves.toBeNull();
    warn.mockRestore();
  });

  it("throws when sending before start", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await expect(channel.send("oc_1", "hi")).rejects.toThrow(/start must be called/);
  });

  it("redacts secrets from outbound text", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(
      () => {},
      () => {},
    );
    await channel.send("oc_1", "LARK_APP_SECRET=supersecretvalue123");
    const content = JSON.parse(fake.createCalls[0].data.content);
    expect(content.text).toBe("LARK_APP_SECRET=<redacted>");
  });

  it("redacts secrets from a card body before sending", async () => {
    const fake = makeFakeSdk();
    const channel = new LarkChannel(makeConfig(), fake.sdk);
    await channel.start(
      () => {},
      () => {},
    );
    await channel.sendCard("oc_1", {
      kind: "notice",
      title: "n",
      body: "token=sk-abcdef1234567890",
    });
    const content = JSON.parse(fake.createCalls[0].data.content);
    expect(JSON.stringify(content)).toContain("<redacted>");
    expect(JSON.stringify(content)).not.toContain("sk-abcdef1234567890");
  });

  it("reconnects with backoff after the connection drops", async () => {
    let starts = 0;
    const errors: number[] = [];
    let channel!: LarkChannel;
    const sdk: LarkSdk = {
      createClient: () => ({
        im: { message: { create: async () => ({ data: { message_id: "x" } }) } },
      }),
      createEventDispatcher: () => ({
        register() {
          return this;
        },
      }),
      createWsClient: () => ({
        start: async () => {
          starts += 1;
          throw new Error("dropped");
        },
        close: () => {},
      }),
    };
    channel = new LarkChannel(makeConfig(), sdk, {
      sleep: async () => {},
      onError: (_e, attempt) => {
        errors.push(attempt);
        // Stop the reconnect loop after we've observed a couple of retries.
        if (errors.length >= 2) void channel.stop();
      },
    });
    await channel.start(
      () => {},
      () => {},
    );
    // Let the background reconnect loop settle.
    await new Promise((r) => setTimeout(r, 10));
    expect(starts).toBeGreaterThanOrEqual(2);
    expect(errors).toEqual([0, 1]);
  });

  it("closes the previous ws client before each reconnect (one live connection)", async () => {
    // Guards against Feishu error 1000040350 "connections exceeded the limit":
    // a fresh wsClient must be created per attempt and the previous one closed,
    // so two long connections for the same appId never coexist.
    let created = 0;
    let closed = 0;
    let live = 0;
    let maxLive = 0;
    let channel!: LarkChannel;
    const sdk: LarkSdk = {
      createClient: () => ({
        im: { message: { create: async () => ({ data: { message_id: "x" } }) } },
      }),
      createEventDispatcher: () => ({
        register() {
          return this;
        },
      }),
      createWsClient: () => {
        created += 1;
        live += 1;
        maxLive = Math.max(maxLive, live);
        return {
          start: async () => {
            throw new Error("dropped");
          },
          close: () => {
            live -= 1;
            closed += 1;
          },
        };
      },
    };
    channel = new LarkChannel(makeConfig(), sdk, {
      sleep: async () => {},
      onError: (_e, attempt) => {
        if (attempt >= 2) void channel.stop();
      },
    });
    await channel.start(
      () => {},
      () => {},
    );
    await new Promise((r) => setTimeout(r, 10));
    // Each attempt builds a fresh client, and every prior client is torn down,
    // so at no point are two connections alive simultaneously.
    expect(created).toBeGreaterThanOrEqual(2);
    expect(closed).toBeGreaterThanOrEqual(1);
    expect(maxLive).toBe(1);
  });
});
