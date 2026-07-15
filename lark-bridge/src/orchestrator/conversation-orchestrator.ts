import type { CodeMindCli } from "./automind-cli.js";
import {
  buildConversationPrompt,
  buildCapabilityResultPrompt,
  conversationFromSlash,
  degradedConversation,
  parseConversationJson,
  type CapabilityExecutionResult,
  type ConversationContext,
  type ConversationResponse,
} from "./conversation.js";

export interface ConversationOrchestrator {
  interpret(context: ConversationContext): Promise<ConversationResponse>;
  respondToResults(
    context: ConversationContext,
    results: CapabilityExecutionResult[],
  ): Promise<ConversationResponse>;
}

export class DefaultConversationOrchestrator implements ConversationOrchestrator {
  constructor(
    private readonly cli: CodeMindCli,
    private readonly agent: string,
  ) {}

  async interpret(context: ConversationContext): Promise<ConversationResponse> {
    const slash = conversationFromSlash(context.userText, context.targetTaskCode);
    if (slash) {
      const recorded = await this.cli.recordConversationInput(
        context.chatTaskCode,
        context.userText,
      );
      if (recorded.code !== 0) {
        return {
          version: 1,
          reply: "无法持久化当前命令，请稍后重试。",
          contextSummary: "",
          actions: [],
        };
      }
      return slash;
    }

    const result = await this.cli.converse(
      context.chatTaskCode,
      buildConversationPrompt(context),
      context.userText,
      this.agent,
    );
    if (result.code !== 0) {
      return {
        version: 1,
        reply: "对话模型暂时不可用，请稍后重试。",
        contextSummary: "",
        actions: [],
      };
    }
    return parseConversationJson(result.stdout) ?? degradedConversation(result.stdout);
  }

  async respondToResults(
    context: ConversationContext,
    results: CapabilityExecutionResult[],
  ): Promise<ConversationResponse> {
    const result = await this.cli.converse(
      context.chatTaskCode,
      buildCapabilityResultPrompt(context, results),
      context.userText,
      this.agent,
      true,
    );
    if (result.code !== 0) {
      return {
        version: 1,
        reply: results.map((item) => item.message).filter(Boolean).join("\n") ||
          "操作已执行，但模型暂时无法整理回复。",
        contextSummary: "",
        actions: [],
      };
    }
    const parsed = parseConversationJson(result.stdout);
    return parsed && parsed.actions.length === 0
      ? parsed
      : {
          version: 1,
          reply: results.map((item) => item.message).filter(Boolean).join("\n") ||
            "操作已执行。",
          contextSummary: parsed?.contextSummary ?? "",
          actions: [],
        };
  }
}
