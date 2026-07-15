import type { ConversationAction, ConversationResponse } from "./conversation.js";
import type { TaskRef } from "./session-map.js";
import {
  CAPABILITY_CATALOG,
  type CapabilityPolicy,
} from "./capability-catalog.js";

export interface ValidatedPlan {
  ok: true;
  actions: ConversationAction[];
}

export interface RejectedPlan {
  ok: false;
  error: string;
}

export class CapabilityRegistry {
  policyFor(capability: ConversationAction["capability"]): CapabilityPolicy {
    return CAPABILITY_CATALOG[capability].policy;
  }

  validate(response: ConversationResponse, tasks: TaskRef[]): ValidatedPlan | RejectedPlan {
    if (response.actions.length > 4) {
      return { ok: false, error: "单轮 action 数量超过上限。" };
    }
    const taskCodes = new Set(tasks.map((task) => task.taskCode));
    let writeCount = 0;
    let confirmationCount = 0;
    for (const action of response.actions) {
      const policy = this.policyFor(action.capability);
      if (policy.effect === "write") writeCount += 1;
      if (policy.requiresConfirmation) confirmationCount += 1;
      if (policy.requiresTaskBinding) {
        const taskCode = taskCodeOf(action);
        if (!taskCode || !taskCodes.has(taskCode)) {
          return {
            ok: false,
            error: taskCode
              ? `任务 ${taskCode} 不属于当前会话。`
              : `${action.capability} 缺少明确 taskCode。`,
          };
        }
      }
    }
    if (writeCount > 1) {
      return { ok: false, error: "单轮只能执行一个会改变任务状态的 action。" };
    }
    if (confirmationCount > 1) {
      return { ok: false, error: "单轮只能创建一个待确认操作。" };
    }
    if (confirmationCount === 1 && response.actions.length !== 1) {
      return { ok: false, error: "需要确认的 action 必须单独执行。" };
    }
    if (writeCount === 1 && response.actions.length !== 1) {
      return { ok: false, error: "会改变任务状态的 action 必须单独执行。" };
    }
    return { ok: true, actions: response.actions };
  }
}

function taskCodeOf(action: ConversationAction): string | null {
  if ("taskCode" in action.arguments) return action.arguments.taskCode;
  return null;
}
