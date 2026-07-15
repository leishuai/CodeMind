import { describe, it, expect } from "vitest";
import { parseMessageEvent, parseCardAction, extractText } from "./events.js";

describe("parseMessageEvent", () => {
  it("parses a text message into an IncomingMessage", () => {
    const msg = parseMessageEvent(
      {
        message: {
          message_id: "om_1",
          chat_id: "oc_1",
          thread_id: "omt_9",
          message_type: "text",
          content: JSON.stringify({ text: "帮我加个登录页" }),
        },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg).toEqual({
      channelId: "lark-bot",
      threadId: "oc_1",
      userId: "ou_user",
      text: "帮我加个登录页",
      isSlashCommand: false,
      messageId: "om_1",
    });
  });

  it("falls back to thread_id when chat_id is absent", () => {
    const msg = parseMessageEvent(
      {
        message: {
          thread_id: "omt_9",
          message_type: "text",
          content: JSON.stringify({ text: "hi" }),
        },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg?.threadId).toBe("omt_9");
  });

  it("marks slash commands", () => {
    const msg = parseMessageEvent(
      {
        message: {
          chat_id: "oc_1",
          content: JSON.stringify({ text: "/status" }),
        },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg?.isSlashCommand).toBe(true);
  });

  it("returns null for non-text messages", () => {
    const msg = parseMessageEvent(
      {
        message: {
          chat_id: "oc_1",
          message_type: "image",
          content: JSON.stringify({ image_key: "img" }),
        },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg).toBeNull();
  });

  it("returns null when message is missing", () => {
    expect(parseMessageEvent({}, "lark-bot")).toBeNull();
  });

  it("returns null when text is empty", () => {
    const msg = parseMessageEvent(
      {
        message: { chat_id: "oc_1", content: JSON.stringify({ text: "   " }) },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg).toBeNull();
  });

  it("returns null when both thread_id and chat_id are missing", () => {
    const msg = parseMessageEvent(
      {
        message: { content: JSON.stringify({ text: "hi" }) },
        sender: { sender_id: { open_id: "ou_user" } },
      },
      "lark-bot",
    );
    expect(msg).toBeNull();
  });

  it("defaults userId to empty string when sender is missing", () => {
    const msg = parseMessageEvent(
      {
        message: { chat_id: "oc_1", content: JSON.stringify({ text: "hi" }) },
      },
      "lark-bot",
    );
    expect(msg?.userId).toBe("");
  });
});

describe("parseCardAction", () => {
  it("parses an object value carrying threadId", () => {
    const parsed = parseCardAction({
      operator: { open_id: "ou_user" },
      action: {
        value: { optionId: "confirm", cardKind: "confirm", threadId: "omt_9" },
      },
    });
    expect(parsed).toEqual({
      threadId: "omt_9",
      optionId: "confirm",
      cardKind: "confirm",
      userId: "ou_user",
      token: "",
    });
  });

  it("parses the anti-replay token from the value", () => {
    const parsed = parseCardAction({
      operator: { open_id: "ou_user" },
      action: {
        value: {
          optionId: "confirm",
          cardKind: "confirm",
          threadId: "omt_9",
          token: "nonce-abc",
        },
      },
    });
    expect(parsed?.token).toBe("nonce-abc");
  });

  it("parses a JSON-string value", () => {
    const parsed = parseCardAction({
      operator: { open_id: "ou_user" },
      action: {
        value: JSON.stringify({
          optionId: "1",
          cardKind: "ask_user",
          threadId: "omt_9",
        }),
      },
    });
    expect(parsed?.optionId).toBe("1");
    expect(parsed?.threadId).toBe("omt_9");
  });

  it("returns null when value is missing", () => {
    expect(parseCardAction({ operator: { open_id: "x" }, action: {} })).toBeNull();
  });

  it("returns null when threadId is missing", () => {
    const parsed = parseCardAction({
      operator: { open_id: "ou_user" },
      action: { value: { optionId: "confirm", cardKind: "confirm" } },
    });
    expect(parsed).toBeNull();
  });

  it("returns null when optionId or cardKind is missing", () => {
    const parsed = parseCardAction({
      operator: { open_id: "ou_user" },
      action: { value: { threadId: "omt_9" } },
    });
    expect(parsed).toBeNull();
  });

  it("defaults userId to empty string when operator is missing", () => {
    const parsed = parseCardAction({
      action: {
        value: { optionId: "confirm", cardKind: "confirm", threadId: "omt_9" },
      },
    });
    expect(parsed?.userId).toBe("");
  });
});

describe("extractText", () => {
  it("extracts and trims text from content JSON", () => {
    expect(extractText(JSON.stringify({ text: "  hi  " }))).toBe("hi");
  });

  it("returns empty string for undefined content", () => {
    expect(extractText(undefined)).toBe("");
  });

  it("returns empty string for non-JSON content", () => {
    expect(extractText("not json")).toBe("");
  });

  it("returns empty string when text field is not a string", () => {
    expect(extractText(JSON.stringify({ text: 123 }))).toBe("");
  });
});
