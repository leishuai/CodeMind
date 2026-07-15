/**
 * backoff.ts (design §13.5 / M5) — pure exponential-backoff helpers used by the
 * long-connection reconnect loop. Kept side-effect free so the schedule is
 * unit-testable without real timers.
 */
export interface BackoffOptions {
  /** First delay in ms. */
  baseMs?: number;
  /** Upper bound per delay in ms. */
  maxMs?: number;
  /** Multiplier per attempt. */
  factor?: number;
}

/**
 * Delay before retry `attempt` (0-based): base * factor^attempt, capped at max.
 * Deterministic (no jitter) so tests can assert exact values.
 */
export function nextBackoffMs(attempt: number, options: BackoffOptions = {}): number {
  const base = options.baseMs ?? 1000;
  const max = options.maxMs ?? 30_000;
  const factor = options.factor ?? 2;
  const raw = base * Math.pow(factor, Math.max(0, attempt));
  return Math.min(max, Math.round(raw));
}

export type Sleep = (ms: number) => Promise<void>;

/** Default real-timer sleep. */
export const realSleep: Sleep = (ms) =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Keep calling `connect` (which resolves once a session ends or rejects on a
 * failed attempt) forever, waiting a backoff delay between attempts. Returns
 * only when `shouldStop()` becomes true, enabling clean shutdown in tests.
 */
export async function runWithReconnect(
  connect: () => Promise<void>,
  opts: {
    backoff?: BackoffOptions;
    sleep?: Sleep;
    shouldStop?: () => boolean;
    onError?: (err: unknown, attempt: number) => void;
  } = {},
): Promise<void> {
  const sleep = opts.sleep ?? realSleep;
  const shouldStop = opts.shouldStop ?? (() => false);
  let attempt = 0;
  while (!shouldStop()) {
    try {
      await connect();
      attempt = 0; // a clean session resets the backoff schedule.
    } catch (err) {
      opts.onError?.(err, attempt);
      attempt += 1;
    }
    if (shouldStop()) break;
    await sleep(nextBackoffMs(attempt, opts.backoff));
  }
}
