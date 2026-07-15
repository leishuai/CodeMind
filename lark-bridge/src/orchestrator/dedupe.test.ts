import { describe, it, expect } from "vitest";
import { Deduper } from "./dedupe.js";

describe("Deduper", () => {
  it("reports first-seen keys as not duplicate, repeats as duplicate", () => {
    const d = new Deduper({ now: () => 1000 });
    expect(d.isDuplicate("a")).toBe(false);
    expect(d.isDuplicate("a")).toBe(true);
    expect(d.isDuplicate("b")).toBe(false);
  });

  it("treats a key as unseen again after its TTL expires", () => {
    let t = 0;
    const d = new Deduper({ ttlMs: 100, now: () => t });
    expect(d.isDuplicate("a")).toBe(false);
    t = 50;
    expect(d.isDuplicate("a")).toBe(true); // within TTL
    t = 200;
    expect(d.isDuplicate("a")).toBe(false); // TTL elapsed -> unseen
  });

  it("evicts oldest entries beyond maxEntries", () => {
    let t = 0;
    const d = new Deduper({ maxEntries: 2, ttlMs: 1_000_000, now: () => (t += 1) });
    d.isDuplicate("a");
    d.isDuplicate("b");
    d.isDuplicate("c"); // evicts "a"
    expect(d.isDuplicate("a")).toBe(false); // "a" was evicted -> unseen
    expect(d.isDuplicate("b")).toBe(false); // "b" also evicted after inserting "a"
  });

  it("allows retry after a failed handler rolls back its reservation", () => {
    const d = new Deduper({ now: () => 1000 });
    expect(d.isDuplicate("a")).toBe(false);
    d.forget("a");
    expect(d.isDuplicate("a")).toBe(false);
  });
});
