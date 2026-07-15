import { describe, it, expect } from "vitest";
import { redactText } from "./redact.js";

describe("redactText", () => {
  it("redacts key=value pairs with sensitive key names", () => {
    expect(redactText("LARK_APP_SECRET=abcdef123456")).toBe(
      "LARK_APP_SECRET=<redacted>",
    );
    expect(redactText("api_key = xyz")).toBe("api_key=<redacted>");
  });

  it("redacts inline secret-shaped values", () => {
    const out = redactText("token is sk-abcdef1234567890 ok");
    expect(out).toContain("<redacted>");
    expect(out).not.toContain("sk-abcdef1234567890");
  });

  it("leaves ordinary text untouched", () => {
    expect(redactText("编译/测试通过 ✅")).toBe("编译/测试通过 ✅");
  });

  it("handles multi-line input line by line", () => {
    const out = redactText("password=secretval\nnormal line");
    expect(out).toBe("password=<redacted>\nnormal line");
  });
});
