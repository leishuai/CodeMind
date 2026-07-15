import type {
  CapabilityExecutionResult,
  ConversationAction,
} from "./conversation.js";

export interface TurnTimings {
  totalSeconds: number;
  planSeconds?: number;
  executorSeconds?: number;
  resultResponseSeconds?: number;
}

interface ObservationAudit {
  type: "decision_made" | "branch_taken" | "action_executed" |
    "gate_result" | "policy_evaluation" | "recovery_attempt" |
    "fallback_triggered" | "skip_decision";
  phase: "conversation";
  decisionType?: string;
  action?: string;
  reasonCode?: string;
  riskLevel?: "low" | "medium" | "high" | "critical";
  details?: Record<string, unknown>;
}

interface ObservationMetric {
  name: string;
  value: number;
  unit: "count" | "seconds";
}

export interface TurnObservation {
  source: "lark-bridge";
  audit: ObservationAudit[];
  metrics: ObservationMetric[];
}

function metric(name: string, value: number, unit: "count" | "seconds"): ObservationMetric {
  return { name, value, unit };
}

export function buildTurnObservation(options: {
  actions: ConversationAction[];
  results?: CapabilityExecutionResult[];
  planAccepted: boolean;
  planRejectReasonCode?: string;
  parseFallback?: boolean;
  schemaReject?: boolean;
  timings: TurnTimings;
}): TurnObservation {
  const audit: ObservationAudit[] = [];
  const metrics: ObservationMetric[] = [
    metric("conversation_turn_count", 1, "count"),
    metric("capability_total_duration", options.timings.totalSeconds, "seconds"),
  ];
  if (options.parseFallback) {
    metrics.push(metric("capability_parse_failure_count", 1, "count"));
    audit.push({
      type: "fallback_triggered",
      phase: "conversation",
      decisionType: "conversation_parse",
      action: "reply_only",
      reasonCode: "conversation_response_parse_failed",
      riskLevel: "medium",
      details: {
        entrypoint: "lark",
        parseStatus: "failed",
        decision: "reply_only",
      },
    });
  }
  if (options.schemaReject) {
    metrics.push(metric("capability_schema_reject_count", 1, "count"));
    audit.push({
      type: "policy_evaluation",
      phase: "conversation",
      decisionType: "conversation_schema",
      action: "reject",
      reasonCode: "conversation_response_schema_rejected",
      riskLevel: "medium",
      details: {
        entrypoint: "lark",
        parseStatus: "schema_rejected",
        decision: "reply_only",
      },
    });
  }
  if (options.timings.planSeconds !== undefined) {
    metrics.push(metric("capability_plan_duration", options.timings.planSeconds, "seconds"));
  }
  if (options.timings.executorSeconds !== undefined) {
    metrics.push(metric("capability_executor_duration", options.timings.executorSeconds, "seconds"));
  }
  if (options.timings.resultResponseSeconds !== undefined) {
    metrics.push(metric(
      "capability_result_response_duration",
      options.timings.resultResponseSeconds,
      "seconds",
    ));
  }

  if (options.actions.length === 0) {
    metrics.push(metric("capability_no_action_count", 1, "count"));
    audit.push({
      type: "branch_taken",
      phase: "conversation",
      decisionType: "conversation_route",
      action: "reply_only",
      reasonCode: "no_action_proposed",
      riskLevel: "low",
      details: {
        entrypoint: "lark",
        actionCount: 0,
        decision: "reply_only",
      },
    });
    return { source: "lark-bridge", audit, metrics };
  }

  metrics.push(metric("capability_action_count", options.actions.length, "count"));
  if (!options.planAccepted) {
    metrics.push(metric("capability_policy_reject_count", 1, "count"));
    audit.push({
      type: "policy_evaluation",
      phase: "conversation",
      decisionType: "capability",
      action: "reject",
      reasonCode: options.planRejectReasonCode || "plan_rejected",
      riskLevel: "medium",
      details: {
        entrypoint: "lark",
        actionCount: options.actions.length,
        decision: "reject",
      },
    });
    return { source: "lark-bridge", audit, metrics };
  }

  for (let index = 0; index < options.actions.length; index += 1) {
    const action = options.actions[index];
    const result = options.results?.[index];
    audit.push({
      type: "policy_evaluation",
      phase: "conversation",
      decisionType: "capability",
      action: "allow",
      reasonCode: "validated_capability_plan",
      riskLevel: "low",
      details: {
        entrypoint: "lark",
        capability: action.capability,
        decision: "allow",
        taskBound: "taskCode" in action.arguments,
        confirmationRequired: result?.status === "pending_confirmation",
      },
    });
    if (result) {
      audit.push({
        type: "action_executed",
        phase: "conversation",
        decisionType: "action",
        action: result.status,
        reasonCode: result.ok ? "executor_completed" : "executor_not_completed",
        riskLevel: result.ok ? "low" : "medium",
        details: {
          entrypoint: "lark",
          capability: action.capability,
          actionType: action.capability,
          target: "conversation_task",
          result: result.status,
          sideEffectCommitted: result.status === "completed",
        },
      });
      if (!result.ok) {
        metrics.push(metric("capability_executor_failure_count", 1, "count"));
      }
      if (result.status === "pending_confirmation") {
        metrics.push(metric("capability_confirmation_count", 1, "count"));
      }
    }
  }
  return { source: "lark-bridge", audit, metrics };
}

export function buildDuplicateObservation(
  reasonCode: "duplicate_message" | "duplicate_card" = "duplicate_message",
): TurnObservation {
  return {
    source: "lark-bridge",
    audit: [{
      type: "branch_taken",
      phase: "conversation",
      decisionType: "dedupe",
      action: "suppress",
      reasonCode,
      riskLevel: "low",
      details: {
        entrypoint: "lark",
        duplicateSuppressed: true,
        decision: "suppress",
      },
    }],
    metrics: [
      metric("capability_duplicate_suppressed_count", 1, "count"),
    ],
  };
}

export function buildCardObservation(options: {
  capability: "task.create" | "task.modify" | "task.answer";
  result: "confirmed" | "cancelled" | "completed" | "failed" | "stale";
  duplicateSuppressed?: boolean;
}): TurnObservation {
  const metrics: ObservationMetric[] = [];
  if (options.result === "cancelled") {
    metrics.push(metric("capability_confirmation_cancel_count", 1, "count"));
  }
  if (options.result === "failed") {
    metrics.push(metric("capability_executor_failure_count", 1, "count"));
  }
  if (options.duplicateSuppressed) {
    metrics.push(metric("capability_duplicate_suppressed_count", 1, "count"));
  }
  return {
    source: "lark-bridge",
    audit: [{
      type: "action_executed",
      phase: "conversation",
      decisionType: "card_action",
      action: options.result,
      reasonCode: `card_${options.result}`,
      riskLevel: options.result === "failed" ? "medium" : "low",
      details: {
        entrypoint: "lark",
        capability: options.capability,
        actionType: "card_action",
        target: "conversation_task",
        result: options.result,
        sideEffectCommitted: ["confirmed", "completed"].includes(options.result),
        duplicateSuppressed: options.duplicateSuppressed ?? false,
      },
    }],
    metrics,
  };
}
