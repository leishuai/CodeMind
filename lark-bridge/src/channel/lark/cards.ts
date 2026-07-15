/**
 * cards.ts (design §7 / §13.1) — render channel-neutral `Card` objects into
 * Feishu interactive-card JSON. Pure functions (no SDK calls) so they can be
 * unit-tested without credentials.
 */
import type { Card, CardKind } from "../types.js";

interface FeishuElement {
  [key: string]: unknown;
}

const KIND_TEMPLATE: Record<CardKind, string> = {
  confirm: "blue",
  clarify: "orange",
  ask_user: "blue",
  progress: "grey",
  gitdiff: "grey",
  notice: "grey",
  report: "green",
};

/**
 * Convert a Card into a Feishu interactive card payload.
 *
 * `threadId` is folded into every button `value` because `card.action.trigger`
 * events carry no chat_id; the router recovers the thread dimension from the
 * button value (see events.parseCardAction).
 */
export function renderFeishuCard(
  card: Card,
  threadId?: string,
): Record<string, unknown> {
  const elements: FeishuElement[] = [
    {
      tag: "div",
      text: { tag: "lark_md", content: card.body },
    },
  ];

  for (const section of card.collapsible ?? []) {
    elements.push({ tag: "hr" });
    elements.push({
      tag: "div",
      text: { tag: "lark_md", content: `**${section.label}**\n${section.content}` },
    });
  }

  if (card.options && card.options.length > 0) {
    elements.push({
      tag: "action",
      actions: card.options.map((opt) => ({
        tag: "button",
        text: { tag: "plain_text", content: opt.label },
        type: opt.recommended ? "primary" : "default",
        value: {
          optionId: opt.id,
          cardKind: card.kind,
          ...(threadId ? { threadId } : {}),
          ...(card.token ? { token: card.token } : {}),
        },
      })),
    });
  }

  return {
    config: { wide_screen_mode: true },
    header: {
      template: KIND_TEMPLATE[card.kind],
      title: { tag: "plain_text", content: card.title },
    },
    elements,
  };
}
