import { describe, it, expect } from "vitest";
import { nextBackoffMs, runWithReconnect } from "./backoff.js";

describe("nextBackoffMs", () => {
  it("grows exponentially from base", () => {
    const opts = { baseMs: 1000, factor: 2, maxMs: 30_000 };
    expect(nextBackoffMs(0, opts)).toBe(1000);
    expect(nextBackoffMs(1, opts)).toBe(2000);
    expect(nextBackoffMs(2, opts)).toBe(4000);
  });

  it("caps at maxMs", () => {
    const opts = { baseMs: 1000, factor: 2, maxMs: 5000 };
    expect(nextBackoffMs(10, opts)).toBe(5000);
  });
});

describe("runWithReconnect", () => {
  it("retries after a failed connect and stops when told", async () => {
    const attempts: number[] = [];
    let calls = 0;
    await runWithReconnect(
      async () => {
        calls += 1;
        if (calls <= 2) throw new Error("boom");
        // third call succeeds and we ask to stop right after.
      },
      {
        sleep: async () => {},
        onError: (_e, attempt) => attempts.push(attempt),
        shouldStop: () => calls >= 3,
      },
    );
    expect(calls).toBe(3);
    expect(attempts).toEqual([0, 1]);
  });

  it("does not loop when already stopped", async () => {
    let calls = 0;
    await runWithReconnect(
      async () => {
        calls += 1;
      },
      { sleep: async () => {}, shouldStop: () => true },
    );
    expect(calls).toBe(0);
  });
});
