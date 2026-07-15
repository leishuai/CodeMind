import { z } from "zod";

export type CapabilityEffect = "read" | "conversation" | "write";

export interface CapabilityPolicy {
  effect: CapabilityEffect;
  requiresTaskBinding: boolean;
  requiresConfirmation: boolean;
}

const taskCode = z.string().trim().min(1).max(200).regex(/^[A-Za-z0-9_.-]+$/);
const base = {
  confidence: z.number().min(0).max(1).optional().default(0),
  reason: z.string().max(1000).optional().default(""),
};

const actionSchemas = {
  "workspace.get": z.object({
    capability: z.literal("workspace.get"),
    arguments: z.object({}).strict(),
    ...base,
  }).strict(),
  "workspace.changes": z.object({
    capability: z.literal("workspace.changes"),
    arguments: z.object({}).strict(),
    ...base,
  }).strict(),
  "task.list": z.object({
    capability: z.literal("task.list"),
    arguments: z.object({}).strict(),
    ...base,
  }).strict(),
  "task.select": z.object({
    capability: z.literal("task.select"),
    arguments: z.object({ taskCode }).strict(),
    ...base,
  }).strict(),
  "task.status": z.object({
    capability: z.literal("task.status"),
    arguments: z.object({ taskCode }).strict(),
    ...base,
  }).strict(),
  "task.inspect": z.object({
    capability: z.literal("task.inspect"),
    arguments: z.object({
      taskCode,
      view: z.enum([
        "overview",
        "question",
        "plan",
        "delivery",
        "validation",
        "evaluation",
        "summary",
        "report",
        "evidence",
        "logs",
      ]),
    }).strict(),
    ...base,
  }).strict(),
  "task.create": z.object({
    capability: z.literal("task.create"),
    arguments: z.object({
      requirementSummary: z.string().trim().min(1).max(20000),
    }).strict(),
    ...base,
  }).strict(),
  "task.modify": z.object({
    capability: z.literal("task.modify"),
    arguments: z.object({
      taskCode,
      instruction: z.string().trim().min(1).max(20000),
    }).strict(),
    ...base,
  }).strict(),
  "task.resume": z.object({
    capability: z.literal("task.resume"),
    arguments: z.object({ taskCode }).strict(),
    ...base,
  }).strict(),
  "task.answer": z.object({
    capability: z.literal("task.answer"),
    arguments: z.object({
      taskCode,
      answer: z.string().trim().min(1).max(10000),
    }).strict(),
    ...base,
  }).strict(),
  "clarification.request": z.object({
    capability: z.literal("clarification.request"),
    arguments: z.object({
      question: z.string().trim().min(1).max(4000),
    }).strict(),
    ...base,
  }).strict(),
} as const;

export type CapabilityName = keyof typeof actionSchemas;
export type ConversationAction = {
  [K in CapabilityName]: z.infer<(typeof actionSchemas)[K]>;
}[CapabilityName];

export const conversationActionSchema = z.discriminatedUnion("capability", [
  actionSchemas["workspace.get"],
  actionSchemas["workspace.changes"],
  actionSchemas["task.list"],
  actionSchemas["task.select"],
  actionSchemas["task.status"],
  actionSchemas["task.inspect"],
  actionSchemas["task.create"],
  actionSchemas["task.modify"],
  actionSchemas["task.resume"],
  actionSchemas["task.answer"],
  actionSchemas["clarification.request"],
]);

export interface CapabilitySpec {
  policy: CapabilityPolicy;
  promptExample: string;
  purpose: string;
}

export const CAPABILITY_CATALOG: Record<CapabilityName, CapabilitySpec> = {
  "workspace.get": {
    policy: { effect: "read", requiresTaskBinding: false, requiresConfirmation: false },
    purpose: "Read the bot's current workspace.",
    promptExample: '{"capability":"workspace.get","arguments":{}}',
  },
  "workspace.changes": {
    policy: { effect: "read", requiresTaskBinding: false, requiresConfirmation: false },
    purpose: "Read bounded uncommitted workspace changes; results are workspace-wide, not task attribution.",
    promptExample: '{"capability":"workspace.changes","arguments":{}}',
  },
  "task.list": {
    policy: { effect: "read", requiresTaskBinding: false, requiresConfirmation: false },
    purpose: "List tasks owned by this conversation.",
    promptExample: '{"capability":"task.list","arguments":{}}',
  },
  "task.select": {
    policy: { effect: "conversation", requiresTaskBinding: true, requiresConfirmation: false },
    purpose: "Set the conversation's default task.",
    promptExample: '{"capability":"task.select","arguments":{"taskCode":"完整任务码"}}',
  },
  "task.status": {
    policy: { effect: "read", requiresTaskBinding: true, requiresConfirmation: false },
    purpose: "Read current status/phase/iteration and pending question.",
    promptExample: '{"capability":"task.status","arguments":{"taskCode":"完整任务码"}}',
  },
  "task.inspect": {
    policy: { effect: "read", requiresTaskBinding: true, requiresConfirmation: false },
    purpose: "Read bounded task artifacts, evidence, report, or workspace changes.",
    promptExample: '{"capability":"task.inspect","arguments":{"taskCode":"完整任务码","view":"overview|question|plan|delivery|validation|evaluation|summary|report|evidence|logs"}}',
  },
  "task.create": {
    policy: { effect: "write", requiresTaskBinding: false, requiresConfirmation: true },
    purpose: "Create a formal CodeMind task after user confirmation.",
    promptExample: '{"capability":"task.create","arguments":{"requirementSummary":"完整、自包含需求"}}',
  },
  "task.modify": {
    policy: { effect: "write", requiresTaskBinding: true, requiresConfirmation: true },
    purpose: "Inject a self-contained instruction after user confirmation.",
    promptExample: '{"capability":"task.modify","arguments":{"taskCode":"完整任务码","instruction":"自包含修改指令"}}',
  },
  "task.resume": {
    policy: { effect: "write", requiresTaskBinding: true, requiresConfirmation: false },
    purpose: "Resume a paused/retryable task.",
    promptExample: '{"capability":"task.resume","arguments":{"taskCode":"完整任务码"}}',
  },
  "task.answer": {
    policy: { effect: "write", requiresTaskBinding: true, requiresConfirmation: false },
    purpose: "Answer the task's current ask_user question with free text.",
    promptExample: '{"capability":"task.answer","arguments":{"taskCode":"完整任务码","answer":"用户回答"}}',
  },
  "clarification.request": {
    policy: { effect: "conversation", requiresTaskBinding: false, requiresConfirmation: false },
    purpose: "Ask for missing information instead of guessing.",
    promptExample: '{"capability":"clarification.request","arguments":{"question":"澄清问题"}}',
  },
};

export function capabilityPromptLines(): string[] {
  return (Object.entries(CAPABILITY_CATALOG) as [CapabilityName, CapabilitySpec][])
    .map(([name, spec]) => `- ${name}: ${spec.promptExample} // ${spec.purpose}`);
}
