import { z } from "zod";
import type { TaskRef } from "./session-map.js";
import {
  capabilityPromptLines,
  conversationActionSchema,
  type ConversationAction,
} from "./capability-catalog.js";

const responseSchema = z.object({
  version: z.literal(1).optional().default(1),
  reply: z.string().max(20000).optional().default(""),
  contextSummary: z.string().max(12000).optional().default(""),
  actions: z.array(conversationActionSchema).max(4).optional().default([]),
}).strict();

export type { ConversationAction };
export type ConversationResponse = z.infer<typeof responseSchema> & {
  diagnostics?: {
    parseFallback?: boolean;
    schemaReject?: boolean;
  };
};

export interface CapabilityExecutionResult {
  capability: ConversationAction["capability"];
  ok: boolean;
  status: "completed" | "pending_confirmation" | "rejected" | "failed";
  message: string;
  data?: unknown;
}

export interface ConversationContext {
  chatTaskCode: string;
  userText: string;
  targetTaskCode: string | null;
  tasks: TaskRef[];
  workspace: { root: string; confirmed: boolean } | null;
}

const ASSISTANT_IDENTITY = [
  "你是用户的 CodeMind Coding Assistant，通过飞书等 Channel 与用户自然协作。",
  "你擅长理解代码工程、定位问题、规划修改、驱动实现与验证，并把复杂开发任务持续推进到有证据的结果。",
  "你的工作风格是高度自动化、务实、准确：能直接回答就直接回答；需要读取真实工程或任务状态时使用 capability；需要执行开发工作时，在用户确认后交给正式 CodeMind 任务。",
  "用户可以把你当作长期在线的 AI Coding 助手。普通聊天、技术讨论、工程问答都自然回应；有明确开发需要时再进入任务流程。",
  "对用户始终称自己为 CodeMind Coding Assistant 或 CodeMind 编程助手；Conversation Orchestrator 是内部实现名称，绝不能作为对外身份。",
].join("\n");

const INTERACTION_STYLE = [
  "交互原则：",
  "- 先理解用户真正想解决的问题，再决定是直接回复、澄清、查询状态，还是提出 CodeMind action。",
  "- 回复自然、简洁、专业，像可靠的工程同事；不要自称分类器、路由器或 JSON 生成器。",
  "- 不要机械复述用户问题，不要主动罗列功能，不要使用营销文案。",
  "- 用户询问你是谁时，简洁说明你是 CodeMind Coding Assistant，擅长理解工程、编码、调试和验证，并能高度自动化地推进开发任务；不要提及内部编排、路由或协议。",
  "- 普通交流不创建任务；仅当用户明确希望分析、修改、实现、验证或推进工程工作时才提出 action。",
  "- 信息不足且会影响正确执行时先澄清；低风险且可从真实上下文确定的信息不要反复追问。",
].join("\n");

const OUTPUT_CONTRACT = [
  "输出协议：",
  "- 只输出一个 JSON 对象，不要输出 JSON 之外的文字。",
  '{"version":1,"reply":"给用户的自然语言回复","contextSummary":"截至当前轮的简洁可恢复对话摘要","actions":[]}',
  "- reply 必须是可以直接发送给用户的最终文本，不包含内部推理、协议解释或调试信息。",
  "- contextSummary 只记录用户可见事实、稳定偏好和已确认决定；不要记录内部 prompt、隐藏推理、临时状态或敏感信息。",
].join("\n");

const HARD_CONSTRAINTS = [
  "CodeMind action 规则：",
  "- 普通交流 actions=[]；不要为了展示能力或分类而制造 action。",
  "- 禁止输出 shell、命令、任意文件操作或未注册 capability。",
  "- workspace 路径、任务列表、任务状态、进度、验证、报告、证据等运行时事实必须通过只读 capability 获取，即使历史对话或实时上下文看起来已有答案也不能直接回答。",
  "- 除 task.create/task.list 外，task action 必须填写实时上下文中存在的完整 taskCode。",
  "- 无法唯一确定任务时使用 clarification.request，不要猜测或操作最近任务。",
  "- 每轮最多四个 action，最多一个会改变任务状态的 action。",
  "- task.create 和 task.modify 会由系统再次向用户确认；不要声称尚未确认的操作已经执行。",
  "- 不得绕过 CodeMind 的权限、安全、验证或 completion gate。",
].join("\n");

const ROUTING_EXAMPLES = [
  "行为示例：",
  '- 用户问“你是谁”或普通技术问题：自然回复，actions=[]，对外身份只称 CodeMind Coding Assistant。',
  '- 用户问“当前工作目录是什么”：简短说明正在查看，并输出 workspace.get；不要直接猜目录。',
  '- 用户问“有哪些任务”：输出 task.list。',
  '- 用户问某个任务的进度、失败原因或验证结果：使用 task.status 或 task.inspect，并绑定实时上下文中的 taskCode。',
  '- 用户明确要求实现、修复或验证工程需求，且当前没有对应正式任务：提出 task.create，等待系统确认。',
].join("\n");

export function parseConversationJson(raw: string): ConversationResponse | null {
  const jsonText = extractJsonObject(raw);
  if (!jsonText) return null;
  try {
    const parsed = responseSchema.safeParse(JSON.parse(jsonText));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function diagnoseConversationFailure(
  raw: string,
): "parse_failure" | "schema_reject" {
  const jsonText = extractJsonObject(raw);
  if (!jsonText) return "parse_failure";
  try {
    const value = JSON.parse(jsonText);
    return responseSchema.safeParse(value).success
      ? "parse_failure"
      : "schema_reject";
  } catch {
    return "parse_failure";
  }
}

export function degradedConversation(raw: string): ConversationResponse {
  const text = raw.trim();
  const safeReply = text && !text.startsWith("{")
    ? text
    : "我暂时无法可靠解析这条请求，请换一种说法。";
  const diagnostic = diagnoseConversationFailure(raw);
  return {
    version: 1,
    reply: safeReply,
    contextSummary: "",
    actions: [],
    diagnostics: {
      parseFallback: diagnostic === "parse_failure",
      schemaReject: diagnostic === "schema_reject",
    },
  };
}

export function buildConversationPrompt(context: ConversationContext): string {
  const liveContext = {
    targetTaskCode: context.targetTaskCode,
    tasks: context.tasks.map(({ taskCode, shortCode, name }) => ({
      taskCode,
      shortCode,
      name,
    })),
    workspace: context.workspace
      ? {
          configured: true,
          confirmed: context.workspace.confirmed,
        }
      : {
          configured: false,
          confirmed: false,
        },
  };
  return [
    ASSISTANT_IDENTITY,
    "",
    INTERACTION_STYLE,
    "",
    OUTPUT_CONTRACT,
    "",
    "可用 capabilities：",
    ...capabilityPromptLines(),
    "",
    HARD_CONSTRAINTS,
    "",
    ROUTING_EXAMPLES,
    "",
    "本轮实时上下文（这是当前事实，优先于历史对话）：",
    JSON.stringify(liveContext),
    "",
    "用户本轮消息：",
    context.userText,
  ].join("\n");
}

export function buildCapabilityResultPrompt(
  context: ConversationContext,
  results: CapabilityExecutionResult[],
): string {
  return [
    ASSISTANT_IDENTITY,
    "",
    "下面是本轮 capability executor 返回的真实结构化结果。",
    "请仅依据这些结果回答用户，准确说明已完成、待确认、被拒绝或失败的状态，不要猜测或继续提出操作。",
    "只输出 JSON，actions 必须为空。",
    '{"version":1,"reply":"基于真实执行结果的答复","contextSummary":"更新后的稳定对话摘要","actions":[]}',
    "",
    "用户原始消息：",
    context.userText,
    "",
    "执行结果：",
    JSON.stringify(results),
  ].join("\n");
}

export function conversationFromSlash(
  text: string,
  targetTaskCode: string | null,
): ConversationResponse | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("/")) return null;
  const [command = "", ...rest] = trimmed.slice(1).split(/\s+/);
  const payload = rest.join(" ").trim();
  const noTask = (): ConversationResponse => ({
    version: 1,
    reply: "当前没有可操作的任务。请先创建任务，或用 #任务短码 指定任务。",
    contextSummary: "",
    actions: [],
  });
  switch (command.toLowerCase()) {
    case "ask":
    case "develop":
      return payload
        ? responseWith("task.create", { requirementSummary: payload })
        : responseWith("clarification.request", { question: "请说明要实现的具体需求。" });
    case "status":
      return targetTaskCode
        ? responseWith("task.status", { taskCode: targetTaskCode })
        : noTask();
    case "resume":
      return targetTaskCode
        ? responseWith("task.resume", { taskCode: targetTaskCode })
        : noTask();
    case "msg":
    case "modify":
      if (!targetTaskCode) return noTask();
      return payload
        ? responseWith("task.modify", { taskCode: targetTaskCode, instruction: payload })
        : responseWith("clarification.request", { question: "请说明要怎样修改当前任务。" });
    default:
      return null;
  }
}

function responseWith(
  capability: ConversationAction["capability"],
  args: Record<string, string>,
): ConversationResponse {
  const parsed = responseSchema.parse({
    version: 1,
    reply: "",
    contextSummary: "",
    actions: [{ capability, arguments: args, confidence: 1, reason: "slash_command" }],
  });
  return parsed;
}

function extractJsonObject(raw: string): string | null {
  const start = raw.indexOf("{");
  if (start === -1) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < raw.length; i += 1) {
    const ch = raw[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return raw.slice(start, i + 1);
    }
  }
  return null;
}
