import { describe, expect, it } from "vitest";
import {
  buildCardObservation,
  buildDuplicateObservation,
  buildTurnObservation,
} from "./turn-observation.js";

describe("turn observations", () => {
  it("records reply-only turns without user text or prompts", () => {
    const observation = buildTurnObservation({
      actions: [],
      planAccepted: true,
      timings: { planSeconds: 0.2, totalSeconds: 0.3 },
    });
    expect(observation.metrics).toContainEqual({
      name: "capability_no_action_count",
      value: 1,
      unit: "count",
    });
    const serialized = JSON.stringify(observation);
    expect(serialized).not.toContain("userText");
    expect(serialized).not.toContain("prompt");
  });

  it("folds parse fallback into the same turn observation", () => {
    const observation = buildTurnObservation({
      actions: [],
      parseFallback: true,
      planAccepted: true,
      timings: { totalSeconds: 0.1 },
    });
    expect(observation.metrics).toContainEqual({
      name: "capability_parse_failure_count",
      value: 1,
      unit: "count",
    });
    expect(observation.audit.some((item) =>
      item.type === "fallback_triggered")).toBe(true);
  });

  it("records model response schema rejection separately", () => {
    const observation = buildTurnObservation({
      actions: [],
      schemaReject: true,
      planAccepted: true,
      timings: { totalSeconds: 0.1 },
    });
    expect(observation.metrics).toContainEqual({
      name: "capability_schema_reject_count",
      value: 1,
      unit: "count",
    });
  });

  it("records allowed actions and executor results", () => {
    const observation = buildTurnObservation({
      actions: [{
        capability: "task.status",
        arguments: { taskCode: "task01" },
        confidence: 1,
        reason: "",
      }],
      results: [{
        capability: "task.status",
        ok: true,
        status: "completed",
        message: "done",
      }],
      planAccepted: true,
      timings: {
        planSeconds: 0.1,
        executorSeconds: 0.2,
        resultResponseSeconds: 0.3,
        totalSeconds: 0.6,
      },
    });
    expect(observation.audit.map((item) => item.type)).toEqual([
      "policy_evaluation",
      "action_executed",
    ]);
    expect(observation.metrics).toContainEqual({
      name: "capability_action_count",
      value: 1,
      unit: "count",
    });
  });

  it("records rejected plans and duplicates", () => {
    const rejected = buildTurnObservation({
      actions: [{
        capability: "task.resume",
        arguments: { taskCode: "other-task" },
        confidence: 1,
        reason: "",
      }],
      planAccepted: false,
      planRejectReasonCode: "task_not_owned",
      timings: { totalSeconds: 0.1 },
    });
    expect(rejected.metrics).toContainEqual({
      name: "capability_policy_reject_count",
      value: 1,
      unit: "count",
    });
    expect(buildDuplicateObservation().metrics[0].name).toBe(
      "capability_duplicate_suppressed_count",
    );
  });

  it("records confirmation cancellation and action failure", () => {
    const cancelled = buildCardObservation({
      capability: "task.create",
      result: "cancelled",
    });
    expect(cancelled.metrics).toContainEqual({
      name: "capability_confirmation_cancel_count",
      value: 1,
      unit: "count",
    });
    const failed = buildCardObservation({
      capability: "task.answer",
      result: "failed",
    });
    expect(failed.metrics).toContainEqual({
      name: "capability_executor_failure_count",
      value: 1,
      unit: "count",
    });
  });
});
