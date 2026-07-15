/**
 * dedupe.ts (design §13.5 / M5) — a tiny bounded LRU-ish set for idempotent
 * event handling. Feishu delivers events at-least-once, so the same message or
 * card tap can arrive more than once (retries, reconnects). We drop a repeat
 * by remembering recently-seen keys.
 *
 * Pure and injectable: no timers, no I/O. `now` is injectable for tests.
 */
export interface DedupeOptions {
  /** Max keys to retain before evicting the oldest. */
  maxEntries?: number;
  /** Time-to-live per key in ms; expired keys are treated as unseen. */
  ttlMs?: number;
  /** Injectable clock (defaults to Date.now). */
  now?: () => number;
}

export class Deduper {
  private readonly maxEntries: number;
  private readonly ttlMs: number;
  private readonly now: () => number;
  /** key -> insertion timestamp; Map preserves insertion order for eviction. */
  private readonly seen = new Map<string, number>();

  constructor(options: DedupeOptions = {}) {
    this.maxEntries = options.maxEntries ?? 1000;
    this.ttlMs = options.ttlMs ?? 10 * 60 * 1000;
    this.now = options.now ?? (() => Date.now());
  }

  /**
   * Record `key` and report whether it is a duplicate. Returns true when the
   * key was already seen within the TTL (caller should skip processing).
   */
  isDuplicate(key: string): boolean {
    const ts = this.now();
    const previous = this.seen.get(key);
    if (previous !== undefined && ts - previous < this.ttlMs) {
      return true;
    }
    // (Re)insert at the end so eviction order stays newest-last.
    this.seen.delete(key);
    this.seen.set(key, ts);
    this.evict(ts);
    return false;
  }

  /** Roll back a reservation when processing failed before successful commit. */
  forget(key: string): void {
    this.seen.delete(key);
  }

  private evict(now: number): void {
    // Drop expired entries first, then trim to size from the oldest end.
    for (const [key, ts] of this.seen) {
      if (now - ts >= this.ttlMs) {
        this.seen.delete(key);
      } else {
        break; // insertion order == age order, so the rest are fresher.
      }
    }
    while (this.seen.size > this.maxEntries) {
      const oldest = this.seen.keys().next().value;
      if (oldest === undefined) break;
      this.seen.delete(oldest);
    }
  }
}
