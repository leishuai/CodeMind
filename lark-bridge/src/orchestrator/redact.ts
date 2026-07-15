/**
 * redact.ts (design §13.5 / M5) — outbound-side secret redaction for the
 * bridge. Mirrors the core's `session/events.py` redaction so tokens/keys never
 * leak into Feishu messages or bridge logs. Pure and dependency-free.
 */

/** Sensitive key names (matched on the left side of `key=value` lines). */
const SENSITIVE_KEY =
  /(api[_-]?key|token|secret|password|passwd|auth[_-]?token|access[_-]?token|private[_-]?key|secret[_-]?key)/i;

/** Sensitive value shapes (OpenAI-style keys, Figma tokens, JWT-like triples). */
const SENSITIVE_VALUE =
  /(sk-[A-Za-z0-9_-]{12,}|figd_[A-Za-z0-9_-]{12,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})/g;

/** Redact secrets from a free-text string (per-line, like the core). */
export function redactText(text: string): string {
  return String(text ?? "")
    .split("\n")
    .map((line) => {
      const eq = line.indexOf("=");
      if (eq !== -1) {
        const key = line.slice(0, eq).trim();
        if (SENSITIVE_KEY.test(key)) {
          return `${key}=<redacted>`;
        }
      }
      return line.replace(SENSITIVE_VALUE, "<redacted>");
    })
    .join("\n");
}
